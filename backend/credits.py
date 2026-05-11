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
        "monthly_credits": 50,
        "daily_credit_cap": 30,  # free users: max 30 deductions/day regardless of balance
        "description": "50 credits on signup. Daily 30-credit cap. Basic chat surfaces.",
        "features": [
            "AI Clone Chat",
            "Mood-Based Chat",
            "Smart Reply (limited)",
            "Anonymous Reality",
            "Translation Chat",
            "Conversation Memory",
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
        "description": "500 credits / month. Access to all basic AI chat categories.",
        "features": [
            "Everything in Free",
            "500 credits / month",
            "No daily caps",
            "Smart Reply unlimited modes",
        ],
        "tier_rank": 1,
        "is_active": True,
    },
    {
        "plan_id": "pro",
        "name": "Pro Chat",
        "price_inr": 1499,
        "monthly_credits": 2000,
        "daily_credit_cap": None,
        "description": "2,000 credits / month. Priority responses. All standard chats unlocked.",
        "features": [
            "Everything in Starter",
            "2,000 credits / month",
            "Priority response queue",
            "Voice → Message (full access)",
            "AI Debate Rooms (unlocked)",
        ],
        "tier_rank": 2,
        "is_active": True,
    },
    {
        "plan_id": "premium",
        "name": "Premium Emotional Chat",
        "price_inr": 3999,
        "monthly_credits": 7500,
        "daily_credit_cap": None,
        "description": "7,500 credits / month. Access to premium emotional & productivity surfaces.",
        "features": [
            "Everything in Pro",
            "7,500 credits / month",
            "Delayed Emotional Chat (full)",
            "Conversation Memory extraction",
            "Higher daily usage ceilings",
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
        "description": "25,000 credits / month. Heaviest users. Video Avatar Chat unlocked.",
        "features": [
            "Everything in Premium",
            "25,000 credits / month",
            "Video Avatar Chat (lipsync)",
            "Highest concurrency",
            "Priority support",
        ],
        "tier_rank": 4,
        "is_active": True,
    },
]


PLAN_INDEX = {p["plan_id"]: p for p in PLANS}


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
# Locked: text=1, smart-reply=2, voice=3, video-avatar=5, translation=1, delayed-create=2
CREDIT_COST = {
    "clone_chat": 1,
    "mood_chat": 1,
    "anonymous_chat": 1,
    "translation_chat": 1,
    "debate_chat": 1,
    "smart_reply": 2,
    "voice_message": 3,
    "video_avatar": 5,
    "delayed_create": 2,  # creation only — reveal is always free
    "conversation_memory": 1,
}


# ----- Credit grant: 50 credits, once per user, only after email verification -----
async def grant_signup_credits_if_eligible(user_id: str, email: str, ip_address: Optional[str], device_id: Optional[str]) -> dict:
    """Idempotent free-credit grant.

    Eligibility:
      - Caller MUST have already verified email (called by /verify-email/confirm on first success)
      - No prior grant exists for this user_id (unique index)
      - No prior grant for this normalized email (covers re-signup with same address)
      - Fraud signal not in cooldown for this device or IP
      - Admin email exempt — admins get nothing through this path; they have unlimited bypass

    Returns: {granted: bool, reason: str, credits: int}
    """
    email_norm = (email or "").lower().strip()
    if email_norm == ADMIN_UNLIMITED_EMAIL:
        # Admin doesn't need free credits — unlimited via bypass
        return {"granted": False, "reason": "admin_unlimited", "credits": 0}

    # Hard-block: existing grant on this email (covers same person re-registering)
    existing_email_grant = await db.credit_grants.find_one({"email": email_norm}, {"_id": 0})
    if existing_email_grant:
        await _log_fraud_signal(user_id, email_norm, ip_address, device_id, "duplicate_email_grant_attempt", severity=2)
        return {"granted": False, "reason": "email_already_granted", "credits": 0}

    # Hard-block: existing grant on this user_id (idempotent retries)
    existing_user_grant = await db.credit_grants.find_one({"user_id": user_id}, {"_id": 0})
    if existing_user_grant:
        return {"granted": False, "reason": "user_already_granted", "credits": 0}

    # Heuristic: device fingerprint already used for a prior grant
    if device_id:
        device_prior = await db.credit_grants.find_one({"device_id": device_id}, {"_id": 0})
        if device_prior:
            await _log_fraud_signal(user_id, email_norm, ip_address, device_id, "duplicate_device_grant_attempt", severity=3)
            return {"granted": False, "reason": "device_already_granted", "credits": 0}

    # Heuristic: >5 signups from same IP in last 24h → suspicious
    if ip_address:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        ip_count = await db.credit_grants.count_documents({"ip_address": ip_address, "created_at": {"$gte": since}})
        if ip_count >= 5:
            await _log_fraud_signal(user_id, email_norm, ip_address, device_id, "ip_signup_burst", severity=4)
            return {"granted": False, "reason": "ip_burst_cooldown", "credits": 0}

    # Cooldown check: this device or IP under active cooldown
    if device_id or ip_address:
        cooldown = await db.fraud_cooldowns.find_one({
            "$or": [{"device_id": device_id}, {"ip_address": ip_address}],
            "expires_at": {"$gt": now_iso()},
        }, {"_id": 0})
        if cooldown:
            return {"granted": False, "reason": "fraud_cooldown_active", "credits": 0}

    # All checks passed — issue the grant
    free_credits = PLAN_INDEX["free"]["monthly_credits"]  # 50
    grant_doc = {
        "grant_id": uuid.uuid4().hex,
        "user_id": user_id,
        "email": email_norm,
        "credits": free_credits,
        "ip_address": ip_address,
        "device_id": device_id,
        "reason": "signup_email_verified",
        "created_at": now_iso(),
    }
    try:
        await db.credit_grants.insert_one(dict(grant_doc))
    except Exception as e:
        # Unique-index race — treat as already-granted
        logger.warning("credit_grants insert race: %s", e)
        return {"granted": False, "reason": "race_already_granted", "credits": 0}

    # Apply credits to user
    await db.users.update_one(
        {"user_id": user_id},
        {"$set": {
            "credits_balance": free_credits,
            "credits_granted_at": now_iso(),
            "plan_id": "free",
            "plan_status": "active",
            "plan_renewed_at": now_iso(),
            "daily_credits_used": 0,
            "daily_credits_reset_at": now_iso(),
        }},
    )

    await _emit_credit_event(user_id, "grant", free_credits, 0, free_credits, surface="signup", request_id=grant_doc["grant_id"])
    return {"granted": True, "reason": "ok", "credits": free_credits}


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


async def credit_payment(user_id: str, credits: int, order_id: str, plan_id: str) -> int:
    """Add credits from a verified Cashfree webhook. Idempotency is the caller's
    responsibility — this function is only ever called from inside the webhook
    handler after the order's credited_at marker is checked.
    """
    res = await db.users.find_one_and_update(
        {"user_id": user_id},
        {"$set": {
            "plan_id": plan_id,
            "plan_status": "active",
            "plan_renewed_at": now_iso(),
        }, "$inc": {"credits_balance": credits}},
        return_document=True,
        projection={"_id": 0, "credits_balance": 1},
    )
    new_balance = (res or {}).get("credits_balance", credits)
    await _emit_credit_event(user_id, "grant", credits, new_balance - credits, new_balance, surface=f"payment:{plan_id}", request_id=order_id)
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
