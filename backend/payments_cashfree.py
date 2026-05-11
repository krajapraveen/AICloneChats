"""
Cashfree Payment Gateway — order creation, hosted checkout, webhook handling.

Security model:
  - Server constructs every order. Frontend NEVER supplies amount or plan_id
    via mutable channels. The plan_id arrives in the body, the price comes
    from PLAN_INDEX. Tampering the body to send a different amount is
    impossible because we don't read amount from the body.
  - Cashfree returns a payment_session_id which is what the frontend uses
    with the JS SDK. We also return the hosted-checkout `payment_link`
    URL as a fallback.
  - Webhook signature is HMAC-SHA256 of (timestamp + raw_body) using the
    Cashfree secret key, base64-encoded, compared to header
    `x-webhook-signature`. We use hmac.compare_digest for constant-time
    comparison.
  - Idempotency: the order document carries `credited_at` after credits are
    applied. Webhook handler only credits if `credited_at` is missing.
    Duplicate webhooks no-op safely.
  - Replay protection: timestamps older than 5 minutes are rejected.
  - Order amount mismatch (between webhook payload and stored order) is
    rejected with a fraud signal log.
"""
from __future__ import annotations

import os
import json
import hmac
import base64
import hashlib
import uuid
import logging
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from db import db
from auth import get_current_user
from models import now_iso
from credits import (
    PLAN_INDEX,
    TOPUP_INDEX,
    credit_payment,
    is_admin_unlimited_user,
    is_active_subscriber,
    _log_fraud_signal,
)
from pricing import compute_price_for_plan, detect_country_from_request

router = APIRouter(prefix="/api/payments", tags=["payments"])
logger = logging.getLogger(__name__)

CASHFREE_APP_ID = os.environ.get("CASHFREE_APP_ID", "").strip()
CASHFREE_SECRET_KEY = os.environ.get("CASHFREE_SECRET_KEY", "").strip()
CASHFREE_MODE = os.environ.get("CASHFREE_MODE", "TEST").upper()
CASHFREE_API_VERSION = os.environ.get("CASHFREE_API_VERSION", "2023-08-01")
FRONTEND_PUBLIC_URL = os.environ.get("FRONTEND_PUBLIC_URL", "").rstrip("/")

CF_BASE = "https://sandbox.cashfree.com/pg" if CASHFREE_MODE == "TEST" else "https://api.cashfree.com/pg"

WEBHOOK_REPLAY_WINDOW_SEC = 300  # 5 minutes


def _cf_headers() -> dict:
    return {
        "x-client-id": CASHFREE_APP_ID,
        "x-client-secret": CASHFREE_SECRET_KEY,
        "x-api-version": CASHFREE_API_VERSION,
        "Content-Type": "application/json",
    }


def _is_configured() -> bool:
    return bool(CASHFREE_APP_ID and CASHFREE_SECRET_KEY)


class CreateOrderRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=40)


@router.get("/config")
async def payments_config():
    """Public — surfaces whether payments are configured + the JS SDK mode."""
    return {
        "configured": _is_configured(),
        "mode": CASHFREE_MODE.lower(),
        "api_version": CASHFREE_API_VERSION,
    }


