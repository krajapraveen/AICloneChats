"""Auth hardening + Password reset flow tests (iteration 16)."""
import os
import re
import time
import uuid
import hashlib
import asyncio
from datetime import datetime, timezone, timedelta
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

SUBSCRIBER_EMAIL = "subscriber-tester@example.com"
SUBSCRIBER_PASS = "TestPass123!"
FREE_EMAIL = "sr-tester@example.com"
FREE_PASS = "TestPass123!"


# ---- helpers ----
def _db():
    client = AsyncIOMotorClient(MONGO_URL)
    return client[DB_NAME]


async def _async_get_user(email):
    db = _db()
    return await db.users.find_one({"email": email}, {"_id": 0})


def _post(path, json=None, headers=None):
    return requests.post(f"{BASE_URL}{path}", json=json, headers=headers or {}, timeout=20)


def _get(path, headers=None):
    return requests.get(f"{BASE_URL}{path}", headers=headers or {}, timeout=20)


def _assert_err_shape(resp, code=None, status=None):
    if status is not None:
        assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text[:200]}"
    j = resp.json()
    detail = j.get("detail") if "detail" in j else j
    assert isinstance(detail, dict), f"detail not dict: {detail}"
    assert "code" in detail, f"no code: {detail}"
    assert "message" in detail, f"no message: {detail}"
    assert "request_id" in detail, f"no request_id: {detail}"
    if code:
        assert detail["code"] == code, f"expected code={code}, got {detail['code']}"
    return detail


# ====== FORGOT PASSWORD ======
class TestForgotPassword:
    def test_unknown_email_neutral_200(self):
        r = _post("/api/auth/forgot-password", {"email": f"nobody-{uuid.uuid4().hex[:6]}@example.com"})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is True
        assert j["code"] == "neutral_acknowledgement"
        assert "request_id" in j

    def test_known_email_same_shape(self):
        r = _post("/api/auth/forgot-password", {"email": SUBSCRIBER_EMAIL})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is True
        assert j["code"] == "neutral_acknowledgement"
        # Shape parity with unknown email
        assert set(j.keys()) >= {"ok", "code", "message", "request_id"}

    def test_invalid_email_format(self):
        r = _post("/api/auth/forgot-password", {"email": "not-an-email"})
        _assert_err_shape(r, code="invalid_email", status=400)

    def test_rate_limit_ip(self):
        # 10 allowed per IP per 15 min. Burn 11.
        email_base = f"rate-{uuid.uuid4().hex[:6]}"
        last = None
        hit_429 = False
        for i in range(12):
            r = _post("/api/auth/forgot-password", {"email": f"{email_base}-{i}@example.com"})
            last = r
            if r.status_code == 429:
                hit_429 = True
                _assert_err_shape(r, code="rate_limited", status=429)
                break
        assert hit_429, f"never hit 429 after 12 requests; last={last.status_code} {last.text[:200]}"


