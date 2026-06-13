"""Strict 0-credit premium policy — end-to-end enforcement.

Locks four invariants that together implement the original product
philosophy "all non-admin users get zero credits; everything paid is
gated behind a subscription or top-up":

  1. New non-admin signups land with `credits_balance == 0` and
     `plan_id == 'free'` — no welcome grant.
  2. Both production admin emails (`admin@aiclonechats.com` and
     `krajapraveen@aiclonechats.com`) — plus `krajapraveen@gmail.com`,
     which the platform was originally seeded with — resolve to
     `is_admin_unlimited_user == True`. They bypass the paywall.
  3. Any feature endpoint (we use `/api/clones/companion/chat` as the
     canonical example) returns 402 for a free user with 0 credits.
  4. A free (non-subscriber) user CANNOT purchase a top-up — the
     `/api/payments/create-order` endpoint returns 403 with code
     `subscription_required_for_topup`. Only subscribers can buy
     top-ups.
"""
from __future__ import annotations

import os
import sys
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

from conftest import get_shared_loop  # noqa: E402
from credits import ADMIN_UNLIMITED_EMAILS, is_admin_unlimited_user  # noqa: E402


def _run(coro):
    return get_shared_loop().run_until_complete(coro)


# ──────────── Invariant 1: new signups get 0 credits ────────────

def test_new_signup_gets_zero_credits():
    """A freshly registered user must have credits_balance=0 and plan=free."""
    email = f"zerog_{uuid.uuid4().hex[:10]}@example.com"
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "TestPass123!", "name": "ZeroGrant Tester"},
        timeout=20,
    )
    assert r.status_code == 200, r.text

    async def _fetch():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        u = await db.users.find_one({"email": email}, {"_id": 0, "credits_balance": 1, "plan_id": 1})
        # Cleanup
        await db.users.delete_one({"email": email})
        await db.user_sessions.delete_many({"user_id": r.json()["user"]["user_id"]})
        return u

    u = _run(_fetch())
    assert u is not None, "user must be created"
    assert (u.get("credits_balance") or 0) == 0, (
        f"new signup credits must be 0 (strict 0-credit policy), got {u.get('credits_balance')}"
    )
    assert (u.get("plan_id") or "free") == "free"


# ──────────── Invariant 2: both production admins are unlimited ────────────

@pytest.mark.parametrize("admin_email", [
    "admin@aiclonechats.com",
    "krajapraveen@aiclonechats.com",
    "krajapraveen@gmail.com",  # the originally-seeded admin
])
def test_production_admin_emails_are_unlimited(admin_email):
    """Both admins the operator named MUST bypass the paywall."""
    assert admin_email.lower() in {e.lower() for e in ADMIN_UNLIMITED_EMAILS}, (
        f"{admin_email} is not in ADMIN_UNLIMITED_EMAILS — set ADMIN_UNLIMITED_EMAIL "
        f"in backend/.env to include this address"
    )
    assert is_admin_unlimited_user({"email": admin_email}) is True


def test_random_user_is_not_unlimited():
    assert is_admin_unlimited_user({"email": "random@example.com"}) is False
    assert is_admin_unlimited_user({"email": ""}) is False
    assert is_admin_unlimited_user({}) is False


# ──────────── Invariant 3: feature endpoint 402s for 0-credit free user ────────────