@router.post("/create-order")
async def create_order(payload: CreateOrderRequest, request: Request, user: dict = Depends(get_current_user)):
    """Server-authored Cashfree order. Frontend never controls amount."""
    if not _is_configured():
        raise HTTPException(503, "Payments are not configured. Try again later.")

    if is_admin_unlimited_user(user):
        raise HTTPException(400, "Admin account has unlimited credits and does not need to pay.")

    plan = PLAN_INDEX.get(payload.plan_id)
    if not plan or not plan.get("is_active") or plan["plan_id"] == "free":
        raise HTTPException(400, "Invalid plan")

    if not user.get("email_verified"):
        raise HTTPException(403, {"code": "email_not_verified", "message": "Verify your email before purchasing."})

    # ---- Resolve country & price (server is the single source of truth) ----
    country_code, country_source = detect_country_from_request(request, user)
    price = compute_price_for_plan(plan["plan_id"], country_code)
    if price["charge_amount"] <= 0:
        raise HTTPException(400, "This plan is free; no payment required.")

    # Persist the user's detected country on first hit so we don't re-detect every order
    if not user.get("country_code"):
        await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"country_code": country_code, "country_source": country_source}})

    order_id = f"order_{user['user_id']}_{int(datetime.now(timezone.utc).timestamp())}_{uuid.uuid4().hex[:6]}"

    # Build hosted-checkout return URL — frontend lands here, then asks the
    # backend whether the order is actually paid. We never trust the URL params.
    return_url = f"{FRONTEND_PUBLIC_URL}/pay/return?order_id={order_id}" if FRONTEND_PUBLIC_URL else None
    # Notification URL = our webhook. Cashfree posts here server-to-server.
    backend_public = os.environ.get("BACKEND_PUBLIC_URL", "").rstrip("/") or os.environ.get("FRONTEND_PUBLIC_URL", "").rstrip("/")
    notify_url = f"{backend_public}/api/payments/webhook/cashfree" if backend_public else None

    body = {
        "order_id": order_id,
        "order_amount": float(price["charge_amount"]),
        "order_currency": price["charge_currency"],
        "customer_details": {
            "customer_id": user["user_id"],
            "customer_email": user["email"],
            "customer_name": user.get("name") or user["email"].split("@")[0],
            "customer_phone": user.get("phone") or "9999999999",  # Cashfree requires this; placeholder OK for sandbox
        },
        "order_meta": {
            "return_url": return_url,
            "notify_url": notify_url,
        },
        "order_note": f"aiclonechats.com — {plan['name']} plan",
        "order_tags": {"plan_id": plan["plan_id"], "user_id": user["user_id"], "country": country_code, "display_currency": price["currency_code"]},
    }

    try:
        r = requests.post(f"{CF_BASE}/orders", headers=_cf_headers(), json=body, timeout=20)
    except Exception as e:
        logger.exception("cashfree create order request failed")
        raise HTTPException(502, f"Payment provider unavailable: {e}")

    if r.status_code not in (200, 201):
        logger.error("cashfree create order non-2xx: %s %s", r.status_code, r.text[:500])
        raise HTTPException(502, f"Payment provider error: {r.text[:200]}")

    cf = r.json()
    payment_session_id = cf.get("payment_session_id")
    if not payment_session_id:
        raise HTTPException(502, "Payment provider returned no session id")

    # Persist the order — this is the source of truth, NOT the webhook.
    await db.payment_orders.insert_one({
        "order_id": order_id,
        "user_id": user["user_id"],
        "email": user["email"],
        "plan_id": plan["plan_id"],
        # Legacy field kept for backward-compat with older tests/admin views.
        "amount_inr": float(price["charge_amount"]) if price["charge_currency"] == "INR" else None,
        # New canonical pricing fields
        "country_code": country_code,
        "country_source": country_source,
        "display_currency": price["currency_code"],
        "display_amount": price["display_amount"],
        "charge_currency": price["charge_currency"],
        "charge_amount": price["charge_amount"],
        "amount_minor": price["amount_minor"],
        "requires_currency_disclosure": price["requires_currency_disclosure"],
        "exchange_source": price["exchange_source"],
        "exchange_version": price["exchange_version"],
        # Credits
        "credits": plan["monthly_credits"],
        "status": "created",
        "cashfree_order_id": cf.get("cf_order_id"),
        "payment_session_id": payment_session_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "credited_at": None,
        "webhook_count": 0,
    })

    return {
        "order_id": order_id,
        "payment_session_id": payment_session_id,
        # Legacy field for backward-compat with the existing pricing UI
        "amount_inr": price["charge_amount"] if price["charge_currency"] == "INR" else None,
        # Canonical price record
        "display_amount": price["display_amount"],
        "display_currency": price["currency_code"],
        "charge_amount": price["charge_amount"],
        "charge_currency": price["charge_currency"],
        "requires_currency_disclosure": price["requires_currency_disclosure"],
        "country_code": country_code,
        "plan_id": plan["plan_id"],
        "mode": CASHFREE_MODE.lower(),
    }


