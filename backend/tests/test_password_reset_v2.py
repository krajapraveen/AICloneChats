"""Password reset hardening tests — iteration 20.

Focus on the THREE deltas:
  (a) new special-char + whitespace + max-length strength rules,
  (b) confirmation email + audit metadata (confirmation_email_sent),
  (c) end-to-end token + sessions invalidation + new-pw login.

We avoid pytest-asyncio entanglement by using asyncio.run() per DB hop with a
fresh Motor client each time. Slower but bullet-proof inside this test runner.
"""
import os
import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

TEST_EMAIL = "sr-tester@example.com"
ORIG_PW = "TestPass123!"


def _load_env():
    env = {}
    with open("/app/backend/.env") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"')
    return env


_E = _load_env()
MONGO_URL = _E["MONGO_URL"]
DB_NAME = _E["DB_NAME"]


def _run(coro):
    """Run an async coroutine using a fresh loop + client every call to dodge
    'Event loop is closed' issues from sharing Motor instances across tests."""
    return asyncio.new_event_loop().run_until_complete(coro)


async def _with_db(fn):
    client = AsyncIOMotorClient(MONGO_URL)
    try:
        return await fn(client[DB_NAME])
    finally:
        client.close()


def db_run(async_fn):
    return _run(_with_db(async_fn))


async def _mint_token(db, email: str, ttl_min: int = 30):
    user = await db.users.find_one({"email": email}, {"_id": 0, "user_id": 1})
    assert user, f"user {email} missing"
    raw = secrets.token_urlsafe(32)
    h = hashlib.sha256(raw.encode()).hexdigest()
    await db.password_reset_tokens.update_many(
        {"user_id": user["user_id"], "consumed": False},
        {"$set": {"consumed": True, "consumed_reason": "test_superseded"}},
    )
    await db.password_reset_tokens.insert_one({
        "token_id": uuid.uuid4().hex,
        "user_id": user["user_id"],
        "email": email,
        "token_hash": h,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=ttl_min)).isoformat(),
        "consumed": False,
        "consumed_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ip_address_hash": "test",
        "request_id": "req_test_" + uuid.uuid4().hex[:10],
    })
    return raw, user["user_id"]


def mint_token(email=TEST_EMAIL, ttl_min=30):
    async def go(db):
        return await _mint_token(db, email, ttl_min)
    return db_run(go)


@pytest.fixture(scope="session", autouse=True)
def _clear_rate_limits():
    async def go(db):
        await db.auth_rate_limits.delete_many({})
    db_run(go)
    yield


@pytest.fixture
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------- forgot-password ----------
class TestForgotPassword:
    def test_valid_existing_email_neutral_200(self, session):
        r = session.post(f"{API}/auth/forgot-password", json={"email": TEST_EMAIL})
        assert r.status_code in (200, 429), r.text  # may already be rate limited from earlier runs
        if r.status_code == 200:
            body = r.json()
            assert body["code"] == "neutral_acknowledgement"
            assert "request_id" in body

    def test_unknown_email_neutral_200(self, session):
        unique_unknown = f"nobody-{uuid.uuid4().hex[:6]}@example.invalid"
        r = session.post(f"{API}/auth/forgot-password", json={"email": unique_unknown})
        assert r.status_code == 200, r.text
        assert r.json()["code"] == "neutral_acknowledgement"

    def test_malformed_email_returns_400(self, session):
        r = session.post(f"{API}/auth/forgot-password", json={"email": "not-an-email"})
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "invalid_email"

    def test_rate_limit_429(self, session):
        email = f"rl-{uuid.uuid4().hex[:8]}@example.com"
        codes = [session.post(f"{API}/auth/forgot-password", json={"email": email}).status_code for _ in range(7)]
        assert 429 in codes, f"expected 429 in {codes}"


# ---------- validate-token ----------
class TestValidateToken:
    def test_short_token_invalid(self, session):
        r = session.get(f"{API}/auth/reset-password/validate?token=")
        assert r.status_code == 200
        assert r.json()["valid"] is False

    def test_bogus_long_token_invalid(self, session):
        r = session.get(f"{API}/auth/reset-password/validate?token={'x' * 100}")
        assert r.status_code == 200
        assert r.json() == {"valid": False, "code": "token_invalid", **{k: v for k, v in r.json().items() if k == "request_id"}}

    def test_real_valid_token_passes(self, session):
        raw, _ = mint_token()
        r = session.get(f"{API}/auth/reset-password/validate?token={raw}")
        assert r.status_code == 200, r.text
        assert r.json()["valid"] is True