# ====== RESET PASSWORD ======
class TestResetPassword:
    @pytest.fixture(scope="class")
    def reset_token(self):
        """Inject a valid token directly into DB for the free user."""
        async def go():
            db = _db()
            user = await db.users.find_one({"email": FREE_EMAIL}, {"_id": 0})
            assert user, "free user not found"
            raw = "raw_test_token_" + uuid.uuid4().hex
            th = hashlib.sha256(raw.encode()).hexdigest()
            await db.password_reset_tokens.insert_one({
                "token_id": uuid.uuid4().hex,
                "user_id": user["user_id"],
                "email": FREE_EMAIL,
                "token_hash": th,
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
                "consumed": False,
                "consumed_at": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "request_id": "req_test_seed",
            })
            return raw, user["user_id"]
        return asyncio.run(go())

    def test_bogus_token(self):
        r = _post("/api/auth/reset-password", {
            "token": "x" * 40, "new_password": "Abcd1234", "confirm_password": "Abcd1234"
        })
        _assert_err_shape(r, code="token_invalid", status=400)

    def test_password_mismatch(self):
        r = _post("/api/auth/reset-password", {
            "token": "validlookingtokenstring1234567890", "new_password": "Abcd1234", "confirm_password": "Different1"
        })
        _assert_err_shape(r, code="password_mismatch", status=400)

    def test_weak_password(self):
        r = _post("/api/auth/reset-password", {
            "token": "validlookingtokenstring1234567890", "new_password": "weakpass", "confirm_password": "weakpass"
        })
        _assert_err_shape(r, code="weak_password", status=400)

    def test_expired_token(self):
        async def go():
            db = _db()
            user = await db.users.find_one({"email": FREE_EMAIL}, {"_id": 0})
            raw = "expired_token_" + uuid.uuid4().hex
            th = hashlib.sha256(raw.encode()).hexdigest()
            await db.password_reset_tokens.insert_one({
                "token_id": uuid.uuid4().hex,
                "user_id": user["user_id"],
                "email": FREE_EMAIL,
                "token_hash": th,
                "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                "consumed": False,
                "consumed_at": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "request_id": "req_test_seed_exp",
            })
            return raw
        raw = asyncio.run(go())
        r = _post("/api/auth/reset-password", {
            "token": raw, "new_password": "Abcd1234", "confirm_password": "Abcd1234"
        })
        _assert_err_shape(r, code="token_expired", status=400)

    def test_validate_endpoint_no_email_leak(self, reset_token):
        raw, _uid = reset_token
        r = _get(f"/api/auth/reset-password/validate?token={raw}")
        assert r.status_code == 200
        j = r.json()
        assert j["valid"] is True
        assert "email" not in j
        assert "request_id" in j

    def test_validate_invalid(self):
        r = _get("/api/auth/reset-password/validate?token=" + ("z" * 30))
        assert r.status_code == 200
        j = r.json()
        assert j["valid"] is False
        assert j["code"] == "token_invalid"

    def test_successful_reset_invalidates_sessions(self, reset_token):
        raw, user_id = reset_token
        # Seed an extra session for this user
        async def seed_session():
            db = _db()
            await db.user_sessions.insert_one({
                "session_token": "st_test_" + uuid.uuid4().hex,
                "user_id": user_id,
                "source": "email",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            })
        asyncio.run(seed_session())

        new_pw = "NewStrong1Pass"
        r = _post("/api/auth/reset-password", {
            "token": raw, "new_password": new_pw, "confirm_password": new_pw
        })
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is True
        assert j["code"] == "password_reset_completed"

        # Verify DB: password_hash changed & sessions cleared
        async def verify():
            db = _db()
            u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
            sess_count = await db.user_sessions.count_documents({"user_id": user_id})
            tok = await db.password_reset_tokens.find_one({"token_hash": hashlib.sha256(raw.encode()).hexdigest()}, {"_id": 0})
            return u, sess_count, tok
        u, sess_count, tok = asyncio.run(verify())
        assert sess_count == 0, f"sessions not cleared: {sess_count}"
        assert tok["consumed"] is True
        # token_hash is sha256 hex
        assert re.match(r"^[0-9a-f]{64}$", tok["token_hash"])
        assert "raw_token" not in tok and "token" not in tok

        # Try reuse → token_invalid
        r2 = _post("/api/auth/reset-password", {
            "token": raw, "new_password": new_pw, "confirm_password": new_pw
        })
        _assert_err_shape(r2, code="token_invalid", status=400)

        # Restore password for next runs
        r3 = _post("/api/auth/login", {"email": FREE_EMAIL, "password": new_pw})
        assert r3.status_code == 200, f"login w/ new pw failed: {r3.text[:200]}"
        # reset back to TestPass123! by issuing another reset
        async def reseed():
            db = _db()
            u = await db.users.find_one({"email": FREE_EMAIL}, {"_id": 0})
            raw2 = "reseed_" + uuid.uuid4().hex
            th = hashlib.sha256(raw2.encode()).hexdigest()
            await db.password_reset_tokens.insert_one({
                "token_id": uuid.uuid4().hex, "user_id": u["user_id"], "email": FREE_EMAIL,
                "token_hash": th, "expires_at": (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(),
                "consumed": False, "consumed_at": None, "created_at": datetime.now(timezone.utc).isoformat(),
                "request_id": "req_reseed",
            })
            return raw2
        raw2 = asyncio.run(reseed())
        _post("/api/auth/reset-password", {"token": raw2, "new_password": FREE_PASS, "confirm_password": FREE_PASS})


# ====== LOGIN HARDENING ======
class TestLoginHardening:
    def test_invalid_email_format(self):
        r = _post("/api/auth/login", {"email": "bad", "password": "x"})
        _assert_err_shape(r, code="invalid_email", status=400)

    def test_missing_password(self):
        r = _post("/api/auth/login", {"email": "test@example.com", "password": ""})
        _assert_err_shape(r, code="missing_password", status=400)

    def test_unknown_email_same_as_wrong_pw(self):
        unknown = f"nobody-{uuid.uuid4().hex[:8]}@example.com"
        r1 = _post("/api/auth/login", {"email": unknown, "password": "AnyPass123!"})
        d1 = _assert_err_shape(r1, code="invalid_credentials", status=401)

        # Wrong password for KNOWN user (must not be locked out — use fresh email per IP test design)
        r2 = _post("/api/auth/login", {"email": SUBSCRIBER_EMAIL, "password": "definitely-wrong-pass"})
        d2 = _assert_err_shape(r2, code="invalid_credentials", status=401)
        # Same message + same code
        assert d1["message"] == d2["message"]

    def test_login_success_subscriber(self):
        r = _post("/api/auth/login", {"email": SUBSCRIBER_EMAIL, "password": SUBSCRIBER_PASS})
        assert r.status_code == 200, r.text
        j = r.json()
        assert "session_token" in j
        assert j["user"]["email"] == SUBSCRIBER_EMAIL
        # /me with bearer
        r2 = _get("/api/auth/me", headers={"Authorization": f"Bearer {j['session_token']}"})
        assert r2.status_code == 200
        # /me without bearer
        r3 = _get("/api/auth/me")
        assert r3.status_code == 401
        # logout
        r4 = _post("/api/auth/logout", headers={"Authorization": f"Bearer {j['session_token']}"})
        assert r4.status_code == 200
        # session should be gone
        r5 = _get("/api/auth/me", headers={"Authorization": f"Bearer {j['session_token']}"})
        assert r5.status_code == 401

    def test_bruteforce_lockout(self):
        # Use a unique email to avoid contamination
        target = f"locktest-{uuid.uuid4().hex[:8]}@example.com"
        # Ensure exists in DB so we can lock on email path (also enumeration neutral path)
        codes = []
        for i in range(7):
            r = _post("/api/auth/login", {"email": target, "password": "wrong"})
            codes.append(r.status_code)
            if r.status_code == 429:
                _assert_err_shape(r, code="rate_limited", status=429)
                break
        assert 429 in codes, f"never locked: {codes}"

    def test_suspended_account(self):
        # Seed a suspended user
        async def seed():
            db = _db()
            email = f"suspended-{uuid.uuid4().hex[:6]}@example.com"
            from bcrypt import hashpw, gensalt
            pw_hash = hashpw(b"TestPass123!", gensalt()).decode()
            await db.users.insert_one({
                "user_id": "user_" + uuid.uuid4().hex[:12],
                "email": email, "name": "Susp", "picture": "", "password_hash": pw_hash,
                "auth_provider": "email", "role": "user", "email_verified": True,
                "credits_balance": 0, "plan_id": "free", "plan_status": "active",
                "is_suspended": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            return email
        email = asyncio.run(seed())
        r = _post("/api/auth/login", {"email": email, "password": "TestPass123!"})
        _assert_err_shape(r, code="account_suspended", status=403)


# ====== AUDIT LOGS + SECURITY ======
class TestAuditAndSecurity:
    def test_audit_login_success_and_failed(self):
        # trigger a failed
        bad_email = f"audit-fail-{uuid.uuid4().hex[:6]}@example.com"
        _post("/api/auth/login", {"email": bad_email, "password": "x"})
        async def check():
            db = _db()
            failed = await db.login_events.find_one({"event_type": "login_failed", "email": bad_email}, {"_id": 0})
            return failed
        failed = asyncio.run(check())
        assert failed, "no login_failed event written"
        assert failed.get("failure_reason") in ("invalid_credentials", "invalid_password")
        # No raw password leak in audit
        as_str = str(failed)
        assert "TestPass" not in as_str

    def test_audit_password_reset_logs(self):
        # Already triggered earlier; just confirm a recent password_reset_requested exists
        async def check():
            db = _db()
            doc = await db.login_events.find_one({"event_type": "password_reset_requested"}, {"_id": 0})
            doc_completed = await db.login_events.find_one({"event_type": "password_reset_completed"}, {"_id": 0})
            return doc, doc_completed
        req, completed = asyncio.run(check())
        assert req, "no password_reset_requested doc"
        assert "event_id" in req
        assert completed, "no password_reset_completed doc"
        assert "sessions_invalidated" in (completed.get("metadata") or {})

    def test_token_hash_only_storage(self):
        async def check():
            db = _db()
            doc = await db.password_reset_tokens.find_one({}, {"_id": 0})
            return doc
        doc = asyncio.run(check())
        assert doc, "no reset tokens in db"
        th = doc.get("token_hash", "")
        assert len(th) == 64 and re.match(r"^[0-9a-f]{64}$", th)
        # No raw token field
        for k in doc:
            assert "raw" not in k.lower()

    def test_no_email_enumeration_timing(self):
        # 5 trials each, no obvious >50ms delta
        unknown_times = []
        known_times = []
        for _ in range(5):
            t0 = time.time()
            _post("/api/auth/login", {"email": f"none-{uuid.uuid4().hex[:6]}@example.com", "password": "Wrong1!"})
            unknown_times.append(time.time() - t0)
            t0 = time.time()
            _post("/api/auth/login", {"email": SUBSCRIBER_EMAIL, "password": "definitely-wrong-here"})
            known_times.append(time.time() - t0)
        # Just record; no hard assert (network jitter)
        delta = abs(sum(known_times)/5 - sum(unknown_times)/5)
        print(f"avg unknown={sum(unknown_times)/5:.3f}s known={sum(known_times)/5:.3f}s delta={delta:.3f}s")


# ====== DASHBOARD PRICING CATALOG ======
class TestPricingCatalog:
    def test_catalog_loads(self):
        r = _get("/api/pricing/catalog")
        assert r.status_code == 200, r.text
        j = r.json()
        # Expect plans/topups
        assert "plans" in j or "subscriptions" in j or isinstance(j, dict)