class CreateTopupOrderRequest(BaseModel):
    pack_id: str = Field(min_length=1, max_length=40)


@router.post("/create-topup-order")
async def create_topup_order(payload: CreateTopupOrderRequest, request: Request, user: dict = Depends(get_current_user)):
    """Top-up packs: balance-only purchases reserved for ACTIVE subscribers.

    Server-side gate is the source of truth — even if the frontend exposes
    the button, this 403s for free/expired users.
    """
    if not _is_configured():
        raise HTTPException(503, "Payments are not configured. Try again later.")

    if is_admin_unlimited_user(user):
        raise HTTPException(400, "Admin account has unlimited credits and does not need to pay.")

    pack = TOPUP_INDEX.get(payload.pack_id)
    if not pack or not pack.get("is_active"):
        raise HTTPException(400, "Invalid top-up pack")

    # Hard subscriber gate — re-read the user from DB so a stale dep dict can't bypass.
    fresh = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0, "password_hash": 0})
    if not is_active_subscriber(fresh or user):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "subscription_required_for_topup",
                "message": "Top-up packs are available to active subscribers only. Subscribe to a plan first.",
            },
        )

    if not (fresh or user).get("email_verified"):
        raise HTTPException(403, {"code": "email_not_verified", "message": "Verify your email before purchasing."})

    country_code, country_source = detect_country_from_request(request, fresh or user)
    price = compute_price_for_plan(pack["pack_id"], country_code)
    if price["charge_amount"] <= 0:
        raise HTTPException(400, "This pack has no chargeable amount.")

    if not (fresh or user).get("country_code"):
        await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"country_code": country_code, "country_source": country_source}})

    order_id = f"topup_{user['user_id']}_{int(datetime.now(timezone.utc).timestamp())}_{uuid.uuid4().hex[:6]}"

    return_url = f"{FRONTEND_PUBLIC_URL}/pay/return?order_id={order_id}" if FRONTEND_PUBLIC_URL else None
    backend_public = os.environ.get("BACKEND_PUBLIC_URL", "").rstrip("/") or os.environ.get("FRONTEND_PUBLIC_URL", "").rstrip("/")
    notify_url = f"{backend_public}/api/payments/webhook/cashfree" if backend_public else None

    body = {
        "order_id": order_id,
        "order_amount": float(price["charge_amount"]),
        "order_currency": price["charge_currency"],
        "customer_details": {
            "customer_id": user["user_id"],
            "customer_email": user["email"],
            "customer_name": user.get("name") or user["email"].split("@")[0],
            "customer_phone": user.get("phone") or "9999999999",
        },
        "order_meta": {"return_url": return_url, "notify_url": notify_url},
        "order_note": f"aiclonechats.com — {pack['name']} top-up",
        "order_tags": {"kind": "topup", "pack_id": pack["pack_id"], "user_id": user["user_id"], "country": country_code, "display_currency": price["currency_code"]},
    }

    try:
        r = requests.post(f"{CF_BASE}/orders", headers=_cf_headers(), json=body, timeout=20)
    except Exception as e:
        logger.exception("cashfree create topup order request failed")
        raise HTTPException(502, f"Payment provider unavailable: {e}")
    if r.status_code not in (200, 201):
        logger.error("cashfree create topup non-2xx: %s %s", r.status_code, r.text[:500])
        raise HTTPException(502, f"Payment provider error: {r.text[:200]}")

    cf = r.json()
    payment_session_id = cf.get("payment_session_id")
    if not payment_session_id:
        raise HTTPException(502, "Payment provider returned no session id")

    await db.payment_orders.insert_one({
        "order_id": order_id,
        "user_id": user["user_id"],
        "email": user["email"],
        "kind": "topup",
        "pack_id": pack["pack_id"],
        # No plan_id — top-ups don't change subscription status
        "plan_id": None,
        "amount_inr": float(price["charge_amount"]) if price["charge_currency"] == "INR" else None,
        "country_code": country_code,
        "country_source": country_source,
        "display_currency": price["currency_code"],
        "display_amount": price["display_amount"],
        "charge_currency": price["charge_currency"],
        "charge_amount": price["charge_amount"],
        "amount_minor": price["amount_minor"],
        "requires_currency_disclosure": price["requires_currency_disclosure"],
        "exchange_source": price["exchange_source"],
        "exchange_version": price["exchange_version"],
        "credits": pack["credits"],
        "status": "created",
        "cashfree_order_id": cf.get("cf_order_id"),
        "payment_session_id": payment_session_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "credited_at": None,
        "webhook_count": 0,
    })

    return {
        "order_id": order_id,
        "payment_session_id": payment_session_id,
        "amount_inr": price["charge_amount"] if price["charge_currency"] == "INR" else None,
        "display_amount": price["display_amount"],
        "display_currency": price["currency_code"],
        "charge_amount": price["charge_amount"],
        "charge_currency": price["charge_currency"],
        "requires_currency_disclosure": price["requires_currency_disclosure"],
        "country_code": country_code,
        "pack_id": pack["pack_id"],
        "credits": pack["credits"],
        "mode": CASHFREE_MODE.lower(),
        "kind": "topup",
    }


