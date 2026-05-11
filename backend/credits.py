"""
Plans, Credits, and Fraud Signals — the monetization backbone.

Constitutional notes encoded as architecture:
- Admin email is HARDCODED in code (env var as override). Frontend role flags are NEVER
  trusted. The admin-unlimited bypass reads the email off the authenticated user
  document, which was looked up from the DB by session token. Cannot be spoofed.
- Free credits are granted ONCE per (user_id, email) pair AND only after email
  verification (separate module). The grant record is in `credit_grants` collection,
  unique index on user_id. Concurrent attempts will hit the unique index, not a race.
- Daily hard cap is enforced INDEPENDENTLY of balance. A free user with 500 credits
  who hits the daily cap is still blocked. Cap exists to bound infra cost when an
  attacker successfully harvests credits.
- Negative balance is impossible: the ledger uses `find_one_and_update` with a
  `$gte` guard on balance. If insufficient, the update matches no document and
  the operation reports failure to the caller. There is no path that subtracts
  without checking first.
- Every deduction emits a `credit_events` record with surface, cost, balance_before,
  balance_after, request_id. This is the audit log.
"""
from __future__ import annotations

import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from db import db
from models import now_iso

logger = logging.getLogger(__name__)


# ----- Admin allowlist (backend-only, NEVER trust frontend) -----
ADMIN_UNLIMITED_EMAIL = os.environ.get("ADMIN_UNLIMITED_EMAIL", "krajapraveen@gmail.com").lower().strip()


def is_admin_unlimited_user(user: dict) -> bool:
    """Single source of truth for the admin-unlimited bypass.

    Reads the email off the authenticated user document looked up by session
    token. Does NOT consult any client-supplied header, body, role flag, or
    JWT claim. If the email matches the env-configured admin address, the
    user has unlimited credits, no daily cap, and no fraud-cooldown lockout.
    """
    if not user:
        return False
    email = (user.get("email") or "").lower().strip()
    return bool(email) and email == ADMIN_UNLIMITED_EMAIL


# ----- Plans -----
PLANS = [
    {
        "plan_id": "free",
        "name": "Free",
        "price_inr": 0,
        "monthly_credits": 0,  # No free credits. Email verification still required to use anything paid-for.
        "daily_credit_cap": None,
        "description": "Verify your email to start. Subscribe to use any chat surface.",
        "features": [
            "Email verification",
            "Subscribe to begin",
        ],
        "tier_rank": 0,
        "is_active": True,
    },
    {
        "plan_id": "starter",
        "name": "Starter Chat",
        "price_inr": 499,
        "monthly_credits": 500,
        "daily_credit_cap": None,
        "description": "500 credits / month. AI Clone, Mood, Translation, basic Smart Reply, limited Memory.",
        "features": [
            "500 credits / month",
            "AI Clone Chat",
            "Mood-Based Chat",
            "Translation Chat",
            "Basic Smart Reply",
            "Limited Conversation Memory",
        ],
        "tier_rank": 1,
        "is_active": True,
    },
    {
        "plan_id": "pro",
        "name": "Pro Chat",
        "price_inr": 1499,
        "monthly_credits": 2500,
        "daily_credit_cap": None,
        "description": "2,500 credits / month. Full Smart Reply, Voice, Debates, Delayed Emotional, Anonymous.",
        "features": [
            "2,500 credits / month",
            "Full Smart Reply",
            "Voice → Message",
            "AI Debate Rooms",
            "Delayed-Delivery Emotional Chat",
            "Expanded Conversation Memory",
            "Anonymous Reality",
            "Priority response queue",
        ],
        "tier_rank": 2,
        "is_active": True,
    },
    {
        "plan_id": "premium",
        "name": "Premium Emotional Chat",
        "price_inr": 3999,
        "monthly_credits": 8000,
        "daily_credit_cap": None,
        "description": "8,000 credits / month. Advanced emotional AI, long memory, premium voice, early-access features.",
        "features": [
            "8,000 credits / month",
            "Advanced emotional AI",
            "Long memory threads",
            "Mood adaptation",
            "Premium voice styles",
            "Full Anonymous Reality",
            "Advanced Delayed Emotional Chat",
            "Early-access experimental features",
            "Faster queue priority",
        ],
        "tier_rank": 3,
        "is_active": True,
    },
    {
        "plan_id": "ultimate",
        "name": "Ultimate Creator",
        "price_inr": 9999,
        "monthly_credits": 25000,
        "daily_credit_cap": None,
        "description": "25,000 credits / month. Video Chat unlocked. Highest limits + premium rendering queue.",
        "features": [
            "25,000 credits / month",
            "Video Chat access",
            "Highest concurrency",
            "Highest memory retention",
            "Premium rendering queue",
            "Future premium tools auto-unlocked",
        ],
        "tier_rank": 4,
        "is_active": True,
    },
]


