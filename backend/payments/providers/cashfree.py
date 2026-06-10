"""
Cashfree Payments v3 provider for aiclonechats.com (PRODUCTION integration).

Plugs into the Payment Gateway Abstraction Layer the same way Instamojo does:
- Self-registers via `register_provider()` at module import time.
- `server.py` imports `payments.providers` which imports this module.
- Set `PAYMENT_PROVIDER=cashfree` in /app/backend/.env to make it active.

Cashfree v3 REST API:
  Sandbox base: https://sandbox.cashfree.com/pg
  Live base:    https://api.cashfree.com/pg
  Headers:
    x-client-id:     CASHFREE_APP_ID
    x-client-secret: CASHFREE_SECRET_KEY
    x-api-version:   2023-08-01

Endpoints we use:
  POST /pg/orders                    → create order, returns payment_session_id
  GET  /pg/orders/{order_id}         → fetch authoritative order_status
  GET  /pg/orders/{order_id}/payments → fetch payments[] for reconcile

Webhook signature (v3):
  HMAC-SHA256(secret, timestamp + raw_body), base64 encoded.
  Headers: x-webhook-signature, x-webhook-timestamp.

Constitutional rules (same as every provider):
- Persist `payment_orders` BEFORE calling the gateway so a network error still
  leaves an audit trail.
- Credits are granted ONLY inside `handle_webhook` (after sig verify + amount
  equality + dedup) or inside `verify_payment` (after authoritative GET).
- Every order tagged `provider="cashfree"` so the historical Cashfree audit
  data from the pre-2026-05-11 era stays unambiguous (those rows existed before
  this provider — they are read-only history, never fulfilled by us).
"""
from __future__ import annotations

import os
import hmac
import hashlib
import base64
import logging
from typing import Optional

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

CASHFREE_API_VERSION = "2023-08-01"


def _env() -> str:
    """`prod` (default for this account) or `sandbox`."""
    return (os.environ.get("CASHFREE_ENV") or "prod").lower().strip()


def _app_id() -> str:
    return (os.environ.get("CASHFREE_APP_ID") or "").strip()


def _secret_key() -> str:
    return (os.environ.get("CASHFREE_SECRET_KEY") or "").strip()


def _webhook_secret() -> str:
    """Cashfree v3 signs webhooks with the same secret key. If the dashboard
    issues a separate webhook secret, set CASHFREE_WEBHOOK_SECRET; otherwise
    we fall back to CASHFREE_SECRET_KEY."""
    explicit = (os.environ.get("CASHFREE_WEBHOOK_SECRET") or "").strip()
    return explicit or _secret_key()