@router.get("/order/{order_id}")
async def get_order_status(order_id: str, user: dict = Depends(get_current_user)):
    """Server-side truth read. Frontend ALWAYS polls this after Cashfree redirect.
    Never marks success based on URL params alone.

    Re-fetches the order from Cashfree if status is still `created`/`active`
    so a successful payment isn't blocked behind a delayed webhook.
    """
    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    if order["user_id"] != user["user_id"] and not is_admin_unlimited_user(user):
        raise HTTPException(403, "Not your order")

    if order["status"] in ("created", "active") and _is_configured():
        # Pull fresh status from Cashfree to side-step webhook delays
        try:
            r = requests.get(f"{CF_BASE}/orders/{order_id}", headers=_cf_headers(), timeout=15)
            if r.status_code == 200:
                cf = r.json()
                cf_status = (cf.get("order_status") or "").upper()
                if cf_status == "PAID" and not order.get("credited_at"):
                    await _handle_paid_event(order)
                    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
                elif cf_status in ("EXPIRED", "TERMINATED"):
                    await db.payment_orders.update_one(
                        {"order_id": order_id, "status": {"$in": ["created", "active"]}},
                        {"$set": {"status": cf_status.lower(), "updated_at": now_iso()}},
                    )
                    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
        except Exception as e:
            logger.warning("order status poll failed: %s", e)

    return {"order": order}


