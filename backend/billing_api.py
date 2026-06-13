"""
Public + admin endpoints for plans, credits, payments, fraud signals.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from db import db
from auth import get_current_user, get_optional_user
from credits import (
    PLANS,
    TOP_UP_PACKS,
    CREDIT_COST,
    get_user_credit_state,
    is_admin_unlimited_user,
    is_active_subscriber,
    ADMIN_UNLIMITED_EMAILS,
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


@public_router.get("/api/topups/catalog")
async def topups_catalog(request: Request, country: Optional[str] = None, user: Optional[dict] = Depends(get_optional_user)):
    """Public top-up catalog with country-aware pricing.
    Purchase is server-gated to active subscribers; this endpoint just lists.
    """
    from pricing import compute_price_for_plan
    if country:
        country_code = country.upper()
        source = "query_override"
    else:
        country_code, source = detect_country_from_request(request, user)
    packs_with_price = []
    for pack in TOP_UP_PACKS:
        if not pack.get("is_active"):
            continue
        try:
            price = compute_price_for_plan(pack["pack_id"], country_code)
        except Exception:
            continue
        packs_with_price.append({**pack, "price": price})
    return {
        "country_code": country_code,
        "country_source": source,
        "subscriber_only": True,
        "packs": packs_with_price,
        "is_active_subscriber": is_active_subscriber(user) if user else False,
    }


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
    topup_ids = [t["pack_id"] for t in TOP_UP_PACKS if t.get("is_active")]
    cat = catalog_for_country(country_code, paid_plan_ids + topup_ids)
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


@public_router.get("/api/me/orders")
async def my_orders(user: dict = Depends(get_current_user), limit: int = Query(default=50, ge=1, le=200)):
    """Return this user's purchase history (subscriptions + top-ups). Used by
    the My Profile → Manage Subscriptions page. Sorted newest-first.
    """
    rows = await db.payment_orders.find(
        {"user_id": user["user_id"]},
        {"_id": 0, "order_id": 1, "status": 1, "amount": 1, "currency": 1,
         "plan_id": 1, "pack_id": 1, "credits_to_grant": 1, "provider": 1,
         "created_at": 1, "paid_at": 1},
    ).sort("created_at", -1).limit(limit).to_list(limit)
    state = await get_user_credit_state(user)

    # Derive plan dates + active/expired status from the most recent paid
    # order matching the user's current plan_id. This is a pragmatic stand-in
    # for a full subscription state machine until one is built.
    current_plan_id = state.get("plan_id")
    plan_started_at = None
    plan_expires_at = None
    plan_status = "Free"
    if current_plan_id and current_plan_id != "free":
        last_paid = next((o for o in rows if o.get("status") == "paid" and o.get("plan_id") == current_plan_id), None)
        if last_paid and last_paid.get("paid_at"):
            try:
                from datetime import datetime as _dt, timedelta as _td, timezone as _tz
                started = _dt.fromisoformat(last_paid["paid_at"].replace("Z", "+00:00"))
                expires = started + _td(days=30)
                now = _dt.now(_tz.utc)
                plan_started_at = started.isoformat()
                plan_expires_at = expires.isoformat()
                plan_status = "Active" if now < expires else "Expired"
            except Exception:
                plan_status = "Active"
    if state.get("admin_unlimited"):
        plan_status = "Admin · Unlimited"

    return {
        "items": rows,
        "count": len(rows),
        "current_plan_id": current_plan_id,
        "current_plan_name": state.get("plan_name"),
        "plan_status": plan_status,
        "plan_started_at": plan_started_at,
        "plan_expires_at": plan_expires_at,
        "credits_balance": state.get("credits_balance", 0),
        "admin_unlimited": bool(state.get("admin_unlimited")),
    }


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


@admin_router.get("/alerts")
async def admin_alerts(_admin: dict = Depends(_require_admin), resolved: Optional[bool] = None, kind: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500)):
    query: dict = {}
    if resolved is not None:
        query["resolved"] = resolved
    if kind:
        query["kind"] = kind
    rows = await db.admin_alerts.find(query, {"_id": 0}).sort([("severity", -1), ("created_at", -1)]).limit(limit).to_list(limit)
    open_count = await db.admin_alerts.count_documents({"resolved": False})
    return {"alerts": rows, "open_count": open_count}


@admin_router.post("/alerts/{alert_id}/resolve")
async def admin_resolve_alert(alert_id: str, payload: dict, _admin: dict = Depends(_require_admin)):
    note = (payload or {}).get("note") or ""
    res = await db.admin_alerts.update_one(
        {"alert_id": alert_id, "resolved": False},
        {"$set": {"resolved": True, "resolved_at": now_iso(), "resolved_by": _admin.get("email"), "resolved_note": note}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Alert not found or already resolved")
    return {"ok": True}


@admin_router.get("/payment/{order_id}")
async def admin_payment_detail(order_id: str, _admin: dict = Depends(_require_admin)):
    """Comprehensive forensic view of a single order: order doc, all webhook
    arrivals, all credit events tied to the order, all refunds, all alerts."""
    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    webhook_arrivals = await db.webhook_logs.find({"order_id": order_id}, {"_id": 0}).sort("received_at", -1).limit(100).to_list(100)
    credit_events = await db.credit_events.find({"request_id": order_id}, {"_id": 0}).sort("created_at", -1).limit(100).to_list(100)
    refunds = await db.payment_refunds.find({"order_id": order_id}, {"_id": 0}).sort("created_at", -1).to_list(50)
    alerts = await db.admin_alerts.find({"order_id": order_id}, {"_id": 0}).sort("created_at", -1).to_list(50)
    audit = await db.payment_audit_log.find({"order_id": order_id}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return {
        "order": order,
        "webhook_arrivals": webhook_arrivals,
        "credit_events": credit_events,
        "refunds": refunds,
        "alerts": alerts,
        "audit_log": audit,
    }


@admin_router.get("/diagnostic/by-email/{email}")
async def admin_user_diagnostic_by_email(
    email: str,
    _admin: dict = Depends(_require_admin),
):
    """Single-shot forensic view of one user — built specifically to debug
    "user paid but UI thinks they're free" reports.

    Returns:
      - The raw `users` document (sanitized of password_hash).
      - The user's last 5 `payment_orders` (newest first).
      - The user's last 10 `credit_events` (newest first).
      - The user's last 10 `funnel_events` (signals which CTA / surface
        treated them as free).
      - The user's last 10 `paywall_events` (every 402 the user has hit).
      - The user's last 5 `subscription_transitions` if any.
      - Derived `credit_state` (what get_user_credit_state currently
        reports — this is the source of truth the frontend sees).
      - Webhook arrivals for the user's last paid order (to confirm
        whether the gateway actually called us back successfully).
      - `consistency_check`: explicit ✅/⚠ summary of common drift modes.

    Read-only. Admin-only.
    """
    norm_email = (email or "").strip().lower()
    if not norm_email:
        raise HTTPException(400, {"code": "missing_email", "message": "Email is required"})

    user = await db.users.find_one(
        {"email": norm_email},
        {"_id": 0, "password_hash": 0, "reset_token_hash": 0},
    )
    if not user:
        raise HTTPException(404, {"code": "user_not_found", "message": f"No user with email {norm_email}"})

    uid = user["user_id"]

    orders = await db.payment_orders.find(
        {"user_id": uid}, {"_id": 0},
    ).sort("created_at", -1).limit(5).to_list(5)

    credit_events = await db.credit_events.find(
        {"user_id": uid}, {"_id": 0},
    ).sort("created_at", -1).limit(10).to_list(10)

    funnel = await db.funnel_events.find(
        {"user_id": uid}, {"_id": 0},
    ).sort("created_at", -1).limit(10).to_list(10)

    paywall = await db.paywall_events.find(
        {"user_id": uid}, {"_id": 0},
    ).sort("created_at", -1).limit(10).to_list(10)

    transitions = await db.subscription_transitions.find(
        {"user_id": uid}, {"_id": 0},
    ).sort("at", -1).limit(5).to_list(5)

    # Derived credit state — exactly what /api/me/credits returns
    try:
        derived_state = await get_user_credit_state(user)
    except Exception as e:
        derived_state = {"error": f"get_user_credit_state failed: {type(e).__name__}: {e}"}

    # Last paid order + its webhook arrivals
    last_paid_order = next((o for o in orders if o.get("status") == "paid"), None)
    webhook_arrivals: list[dict] = []
    if last_paid_order and last_paid_order.get("order_id"):
        webhook_arrivals = await db.webhook_logs.find(
            {"order_id": last_paid_order["order_id"]}, {"_id": 0},
        ).sort("received_at", -1).limit(20).to_list(20)

    # Consistency checks — surface the common drift modes that cause
    # "paid but UI says free":
    checks: list[dict] = []

    paid_orders = [o for o in orders if o.get("status") == "paid"]
    user_plan = user.get("plan_id") or "free"
    user_plan_status = user.get("plan_status") or ""

    if paid_orders and user_plan == "free":
        checks.append({
            "name": "paid_order_but_user_plan_free",
            "level": "critical",
            "message": (
                f"User has {len(paid_orders)} paid order(s), most recent for plan "
                f"{paid_orders[0].get('plan_id')!r}, but users.plan_id is 'free'. "
                f"The Cashfree webhook likely arrived (order is paid) but the "
                f"user-plan update side-effect didn't fire (or fired against "
                f"the wrong user_id)."
            ),
        })

    if paid_orders and paid_orders[0].get("plan_id") != user_plan and user_plan != "free":
        checks.append({
            "name": "user_plan_does_not_match_last_paid_order",
            "level": "warning",
            "message": (
                f"Most recent paid order is for plan {paid_orders[0].get('plan_id')!r} "
                f"but users.plan_id is {user_plan!r}. Possible plan_id rename or "
                f"a stale earlier plan still active."
            ),
        })

    if user_plan and user_plan != "free" and user_plan_status not in ("active", "cancel_at_period_end"):
        checks.append({
            "name": "plan_id_set_but_status_inactive",
            "level": "warning",
            "message": (
                f"users.plan_id={user_plan!r} but plan_status={user_plan_status!r}. "
                f"Subscriber-gated features will refuse this user."
            ),
        })

    grant_events = [e for e in credit_events if e.get("kind") in ("subscription_grant", "topup_grant", "grant")]
    if paid_orders and not grant_events:
        checks.append({
            "name": "paid_order_but_no_credit_grant",
            "level": "critical",
            "message": (
                "User has paid orders but no subscription_grant credit_events. "
                "The grant side of the payment fulfillment did not fire."
            ),
        })

    if last_paid_order and not webhook_arrivals:
        checks.append({
            "name": "paid_order_no_webhook_arrivals_logged",
            "level": "info",
            "message": (
                "No webhook_logs rows for the most recent paid order. Either the "
                "logger is off or the order was marked paid by a non-webhook path "
                "(e.g., manual admin action / verify-payment poll)."
            ),
        })

    if not checks:
        checks.append({"name": "ok", "level": "ok", "message": "No drift detected. User state is internally consistent."})

    return {
        "user": user,
        "derived_credit_state": derived_state,
        "orders": orders,
        "credit_events": credit_events,
        "funnel_events": funnel,
        "paywall_events": paywall,
        "subscription_transitions": transitions,
        "last_paid_order_webhook_arrivals": webhook_arrivals,
        "consistency_check": checks,
        "computed_at": now_iso(),
    }


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


@admin_router.post("/enforce-zero-credit-policy")
async def admin_enforce_zero_credit_policy(
    payload: Optional[dict] = None,
    _admin: dict = Depends(_require_admin),
):
    """Sweep & zero-out stray `credits_balance` for non-admin, non-subscriber users.

    The platform enforces a strict 0-credit policy for free users (no signup
    grants, no daily free allowance — only subscribers and admin-unlimited
    accounts can spend). Historical rows in production may still carry stale
    positive balances from earlier code paths. This endpoint reconciles them
    in one transaction-per-user.

    Request body (all optional):
      - dry_run: bool — if true, report what WOULD change without writing. Default false.
      - reason: str  — audit label, persisted on the credit_events row.
                       Default `enforce_zero_policy`.

    Response shape:
      {
        ok: true,
        dry_run: bool,
        scanned: int,        # non-admin non-subscriber users with balance > 0
        affected: int,       # users actually zeroed (== scanned when not dry_run)
        total_credits_zeroed: int,  # sum of credits removed
        sample: [             # first 20 affected users for operator inspection
          {user_id, email, plan_id, plan_status, balance_before},
          ...
        ],
        skipped: {
          admins: int,        # admin-unlimited users skipped (kept untouched)
          subscribers: int,   # active subscribers skipped
        },
      }
    """
    body = payload or {}
    dry_run = bool(body.get("dry_run", False))
    reason = (body.get("reason") or "enforce_zero_policy").strip() or "enforce_zero_policy"

    admin_emails = ADMIN_UNLIMITED_EMAILS  # imported at module-bottom via credits

    # Pull every user with positive balance; classify in Python so the
    # admin-bypass + subscriber rules stay identical to runtime.
    cursor = db.users.find(
        {"credits_balance": {"$gt": 0}},
        {"_id": 0, "user_id": 1, "email": 1, "plan_id": 1, "plan_status": 1, "credits_balance": 1},
    )

    skipped_admins = 0
    skipped_subscribers = 0
    affected_users: list[dict] = []

    async for u in cursor:
        email = (u.get("email") or "").lower().strip()
        if email in admin_emails:
            skipped_admins += 1
            continue
        if is_active_subscriber(u):
            skipped_subscribers += 1
            continue
        affected_users.append(u)

    scanned = len(affected_users)
    total_credits_zeroed = sum(int(u.get("credits_balance") or 0) for u in affected_users)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "scanned": scanned,
            "affected": 0,
            "total_credits_zeroed": total_credits_zeroed,
            "sample": [
                {
                    "user_id": u["user_id"],
                    "email": u.get("email"),
                    "plan_id": u.get("plan_id"),
                    "plan_status": u.get("plan_status"),
                    "balance_before": int(u.get("credits_balance") or 0),
                }
                for u in affected_users[:20]
            ],
            "skipped": {"admins": skipped_admins, "subscribers": skipped_subscribers},
        }

    # Apply the sweep. Per-user transactional shape: set balance to 0 only if
    # the current balance is still positive (no negative-balance race), then
    # emit an `admin_adjust` credit event. If a user's balance was top'd-up
    # between scan and write (e.g., they subscribed in the meantime), the
    # update simply matches no document and we skip the audit row.
    affected = 0
    for u in affected_users:
        before = int(u.get("credits_balance") or 0)
        if before <= 0:
            continue
        updated = await db.users.find_one_and_update(
            {"user_id": u["user_id"], "credits_balance": {"$gt": 0}},
            {"$set": {"credits_balance": 0}},
            projection={"_id": 0, "credits_balance": 1},
            return_document=False,
        )
        if not updated:
            continue
        await db.credit_events.insert_one({
            "event_id": f"{u['user_id']}_{uuid.uuid4().hex}",
            "user_id": u["user_id"],
            "kind": "admin_adjust",
            "delta": -before,
            "balance_before": before,
            "balance_after": 0,
            "surface": f"admin_adjust:{reason}",
            "feature": "admin_adjustment",
            "request_id": _admin.get("email"),
            "created_at": now_iso(),
        })
        affected += 1

    return {
        "ok": True,
        "dry_run": False,
        "scanned": scanned,
        "affected": affected,
        "total_credits_zeroed": total_credits_zeroed,
        "sample": [
            {
                "user_id": u["user_id"],
                "email": u.get("email"),
                "plan_id": u.get("plan_id"),
                "plan_status": u.get("plan_status"),
                "balance_before": int(u.get("credits_balance") or 0),
            }
            for u in affected_users[:20]
        ],
        "skipped": {"admins": skipped_admins, "subscribers": skipped_subscribers},
    }


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
        "feature": "admin_adjustment",
        "request_id": _admin.get("email"),
        "created_at": now_iso(),
    })
    return {"ok": True, "new_balance": new_balance}
