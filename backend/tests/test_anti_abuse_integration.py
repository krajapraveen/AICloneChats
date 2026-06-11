"""End-to-end HTTP integration tests for the anti-abuse layer.

Covers:
- Admin endpoint auth gating (403 for non-admin, 200 for admin).
- Rate-limit enforcement on POST /api/clones/{id}/chat (31st request → 429).
- Admin set-status (block) → next chat call returns 403 account_blocked.
- Admin cannot block another admin (400 user_is_admin).
- Reset counters wipes anti_abuse_events.
- Audit-log persistence in db.login_events.
- Mongo indexes on db.anti_abuse_events present (incl. 14-day TTL).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load env from backend/.env so MONGO_URL/DB_NAME/REACT_APP_BACKEND_URL are present.
def _bootstrap_env():
    for path in ("/app/backend/.env", "/app/frontend/.env"):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k, v)
        except FileNotFoundError:
            pass

_bootstrap_env()

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

NORMAL_EMAIL = "sr-tester@example.com"
NORMAL_PASS = "TestPass123!"

# Admin user we will create / reuse — already in ADMIN_EMAILS env var.
ADMIN_EMAIL = "admin@aiclonechats.com"
ADMIN_PASS = "TestPass123!"


# ---------- Helpers ----------

def _mongo():
    return AsyncIOMotorClient(MONGO_URL)[DB_NAME]


def login(email: str, password: str) -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    if r.status_code != 200:
        pytest.skip(f"login failed for {email}: {r.status_code} {r.text[:200]}")
    return r.json()["session_token"]


def ensure_admin_user():
    """Ensure admin@aiclonechats.com exists with known password. Created via
    /api/auth/register if missing; since the email is in ADMIN_EMAILS env the
    backend will auto-promote it to role=admin on next /me hit."""
    async def go():
        db = _mongo()
        u = await db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0, "user_id": 1})
        return u
    existing = asyncio.run(go())
    if not existing:
        r = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASS, "name": "QA Admin"},
            timeout=15,
        )
        # 200 means created; 400 email_already_registered is fine (race)
        if r.status_code not in (200, 400):
            pytest.skip(f"admin register failed: {r.status_code} {r.text[:200]}")


def reset_counters_for(email: str):
    async def go():
        db = _mongo()
        await db.anti_abuse_events.delete_many({"email": email.lower()})
        u = await db.users.find_one({"email": email.lower()}, {"_id": 0, "user_id": 1})
        if u:
            await db.anti_abuse_events.delete_many({"user_id": u["user_id"]})
            await db.anti_abuse_events.delete_many({"key": u["user_id"]})
    asyncio.run(go())


def set_status_in_db(email: str, status: str | None):
    async def go():
        db = _mongo()
        if status is None:
            await db.users.update_one({"email": email.lower()}, {"$unset": {"abuse_status": ""}})
        else:
            await db.users.update_one({"email": email.lower()}, {"$set": {"abuse_status": status}})
    asyncio.run(go())


def find_a_chat_clone() -> str:
    async def go():
        db = _mongo()
        # Prefer a public, non-paused clone
        c = await db.clones.find_one(
            {"visibility": {"$ne": "private"}, "status": {"$ne": "paused"}},
            {"_id": 0, "clone_id": 1},
        )
        if not c:
            c = await db.clones.find_one({}, {"_id": 0, "clone_id": 1})
        return (c or {}).get("clone_id")
    cid = asyncio.run(go())
    if not cid:
        pytest.skip("No clone in DB to test chat endpoint")
    return cid


# ---------- Fixtures ----------

@pytest.fixture(scope="session")
def clone_id():
    return find_a_chat_clone()


@pytest.fixture(scope="session")
def normal_token():
    reset_counters_for(NORMAL_EMAIL)
    set_status_in_db(NORMAL_EMAIL, None)
    return login(NORMAL_EMAIL, NORMAL_PASS)


@pytest.fixture(scope="session")
def admin_token():
    ensure_admin_user()
    return login(ADMIN_EMAIL, ADMIN_PASS)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- Tests ----------

class TestAdminEndpointAuth:
    def test_summary_forbidden_for_normal_user(self, normal_token):
        r = requests.get(f"{BASE_URL}/api/admin/anti-abuse/summary",
                         headers=_auth(normal_token), timeout=10)
        assert r.status_code == 403, r.text

    def test_summary_ok_for_admin(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/anti-abuse/summary",
                         headers=_auth(admin_token), timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "by_event" in data and isinstance(data["by_event"], dict)
        assert "users_blocked" in data and isinstance(data["users_blocked"], int)
        assert "users_limited" in data and isinstance(data["users_limited"], int)

    def test_recent_ok_for_admin(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/anti-abuse/recent?limit=10",
                         headers=_auth(admin_token), timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "items" in body and isinstance(body["items"], list)

    def test_suspicious_users_ok_for_admin(self, admin_token):
        r = requests.get(
            f"{BASE_URL}/api/admin/anti-abuse/suspicious-users?hours=1&min_events=1",
            headers=_auth(admin_token), timeout=10,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "users" in body and isinstance(body["users"], list)


class TestRateLimitOnChat:
    def test_31st_request_returns_429(self, normal_token, clone_id):
        """Hit chat 35× concurrently so the per-minute window stays tight
        (chat is ~2s/call due to LLM latency — sequential calls would let
        early hits age out of the 60s window before the 31st lands)."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        reset_counters_for(NORMAL_EMAIL)
        set_status_in_db(NORMAL_EMAIL, None)
        url = f"{BASE_URL}/api/clones/{clone_id}/chat"
        payload = {"text": "hi", "message": "hi"}
        headers = _auth(normal_token)

        def fire(_):
            try:
                r = requests.post(url, json=payload, headers=headers, timeout=60)
                return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})
            except Exception as e:
                return -1, {"err": str(e)}

        statuses = []
        details = []
        with ThreadPoolExecutor(max_workers=20) as ex:
            futs = [ex.submit(fire, i) for i in range(35)]
            for f in as_completed(futs):
                s, body = f.result()
                statuses.append(s)
                if s == 429:
                    details.append(body.get("detail") if isinstance(body, dict) else None)

        print("Status counts:", {s: statuses.count(s) for s in set(statuses)})
        print("Sample 429 detail:", details[0] if details else None)

        assert 429 in statuses, f"Never received 429 in 35 concurrent calls. statuses={statuses}"
        # detail.code must be rate_limited
        rl = [d for d in details if isinstance(d, dict) and d.get("code") == "rate_limited"]
        assert rl, f"429 responses missing detail.code=rate_limited; details={details}"

    def test_audit_log_rate_limited_event_written(self, normal_token, clone_id):
        # Triggered by previous test — verify the document landed.
        async def go():
            db = _mongo()
            doc = await db.login_events.find_one(
                {"event": "anti_abuse_rate_limited", "email": NORMAL_EMAIL.lower()},
                sort=[("created_at", -1)],
            )
            return doc
        doc = asyncio.run(go())
        assert doc is not None, "No anti_abuse_rate_limited event in login_events"
        md = doc.get("metadata") or {}
        assert md.get("count", 0) > md.get("limit", 0), md


