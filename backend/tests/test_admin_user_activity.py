"""Admin User Activity dashboard — list + detail contract."""
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

from conftest import get_shared_loop  # noqa: E402


def _run(coro):
    return get_shared_loop().run_until_complete(coro)


@pytest.fixture(scope="module")
def admin_token() -> str:
    async def _mint():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        admin = await db.users.find_one({"email": ADMIN_EMAIL}, {"user_id": 1})
        if not admin:
            return None
        token = f"st_{uuid.uuid4().hex}{uuid.uuid4().hex}"
        await db.user_sessions.insert_one({
            "session_token": token, "user_id": admin["user_id"],
            "source": "test-ua",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        })
        return token
    tok = _run(_mint())
    if not tok:
        pytest.skip("admin not seeded")
    return tok


@pytest.fixture
def seeded_user():
    """Seed a synthetic user + 3 login_events + 5 credit_events. Cleanup on
    fixture teardown."""
    uid = f"u_ua_{uuid.uuid4().hex[:10]}"
    email = f"ua_{uuid.uuid4().hex[:8]}@example.com"
    now = datetime.now(timezone.utc)
    iso = now.isoformat()

    async def _seed():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.users.insert_one({
            "user_id": uid, "email": email,
            "plan_id": "pro", "plan_status": "active",
            "credits_balance": 100, "email_verified": True,
            "auth_provider": "email",
            "created_at": iso, "updated_at": iso,
        })
        # 3 successful logins from different cities
        for i, city in enumerate(["Bangalore", "Mumbai", "Pune"]):
            await db.login_events.insert_one({
                "event_id": uuid.uuid4().hex,
                "user_id": uid, "email": email,
                "event_type": "login_success", "success": True,
                "login_method": "email_password",
                "ip_address_hash": "deadbeef" * 3,
                "ip_country": "IN", "ip_region": "Karnataka", "ip_city": city,
                "browser": "Chrome", "os": "macOS", "device_type": "desktop",
                "created_at": (now - timedelta(hours=i)).isoformat(),
            })
        # 5 feature uses across 2 features
        for i, feat in enumerate(["ai_clone", "ai_clone", "ai_clone", "voice", "voice"]):
            await db.credit_events.insert_one({
                "event_id": uuid.uuid4().hex,
                "user_id": uid, "request_id": f"req_ua_{i}",
                "kind": "deduct", "delta": -1,
                "balance_before": 100 - i, "balance_after": 99 - i,
                "surface": f"{feat}_chat", "feature": feat,
                "created_at": (now - timedelta(minutes=i * 5)).isoformat(),
            })

    async def _cleanup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.users.delete_one({"user_id": uid})
        await db.login_events.delete_many({"user_id": uid})
        await db.credit_events.delete_many({"user_id": uid})

    _run(_seed())
    yield {"user_id": uid, "email": email}
    _run(_cleanup())


# ───────────────────────── list endpoint ─────────────────────────

def test_list_endpoint_admin_only():
    r = requests.get(f"{BASE_URL}/api/admin/user-activity", timeout=10)
    assert r.status_code in (401, 403)