# ----- Webhook -----
def _verify_webhook_signature(raw_body: bytes, timestamp: str, signature: str) -> bool:
    """Cashfree webhook signature: base64(HMAC-SHA256(timestamp + raw_body, secret))."""
    if not (CASHFREE_SECRET_KEY and timestamp and signature):
        return False
    msg = timestamp.encode("utf-8") + raw_body
    digest = hmac.new(CASHFREE_SECRET_KEY.encode("utf-8"), msg, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    try:
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


from typing import Optional


async def _record_admin_alert(*, kind: str, severity: int, order_id: Optional[str], user_id: Optional[str], summary: str, payload: Optional[dict] = None) -> None:
    """Persist a manual-review alert. Surfaced in /admin/webhook-logs (manual_review_required filter)."""
    await db.admin_alerts.insert_one({
        "alert_id": uuid.uuid4().hex,
        "kind": kind,
        "severity": severity,
        "order_id": order_id,
        "user_id": user_id,
        "summary": summary,
        "payload_preview": (json.dumps(payload)[:600] if payload else None),
        "resolved": False,
        "created_at": now_iso(),
    })


async def _used_credits_since_grant(user_id: str, order: dict) -> int:
    """Best-effort: credits consumed since this order's grant. Used by the
    refund path to decide whether to auto-reverse or escalate to manual review.
    """
    credited_at = order.get("credited_at")
    if not credited_at:
        return 0
    rows = await db.credit_events.aggregate([
        {"$match": {"user_id": user_id, "kind": "deduct", "created_at": {"$gt": credited_at}}},
        {"$group": {"_id": None, "used": {"$sum": {"$abs": "$delta"}}}},
    ]).to_list(1)
    return rows[0]["used"] if rows else 0


# ---- Event-specific handlers ----
async def _handle_paid_event(order: dict) -> str:
    """Returns the verdict to write into webhook_logs.result."""
    # Atomic guard: only the first arrival flips status + sets credited_at.
    res = await db.payment_orders.find_one_and_update(
        {"order_id": order["order_id"], "credited_at": None, "status": {"$ne": "refunded"}},
        {"$set": {"status": "paid", "credited_at": now_iso(), "updated_at": now_iso()}},
        return_document=True,
        projection={"_id": 0},
    )
    if not res:
        await db.payment_audit_log.insert_one({
            "event_id": uuid.uuid4().hex,
            "order_id": order["order_id"],
            "action": "duplicate_webhook_no_op",
            "created_at": now_iso(),
        })
        return "duplicate_webhook_no_op"

    # Expired/cancelled-before-success — money came in but order was abandoned.
    # Still grant (the customer paid), but flag for admin review.
    if order.get("status") in ("expired", "terminated", "user_dropped"):
        await _record_admin_alert(
            kind="payment_after_terminal",
            severity=4,
            order_id=order["order_id"],
            user_id=order["user_id"],
            summary=f"Order was {order.get('status')} but a PAID webhook arrived afterward.",
        )

    new_balance = await credit_payment(
        user_id=order["user_id"],
        credits=order["credits"],
        order_id=order["order_id"],
        plan_id=order["plan_id"],
        kind=order.get("kind", "subscription"),
        pack_id=order.get("pack_id"),
    )
    await db.payment_audit_log.insert_one({
        "event_id": uuid.uuid4().hex,
        "order_id": order["order_id"],
        "user_id": order["user_id"],
        "action": "credits_granted",
        "credits": order["credits"],
        "plan_id": order["plan_id"],
        "new_balance": new_balance,
        "created_at": now_iso(),
    })
    return "accepted"


async def _handle_failed_event(order: dict, *, event_type: str, status_label: str, full_payload: dict) -> str:
    failure_reason = (full_payload.get("data") or {}).get("payment", {}).get("payment_message") or status_label or event_type
    payment_method = (full_payload.get("data") or {}).get("payment", {}).get("payment_method")
    if order.get("credited_at"):
        # Edge case: a failed webhook for an already-credited order. Don't reverse —
        # this is usually a delayed/duplicate from an earlier failed attempt. Flag.
        await _record_admin_alert(
            kind="failed_after_paid",
            severity=3,
            order_id=order["order_id"],
            user_id=order["user_id"],
            summary=f"FAILED webhook for an order already credited. event={event_type} reason={failure_reason}",
            payload=full_payload,
        )
        return "manual_review_required"

    await db.payment_orders.update_one(
        {"order_id": order["order_id"], "credited_at": None},
        {"$set": {
            "status": "failed",
            "updated_at": now_iso(),
            "failure_reason": failure_reason,
            "failure_event": event_type,
            "payment_method": payment_method,
        }},
    )
    return "failed"


async def _handle_user_dropped(order: dict, *, event_type: str, full_payload: dict) -> str:
    if order.get("credited_at"):
        return "duplicate_webhook_no_op"
    await db.payment_orders.update_one(
        {"order_id": order["order_id"], "credited_at": None},
        {"$set": {
            "status": "user_dropped",
            "updated_at": now_iso(),
            "failure_event": event_type,
            "checkout_session_id": (full_payload.get("data") or {}).get("payment", {}).get("payment_session_id"),
        }},
    )
    return "user_dropped"


async def _handle_refund_event(order: dict, *, event_type: str, full_payload: dict) -> str:
    """Refund taxonomy:
        REFUND_STATUS_WEBHOOK with refund_status=SUCCESS → reversal
        REFUND_STATUS_WEBHOOK with refund_status=FAILED → log only
    Idempotency: by refund_id (Cashfree-issued).
    """
    refund = (full_payload.get("data") or {}).get("refund") or {}
    refund_id = refund.get("refund_id") or refund.get("cf_refund_id")
    refund_amount = float(refund.get("refund_amount") or 0)
    refund_currency = (refund.get("refund_currency") or order.get("charge_currency") or "INR").upper()
    refund_status = (refund.get("refund_status") or "").upper()
    refund_reason = refund.get("refund_note") or refund.get("refund_reason")

    if not refund_id:
        await _record_admin_alert(
            kind="refund_missing_id",
            severity=3,
            order_id=order["order_id"],
            user_id=order["user_id"],
            summary="Refund webhook arrived without a refund_id; cannot dedup.",
            payload=full_payload,
        )
        return "manual_review_required"

    paid_amount = float(order.get("charge_amount") or order.get("amount_inr") or 0)
    if refund_amount > paid_amount + 0.01:
        await _log_fraud_signal(order["user_id"], order["email"], None, None, "refund_exceeds_paid", severity=5)
        return "manual_review_required"
    if (refund.get("refund_currency") or "").upper() and refund_currency != (order.get("charge_currency") or "INR").upper():
        await _log_fraud_signal(order["user_id"], order["email"], None, None, "refund_currency_mismatch", severity=4)
        return "currency_mismatch"

    # Idempotent insert keyed on refund_id
    try:
        await db.payment_refunds.insert_one({
            "refund_id": refund_id,
            "order_id": order["order_id"],
            "user_id": order["user_id"],
            "amount": refund_amount,
            "currency": refund_currency,
            "status": refund_status or "UNKNOWN",
            "reason": refund_reason,
            "event_type": event_type,
            "created_at": now_iso(),
        })
    except Exception:
        # duplicate refund_id → idempotent no-op
        return "duplicate_webhook_no_op"

    if refund_status not in ("SUCCESS", "PAID"):
        # Refund failed or pending — log only
        return "refund_failed" if refund_status == "FAILED" else "accepted"

    # Refund succeeded — decide reversal vs manual review
    is_partial = refund_amount < paid_amount - 0.01
    new_order_status = "partially_refunded" if is_partial else "refunded"
    await db.payment_orders.update_one(
        {"order_id": order["order_id"]},
        {"$set": {
            "status": new_order_status,
            "refund_status": new_order_status,
            "refunded_at": now_iso(),
            "updated_at": now_iso(),
        }},
    )

    # Credit reversal logic: only if granted credits are still mostly unused
    if not order.get("credited_at"):
        # No credits were ever granted (refund of a failed order) — nothing to do
        return "refunded"

    granted = int(order.get("credits") or 0)
    used = await _used_credits_since_grant(order["user_id"], order)
    # Pro-rated reversal: reverse the unused portion proportional to the refund.
    refund_ratio = min(1.0, refund_amount / paid_amount) if paid_amount > 0 else 1.0
    target_reverse = int(round(granted * refund_ratio))
    reversible = max(0, granted - used)

    if reversible <= 0 and target_reverse > 0:
        # All credits consumed — cannot auto-reverse without going negative
        await _record_admin_alert(
            kind="refund_after_usage",
            severity=4,
            order_id=order["order_id"],
            user_id=order["user_id"],
            summary=f"Refund {refund_amount} {refund_currency} succeeded but all {granted} credits already consumed. Manual review.",
            payload=full_payload,
        )
        return "manual_review_required"

    to_reverse = min(target_reverse, reversible)
    if to_reverse > 0:
        # Subtract from balance, never below zero
        res = await db.users.find_one_and_update(
            {"user_id": order["user_id"], "credits_balance": {"$gte": to_reverse}},
            {"$inc": {"credits_balance": -to_reverse}},
            return_document=True,
            projection={"_id": 0, "credits_balance": 1},
        )
        if not res:
            # Race — balance moved below threshold between read and write
            await _record_admin_alert(
                kind="refund_balance_race",
                severity=3,
                order_id=order["order_id"],
                user_id=order["user_id"],
                summary=f"Refund reversal of {to_reverse} credits aborted to avoid negative balance.",
                payload=full_payload,
            )
            return "manual_review_required"
        new_balance = res.get("credits_balance", 0)
        await db.credit_events.insert_one({
            "event_id": uuid.uuid4().hex,
            "user_id": order["user_id"],
            "kind": "refund_reversal",
            "delta": -to_reverse,
            "balance_before": new_balance + to_reverse,
            "balance_after": new_balance,
            "surface": f"refund:{refund_id}",
            "request_id": refund_id,
            "created_at": now_iso(),
        })

    return "partially_refunded" if is_partial else "refunded"


async def _handle_chargeback_event(order: dict, *, event_type: str, full_payload: dict) -> str:
    """Freeze and alert. Never auto-reverse credits — operators must review."""
    await db.payment_orders.update_one(
        {"order_id": order["order_id"]},
        {"$set": {"dispute_event": event_type, "disputed_at": now_iso(), "updated_at": now_iso()}},
    )
    await _record_admin_alert(
        kind="chargeback",
        severity=5,
        order_id=order["order_id"],
        user_id=order["user_id"],
        summary=f"Chargeback / dispute event: {event_type}. Plan/credits frozen pending review.",
        payload=full_payload,
    )
    await db.users.update_one(
        {"user_id": order["user_id"]},
        {"$set": {"chargeback_frozen": True, "chargeback_frozen_at": now_iso()}},
    )
    return "manual_review_required"


@router.post("/webhook/cashfree")
async def cashfree_webhook(request: Request, response: Response):
    raw_body = await request.body()
    timestamp = request.headers.get("x-webhook-timestamp") or ""
    signature = request.headers.get("x-webhook-signature") or ""
    version = request.headers.get("x-webhook-version") or ""

    # Replay protection: reject if timestamp older than window
    if timestamp:
        try:
            ts_int = int(timestamp)
            age = abs(datetime.now(timezone.utc).timestamp() - ts_int / 1000)
            if age > WEBHOOK_REPLAY_WINDOW_SEC:
                await db.webhook_logs.insert_one({
                    "event_id": uuid.uuid4().hex,
                    "received_at": now_iso(),
                    "result": "rejected_replay",
                    "raw_age_sec": age,
                })
                raise HTTPException(400, "Webhook timestamp out of window")
        except HTTPException:
            raise
        except Exception:
            pass

    # Signature verification — without this, anyone can post a fake success
    if not _verify_webhook_signature(raw_body, timestamp, signature):
        await db.webhook_logs.insert_one({
            "event_id": uuid.uuid4().hex,
            "received_at": now_iso(),
            "result": "rejected_signature",
            "version": version,
            "body_preview": raw_body[:300].decode("utf-8", errors="replace"),
        })
        raise HTTPException(401, "Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    event_type = (payload.get("type") or payload.get("event") or "unknown").upper()
    data = payload.get("data") or {}
    order_data = data.get("order") or {}
    payment_data = data.get("payment") or {}
    refund_data = data.get("refund") or {}
    order_id = order_data.get("order_id") or payment_data.get("order_id") or refund_data.get("order_id") or data.get("order_id")
    # Idempotency key: Cashfree event_id when present, fallback to signature
    cf_event_id = (
        payload.get("event_id")
        or payment_data.get("cf_payment_id")
        or refund_data.get("cf_refund_id")
        or signature
    )

    # Idempotency guard — unique index on dedup_key in webhook_dedup will
    # reject duplicates. We insert FIRST; if it fails, it's a duplicate.
    dedup_key = f"{event_type}:{cf_event_id}:{order_id}"
    try:
        await db.webhook_dedup.insert_one({"dedup_key": dedup_key, "created_at": now_iso(), "event_type": event_type, "order_id": order_id})
        is_duplicate = False
    except Exception:
        is_duplicate = True

    log_doc = {
        "event_id": uuid.uuid4().hex,
        "received_at": now_iso(),
        "result": "accepted",
        "event_type": event_type,
        "order_id": order_id,
        "version": version,
        "dedup_key": dedup_key,
        "is_duplicate": is_duplicate,
    }
    await db.webhook_logs.insert_one(dict(log_doc))

    if is_duplicate:
        await db.webhook_logs.update_one({"event_id": log_doc["event_id"]}, {"$set": {"result": "duplicate_webhook_no_op"}})
        return {"ok": True, "duplicate": True}

    if not order_id:
        # Unknown event without order context — log and 200, no mutation.
        await db.webhook_logs.update_one({"event_id": log_doc["event_id"]}, {"$set": {"result": "unknown_event", "body_preview": raw_body[:300].decode("utf-8", errors="replace")}})
        await _record_admin_alert(
            kind="unknown_event_no_order",
            severity=2,
            order_id=None,
            user_id=None,
            summary=f"Webhook arrived without an order_id. event_type={event_type}",
            payload=payload,
        )
        return {"ok": True, "no_order_id": True}

    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        await db.webhook_logs.update_one({"event_id": log_doc["event_id"]}, {"$set": {"result": "order_not_found"}})
        return {"ok": True, "order_not_found": True}

    # Amount + currency tampering guard — only check on payment events (refund
    # has its own currency-match logic and a different amount semantics).
    is_refund_event = "REFUND" in event_type
    if not is_refund_event:
        cf_amount = float(order_data.get("order_amount") or payment_data.get("payment_amount") or 0)
        cf_currency = (order_data.get("order_currency") or payment_data.get("payment_currency") or "").upper()
        stored_charge_amount = float(order.get("charge_amount") or order.get("amount_inr") or 0)
        stored_charge_currency = (order.get("charge_currency") or "INR").upper()
        if cf_amount and abs(cf_amount - stored_charge_amount) > 0.01:
            await _log_fraud_signal(order["user_id"], order["email"], None, None, "webhook_amount_mismatch", severity=5)
            await db.webhook_logs.update_one({"event_id": log_doc["event_id"]}, {"$set": {"result": "amount_mismatch"}})
            raise HTTPException(400, "Amount mismatch")
        if cf_currency and cf_currency != stored_charge_currency:
            await _log_fraud_signal(order["user_id"], order["email"], None, None, "webhook_currency_mismatch", severity=5)
            await db.webhook_logs.update_one({"event_id": log_doc["event_id"]}, {"$set": {"result": "currency_mismatch"}})
            raise HTTPException(400, "Currency mismatch")

    await db.payment_orders.update_one(
        {"order_id": order_id},
        {"$inc": {"webhook_count": 1}, "$set": {"last_webhook_at": now_iso(), "last_webhook_event": event_type}},
    )

    cf_order_status = (order_data.get("order_status") or "").upper()
    cf_payment_status = (payment_data.get("payment_status") or "").upper()

    # ---- Dispatch by event_type ----
    verdict = "accepted"
    try:
        if event_type == "PAYMENT_SUCCESS_WEBHOOK" or cf_order_status in ("PAID", "SUCCESS"):
            verdict = await _handle_paid_event(order)
        elif event_type == "PAYMENT_FAILED_WEBHOOK" or cf_payment_status == "FAILED":
            verdict = await _handle_failed_event(order, event_type=event_type, status_label=cf_payment_status, full_payload=payload)
        elif event_type == "PAYMENT_USER_DROPPED_WEBHOOK" or cf_payment_status == "USER_DROPPED" or cf_order_status == "USER_DROPPED":
            verdict = await _handle_user_dropped(order, event_type=event_type, full_payload=payload)
        elif "REFUND" in event_type:
            verdict = await _handle_refund_event(order, event_type=event_type, full_payload=payload)
        elif "CHARGEBACK" in event_type or "DISPUTE" in event_type:
            verdict = await _handle_chargeback_event(order, event_type=event_type, full_payload=payload)
        else:
            # Unknown event — log + alert, no mutation
            await _record_admin_alert(
                kind="unknown_event",
                severity=2,
                order_id=order_id,
                user_id=order.get("user_id"),
                summary=f"Unrecognized event_type={event_type}",
                payload=payload,
            )
            verdict = "unknown_event"
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("webhook handler failed for event=%s order=%s", event_type, order_id)
        await _record_admin_alert(
            kind="handler_exception",
            severity=4,
            order_id=order_id,
            user_id=order.get("user_id"),
            summary=f"Handler raised: {type(e).__name__}: {str(e)[:200]}",
            payload=payload,
        )
        verdict = "manual_review_required"

    await db.webhook_logs.update_one({"event_id": log_doc["event_id"]}, {"$set": {"result": verdict}})
    return {"ok": True, "verdict": verdict}