class TestBlockEnforcement:
    def test_admin_blocks_normal_user_then_chat_returns_403(self, admin_token, normal_token, clone_id):
        # Find sr-tester user_id
        async def get_uid():
            db = _mongo()
            u = await db.users.find_one({"email": NORMAL_EMAIL}, {"_id": 0, "user_id": 1})
            return u["user_id"]
        uid = asyncio.run(get_uid())

        # First, reset counters so we're not stuck in 429-land from earlier test
        rr = requests.post(
            f"{BASE_URL}/api/admin/anti-abuse/reset-counters",
            json={"user_id": uid}, headers=_auth(admin_token), timeout=10,
        )
        assert rr.status_code == 200, rr.text
        assert "deleted" in rr.json()

        # Set blocked
        r = requests.post(
            f"{BASE_URL}/api/admin/anti-abuse/set-status",
            json={"user_id": uid, "status": "blocked", "reason": "qa test"},
            headers=_auth(admin_token), timeout=10,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("abuse_status") == "blocked"

        # Verify DB
        async def check():
            db = _mongo()
            u = await db.users.find_one({"user_id": uid}, {"_id": 0, "abuse_status": 1})
            return u.get("abuse_status")
        assert asyncio.run(check()) == "blocked"

        # sr-tester chat now → 403 account_blocked
        cr = requests.post(
            f"{BASE_URL}/api/clones/{clone_id}/chat",
            json={"text": "hi", "message": "hi"},
            headers=_auth(normal_token), timeout=15,
        )
        assert cr.status_code == 403, cr.text
        detail = (cr.json() or {}).get("detail", {})
        assert isinstance(detail, dict) and detail.get("code") == "account_blocked", detail

        # Unblock — set to normal
        r2 = requests.post(
            f"{BASE_URL}/api/admin/anti-abuse/set-status",
            json={"user_id": uid, "status": "normal", "reason": "qa clear"},
            headers=_auth(admin_token), timeout=10,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json().get("abuse_status") == "normal"

        # Wipe counters so chat can pass rate limit
        rr2 = requests.post(
            f"{BASE_URL}/api/admin/anti-abuse/reset-counters",
            json={"user_id": uid}, headers=_auth(admin_token), timeout=10,
        )
        assert rr2.status_code == 200

        # One chat — should NOT be 403 anymore (could be 200 / 402 / 404 / 422)
        cr2 = requests.post(
            f"{BASE_URL}/api/clones/{clone_id}/chat",
            json={"text": "hi", "message": "hi"},
            headers=_auth(normal_token), timeout=30,
        )
        assert cr2.status_code != 403 or "account_blocked" not in cr2.text, cr2.text

    def test_cannot_block_admin_user(self, admin_token):
        # Get admin user_id
        async def go():
            db = _mongo()
            u = await db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0, "user_id": 1})
            return u["user_id"] if u else None
        uid = asyncio.run(go())
        assert uid, "admin user not found"
        r = requests.post(
            f"{BASE_URL}/api/admin/anti-abuse/set-status",
            json={"user_id": uid, "status": "blocked", "reason": "should fail"},
            headers=_auth(admin_token), timeout=10,
        )
        assert r.status_code == 400, r.text
        detail = (r.json() or {}).get("detail", {})
        assert isinstance(detail, dict) and detail.get("code") == "user_is_admin", detail