PLAN_INDEX = {p["plan_id"]: p for p in PLANS}


# ----- Top-up packs (subscribers-only) -----
# Founder-locked 2026-05-11. Top-ups never carry a recurring plan; they only
# top up the balance of an ACTIVE subscriber. Server-side enforcement lives
# in payments_cashfree.create_topup_order — frontend gating is cosmetic.
TOP_UP_PACKS = [
    {
        "pack_id": "topup_small",
        "name": "Small Top-Up",
        "price_inr": 299,
        "credits": 300,
        "blurb": "300 extra credits — perfect for a few late-night chats.",
        "tier_rank": 1,
        "is_active": True,
    },
    {
        "pack_id": "topup_medium",
        "name": "Medium Top-Up",
        "price_inr": 999,
        "credits": 1200,
        "blurb": "1,200 credits — keeps you going for a couple of weeks.",
        "tier_rank": 2,
        "is_active": True,
        "is_popular": True,
    },
    {
        "pack_id": "topup_large",
        "name": "Large Top-Up",
        "price_inr": 2999,
        "credits": 4000,
        "blurb": "4,000 credits — for power users between renewals.",
        "tier_rank": 3,
        "is_active": True,
    },
    {
        "pack_id": "topup_mega",
        "name": "Creator Top-Up",
        "price_inr": 7999,
        "credits": 12000,
        "blurb": "12,000 credits — for creators and heavy users.",
        "tier_rank": 4,
        "is_active": True,
    },
]

TOPUP_INDEX = {p["pack_id"]: p for p in TOP_UP_PACKS}


def is_active_subscriber(user: dict) -> bool:
    """Top-up purchase guard. True iff the user holds a non-free plan that is
    currently active. Admin-unlimited users are NOT considered subscribers
    (they have no plan to top up)."""
    if not user:
        return False
    if is_admin_unlimited_user(user):
        return False
    plan_id = (user.get("plan_id") or "free").lower()
    plan_status = (user.get("plan_status") or "").lower()
    return plan_id != "free" and plan_status == "active"


async def ensure_plans_seeded() -> None:
    """Upsert plans on every boot. Plans are CODE, not user data."""
    for p in PLANS:
        await db.subscription_plans.update_one(
            {"plan_id": p["plan_id"]},
            {"$set": {**p, "updated_at": now_iso()}, "$setOnInsert": {"created_at": now_iso()}},
            upsert=True,
        )
    logger.info("subscription_plans seeded (%d)", len(PLANS))


# ----- Credit costs per surface -----
# Locked per founder-directed pricing reset (2026-05-11):
#   cheap/high-freq: 1 (clone, mood, translation)
#   medium: 2-3 (smart_reply, debate, memory, voice)
#   expensive: 3-4 (anonymous, delayed_create)
#   highest: 5 (video_avatar) — dynamic 5-8 future
CREDIT_COST = {
    "clone_chat": 1,
    "mood_chat": 1,
    "translation_chat": 1,
    "smart_reply": 2,
    "debate_chat": 2,
    "conversation_memory": 2,
    "voice_message": 3,
    "anonymous_chat": 3,
    "delayed_create": 4,
    "video_avatar": 5,
}


# ----- Credit grant: signup grants are DISABLED (founder directive 2026-05-11) -----
# New users start at 0. They must subscribe to receive credits.
# This function is kept callable for backward compatibility but now returns
# {granted: false, reason: "signup_grants_disabled"} for every caller.
SIGNUP_GRANTS_DISABLED = True


async def grant_signup_credits_if_eligible(user_id: str, email: str, ip_address: Optional[str], device_id: Optional[str]) -> dict:
    """Permanently disabled per founder directive 2026-05-11. New users start
    at 0 credits and must subscribe. Admin is handled by the unlimited bypass.
    """
    email_norm = (email or "").lower().strip()
    if email_norm == ADMIN_UNLIMITED_EMAIL:
        return {"granted": False, "reason": "admin_unlimited", "credits": 0}
    return {"granted": False, "reason": "signup_grants_disabled", "credits": 0}


