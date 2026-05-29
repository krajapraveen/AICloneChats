"""
Instamojo provider — fits into the Payment Gateway Abstraction Layer.

Wire-up:
  1. This module self-registers on import via `register_provider()` at the bottom.
  2. `server.py` imports `payments.providers.instamojo` so the registration runs at boot.
  3. Set `PAYMENT_PROVIDER=instamojo` in /app/backend/.env to flip it active.

API used: Instamojo v1.1 REST (X-Api-Key + X-Auth-Token).
Sandbox base:  https://test.instamojo.com/api/1.1/
Live base:     https://www.instamojo.com/api/1.1/

Webhook signature (MAC):
  HMAC-SHA1, key = Private Salt (INSTAMOJO_WEBHOOK_SECRET),
  message = pipe-joined values of the POST body parameters (excluding `mac`)
  sorted alphabetically by key.

Constitutional guarantees re-enforced here:
- Credits are granted ONLY inside `handle_webhook` after MAC verification + amount
  equality + `credited_at` idempotency guard, OR inside `verify_payment` after a
  direct Payment Details API call confirms status=Credit.
- Every order is persisted with `provider="instamojo"` so historical Cashfree
  (`provider=cashfree`) and Easebuzz (`provider=easebuzz, legacy=true`) audit data
  remains unambiguous.
"""
from __future__ import annotations

import os
import hmac
import hashlib
import logging
import uuid
from typing import Optional
from urllib.parse import parse_qsl

import httpx

from db import db
from credits import credit_payment
from models import now_iso

from ..base import (
    PaymentProvider,
    OrderRequest,
    OrderResponse,
    VerifyResult,
    WebhookResult,
    RefundResult,
    ProviderStatus,
)
from ..registry import register_provider

logger = logging.getLogger(__name__)


def _env() -> str:
    return (os.environ.get("INSTAMOJO_ENV") or "test").lower().strip()


def _api_key() -> str:
    return (os.environ.get("INSTAMOJO_API_KEY") or "").strip()


def _auth_token() -> str:
    return (os.environ.get("INSTAMOJO_AUTH_TOKEN") or "").strip()


def _webhook_secret() -> str:
    return (os.environ.get("INSTAMOJO_WEBHOOK_SECRET") or "").strip()