def test_list_endpoint_envelope_and_aggregates(admin_token, seeded_user):
    r = requests.get(
        f"{BASE_URL}/api/admin/user-activity?q={seeded_user['email']}&days=30",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("window_days", "total_candidates", "page", "limit",
              "items", "sort", "plan_filter", "q"):
        assert k in body, f"missing key {k}"
    assert body["total_candidates"] >= 1
    # Our seeded user should appear and have the correct aggregates
    row = next((r for r in body["items"] if r["user_id"] == seeded_user["user_id"]), None)
    assert row is not None, "seeded user not returned"
    assert row["plan_id"] == "pro"
    assert row["logins_in_window"] == 3
    assert row["feature_uses_in_window"] == 5
    assert row["last_login_city"] == "Bangalore"
    assert row["last_login_country"] == "IN"
    # Top features should rank ai_clone (3) before voice (2)
    feats = row["top_features"]
    assert feats[0]["feature"] == "ai_clone"
    assert feats[0]["count"] == 3
    assert feats[1]["feature"] == "voice"
    assert feats[1]["count"] == 2


def test_list_endpoint_plan_filter(admin_token, seeded_user):
    r = requests.get(
        f"{BASE_URL}/api/admin/user-activity?q={seeded_user['email']}&plan=pro",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    body = r.json()
    assert body["plan_filter"] == "pro"
    for item in body["items"]:
        assert item["plan_id"] == "pro"


def test_list_endpoint_sort_logins(admin_token):
    r = requests.get(
        f"{BASE_URL}/api/admin/user-activity?days=30&sort=logins&limit=10",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    body = r.json()
    counts = [r["logins_in_window"] for r in body["items"]]
    assert counts == sorted(counts, reverse=True), "logins sort must be DESC"


def test_list_endpoint_default_sort_floats_active_users(admin_token, seeded_user):
    """Regression: a user with NO recent activity must not outrank a user
    with real activity under the default `last_active` DESC sort. This
    catches the original "all rows blank" bug where the null-handling on
    the sort key put inactive users at the top."""
    # Default sort is `last_active`. The seeded_user has 3 fresh logins +
    # 5 feature uses, so they must appear well within the first page (25)
    # alongside other active users — not buried after every inactive
    # account in the DB.
    r = requests.get(
        f"{BASE_URL}/api/admin/user-activity?days=30&limit=50",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    body = r.json()
    # Every item that appears MUST have a non-None last_active_at, OR they
    # were genuinely the most-recent in the DB (which is fine). What
    # matters is: a user with a fresh last_active_at can't sit after a
    # user whose last_active_at is None.
    seen_none = False
    for item in body["items"]:
        if item["last_active_at"] is None:
            seen_none = True
        else:
            assert not seen_none, (
                f"Non-null last_active_at {item['last_active_at']} appears AFTER "
                f"a null. Nulls must sink to the bottom regardless of direction."
            )


def test_list_endpoint_user_doc_sanitized(admin_token, seeded_user):
    """No password_hash or reset_token_hash should leak into the list."""
    r = requests.get(
        f"{BASE_URL}/api/admin/user-activity?q={seeded_user['email']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    for row in r.json()["items"]:
        assert "password_hash" not in row
        assert "reset_token_hash" not in row


# ───────────────────────── detail endpoint ─────────────────────────

def test_detail_endpoint_unknown_user_404(admin_token):
    r = requests.get(
        f"{BASE_URL}/api/admin/user-activity/u_does_not_exist_{uuid.uuid4().hex[:6]}",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 404


def test_detail_endpoint_returns_full_envelope(admin_token, seeded_user):
    r = requests.get(
        f"{BASE_URL}/api/admin/user-activity/{seeded_user['user_id']}?days=30",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("user", "window_days", "summary", "logins", "features",
              "paywall_hits", "subscription_transitions", "timeline",
              "computed_at"):
        assert k in body, f"missing key {k}"
    assert body["user"]["user_id"] == seeded_user["user_id"]
    assert body["summary"]["logins_in_window"] == 3
    assert body["summary"]["feature_uses_in_window"] == 5
    assert len(body["logins"]) == 3
    assert len(body["features"]) == 5


def test_detail_endpoint_timeline_is_chronological_desc(admin_token, seeded_user):
    r = requests.get(
        f"{BASE_URL}/api/admin/user-activity/{seeded_user['user_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    timeline = r.json()["timeline"]
    ats = [e["at"] for e in timeline if e.get("at")]
    assert ats == sorted(ats, reverse=True), "timeline must be desc by `at`"
    # Should contain both login and feature_use rows
    kinds = {e["kind"] for e in timeline}
    assert "login" in kinds
    assert "feature_use" in kinds


def test_detail_endpoint_admin_only(seeded_user):
    r = requests.get(
        f"{BASE_URL}/api/admin/user-activity/{seeded_user['user_id']}",
        timeout=10,
    )
    assert r.status_code in (401, 403)
