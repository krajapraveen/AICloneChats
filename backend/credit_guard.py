"""
credit_guard: a tiny adapter that wraps deduct/refund around any monetized
chat surface. Designed to be used inside FastAPI route handlers.

Usage:
    handle = await charge_credits_or_402(user, surface="clone_chat")
    try:
        ...do work...
    except Exception:
        await handle.refund(reason="llm_failure")
        raise

Constitutional notes:
- Admin-unlimited users get a no-op handle (no deduction, no refund). The
  ledger never records them. is_admin_unlimited_user is the single source.
- Email verification gate is enforced here: an unverified user cannot consume
  paid surfaces (we use `email_not_verified` as the 402 reason).
- Plan-gating: every surface beyond `clone_chat` / `mood_chat` / `translation_chat`
  requires an active subscription. We don't soft-degrade — if the user does
  not have plan_status == 'active' AND a non-free plan, we 402 with
  `subscription_required`. This keeps revenue defenders simple.
"""
from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException

from credits import (
    CREDIT_COST,
    deduct_credits,
    refund_credits,
    is_admin_unlimited_user,
)
from db import db

logger = logging.getLogger(__name__)


# Surfaces that REQUIRE an active paid subscription (no pay-per-use on the
# free tier). The free tier is *only* a placeholder while the user verifies
# their email; it cannot consume any monetized surface.
SURFACES_REQUIRING_SUBSCRIPTION = {
    "smart_reply",
    "voice_message",
    "debate_chat",
    "anonymous_chat",
    "delayed_create",
    "video_avatar",
    "conversation_memory",
}

# Surfaces that are unlocked at every PAID tier (Starter and up). Free is still
# locked out — the credit balance check would fail anyway, but we surface a
# clearer 402 reason.
SURFACES_PAID_BASELINE = {
    "clone_chat",
    "mood_chat",
    "translation_chat",
}

# Surfaces that require Pro tier or above
SURFACES_PRO_PLUS = {
    "smart_reply",
    "voice_message",
    "debate_chat",
    "anonymous_chat",
    "delayed_create",
    "conversation_memory",
}

# Surfaces that require Ultimate
SURFACES_ULTIMATE_ONLY = {
    "video_avatar",
}

PLAN_TIER_RANK = {
    "free": 0,
    "starter": 1,
    "pro": 2,
    "premium": 3,
    "ultimate": 4,
}


@dataclass
class CreditHandle:
    """Handle returned by charge_credits_or_402; provides idempotent refund."""

    user: dict
    surface: str
    cost: int
    request_id: str
    admin: bool
    refunded: bool = False

    async def refund(self, *, reason: str = "ai_failure") -> None:
        if self.admin or self.refunded or self.cost <= 0:
            return
        await refund_credits(self.user, surface=self.surface, request_id=self.request_id)
        self.refunded = True
        logger.info("credit_refund surface=%s user=%s reason=%s req=%s", self.surface, self.user.get("user_id"), reason, self.request_id)


def _required_tier_rank(surface: str) -> int:
    if surface in SURFACES_ULTIMATE_ONLY:
        return PLAN_TIER_RANK["ultimate"]
    if surface in SURFACES_PRO_PLUS:
        return PLAN_TIER_RANK["pro"]
    if surface in SURFACES_PAID_BASELINE:
        return PLAN_TIER_RANK["starter"]
    return PLAN_TIER_RANK["starter"]  # default — every monetized surface requires Starter+