# ----- Deduction / refund -----
async def deduct_credits(user: dict, surface: str, request_id: Optional[str] = None) -> dict:
    """Atomic deduction. Returns {ok, balance, cost, reason, admin_unlimited}.

    Reasons (when ok=False):
      - insufficient_balance
      - daily_cap_reached
      - fraud_cooldown
      - plan_inactive
    """
    if is_admin_unlimited_user(user):
        return {"ok": True, "balance": -1, "cost": 0, "reason": "admin_unlimited", "admin_unlimited": True}

    cost = CREDIT_COST.get(surface)
    if cost is None:
        raise ValueError(f"Unknown surface for credit deduction: {surface}")

    user_id = user["user_id"]

    # Fresh fetch — never trust the user dict from the request handler for balance reads
    fresh = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not fresh:
        return {"ok": False, "balance": 0, "cost": cost, "reason": "user_not_found", "admin_unlimited": False}

    # Plan-level fraud cooldown check
    if fresh.get("fraud_cooldown_until") and fresh["fraud_cooldown_until"] > now_iso():
        return {"ok": False, "balance": fresh.get("credits_balance", 0), "cost": cost, "reason": "fraud_cooldown", "admin_unlimited": False}

    # Daily cap for free users (independent of balance)
    plan_id = fresh.get("plan_id") or "free"
    plan = PLAN_INDEX.get(plan_id, PLAN_INDEX["free"])
    daily_cap = plan.get("daily_credit_cap")

    # Daily reset rollover
    daily_used = fresh.get("daily_credits_used") or 0
    daily_reset = fresh.get("daily_credits_reset_at") or "1970-01-01T00:00:00+00:00"
    reset_dt = _parse_iso(daily_reset)
    if reset_dt < datetime.now(timezone.utc) - timedelta(hours=24):
        daily_used = 0  # local view; will be persisted by the atomic update below
        # We do the persistent reset in the atomic step

    if daily_cap is not None and (daily_used + cost) > daily_cap:
        return {"ok": False, "balance": fresh.get("credits_balance", 0), "cost": cost, "reason": "daily_cap_reached", "admin_unlimited": False, "daily_cap": daily_cap, "daily_used": daily_used}

    # Atomic deduction with balance guard
    res = await db.users.find_one_and_update(
        {
            "user_id": user_id,
            "credits_balance": {"$gte": cost},
        },
        [
            # Aggregation pipeline update — lets us conditionally reset daily counters
            {"$set": {
                "credits_balance": {"$subtract": ["$credits_balance", cost]},
                "daily_credits_used": {
                    "$cond": [
                        {"$lt": [{"$toDate": "$daily_credits_reset_at"}, {"$dateSubtract": {"startDate": "$$NOW", "unit": "hour", "amount": 24}}]},
                        cost,
                        {"$add": [{"$ifNull": ["$daily_credits_used", 0]}, cost]},
                    ]
                },
                "daily_credits_reset_at": {
                    "$cond": [
                        {"$lt": [{"$toDate": "$daily_credits_reset_at"}, {"$dateSubtract": {"startDate": "$$NOW", "unit": "hour", "amount": 24}}]},
                        {"$toString": "$$NOW"},
                        "$daily_credits_reset_at",
                    ]
                },
            }},
        ],
        return_document=True,
        projection={"_id": 0, "credits_balance": 1, "daily_credits_used": 1},
    )
    if not res:
        return {"ok": False, "balance": fresh.get("credits_balance", 0), "cost": cost, "reason": "insufficient_balance", "admin_unlimited": False}

    new_balance = res.get("credits_balance", 0)
    await _emit_credit_event(user_id, "deduct", -cost, new_balance + cost, new_balance, surface=surface, request_id=request_id)
    return {"ok": True, "balance": new_balance, "cost": cost, "reason": "ok", "admin_unlimited": False}


async def refund_credits(user: dict, surface: str, request_id: Optional[str] = None) -> None:
    """Refund a previously-deducted amount on AI generation failure.

    Admin path is a no-op (admin was never debited).
    """
    if is_admin_unlimited_user(user):
        return
    cost = CREDIT_COST.get(surface)
    if cost is None:
        return
    user_id = user["user_id"]
    res = await db.users.find_one_and_update(
        {"user_id": user_id},
        {"$inc": {"credits_balance": cost, "daily_credits_used": -cost}},
        return_document=True,
        projection={"_id": 0, "credits_balance": 1},
    )
    if res:
        await _emit_credit_event(user_id, "refund", cost, res.get("credits_balance", 0) - cost, res.get("credits_balance", 0), surface=surface, request_id=request_id)