def test_feature_endpoint_paywalls_free_user():
    """Free user with 0 credits hits /api/clones/companion/chat → expects
    HTTP 402 with `insufficient_balance` or `subscription_required` code."""
    # Register fresh free user
    email = f"paywall_{uuid.uuid4().hex[:10]}@example.com"
    reg = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "TestPass123!", "name": "Paywall Tester"},
        timeout=20,
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["session_token"]
    user_id = reg.json()["user"]["user_id"]

    try:
        # Mark as verified so we test the credit paywall, not the verify gate
        async def _verify():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.users.update_one(
                {"user_id": user_id},
                {"$set": {"email_verified": True, "credits_balance": 0}},
            )
        _run(_verify())

        r = requests.post(
            f"{BASE_URL}/api/clones/companion/chat",
            json={"message": "Hello"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        assert r.status_code == 402, (
            f"Free user with 0 credits MUST get 402 from feature endpoint, "
            f"got {r.status_code}: {r.text[:200]}"
        )
        detail = r.json().get("detail", {})
        if isinstance(detail, dict):
            code = detail.get("code", "")
            assert "insufficient" in code or "subscription" in code or "balance" in code, (
                f"402 detail.code should signal paywall reason, got: {code}"
            )
    finally:
        async def _cleanup():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.users.delete_one({"user_id": user_id})
            await db.user_sessions.delete_many({"user_id": user_id})
        _run(_cleanup())


# ──────────── Invariant 4: free user cannot purchase a top-up ────────────

def test_free_user_cannot_buy_topup():
    """Free (non-subscriber) user hits /api/payments/create-order with a
    topup_pack_id → expects HTTP 403 with `subscription_required_for_topup`."""
    email = f"notopup_{uuid.uuid4().hex[:10]}@example.com"
    reg = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "TestPass123!", "name": "NoTopup"},
        timeout=20,
    )
    assert reg.status_code == 200
    token = reg.json()["session_token"]
    user_id = reg.json()["user"]["user_id"]

    try:
        # Fetch available topup catalog so we use a real pack_id
        cat = requests.get(f"{BASE_URL}/api/topups/catalog", timeout=15).json()
        packs = cat.get("packs") or []
        if not packs:
            pytest.skip("no topup packs configured")
        pack_id = packs[0]["pack_id"]

        r = requests.post(
            f"{BASE_URL}/api/payments/create-order",
            json={"pack_id": pack_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        assert r.status_code == 403, (
            f"Free user MUST be blocked from top-up purchase, got {r.status_code}: {r.text[:200]}"
        )
        detail = r.json().get("detail", {})
        if isinstance(detail, dict):
            assert detail.get("code") == "subscription_required_for_topup", (
                f"403 code must be `subscription_required_for_topup`, got {detail.get('code')}"
            )
    finally:
        async def _cleanup():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.users.delete_one({"user_id": user_id})
            await db.user_sessions.delete_many({"user_id": user_id})
        _run(_cleanup())


def test_subscriber_can_initiate_topup_purchase():
    """A subscriber (pro/active) is NOT blocked by the
    subscription_required_for_topup gate. We don't complete the order;
    we just verify the gate doesn't reject them."""
    email = f"sub_{uuid.uuid4().hex[:10]}@example.com"
    reg = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "TestPass123!", "name": "Sub"},
        timeout=20,
    )
    assert reg.status_code == 200
    token = reg.json()["session_token"]
    user_id = reg.json()["user"]["user_id"]

    try:
        # Promote to pro/active
        async def _promote():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.users.update_one(
                {"user_id": user_id},
                {"$set": {"plan_id": "pro", "plan_status": "active",
                          "email_verified": True, "credits_balance": 100}},
            )
        _run(_promote())

        cat = requests.get(f"{BASE_URL}/api/topups/catalog", timeout=15).json()
        packs = cat.get("packs") or []
        if not packs:
            pytest.skip("no topup packs configured")
        pack_id = packs[0]["pack_id"]

        r = requests.post(
            f"{BASE_URL}/api/payments/create-order",
            json={"pack_id": pack_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        # Must NOT be 403/subscription_required_for_topup. May be 200
        # (order created) or 503 (gateway not configured) — both prove
        # the subscriber gate passed.
        assert r.status_code != 403 or "subscription_required_for_topup" not in (r.text or ""), (
            f"Subscriber must pass the topup gate, got {r.status_code}: {r.text[:200]}"
        )
    finally:
        async def _cleanup():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.users.delete_one({"user_id": user_id})
            await db.user_sessions.delete_many({"user_id": user_id})
            await db.payment_orders.delete_many({"user_id": user_id})
        _run(_cleanup())