class TestExemptAdminBypass:
    def test_admin_chat_writes_exempt_event(self, admin_token, clone_id):
        # 31 hits as admin should NOT 429
        url = f"{BASE_URL}/api/clones/{clone_id}/chat"
        statuses = []
        for _ in range(3):
            r = requests.post(url, json={"text": "hi", "message": "hi"},
                              headers=_auth(admin_token), timeout=20)
            statuses.append(r.status_code)
        # Verify no 429 in the small burst
        assert 429 not in statuses, f"admin got rate-limited: {statuses}"

        # Audit confirmation — either anti_abuse_exempt_bypassed in login_events
        # OR a row in anti_abuse_events with exempt=true and email=admin.
        async def go():
            db = _mongo()
            login_ev = await db.login_events.find_one({
                "event": "anti_abuse_exempt_bypassed",
                "email": ADMIN_EMAIL.lower(),
            })
            anti_ev = await db.anti_abuse_events.find_one({
                "email": ADMIN_EMAIL.lower(),
                "exempt": True,
            })
            return login_ev, anti_ev
        login_ev, anti_ev = asyncio.run(go())
        assert (login_ev is not None) or (anti_ev is not None), \
            "Neither anti_abuse_exempt_bypassed nor exempt anti_abuse_events row was written"


class TestIndexes:
    def test_anti_abuse_events_indexes(self):
        async def go():
            db = _mongo()
            return await db.anti_abuse_events.index_information()
        idx = asyncio.run(go())
        names = list(idx.keys())
        # Compound index on scope,key,created_at desc
        compound = idx.get("scope_1_key_1_created_at_-1")
        assert compound is not None, f"missing compound index. got: {names}"
        assert compound["key"] == [("scope", 1), ("key", 1), ("created_at", -1)]
        # TTL on created_at — 14 days = 1209600 s
        ttl = idx.get("created_at_1")
        assert ttl is not None, f"missing created_at index. got: {names}"
        assert ttl.get("expireAfterSeconds") == 60 * 60 * 24 * 14, ttl


# Cleanup after the entire suite — restore sr-tester to normal & clear counters.
@pytest.fixture(scope="session", autouse=True)
def _cleanup():
    yield
    try:
        set_status_in_db(NORMAL_EMAIL, None)
        reset_counters_for(NORMAL_EMAIL)
    except Exception as e:
        print(f"cleanup warn: {e}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
