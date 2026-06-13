"""End-to-end coverage for the production backfill endpoint
`POST /api/admin/billing/enforce-zero-credit-policy`.

The platform enforces a strict 0-credit policy: free users start at 0 and
never accrue credits. Historical rows in production may still carry stale
positive balances; this endpoint reconciles them.

Spec we pin here:
  - Admin gate: non-admin callers 403.
  - dry_run=true: reports what would change, writes nothing.
  - Real run: zeroes the balance of (non-admin, non-subscriber) users with
    balance > 0, emits an `admin_adjust` credit_events row per user.
  - Active subscribers AND admin-unlimited accounts are NEVER touched.
  - Audit row carries `surface=admin_adjust:<reason>` + feature=admin_adjustment.
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

ADMIN_EMAIL = "krajapraveen@gmail.com"


def _new_email(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}@example.com"


def _register(email: str, password: str = "TestPass123!") -> dict:
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": password, "name": "ZeroPolicy Tester"},
        timeout=30,
    )
    assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"
    body = r.json()
    # Register returns {user: {...user_id, email...}, session_token}
    return {
        "user_id": body["user"]["user_id"],
        "email": body["user"]["email"],
        "session_token": body["session_token"],
    }


async def _mint_admin_token() -> str | None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    admin_user = await db.users.find_one({"email": ADMIN_EMAIL}, {"user_id": 1})
    if not admin_user:
        return None
    token = f"st_{uuid.uuid4().hex}{uuid.uuid4().hex}"
    await db.user_sessions.insert_one({
        "session_token": token,
        "user_id": admin_user["user_id"],
        "source": "test-mint-admin-zero-policy",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    })
    return token


def _admin_token() -> str:
    token = asyncio.new_event_loop().run_until_complete(_mint_admin_token())
    if not token:
        pytest.skip("admin user not seeded")
    return token


async def _seed_balance(user_id: str, balance: int, plan_id: str = "free", plan_status: str = ""):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    await db.users.update_one(
        {"user_id": user_id},
        {"$set": {
            "credits_balance": balance,
            "plan_id": plan_id,
            "plan_status": plan_status,
        }},
    )


async def _get_balance(user_id: str) -> int:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    u = await db.users.find_one({"user_id": user_id}, {"credits_balance": 1})
    return int((u or {}).get("credits_balance") or 0)


async def _count_admin_adjust_events(user_id: str, surface_substr: str = "enforce_zero_policy") -> int:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    return await db.credit_events.count_documents({
        "user_id": user_id,
        "kind": "admin_adjust",
        "surface": {"$regex": surface_substr},
    })


def _post_enforce(token: str | None, body: dict | None = None) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.post(
        f"{BASE_URL}/api/admin/billing/enforce-zero-credit-policy",
        json=body or {},
        headers=headers,
        timeout=60,
    )


def test_admin_only():
    """Non-admin users must be blocked."""
    email = _new_email("zero_nonadmin")
    reg = _register(email)
    r = _post_enforce(reg["session_token"], {"dry_run": True})
    assert r.status_code == 403, r.text


def test_unauth_blocked():
    r = _post_enforce(None, {"dry_run": True})
    assert r.status_code in (401, 403), r.text


def test_dry_run_does_not_write():
    """Dry-run must report counts but never mutate balances or write events."""
    token = _admin_token()
    email = _new_email("zero_dry")
    reg = _register(email)
    uid = reg["user_id"]
    asyncio.new_event_loop().run_until_complete(_seed_balance(uid, 500))

    r = _post_enforce(token, {"dry_run": True})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["dry_run"] is True
    assert data["affected"] == 0
    assert data["scanned"] >= 1
    assert data["total_credits_zeroed"] >= 500
    # sample should contain THIS user
    # Sample is capped to 20; if there are >20 affected users in DB,
    # the slice may not include us. Verify state directly:
    balance_after = asyncio.new_event_loop().run_until_complete(_get_balance(uid))
    assert balance_after == 500, "dry_run must not mutate"

    events = asyncio.new_event_loop().run_until_complete(_count_admin_adjust_events(uid))
    assert events == 0, "dry_run must not insert audit rows"


def test_real_run_zeroes_free_users_and_audits():
    """Real run: free users with positive balance get reset to 0 + audit row."""
    token = _admin_token()
    email = _new_email("zero_real")
    reg = _register(email)
    uid = reg["user_id"]
    asyncio.new_event_loop().run_until_complete(_seed_balance(uid, 750))

    r = _post_enforce(token, {"dry_run": False, "reason": "enforce_zero_policy"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["dry_run"] is False
    assert data["affected"] >= 1

    balance_after = asyncio.new_event_loop().run_until_complete(_get_balance(uid))
    assert balance_after == 0

    events = asyncio.new_event_loop().run_until_complete(_count_admin_adjust_events(uid))
    assert events >= 1, "real run must emit an admin_adjust credit_events row"


def test_active_subscribers_are_skipped():
    """Active subscribers (plan != free + status=active) must NEVER be zeroed."""
    token = _admin_token()
    email = _new_email("zero_sub")
    reg = _register(email)
    uid = reg["user_id"]
    asyncio.new_event_loop().run_until_complete(
        _seed_balance(uid, 1200, plan_id="pro", plan_status="active")
    )

    r = _post_enforce(token, {"dry_run": False})
    assert r.status_code == 200, r.text

    # Balance untouched.
    balance_after = asyncio.new_event_loop().run_until_complete(_get_balance(uid))
    assert balance_after == 1200, "subscriber balance must not be touched"

    # No audit row for this user.
    events = asyncio.new_event_loop().run_until_complete(_count_admin_adjust_events(uid))
    assert events == 0


def test_admin_unlimited_users_are_skipped():
    """The canonical admin-unlimited account must never be zeroed."""
    token = _admin_token()

    async def _seed_admin_balance():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.users.update_one(
            {"email": ADMIN_EMAIL},
            {"$set": {"credits_balance": 99999}},
        )

    async def _read_admin_balance():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        return await db.users.find_one(
            {"email": ADMIN_EMAIL}, {"credits_balance": 1, "user_id": 1}
        )

    asyncio.new_event_loop().run_until_complete(_seed_admin_balance())

    r = _post_enforce(token, {"dry_run": False})
    assert r.status_code == 200, r.text

    admin_doc = asyncio.new_event_loop().run_until_complete(_read_admin_balance())
    assert admin_doc is not None
    assert int(admin_doc.get("credits_balance") or 0) == 99999, (
        "admin-unlimited account must never have balance reset"
    )


def test_idempotent_second_run_is_noop():
    """After a full sweep, a second run finds nothing to do for the same users."""
    token = _admin_token()
    email = _new_email("zero_idem")
    reg = _register(email)
    uid = reg["user_id"]
    asyncio.new_event_loop().run_until_complete(_seed_balance(uid, 200))

    r1 = _post_enforce(token, {"dry_run": False})
    assert r1.status_code == 200
    assert r1.json()["affected"] >= 1

    # Second run: this user's balance is already 0; no new audit row for them.
    events_before = asyncio.new_event_loop().run_until_complete(_count_admin_adjust_events(uid))
    r2 = _post_enforce(token, {"dry_run": False})
    assert r2.status_code == 200
    events_after = asyncio.new_event_loop().run_until_complete(_count_admin_adjust_events(uid))
    assert events_after == events_before, "second run must not double-zero"


def test_response_envelope_shape():
    """Spec-pin the response envelope so dashboards can rely on it."""
    token = _admin_token()
    r = _post_enforce(token, {"dry_run": True})
    assert r.status_code == 200, r.text
    data = r.json()
    for key in ("ok", "dry_run", "scanned", "affected", "total_credits_zeroed", "sample", "skipped"):
        assert key in data, f"missing field {key}"
    assert isinstance(data["sample"], list)
    assert "admins" in data["skipped"]
    assert "subscribers" in data["skipped"]