async def charge_credits_or_402(user: dict, *, surface: str, request_id: Optional[str] = None) -> CreditHandle:
    """Atomically charge the credit cost for `surface`. Raises HTTPException(402)
    on any failure (insufficient balance, unverified email, fraud cooldown,
    plan/tier gate). Returns a CreditHandle that provides .refund() on AI
    failure.

    Admin-unlimited users get a free pass.
    """
    if not user:
        raise HTTPException(status_code=401, detail={"code": "auth_required", "message": "Sign in to use this feature."})

    if is_admin_unlimited_user(user):
        return CreditHandle(user=user, surface=surface, cost=0, request_id=request_id or "admin", admin=True)

    cost = CREDIT_COST.get(surface)
    if cost is None:
        raise ValueError(f"Unknown surface: {surface}")

    # Plan/tier gate — fail fast with a useful error
    plan_id = (user.get("plan_id") or "free").lower()
    plan_status = (user.get("plan_status") or "").lower()
    needs_tier = _required_tier_rank(surface)
    have_tier = PLAN_TIER_RANK.get(plan_id, 0)
    plan_active = plan_status == "active" and plan_id != "free"

    if not plan_active:
        await _log_paywall_event(user, surface, "subscription_required")
        raise HTTPException(
            status_code=402,
            detail={
                "code": "subscription_required",
                "surface": surface,
                "required_plan": _plan_name_for_rank(needs_tier),
                "message": "Subscribe to use this feature.",
            },
        )

    if have_tier < needs_tier:
        await _log_paywall_event(user, surface, "plan_upgrade_required")
        raise HTTPException(
            status_code=402,
            detail={
                "code": "plan_upgrade_required",
                "surface": surface,
                "current_plan": plan_id,
                "required_plan": _plan_name_for_rank(needs_tier),
                "message": f"This feature requires the {_plan_name_for_rank(needs_tier)} plan or higher.",
            },
        )

    # Email verification gate — paid users still must verify their email
    if not user.get("email_verified"):
        await _log_paywall_event(user, surface, "email_not_verified")
        raise HTTPException(
            status_code=402,
            detail={"code": "email_not_verified", "surface": surface, "message": "Verify your email to start using paid features."},
        )

    rid = request_id or f"{surface}_{uuid.uuid4().hex[:10]}"
    res = await deduct_credits(user, surface=surface, request_id=rid)
    if not res["ok"]:
        await _log_paywall_event(user, surface, res["reason"])
        # Re-shape ledger refusal into a structured 402
        raise HTTPException(
            status_code=402,
            detail={
                "code": res["reason"],
                "surface": surface,
                "cost": res.get("cost"),
                "credits_balance": res.get("balance"),
                "daily_cap": res.get("daily_cap"),
                "daily_used": res.get("daily_used"),
                "message": _user_message_for_reason(res["reason"]),
            },
        )

    return CreditHandle(user=user, surface=surface, cost=cost, request_id=rid, admin=False)


async def _log_paywall_event(user: dict, surface: str, code: str) -> None:
    """One write per 402. Used to compute "first paid intent surface" and the
    paywall→checkout funnel. Fire-and-forget; failures are swallowed so paywall
    enforcement is never blocked by instrumentation.
    """
    try:
        await db.paywall_events.insert_one({
            "event_id": uuid.uuid4().hex,
            "user_id": user.get("user_id"),
            "email": user.get("email"),
            "surface": surface,
            "code": code,
            "plan_id": user.get("plan_id"),
            "plan_status": user.get("plan_status"),
            "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        })
    except Exception:
        pass


def _plan_name_for_rank(rank: int) -> str:
    for pid, r in PLAN_TIER_RANK.items():
        if r == rank:
            return {"starter": "Starter", "pro": "Pro", "premium": "Premium Emotional", "ultimate": "Ultimate Creator"}.get(pid, pid.title())
    return "Starter"


def _user_message_for_reason(reason: str) -> str:
    return {
        "insufficient_balance": "You're out of credits. Top up or upgrade your plan to continue.",
        "daily_cap_reached": "You've hit today's daily limit on this plan.",
        "fraud_cooldown": "We've paused activity on this account briefly. Try again in a few hours.",
        "user_not_found": "Account state is out of sync. Sign out and sign back in.",
    }.get(reason, "We couldn't process this request right now.")


async def fresh_user(user: dict) -> dict:
    """Always re-fetch the user from DB before charging — never trust the dep dict
    for balance state. Returns the dep dict if DB lookup fails (admin path).
    """
    if not user:
        return user
    fresh = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0, "password_hash": 0})
    return fresh or user
