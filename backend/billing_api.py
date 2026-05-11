"""
Public + admin endpoints for plans, credits, payments, fraud signals.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from db import db
from auth import get_current_user, get_optional_user
from credits import (
    PLANS,
    CREDIT_COST,
    get_user_credit_state,
    is_admin_unlimited_user,
)
from pricing import (
    catalog_for_country,
    detect_country_from_request,
    country_to_currency,
    COUNTRY_TO_CURRENCY,
    EXCHANGE_SOURCE,
    EXCHANGE_VERSION,
    GATEWAY_CHARGE_CURRENCIES,
)
from models import now_iso

logger = logging.getLogger(__name__)

public_router = APIRouter(tags=["billing"])
admin_router = APIRouter(prefix="/api/admin/billing", tags=["billing-admin"])


def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if not is_admin_unlimited_user(user) and (user.get("role") != "admin"):
        raise HTTPException(403, "Admin only")
    return user


@public_router.get("/api/plans")
async def list_plans():
    return {"plans": PLANS, "credit_costs": CREDIT_COST}


# ---- Country-aware pricing endpoints ----
@public_router.get("/api/pricing/catalog")
async def pricing_catalog(request: Request, country: Optional[str] = None, user: Optional[dict] = Depends(get_optional_user)):
    """Public — returns the price record for each PAID plan, in the user's
    detected (or requested) country/currency. Frontend renders these EXACTLY,
    never recomputes.
    """
    if country:
        country_code = country.upper()
        source = "query_override"
    else:
        country_code, source = detect_country_from_request(request, user)
    paid_plan_ids = [p["plan_id"] for p in PLANS if p["plan_id"] != "free" and p.get("is_active")]
    cat = catalog_for_country(country_code, paid_plan_ids)
    cat["country_source"] = source
    cat["gateway_currencies"] = sorted(GATEWAY_CHARGE_CURRENCIES)
    cat["exchange_source"] = EXCHANGE_SOURCE
    cat["exchange_version"] = EXCHANGE_VERSION
    return cat


@public_router.get("/api/pricing/my-currency")
async def my_currency(request: Request, user: Optional[dict] = Depends(get_optional_user)):
    country_code, source = detect_country_from_request(request, user)
    return {
        "country_code": country_code,
        "currency_code": country_to_currency(country_code),
        "source": source,
    }


@public_router.get("/api/me/credits")
async def my_credits(user: dict = Depends(get_current_user)):
    state = await get_user_credit_state(user)
    # Recent ledger (last 20)
    rows = await db.credit_events.find(
        {"user_id": user["user_id"]}, {"_id": 0}
    ).sort("created_at", -1).limit(20).to_list(20)
    return {**state, "recent_events": rows, "email_verified": bool(user.get("email_verified")) or is_admin_unlimited_user(user)}


# ----- Admin -----
@admin_router.get("/pricing-catalog")
async def admin_pricing_catalog(_admin: dict = Depends(_require_admin)):
    """Inspect every plan × every supported country. Useful for sanity-checking
    the long-tail derivation rules. Read-only."""
    from credits import PLAN_INDEX as _PI
    paid = [pid for pid in _PI if pid != "free"]
    matrix = {}
    for cc in COUNTRY_TO_CURRENCY:
        cat = catalog_for_country(cc, paid)
        matrix[cc] = cat["prices"]
    return {
        "exchange_source": EXCHANGE_SOURCE,
        "exchange_version": EXCHANGE_VERSION,
        "gateway_currencies": sorted(GATEWAY_CHARGE_CURRENCIES),
        "countries_supported": len(COUNTRY_TO_CURRENCY),
        "matrix": matrix,
    }


@admin_router.post("/test-webhook")
async def admin_test_webhook(payload: dict, request: Request, _admin: dict = Depends(_require_admin)):
    """Admin-only: simulate a signed Cashfree webhook arrival against our own
    webhook endpoint. Useful for end-to-end smoke tests of the signature
    verification + idempotency + amount/currency match flow without needing
    to go through Cashfree's dashboard.

    Body: {order_id, event_type (optional), tamper (optional: 'amount'|'currency'|'signature'|'timestamp')}
    """
    import os as _os, json as _json, hmac as _hmac, base64 as _b64, hashlib as _hash, time as _time
    import httpx as _httpx

    order_id = payload.get("order_id")
    event_type = payload.get("event_type") or "PAYMENT_SUCCESS_WEBHOOK"
    tamper = payload.get("tamper")
    if not order_id:
        raise HTTPException(400, "order_id is required")
    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")

    amount = float(order.get("charge_amount") or order.get("amount_inr") or 0)
    currency = (order.get("charge_currency") or "INR")
    if tamper == "amount":
        amount = 1.0
    if tamper == "currency":
        currency = "USD" if currency != "USD" else "INR"

    body = _json.dumps({
        "type": event_type,
        "data": {
            "order": {
                "order_id": order_id,
                "order_status": "PAID" if event_type == "PAYMENT_SUCCESS_WEBHOOK" else "FAILED",
                "order_amount": amount,
                "order_currency": currency,
            },
            "payment": {"payment_status": "SUCCESS" if event_type == "PAYMENT_SUCCESS_WEBHOOK" else "FAILED"}
        }
    }).encode()

    secret = _os.environ.get("CASHFREE_SECRET_KEY", "").encode()
    if not secret:
        raise HTTPException(503, "CASHFREE_SECRET_KEY not configured")

    ts = str(int(_time.time() * 1000))
    if tamper == "timestamp":
        ts = str(int((_time.time() - 3600) * 1000))  # 1 hour old

    sig = _b64.b64encode(_hmac.new(secret, ts.encode() + body, _hash.sha256).digest()).decode()
    if tamper == "signature":
        sig = "tampered-signature"

    target = "http://127.0.0.1:8001/api/payments/webhook/cashfree"
    try:
        async with _httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(target, content=body, headers={
                "x-webhook-timestamp": ts,
                "x-webhook-signature": sig,
                "x-webhook-version": "2023-08-01",
                "Content-Type": "application/json",
            })
        return {
            "ok": r.status_code == 200,
            "status_code": r.status_code,
            "response_body": r.text[:500],
            "tamper": tamper,
            "event_type": event_type,
            "signed_amount": amount,
            "signed_currency": currency,
        }
    except Exception as e:
        raise HTTPException(502, f"Could not deliver test webhook: {e}")


@admin_router.get("/overview")
async def admin_billing_overview(_admin: dict = Depends(_require_admin), days: int = Query(default=30, ge=1, le=365)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    paid = await db.payment_orders.count_documents({"status": "paid", "credited_at": {"$gte": since}})
    failed = await db.payment_orders.count_documents({"status": "failed", "updated_at": {"$gte": since}})
    created = await db.payment_orders.count_documents({"created_at": {"$gte": since}})
    revenue_rows = await db.payment_orders.aggregate([
        {"$match": {"status": "paid", "credited_at": {"$gte": since}}},
        {"$group": {"_id": "$plan_id", "n": {"$sum": 1}, "revenue_inr": {"$sum": "$amount_inr"}}},
    ]).to_list(20)
    total_users = await db.users.count_documents({})
    active_subscribers = await db.users.count_documents({"plan_status": "active", "plan_id": {"$nin": [None, "free"]}})
    fraud_signals_24h = await db.fraud_signals.count_documents({
        "created_at": {"$gte": (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()}
    })
    return {
        "window_days": days,
        "orders_created": created,
        "orders_paid": paid,
        "orders_failed": failed,
        "conversion_pct": round(100 * paid / max(1, created), 1) if created else 0,
        "revenue_by_plan": [{"plan_id": r["_id"], "orders": r["n"], "revenue_inr": r["revenue_inr"]} for r in revenue_rows],
        "total_users": total_users,
        "active_subscribers": active_subscribers,
        "fraud_signals_24h": fraud_signals_24h,
    }


@admin_router.get("/users")
async def admin_users(_admin: dict = Depends(_require_admin), q: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500)):
    query: dict = {}
    if q:
        query["$or"] = [{"email": {"$regex": q, "$options": "i"}}, {"user_id": q}]
    rows = await db.users.find(
        query,
        {"_id": 0, "password_hash": 0},
    ).sort("created_at", -1).limit(limit).to_list(limit)
    return {"users": rows}


@admin_router.get("/payments")
async def admin_payments(_admin: dict = Depends(_require_admin), status: Optional[str] = None, limit: int = Query(default=200, ge=1, le=1000)):
    query: dict = {}
    if status:
        query["status"] = status
    rows = await db.payment_orders.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return {"payments": rows}


@admin_router.get("/credit-events")
async def admin_credit_events(_admin: dict = Depends(_require_admin), user_id: Optional[str] = None, limit: int = Query(default=200, ge=1, le=1000)):
    query: dict = {}
    if user_id:
        query["user_id"] = user_id
    rows = await db.credit_events.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return {"events": rows}


@admin_router.get("/webhook-logs")
async def admin_webhook_logs(_admin: dict = Depends(_require_admin), result: Optional[str] = None, limit: int = Query(default=200, ge=1, le=1000)):
    query: dict = {}
    if result:
        query["result"] = result
    rows = await db.webhook_logs.find(query, {"_id": 0}).sort("received_at", -1).limit(limit).to_list(limit)
    return {"logs": rows}


@admin_router.get("/fraud-signals")
async def admin_fraud_signals(_admin: dict = Depends(_require_admin), limit: int = Query(default=200, ge=1, le=1000)):
    rows = await db.fraud_signals.find({}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    cooldowns = await db.fraud_cooldowns.find(
        {"expires_at": {"$gt": now_iso()}}, {"_id": 0}
    ).sort("expires_at", -1).limit(50).to_list(50)
    return {"signals": rows, "active_cooldowns": cooldowns}


@admin_router.post("/credit-adjust")
async def admin_credit_adjust(payload: dict, _admin: dict = Depends(_require_admin)):
    """Manual credit adjustment by admin. Recorded in credit_events with admin_id."""
    user_id = payload.get("user_id")
    delta = int(payload.get("delta") or 0)
    reason = payload.get("reason") or "admin_adjustment"
    if not user_id or delta == 0:
        raise HTTPException(400, "user_id and non-zero delta required")
    res = await db.users.find_one_and_update(
        {"user_id": user_id},
        {"$inc": {"credits_balance": delta}},
        return_document=True,
        projection={"_id": 0, "credits_balance": 1},
    )
    if not res:
        raise HTTPException(404, "User not found")
    new_balance = res.get("credits_balance", 0)
    if new_balance < 0:
        # Roll back — never allow negative
        await db.users.update_one({"user_id": user_id}, {"$inc": {"credits_balance": -delta}})
        raise HTTPException(400, "Adjustment would create negative balance")
    await db.credit_events.insert_one({
        "event_id": _admin["user_id"] + "_" + now_iso(),
        "user_id": user_id,
        "kind": "admin_adjust",
        "delta": delta,
        "balance_before": new_balance - delta,
        "balance_after": new_balance,
        "surface": f"admin:{reason}",
        "request_id": _admin.get("email"),
        "created_at": now_iso(),
    })
    return {"ok": True, "new_balance": new_balance}