# ---------- reset-password strength ----------
class TestStrengthRules:
    def test_no_special_char_rejected(self, session):
        raw, _ = mint_token()
        r = session.post(f"{API}/auth/reset-password", json={
            "token": raw, "new_password": "Aaaaaaa1", "confirm_password": "Aaaaaaa1",
        })
        assert r.status_code == 400, r.text
        d = r.json()["detail"]
        assert d["code"] == "weak_password"
        assert "special character" in d["message"].lower()

    def test_space_in_password_rejected(self, session):
        raw, _ = mint_token()
        r = session.post(f"{API}/auth/reset-password", json={
            "token": raw, "new_password": "Aaaaaa1!a b", "confirm_password": "Aaaaaa1!a b",
        })
        assert r.status_code == 400, r.text
        d = r.json()["detail"]
        assert d["code"] == "weak_password"
        assert "space" in d["message"].lower()

    def test_too_long_rejected(self, session):
        raw, _ = mint_token()
        big = "Aa1!" + ("x" * 220)  # >200 chars
        r = session.post(f"{API}/auth/reset-password", json={
            "token": raw, "new_password": big, "confirm_password": big,
        })
        # backend Pydantic max_length=200 returns 422; or the strength check returns 400 weak_password "too long"
        assert r.status_code in (400, 422)

    def test_password_mismatch_returns_400(self, session):
        raw, _ = mint_token()
        r = session.post(f"{API}/auth/reset-password", json={
            "token": raw, "new_password": "GoodPass1!", "confirm_password": "Different1!",
        })
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "password_mismatch"


# ---------- end-to-end + audit ----------
class TestE2EFlow:
    def test_full_reset_flow_and_audit(self, session):
        # Pre: ensure at least one user_session exists so we can verify wipe
        async def setup(db):
            user = await db.users.find_one({"email": TEST_EMAIL}, {"_id": 0, "user_id": 1})
            await db.user_sessions.insert_one({
                "session_id": "test_sess_" + uuid.uuid4().hex[:8],
                "user_id": user["user_id"], "token": "fake",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            return user["user_id"]
        uid = db_run(setup)

        raw, _ = mint_token()
        new_pw = f"NewStrong{uuid.uuid4().hex[:4]}!1Aa"
        r = session.post(f"{API}/auth/reset-password", json={
            "token": raw, "new_password": new_pw, "confirm_password": new_pw,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == "password_reset_completed"
        rid = body["request_id"]

        # token consumed
        r2 = session.post(f"{API}/auth/reset-password", json={
            "token": raw, "new_password": new_pw, "confirm_password": new_pw,
        })
        assert r2.status_code == 400
        assert r2.json()["detail"]["code"] == "token_invalid"

        # sessions wiped
        async def check_sessions(db):
            return await db.user_sessions.count_documents({"user_id": uid})
        assert db_run(check_sessions) == 0

        # new-pw login works, old-pw login fails
        login_new = session.post(f"{API}/auth/login", json={"email": TEST_EMAIL, "password": new_pw})
        assert login_new.status_code == 200, login_new.text
        login_old = session.post(f"{API}/auth/login", json={"email": TEST_EMAIL, "password": ORIG_PW})
        assert login_old.status_code in (400, 401), f"old pw must fail, got {login_old.status_code}"

        # audit log has confirmation_email_sent & sessions_invalidated
        async def fetch_audit(db):
            return await db.login_events.find_one(
                {"event_type": "password_reset_completed", "request_id": rid},
                {"_id": 0},
            )
        evt = db_run(fetch_audit)
        assert evt, "audit doc missing"
        meta = evt.get("metadata", {})
        assert "confirmation_email_sent" in meta, f"meta keys: {list(meta)}"
        assert "confirmation_email_error" in meta
        assert meta.get("sessions_invalidated", 0) >= 1

        # Restore password so future test runs work
        raw2, _ = mint_token()
        restore = session.post(f"{API}/auth/reset-password", json={
            "token": raw2, "new_password": ORIG_PW, "confirm_password": ORIG_PW,
        })
        assert restore.status_code == 200, restore.text

    def test_expired_token_returns_token_expired(self, session):
        raw, _ = mint_token()
        h = hashlib.sha256(raw.encode()).hexdigest()
        async def expire(db):
            await db.password_reset_tokens.update_one(
                {"token_hash": h},
                {"$set": {"expires_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}},
            )
        db_run(expire)
        r = session.post(f"{API}/auth/reset-password", json={
            "token": raw, "new_password": "GoodEnough1!", "confirm_password": "GoodEnough1!",
        })
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "token_expired"

    def test_failed_weak_writes_audit(self, session):
        raw, _ = mint_token()
        r = session.post(f"{API}/auth/reset-password", json={
            "token": raw, "new_password": "weakpass", "confirm_password": "weakpass",
        })
        assert r.status_code == 400
        rid = r.json()["detail"]["request_id"]
        async def fetch(db):
            return await db.login_events.find_one(
                {"event_type": "password_reset_failed", "request_id": rid},
                {"_id": 0},
            )
        evt = db_run(fetch)
        assert evt, "weak_password audit doc missing"
        assert evt["metadata"].get("reason") == "weak_password"