def _base_url() -> str:
    explicit = (os.environ.get("INSTAMOJO_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit + "/" if not explicit.endswith("/") else explicit
    if _env() == "live" or _env() == "prod":
        return "https://www.instamojo.com/api/1.1/"
    return "https://test.instamojo.com/api/1.1/"


def _webhook_callback_url() -> str:
    backend = (os.environ.get("BACKEND_PUBLIC_URL") or "").rstrip("/")
    if not backend:
        # Last-resort fallback for dev — Instamojo will still call us via the
        # public URL registered in their dashboard.
        backend = (os.environ.get("FRONTEND_PUBLIC_URL") or "").rstrip("/")
    return f"{backend}/api/payments/instamojo/webhook"


def _compute_mac(payload: dict, salt: str) -> str:
    """Instamojo webhook signature: HMAC-SHA1 of pipe-joined sorted values."""
    keys = sorted(k for k in payload.keys() if k != "mac")
    message = "|".join(str(payload[k]) for k in keys)
    return hmac.new(salt.encode("utf-8"), message.encode("utf-8"), hashlib.sha1).hexdigest()


class InstamojoProvider(PaymentProvider):
    name = "instamojo"
    display_name = "Instamojo"

    def status(self) -> ProviderStatus:
        configured = bool(_api_key() and _auth_token() and _webhook_secret())
        return ProviderStatus(
            provider=self.name,
            env=_env(),
            configured=configured,
            display_name=self.display_name,
        )

    # ---------- create_order ----------
    async def create_order(self, order: OrderRequest) -> OrderResponse:
        # Persist BEFORE calling the gateway so a network error leaves a trail.
        await db.payment_orders.insert_one({
            "order_id": order.order_id,
            "user_id": order.user_id,
            "email": order.user_email,
            "kind": order.kind,
            "plan_id": order.plan_id,
            "pack_id": order.pack_id,
            "credits": order.credits,
            "amount": order.amount,
            "amount_inr": order.amount,
            "currency": order.currency,
            "display_amount": order.display_amount,
            "display_currency": order.display_currency,
            "status": "created",
            "provider": self.name,
            "env": _env(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })

        purpose = (order.item_name or order.plan_id or order.pack_id or "AI Clone Chats")[:30]
        data = {
            "amount": f"{order.amount:.2f}",
            "purpose": purpose,
            "buyer_name": order.user_name[:100],
            "email": order.user_email,
            "phone": order.user_phone or "",
            "send_email": "false",
            "send_sms": "false",
            "redirect_url": order.success_url,
            "webhook": _webhook_callback_url(),
            "allow_repeated_payments": "false",
        }
        headers = {
            "X-Api-Key": _api_key(),
            "X-Auth-Token": _auth_token(),
        }
        url = f"{_base_url()}payment-requests/"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, data=data, headers=headers)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_raw": resp.text}
        except Exception as e:
            await db.payment_orders.update_one(
                {"order_id": order.order_id},
                {"$set": {"status": "failed", "failure_reason": f"gateway_unreachable: {e}", "updated_at": now_iso()}},
            )
            logger.exception("instamojo create_payment_request failed")
            return OrderResponse(ok=False, order_id=order.order_id, provider=self.name, env=_env(), error="Could not reach the payment gateway.", error_code="gateway_unreachable")

        pr = (body or {}).get("payment_request") or {}
        success_flag = (body or {}).get("success")
        payment_request_id = pr.get("id")
        longurl = pr.get("longurl")

        if not (success_flag and payment_request_id and longurl):
            err = (body or {}).get("message") or str(body)[:300]
            await db.payment_orders.update_one(
                {"order_id": order.order_id},
                {"$set": {"status": "failed", "failure_reason": str(err)[:500], "updated_at": now_iso()}},
            )
            logger.warning("instamojo rejected create order=%s body=%s", order.order_id, body)
            return OrderResponse(ok=False, order_id=order.order_id, provider=self.name, env=_env(), error=str(err)[:300], error_code="gateway_rejected")

        await db.payment_orders.update_one(
            {"order_id": order.order_id},
            {"$set": {
                "status": "pending",
                "instamojo_payment_request_id": payment_request_id,
                "longurl": longurl,
                "updated_at": now_iso(),
            }},
        )

        return OrderResponse(
            ok=True,
            order_id=order.order_id,
            provider=self.name,
            env=_env(),
            checkout_url=longurl,
            provider_payload={"payment_request_id": payment_request_id},
        )

    # ---------- verify_payment ----------
    async def verify_payment(self, order_id: str) -> VerifyResult:
        order = await db.payment_orders.find_one({"order_id": order_id, "provider": self.name}, {"_id": 0})
        if not order:
            return VerifyResult(ok=False, status="unknown", reason="order_not_found")

        pr_id = order.get("instamojo_payment_request_id")
        if not pr_id:
            return VerifyResult(ok=False, status="unknown", reason="missing_payment_request_id")

        headers = {"X-Api-Key": _api_key(), "X-Auth-Token": _auth_token()}
        url = f"{_base_url()}payment-requests/{pr_id}/"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_raw": resp.text}
        except Exception:
            logger.exception("instamojo verify_payment network error order=%s", order_id)
            return VerifyResult(ok=False, status=order.get("status") or "pending", reason="gateway_unreachable")

        pr = (body or {}).get("payment_request") or {}
        pr_status = (pr.get("status") or "").lower()         # Pending | Sent | Completed | Failed
        payments = pr.get("payments") or []
        successful_payment = next((p for p in payments if (p.get("status") or "").lower() == "credit"), None)

        credited = False
        if successful_payment:
            # Authoritative success → ensure credits are granted exactly once.
            payment_amount = float(successful_payment.get("amount") or 0)
            if abs(payment_amount - float(order.get("amount") or 0)) > 0.01:
                logger.warning("instamojo amount mismatch order=%s expected=%s got=%s", order_id, order.get("amount"), payment_amount)
                return VerifyResult(ok=False, status="failed", reason="amount_mismatch", raw=body)

            if not order.get("credited_at"):
                new_balance = await credit_payment(
                    user_id=order["user_id"],
                    credits=int(order.get("credits") or 0),
                    order_id=order_id,
                    plan_id=order.get("plan_id") if order.get("kind") == "subscription" else None,
                    kind=order.get("kind") or "subscription",
                    pack_id=order.get("pack_id") if order.get("kind") == "topup" else None,
                )
                credited = True
                await db.payment_orders.update_one(
                    {"order_id": order_id, "credited_at": {"$exists": False}},
                    {"$set": {
                        "status": "paid",
                        "instamojo_payment_id": successful_payment.get("payment_id"),
                        "payment_mode": successful_payment.get("instrument_type"),
                        "credited_at": now_iso(),
                        "updated_at": now_iso(),
                        "balance_after": new_balance,
                    }},
                )
            return VerifyResult(ok=True, status="paid", credited=credited, provider_txn_id=successful_payment.get("payment_id"), raw=body)

        # No successful payment yet — map Instamojo statuses
        terminal_failure = {"failed"}
        if pr_status in terminal_failure:
            await db.payment_orders.update_one(
                {"order_id": order_id, "status": {"$nin": ["paid"]}},
                {"$set": {"status": "failed", "updated_at": now_iso()}},
            )
            return VerifyResult(ok=True, status="failed", raw=body)
        # Pending / Sent → keep as pending
        return VerifyResult(ok=True, status="pending", raw=body)

    # ---------- handle_webhook ----------
    async def handle_webhook(self, *, raw_body: bytes, headers: dict, content_type: str) -> WebhookResult:
        # Instamojo POSTs application/x-www-form-urlencoded
        try:
            raw_text = (raw_body or b"").decode("utf-8", "replace")
        except Exception:
            raw_text = ""
        payload = dict(parse_qsl(raw_text, keep_blank_values=True))

        received_mac = (payload.get("mac") or "").strip().lower()
        payment_request_id = (payload.get("payment_request_id") or "").strip()
        payment_id = (payload.get("payment_id") or "").strip()
        status = (payload.get("status") or "").strip().lower()

        # Audit-log first, regardless of verification outcome.
        log_doc = {
            "received_at": now_iso(),
            "provider": self.name,
            "source": "webhook",
            "payment_request_id": payment_request_id,
            "payment_id": payment_id,
            "status": status,
            "raw": raw_text[:4000],
        }

        if not _webhook_secret():
            log_doc["result"] = "gateway_not_configured"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=False, status="unknown", reason="gateway_not_configured")

        expected_mac = _compute_mac(payload, _webhook_secret()).lower()
        mac_valid = bool(received_mac) and hmac.compare_digest(received_mac, expected_mac)
        log_doc["mac_valid"] = mac_valid

        if not mac_valid:
            log_doc["result"] = "invalid_mac"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=False, status="unknown", reason="invalid_mac", order_id=None, provider_txn_id=payment_id)

        # Resolve our order by Instamojo payment_request_id (we stored it at create-time).
        order = await db.payment_orders.find_one(
            {"instamojo_payment_request_id": payment_request_id, "provider": self.name},
            {"_id": 0},
        )
        if not order:
            log_doc["result"] = "order_not_found"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=False, status="unknown", reason="order_not_found", provider_txn_id=payment_id)

        order_id = order["order_id"]
        log_doc["order_id"] = order_id

        # Idempotency: dedup key spans (provider, payment_request_id, payment_id, status)
        dedup_key = f"instamojo:{payment_request_id}:{payment_id}:{status}"
        try:
            await db.webhook_dedup.insert_one({"dedup_key": dedup_key, "created_at": now_iso()})
        except Exception:
            log_doc["result"] = "duplicate"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=True, status="duplicate", credited=False, order_id=order_id, reason="duplicate", provider_txn_id=payment_id)

        # Amount integrity
        try:
            payload_amount = float(payload.get("amount") or 0)
        except Exception:
            payload_amount = 0.0
        if abs(payload_amount - float(order.get("amount") or 0)) > 0.01:
            log_doc["result"] = "amount_mismatch"
            await db.webhook_logs.insert_one(log_doc)
            await db.payment_orders.update_one(
                {"order_id": order_id},
                {"$set": {"status": "failed", "failure_reason": "amount_mismatch", "updated_at": now_iso()}},
            )
            return WebhookResult(ok=False, status="failed", reason="amount_mismatch", order_id=order_id, provider_txn_id=payment_id)

        credited = False
        if status == "credit":
            # Successful payment — grant credits exactly once.
            if not order.get("credited_at"):
                new_balance = await credit_payment(
                    user_id=order["user_id"],
                    credits=int(order.get("credits") or 0),
                    order_id=order_id,
                    plan_id=order.get("plan_id") if order.get("kind") == "subscription" else None,
                    kind=order.get("kind") or "subscription",
                    pack_id=order.get("pack_id") if order.get("kind") == "topup" else None,
                )
                credited = True
                await db.payment_orders.update_one(
                    {"order_id": order_id, "credited_at": {"$exists": False}},
                    {"$set": {
                        "status": "paid",
                        "instamojo_payment_id": payment_id,
                        "payment_mode": payload.get("instrument_type") or payload.get("payment_request_payment_method"),
                        "credited_at": now_iso(),
                        "updated_at": now_iso(),
                        "balance_after": new_balance,
                    }},
                )
            log_doc["result"] = "paid"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=True, status="paid", credited=credited, order_id=order_id, provider_txn_id=payment_id)

        if status in ("failed", "failure"):
            await db.payment_orders.update_one(
                {"order_id": order_id, "status": {"$nin": ["paid"]}},
                {"$set": {"status": "failed", "instamojo_payment_id": payment_id, "failure_reason": payload.get("failure_reason") or status, "updated_at": now_iso()}},
            )
            log_doc["result"] = "failed"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=True, status="failed", credited=False, order_id=order_id, provider_txn_id=payment_id)

        # Pending / unknown — keep current state, audit only.
        log_doc["result"] = "pending"
        await db.webhook_logs.insert_one(log_doc)
        return WebhookResult(ok=True, status="pending", credited=False, order_id=order_id, provider_txn_id=payment_id)

    # ---------- refund_payment (placeholder; live flow needs Instamojo refund approval) ----------
    async def refund_payment(self, *, order_id: str, amount: Optional[float] = None, reason: str = "") -> RefundResult:
        order = await db.payment_orders.find_one({"order_id": order_id, "provider": self.name}, {"_id": 0})
        if not order:
            return RefundResult(ok=False, status="not_implemented", reason="order_not_found")
        payment_id = order.get("instamojo_payment_id")
        if not payment_id or order.get("status") != "paid":
            return RefundResult(ok=False, status="not_implemented", reason="order_not_paid_or_missing_payment_id")

        # Instamojo Refund API requires a refund 'type' code + body. Until the
        # operations team confirms the refund policy, leave this as a guarded
        # placeholder — the placeholder is intentionally safer than a half-baked
        # auto-refund.
        return RefundResult(
            ok=False,
            status="not_implemented",
            reason="Instamojo refund flow requires operations sign-off; implement once policy is locked.",
        )


# Self-register on import. `server.py` imports this module so the registration
# fires at boot. Safe to import multiple times — `register_provider` is idempotent.
register_provider(InstamojoProvider())