def _base_url() -> str:
    explicit = (os.environ.get("CASHFREE_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    if _env() in ("prod", "production", "live"):
        return "https://api.cashfree.com/pg"
    return "https://sandbox.cashfree.com/pg"


def _webhook_callback_url() -> str:
    backend = (os.environ.get("BACKEND_PUBLIC_URL") or os.environ.get("FRONTEND_PUBLIC_URL") or "").rstrip("/")
    return f"{backend}/api/payments/cashfree/webhook"


def _verify_webhook_signature(*, raw_body: bytes, timestamp: str, signature_b64: str, secret: str) -> bool:
    """Cashfree v3 spec: signed = base64( hmac_sha256(secret, timestamp + raw_body) ).
    Constant-time compare against the supplied signature header."""
    if not (timestamp and signature_b64 and secret):
        return False
    message = (timestamp.encode("utf-8")) + (raw_body or b"")
    expected = base64.b64encode(hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()).decode()
    try:
        return hmac.compare_digest(expected, signature_b64)
    except Exception:
        return False


def _normalize_phone(raw: Optional[str]) -> str:
    """Cashfree requires a 10-digit Indian phone number. Falls back to a
    deterministic placeholder if the user has no phone on file — Cashfree
    will still accept it for digital goods orders."""
    p = (raw or "").strip()
    digits = "".join(c for c in p if c.isdigit())
    if len(digits) >= 10:
        return digits[-10:]
    return "9999999999"


class CashfreeProvider(PaymentProvider):
    name = "cashfree"
    display_name = "Cashfree"

    def status(self) -> ProviderStatus:
        configured = bool(_app_id() and _secret_key())
        return ProviderStatus(
            provider=self.name,
            env="prod" if _env() in ("prod", "production", "live") else "sandbox",
            configured=configured,
            display_name=self.display_name,
        )

    # ---------- create_order ----------
    async def create_order(self, order: OrderRequest) -> OrderResponse:
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

        body = {
            "order_id": order.order_id,
            "order_amount": round(float(order.amount), 2),
            "order_currency": order.currency,
            "customer_details": {
                "customer_id": order.user_id,
                "customer_email": order.user_email,
                "customer_phone": _normalize_phone(order.user_phone),
                "customer_name": order.user_name[:100],
            },
            "order_meta": {
                "return_url": f"{order.success_url}&cf_order_id={{order_id}}" if "?" in order.success_url else f"{order.success_url}?cf_order_id={{order_id}}",
                "notify_url": _webhook_callback_url(),
            },
            "order_note": f"{order.item_name} ({order.kind})",
            "order_tags": {
                "kind": order.kind,
                "plan_id": order.plan_id or "",
                "pack_id": order.pack_id or "",
                "credits": str(order.credits),
            },
        }
        headers = {
            "x-client-id": _app_id(),
            "x-client-secret": _secret_key(),
            "x-api-version": CASHFREE_API_VERSION,
            "Content-Type": "application/json",
        }
        url = f"{_base_url()}/orders"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=body, headers=headers)
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_raw": resp.text, "_status": resp.status_code}
        except Exception as e:
            await db.payment_orders.update_one(
                {"order_id": order.order_id},
                {"$set": {"status": "failed", "failure_reason": f"gateway_unreachable: {e}", "updated_at": now_iso()}},
            )
            logger.exception("cashfree create_order network error")
            return OrderResponse(ok=False, order_id=order.order_id, provider=self.name, env=_env(), error="Could not reach the payment gateway.", error_code="gateway_unreachable")

        payment_session_id = (data or {}).get("payment_session_id")
        cf_order_id = (data or {}).get("cf_order_id")
        order_status = (data or {}).get("order_status")

        if not payment_session_id:
            err = (data or {}).get("message") or (data or {}).get("error") or str(data)[:300]
            await db.payment_orders.update_one(
                {"order_id": order.order_id},
                {"$set": {"status": "failed", "failure_reason": str(err)[:500], "updated_at": now_iso()}},
            )
            logger.warning("cashfree rejected create order=%s body=%s", order.order_id, data)
            return OrderResponse(ok=False, order_id=order.order_id, provider=self.name, env=_env(), error=str(err)[:300], error_code="gateway_rejected")

        await db.payment_orders.update_one(
            {"order_id": order.order_id},
            {"$set": {
                "status": "pending",
                "cashfree_order_id": cf_order_id,
                "cashfree_payment_session_id": payment_session_id,
                "cashfree_order_status_initial": order_status,
                "updated_at": now_iso(),
            }},
        )

        return OrderResponse(
            ok=True,
            order_id=order.order_id,
            provider=self.name,
            env="production" if _env() in ("prod", "production", "live") else "sandbox",
            provider_payload={
                "payment_session_id": payment_session_id,
                "cf_order_id": cf_order_id,
                "mode": "production" if _env() in ("prod", "production", "live") else "sandbox",
            },
        )

    # ---------- verify_payment ----------
    async def verify_payment(self, order_id: str) -> VerifyResult:
        order = await db.payment_orders.find_one({"order_id": order_id, "provider": self.name}, {"_id": 0})
        if not order:
            return VerifyResult(ok=False, status="unknown", reason="order_not_found")

        headers = {
            "x-client-id": _app_id(),
            "x-client-secret": _secret_key(),
            "x-api-version": CASHFREE_API_VERSION,
        }
        url = f"{_base_url()}/orders/{order_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_raw": resp.text}
        except Exception:
            logger.exception("cashfree verify_payment network error order=%s", order_id)
            return VerifyResult(ok=False, status=order.get("status") or "pending", reason="gateway_unreachable")

        order_status = (data or {}).get("order_status") or ""
        payments_url = f"{_base_url()}/orders/{order_id}/payments"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                presp = await client.get(payments_url, headers=headers)
            payments = presp.json() if presp.headers.get("content-type", "").startswith("application/json") else []
        except Exception:
            payments = []

        successful_payment = None
        if isinstance(payments, list):
            for p in payments:
                if (p.get("payment_status") or "").upper() == "SUCCESS":
                    successful_payment = p
                    break

        credited = False
        if order_status.upper() == "PAID" and successful_payment:
            payment_amount = float(successful_payment.get("payment_amount") or 0)
            if abs(payment_amount - float(order.get("amount") or 0)) > 0.01:
                logger.warning("cashfree amount mismatch order=%s expected=%s got=%s", order_id, order.get("amount"), payment_amount)
                return VerifyResult(ok=False, status="failed", reason="amount_mismatch", raw=data)

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
                        "cashfree_payment_id": successful_payment.get("cf_payment_id"),
                        "payment_mode": successful_payment.get("payment_method") or successful_payment.get("payment_group"),
                        "credited_at": now_iso(),
                        "updated_at": now_iso(),
                        "balance_after": new_balance,
                    }},
                )
            return VerifyResult(ok=True, status="paid", credited=credited, provider_txn_id=str(successful_payment.get("cf_payment_id") or ""), raw=data)

        if order_status.upper() in ("EXPIRED", "TERMINATED"):
            await db.payment_orders.update_one(
                {"order_id": order_id, "status": {"$nin": ["paid"]}},
                {"$set": {"status": "failed", "failure_reason": order_status, "updated_at": now_iso()}},
            )
            return VerifyResult(ok=True, status="failed", raw=data)

        return VerifyResult(ok=True, status="pending", raw=data)

    # ---------- handle_webhook ----------
    async def handle_webhook(self, *, raw_body: bytes, headers: dict, content_type: str) -> WebhookResult:
        signature = (headers.get("x-webhook-signature") or "").strip()
        timestamp = (headers.get("x-webhook-timestamp") or "").strip()

        try:
            raw_text = (raw_body or b"").decode("utf-8", "replace")
        except Exception:
            raw_text = ""

        log_doc = {
            "received_at": now_iso(),
            "provider": self.name,
            "source": "webhook",
            "raw": raw_text[:4000],
            "x_webhook_timestamp": timestamp,
            "x_webhook_signature_present": bool(signature),
        }

        if not _webhook_secret():
            log_doc["result"] = "gateway_not_configured"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=False, status="unknown", reason="gateway_not_configured")

        sig_valid = _verify_webhook_signature(
            raw_body=raw_body or b"",
            timestamp=timestamp,
            signature_b64=signature,
            secret=_webhook_secret(),
        )
        log_doc["sig_valid"] = sig_valid

        if not sig_valid:
            log_doc["result"] = "invalid_signature"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=False, status="unknown", reason="invalid_signature")

        # Parse JSON body (Cashfree v3 sends application/json)
        try:
            import json
            payload = json.loads(raw_text or "{}")
        except Exception:
            log_doc["result"] = "bad_json"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=False, status="unknown", reason="bad_json")

        event_type = (payload.get("type") or "").upper()
        data_block = payload.get("data") or {}
        order_block = data_block.get("order") or {}
        payment_block = data_block.get("payment") or {}

        order_id = (order_block.get("order_id") or "").strip()
        cf_payment_id = str(payment_block.get("cf_payment_id") or "")
        payment_status = (payment_block.get("payment_status") or "").upper()

        log_doc["order_id"] = order_id
        log_doc["cf_payment_id"] = cf_payment_id
        log_doc["event_type"] = event_type
        log_doc["payment_status"] = payment_status

        order = await db.payment_orders.find_one({"order_id": order_id, "provider": self.name}, {"_id": 0})
        if not order:
            log_doc["result"] = "order_not_found"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=False, status="unknown", reason="order_not_found", provider_txn_id=cf_payment_id)

        # Idempotency
        dedup_key = f"cashfree:{order_id}:{cf_payment_id}:{payment_status}"
        try:
            await db.webhook_dedup.insert_one({"dedup_key": dedup_key, "created_at": now_iso()})
        except Exception:
            log_doc["result"] = "duplicate"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=True, status="duplicate", credited=False, order_id=order_id, reason="duplicate", provider_txn_id=cf_payment_id)

        # Amount integrity
        try:
            payload_amount = float(payment_block.get("payment_amount") or order_block.get("order_amount") or 0)
        except Exception:
            payload_amount = 0.0
        if abs(payload_amount - float(order.get("amount") or 0)) > 0.01:
            log_doc["result"] = "amount_mismatch"
            await db.webhook_logs.insert_one(log_doc)
            await db.payment_orders.update_one(
                {"order_id": order_id},
                {"$set": {"status": "failed", "failure_reason": "amount_mismatch", "updated_at": now_iso()}},
            )
            return WebhookResult(ok=False, status="failed", reason="amount_mismatch", order_id=order_id, provider_txn_id=cf_payment_id)

        credited = False
        if payment_status == "SUCCESS" or event_type == "PAYMENT_SUCCESS_WEBHOOK":
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
                        "cashfree_payment_id": cf_payment_id,
                        "payment_mode": payment_block.get("payment_group") or payment_block.get("payment_method"),
                        "credited_at": now_iso(),
                        "updated_at": now_iso(),
                        "balance_after": new_balance,
                    }},
                )
            log_doc["result"] = "paid"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=True, status="paid", credited=credited, order_id=order_id, provider_txn_id=cf_payment_id)

        if payment_status in ("FAILED", "USER_DROPPED", "CANCELLED") or event_type == "PAYMENT_FAILED_WEBHOOK":
            await db.payment_orders.update_one(
                {"order_id": order_id, "status": {"$nin": ["paid"]}},
                {"$set": {"status": "failed", "cashfree_payment_id": cf_payment_id, "failure_reason": payment_block.get("payment_message") or payment_status, "updated_at": now_iso()}},
            )
            log_doc["result"] = "failed"
            await db.webhook_logs.insert_one(log_doc)
            return WebhookResult(ok=True, status="failed", credited=False, order_id=order_id, provider_txn_id=cf_payment_id)

        log_doc["result"] = "pending"
        await db.webhook_logs.insert_one(log_doc)
        return WebhookResult(ok=True, status="pending", credited=False, order_id=order_id, provider_txn_id=cf_payment_id)

    # ---------- refund_payment ----------
    async def refund_payment(self, *, order_id: str, amount: Optional[float] = None, reason: str = "") -> RefundResult:
        order = await db.payment_orders.find_one({"order_id": order_id, "provider": self.name}, {"_id": 0})
        if not order:
            return RefundResult(ok=False, status="not_implemented", reason="order_not_found")
        if order.get("status") != "paid":
            return RefundResult(ok=False, status="not_implemented", reason="order_not_paid")
        # Real Cashfree refund implementation can be wired in once operations
        # confirms the refund policy. The Cashfree v3 endpoint is
        #   POST /pg/orders/{order_id}/refunds with refund_id + refund_amount.
        return RefundResult(
            ok=False,
            status="not_implemented",
            reason="Cashfree refund flow requires operations sign-off; implement once policy is locked.",
        )


# Self-register on import.
register_provider(CashfreeProvider())
