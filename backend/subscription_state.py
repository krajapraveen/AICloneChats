"""
subscription_state.py — Read-side subscription lifecycle state machine.

Why "read-side"
---------------
We deliberately do NOT store a `state` field on every user. State is derived
from immutable source-of-truth rows in `payment_orders`, `payment_refunds`,
`users.cancel_at_period_end`, and `users.deleted_at`. This means:

  - We can't drift between stored state and reality (a manual Mongo edit
    can't make a user "Active" if their last paid order is from 2024).
  - Recomputing is cheap (≤ 2 small queries) and always correct.
  - Bug fixes only need to live in one function.

States (returned as `state` field)
----------------------------------
  free                  — no paid order ever, or plan_id == "free"
  pending_verification  — user.plan_status says so AND no paid order
  active                — most recent paid order is within the period
  pending_cancellation  — user opted out but plan still inside paid window
  grace_period          — past expiry but within GRACE_PERIOD_DAYS
  expired               — past expiry + grace, no renewal
  cancelled             — explicit cancel + plan expired
  payment_failed        — most recent order has status=failed (no later paid one)
  refunded              — most recent paid order has a refund record
  deleted               — user.is_deleted (terminal)

Note: `admin_unlimited` is NOT a state — it's an orthogonal flag the UI
shows alongside the state ("Admin · Unlimited").
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auth import get_current_user
from admin import get_admin_user
from db import db
from anti_abuse import guard_expensive_action
from credits import is_admin_unlimited_user, PLAN_INDEX
from models import now_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])
admin_router = APIRouter(prefix="/api/admin/billing", tags=["billing-admin"])

PLAN_PERIOD_DAYS = 30
GRACE_PERIOD_DAYS = 3
PAYMENT_FAILED_RECENT_HOURS = 48  # only count as payment_failed if very recent


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _plan_name(plan_id: Optional[str]) -> Optional[str]:
    if not plan_id:
        return None
    plan = PLAN_INDEX.get(plan_id)
    return plan["name"] if plan else plan_id.capitalize()


async def compute_subscription_state(user: dict) -> dict:
    """Compute the full subscription state from immutable source rows."""
    user_id = user["user_id"]
    is_admin = is_admin_unlimited_user(user)
    is_deleted = bool(user.get("is_deleted"))
    cancel_at_period_end = bool(user.get("cancel_at_period_end"))
    current_plan_id = user.get("plan_id") or "free"
    plan_status_raw = user.get("plan_status") or ""

    if is_deleted:
        return {
            "state": "deleted", "state_label": "Deleted",
            "state_reason": "Account has been deleted.",
            "current_plan_id": "free", "current_plan_name": None,
            "started_at": None, "expires_at": None,
            "grace_period_until": None,
            "cancel_at_period_end": False,
            "admin_unlimited": False,
        }

    # Pull all paid + failed + refunded orders for this user, newest first
    orders = await db.payment_orders.find(
        {"user_id": user_id, "plan_id": {"$in": [pid for pid in PLAN_INDEX if pid != "free"]}},
        {
            "_id": 0, "order_id": 1, "status": 1, "plan_id": 1,
            "amount": 1, "currency": 1, "amount_inr": 1,
            "created_at": 1, "paid_at": 1, "credited_at": 1, "updated_at": 1,
            "credits_to_grant": 1, "provider": 1,
        },
    ).sort("created_at", -1).limit(50).to_list(50)

    refunds = await db.payment_refunds.find(
        {"user_id": user_id}, {"_id": 0, "order_id": 1, "created_at": 1, "amount": 1},
    ).sort("created_at", -1).limit(20).to_list(20)
    refunded_order_ids = {r["order_id"] for r in refunds}

    most_recent_paid = next((o for o in orders if o.get("status") == "paid"), None)
    most_recent_failed = next((o for o in orders if o.get("status") == "failed"), None)
    now = _now_utc()

    # ── Base case: no paid history
    if not most_recent_paid:
        # Could be a fresh signup waiting to verify, or a free user
        if plan_status_raw == "pending_verification":
            return _state_envelope(
                "pending_verification", "Pending verification",
                "Verify your email before subscribing.",
                current_plan_id, None, None, None, cancel_at_period_end, is_admin,
            )
        if most_recent_failed:
            failed_at = _parse_iso(most_recent_failed.get("updated_at") or most_recent_failed.get("created_at"))
            if failed_at and (now - failed_at) <= timedelta(hours=PAYMENT_FAILED_RECENT_HOURS):
                return _state_envelope(
                    "payment_failed", "Payment failed",
                    "Most recent payment attempt failed.",
                    current_plan_id, None, None, None, cancel_at_period_end, is_admin,
                )
        return _state_envelope(
            "free", "Free",
            "No active subscription.",
            "free", None, None, None, False, is_admin,
        )

    # ── There's at least one paid order
    paid_at = _parse_iso(most_recent_paid.get("paid_at") or most_recent_paid.get("credited_at"))
    if not paid_at:
        return _state_envelope(
            "active", "Active",
            "Most recent paid order has no timestamp; treating as active.",
            most_recent_paid.get("plan_id") or current_plan_id, None, None, None,
            cancel_at_period_end, is_admin,
        )

    expires_at = paid_at + timedelta(days=PLAN_PERIOD_DAYS)
    grace_until = expires_at + timedelta(days=GRACE_PERIOD_DAYS)

    # Refunded?
    if most_recent_paid["order_id"] in refunded_order_ids:
        return _state_envelope(
            "refunded", "Refunded",
            "Most recent paid order was refunded.",
            most_recent_paid.get("plan_id") or current_plan_id, paid_at, expires_at, None,
            cancel_at_period_end, is_admin,
        )

    # Inside paid window?
    if now < expires_at:
        if cancel_at_period_end:
            return _state_envelope(
                "pending_cancellation", "Pending cancellation",
                "You'll keep access until the period ends, then auto-cancel.",
                most_recent_paid.get("plan_id") or current_plan_id, paid_at, expires_at, None,
                True, is_admin,
            )
        return _state_envelope(
            "active", "Active",
            "Subscription is active.",
            most_recent_paid.get("plan_id") or current_plan_id, paid_at, expires_at, None,
            False, is_admin,
        )

    # Past expiry — but within grace?
    if now < grace_until:
        return _state_envelope(
            "grace_period", "Grace period",
            "Subscription expired but still within the 3-day grace window.",
            most_recent_paid.get("plan_id") or current_plan_id, paid_at, expires_at, grace_until,
            cancel_at_period_end, is_admin,
        )

    # Past expiry + grace
    if cancel_at_period_end:
        return _state_envelope(
            "cancelled", "Cancelled",
            "Cancellation took effect when the paid period ended.",
            most_recent_paid.get("plan_id") or current_plan_id, paid_at, expires_at, grace_until,
            True, is_admin,
        )
    return _state_envelope(
        "expired", "Expired",
        "Subscription expired. Renew to continue.",
        most_recent_paid.get("plan_id") or current_plan_id, paid_at, expires_at, grace_until,
        False, is_admin,
    )


def _state_envelope(state, state_label, reason, plan_id, paid_at, expires_at, grace_until, cancel_flag, is_admin):
    return {
        "state": state,
        "state_label": state_label,
        "state_reason": reason,
        "current_plan_id": plan_id,
        "current_plan_name": _plan_name(plan_id),
        "started_at": paid_at.isoformat() if paid_at else None,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "grace_period_until": grace_until.isoformat() if grace_until else None,
        "cancel_at_period_end": bool(cancel_flag),
        "admin_unlimited": bool(is_admin),
    }


# ─────────────── User endpoints ───────────────

@router.get("/subscription/state")
async def my_subscription_state(user: dict = Depends(get_current_user)):
    state = await compute_subscription_state(user)
    return state


class CancelSubscriptionReq(BaseModel):
    confirm: bool = Field(...)
    reason: Optional[str] = Field(default=None, max_length=500)


@router.post("/subscription/cancel")
async def cancel_subscription(payload: CancelSubscriptionReq, request: Request, user: dict = Depends(get_current_user)):
    """User-initiated cancellation. Sets `cancel_at_period_end=True` — access
    continues until expires_at, then state derives to 'cancelled'. No money
    is moved here; refunds are a separate admin action."""
    if not payload.confirm:
        raise HTTPException(status_code=400, detail={"code": "confirmation_required", "message": "Confirmation required."})
    await guard_expensive_action(
        user=user, scope="profile.subscription_cancel", request=request,
        max_per_user_per_min=1, max_per_user_per_hour=5,
        endpoint="POST /api/profile/subscription/cancel",
    )
    state = await compute_subscription_state(user)
    if state["state"] not in ("active", "grace_period"):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "no_active_subscription",
                "message": f"You can't cancel a subscription in state '{state['state_label']}'.",
            },
        )
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$set": {
            "cancel_at_period_end": True,
            "cancel_requested_at": now_iso(),
            "cancel_reason": (payload.reason or "")[:500],
            "updated_at": now_iso(),
        }},
    )
    new_state = await compute_subscription_state({**user, "cancel_at_period_end": True})
    return {"ok": True, "state": new_state}


@router.post("/subscription/resume")
async def resume_subscription(user: dict = Depends(get_current_user)):
    """Reverse a pending cancellation — only valid before the period ends."""
    state = await compute_subscription_state(user)
    if state["state"] != "pending_cancellation":
        raise HTTPException(
            status_code=400,
            detail={"code": "not_pending_cancellation", "message": "There's no pending cancellation to resume."},
        )
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"cancel_at_period_end": False, "updated_at": now_iso()},
         "$unset": {"cancel_requested_at": "", "cancel_reason": ""}},
    )
    new_state = await compute_subscription_state({**user, "cancel_at_period_end": False})
    return {"ok": True, "state": new_state}


# ─────────────── Admin endpoint: per-user subscription history ───────────────

@admin_router.get("/users/{user_id}/subscription-summary")
async def admin_user_subscription_summary(user_id: str, admin: dict = Depends(get_admin_user)):
    """One-shot forensic view used by Admin → Users → Subscription History.

    Returns:
      - account profile (PII)
      - current derived state
      - lifetime totals (revenue INR, credits purchased, credits consumed)
      - order history
      - credit event history (last 200)
    """
    user_doc = await db.users.find_one(
        {"user_id": user_id},
        {"_id": 0, "password_hash": 0},
    )
    if not user_doc:
        raise HTTPException(404, "User not found")

    state = await compute_subscription_state(user_doc)

    orders = await db.payment_orders.find(
        {"user_id": user_id}, {"_id": 0},
    ).sort("created_at", -1).limit(200).to_list(200)

    refunds = await db.payment_refunds.find(
        {"user_id": user_id}, {"_id": 0},
    ).sort("created_at", -1).limit(50).to_list(50)
    refunded_order_ids = {r["order_id"] for r in refunds}

    # Per-order derived sub-state for the timeline view
    for o in orders:
        o["refunded"] = o.get("order_id") in refunded_order_ids

    # Lifetime totals
    paid_orders = [o for o in orders if o.get("status") == "paid"]
    total_revenue_inr = sum((o.get("amount_inr") or 0) for o in paid_orders)
    total_credits_purchased = sum((o.get("credits_to_grant") or 0) for o in paid_orders)

    # Credits consumed: sum of negative deltas from credit_events excluding admin_adjust
    consumed_agg = await db.credit_events.aggregate([
        {"$match": {"user_id": user_id, "delta": {"$lt": 0}, "kind": {"$ne": "admin_adjust"}}},
        {"$group": {"_id": None, "total": {"$sum": "$delta"}}},
    ]).to_list(1)
    total_credits_consumed = abs(consumed_agg[0]["total"]) if consumed_agg else 0

    credit_events = await db.credit_events.find(
        {"user_id": user_id}, {"_id": 0},
    ).sort("created_at", -1).limit(200).to_list(200)

    return {
        "user": user_doc,
        "state": state,
        "lifetime": {
            "total_revenue_inr": total_revenue_inr,
            "total_paid_orders": len(paid_orders),
            "total_credits_purchased": total_credits_purchased,
            "total_credits_consumed": total_credits_consumed,
            "current_credits_balance": user_doc.get("credits_balance", 0),
            "first_paid_at": (paid_orders[-1].get("paid_at") if paid_orders else None),
            "last_paid_at": (paid_orders[0].get("paid_at") if paid_orders else None),
        },
        "orders": orders,
        "refunds": refunds,
        "credit_events": credit_events,
    }


@admin_router.get("/users/search")
async def admin_users_search(
    admin: dict = Depends(get_admin_user),
    q: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(default=25, ge=1, le=100),
):
    """Quick lookup by email substring or user_id exact match. Used by the
    Admin Users page to find an account before drilling into history."""
    query = {"$or": [
        {"email": {"$regex": q, "$options": "i"}},
        {"user_id": q},
    ]}
    rows = await db.users.find(
        query,
        {"_id": 0, "user_id": 1, "email": 1, "plan_id": 1, "plan_status": 1,
         "credits_balance": 1, "created_at": 1, "is_deleted": 1, "role": 1,
         "cancel_at_period_end": 1, "abuse_status": 1},
    ).sort("created_at", -1).limit(limit).to_list(limit)
    return {"users": rows, "count": len(rows)}