async def credit_payment(user_id: str, credits: int, order_id: str, plan_id: Optional[str] = None, kind: str = "subscription", pack_id: Optional[str] = None) -> int:
    """Add credits from a verified Cashfree webhook. Idempotency is the caller's
    responsibility — this function is only ever called from inside the webhook
    handler after the order's credited_at marker is checked.

    For kind="subscription": sets plan_id, marks plan as active, increments credits.
    For kind="topup":         credits-only top up. Plan/status untouched.
    """
    set_fields: dict = {}
    if kind == "subscription" and plan_id:
        set_fields = {"plan_id": plan_id, "plan_status": "active", "plan_renewed_at": now_iso()}
    update_doc: dict = {"$inc": {"credits_balance": credits}}
    if set_fields:
        update_doc["$set"] = set_fields
    res = await db.users.find_one_and_update(
        {"user_id": user_id},
        update_doc,
        return_document=True,
        projection={"_id": 0, "credits_balance": 1},
    )
    new_balance = (res or {}).get("credits_balance", credits)
    surface_label = f"payment:{plan_id}" if kind == "subscription" else f"topup:{pack_id or 'unknown'}"
    await _emit_credit_event(user_id, "grant", credits, new_balance - credits, new_balance, surface=surface_label, request_id=order_id)
    return new_balance


# ----- User-facing balance read -----
async def get_user_credit_state(user: dict) -> dict:
    if is_admin_unlimited_user(user):
        return {
            "admin_unlimited": True,
            "credits_balance": None,
            "plan_id": "admin",
            "plan_name": "Admin (unlimited)",
            "daily_cap": None,
            "daily_used": 0,
            "fraud_cooldown_active": False,
        }
    fresh = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})
    plan_id = (fresh or {}).get("plan_id") or "free"
    plan = PLAN_INDEX.get(plan_id, PLAN_INDEX["free"])
    return {
        "admin_unlimited": False,
        "credits_balance": (fresh or {}).get("credits_balance", 0),
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "daily_cap": plan.get("daily_credit_cap"),
        "daily_used": (fresh or {}).get("daily_credits_used") or 0,
        "fraud_cooldown_active": bool((fresh or {}).get("fraud_cooldown_until") and (fresh or {}).get("fraud_cooldown_until") > now_iso()),
    }


# ----- Audit log -----
async def _emit_credit_event(user_id: str, kind: str, delta: int, balance_before: int, balance_after: int, *, surface: str, request_id: Optional[str]) -> None:
    await db.credit_events.insert_one({
        "event_id": uuid.uuid4().hex,
        "user_id": user_id,
        "kind": kind,
        "delta": delta,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "surface": surface,
        "request_id": request_id,
        "created_at": now_iso(),
    })


# ----- Fraud signals -----
async def _log_fraud_signal(user_id: Optional[str], email: Optional[str], ip_address: Optional[str], device_id: Optional[str], signal: str, severity: int = 1) -> None:
    """Record a single fraud signal. Triggers cooldown if cumulative score crosses 6."""
    doc = {
        "signal_id": uuid.uuid4().hex,
        "user_id": user_id,
        "email": email,
        "ip_address": ip_address,
        "device_id": device_id,
        "signal": signal,
        "severity": severity,
        "created_at": now_iso(),
    }
    await db.fraud_signals.insert_one(dict(doc))

    # Cumulative scoring in last 24h
    if device_id or ip_address:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        match: dict = {"created_at": {"$gte": since}}
        if device_id and ip_address:
            match["$or"] = [{"device_id": device_id}, {"ip_address": ip_address}]
        elif device_id:
            match["device_id"] = device_id
        else:
            match["ip_address"] = ip_address
        rows = await db.fraud_signals.aggregate([
            {"$match": match},
            {"$group": {"_id": None, "score": {"$sum": "$severity"}}},
        ]).to_list(1)
        score = rows[0]["score"] if rows else 0
        if score >= 6:
            cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
            await db.fraud_cooldowns.update_one(
                {"$or": [{"device_id": device_id}, {"ip_address": ip_address}]},
                {"$set": {
                    "device_id": device_id,
                    "ip_address": ip_address,
                    "score": score,
                    "expires_at": cooldown_until,
                    "created_at": now_iso(),
                }},
                upsert=True,
            )
            if user_id:
                await db.users.update_one(
                    {"user_id": user_id},
                    {"$set": {"fraud_cooldown_until": cooldown_until}},
                )


def _parse_iso(s: str) -> datetime:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
