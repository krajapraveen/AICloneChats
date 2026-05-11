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
    credit_payment,
    is_admin_unlimited_user,
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
                    await _apply_paid_credits(order)
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


async def _apply_paid_credits(order: dict) -> None:
    """Idempotently grant credits + activate plan for a paid order.

    Uses a conditional update with `credited_at: None` as the guard.
    Concurrent webhook deliveries collapse to a single credit grant.
    """
    res = await db.payment_orders.find_one_and_update(
        {"order_id": order["order_id"], "credited_at": None},
        {"$set": {
            "status": "paid",
            "credited_at": now_iso(),
            "updated_at": now_iso(),
        }},
        return_document=True,
        projection={"_id": 0},
    )
    if not res:
        # Already credited — duplicate webhook path. Audit and return.
        await db.payment_audit_log.insert_one({
            "event_id": uuid.uuid4().hex,
            "order_id": order["order_id"],
            "action": "duplicate_webhook_no_op",
            "created_at": now_iso(),
        })
        return
    new_balance = await credit_payment(
        user_id=order["user_id"],
        credits=order["credits"],
        order_id=order["order_id"],
        plan_id=order["plan_id"],
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

    event_type = payload.get("type") or payload.get("event") or "unknown"
    data = payload.get("data") or {}
    order_data = data.get("order") or {}
    payment_data = data.get("payment") or {}
    order_id = order_data.get("order_id") or payment_data.get("order_id") or data.get("order_id")

    log_doc = {
        "event_id": uuid.uuid4().hex,
        "received_at": now_iso(),
        "result": "accepted",
        "event_type": event_type,
        "order_id": order_id,
        "version": version,
    }
    await db.webhook_logs.insert_one(dict(log_doc))

    if not order_id:
        return {"ok": True, "no_order_id": True}

    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        await db.webhook_logs.update_one({"event_id": log_doc["event_id"]}, {"$set": {"result": "order_not_found"}})
        return {"ok": True, "order_not_found": True}

    # Amount + currency tampering guard
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

    cf_status = (order_data.get("order_status") or payment_data.get("payment_status") or "").upper()
    is_paid_event = (
        event_type in ("PAYMENT_SUCCESS_WEBHOOK",)
        or cf_status in ("PAID", "SUCCESS")
    )
    is_failed_event = (
        event_type in ("PAYMENT_FAILED_WEBHOOK", "PAYMENT_USER_DROPPED_WEBHOOK")
        or cf_status in ("FAILED", "USER_DROPPED")
    )

    if is_paid_event:
        await _apply_paid_credits(order)
    elif is_failed_event and order["status"] in ("created", "active"):
        await db.payment_orders.update_one(
            {"order_id": order_id, "credited_at": None},
            {"$set": {"status": "failed", "updated_at": now_iso(), "failure_reason": cf_status or event_type}},
        )

    return {"ok": True}
