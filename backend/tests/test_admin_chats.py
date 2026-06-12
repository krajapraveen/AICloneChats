"""Admin chat monitoring + redaction tests."""
import os
import uuid
import asyncio
from datetime import datetime, timezone, timedelta

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")
REAL_ADMIN_EMAIL = "krajapraveen@gmail.com"


def _mint_admin_token():
    """Mint a session token for the canonical admin user directly in Mongo.
    sr-tester is the canonical FREE non-admin (per test_credentials.md), so
    we can no longer log in as it for admin tests. Instead we look up the
    admin user_id and seed a `user_sessions` row pointing at it.
    """
    async def _go():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        admin = await db.users.find_one({"email": REAL_ADMIN_EMAIL}, {"user_id": 1})
        if not admin:
            return None
        token = f"st_{uuid.uuid4().hex}{uuid.uuid4().hex}"
        await db.user_sessions.insert_one({
            "session_token": token, "user_id": admin["user_id"],
            "source": "test-mint-admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        })
        return token
    return asyncio.new_event_loop().run_until_complete(_go())


def _login_or_register(email, password, name="Tester"):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": password, "full_name": name})
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    return body.get("access_token") or body.get("session_token") or body.get("token")


@pytest.fixture(scope="module")
def admin_token():
    tok = _mint_admin_token()
    if not tok:
        pytest.skip("admin not seeded")
    return tok


@pytest.fixture(scope="module")
def normal_token():
    return _login_or_register(f"chats-norm-{uuid.uuid4().hex[:8]}@example.com", "Norm123!", "Normal")


def test_admin_chats_requires_auth():
    r = requests.get(f"{BASE_URL}/api/admin/chats")
    assert r.status_code in (401, 403)


def test_non_admin_403(normal_token):
    r = requests.get(f"{BASE_URL}/api/admin/chats", headers={"Authorization": f"Bearer {normal_token}"})
    assert r.status_code == 403


def test_admin_can_list_all(admin_token):
    r = requests.get(f"{BASE_URL}/api/admin/chats?days=30&limit=50", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "chats" in body and "count" in body
    # We have prior data: anonymous, debate, smart_reply, possibly clone
    types = {c["chat_type"] for c in body["chats"]}
    assert types & {"clone", "anonymous", "debate", "smart_reply"}


def test_filter_by_type(admin_token):
    r = requests.get(f"{BASE_URL}/api/admin/chats?days=30&chat_type=debate&limit=50", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    if body["chats"]:
        assert all(c["chat_type"] == "debate" for c in body["chats"])


def test_filter_safety_flagged(admin_token):
    r = requests.get(f"{BASE_URL}/api/admin/chats?days=30&safety=flagged&limit=50", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    for c in body["chats"]:
        assert c["is_flagged"] or c.get("moderation_status") == "flagged"


def test_get_thread_clone(admin_token):
    r = requests.get(f"{BASE_URL}/api/admin/chats?days=60&chat_type=clone&limit=10", headers={"Authorization": f"Bearer {admin_token}"})
    rows = r.json().get("chats", [])
    if not rows:
        pytest.skip("no clone conversations in window")
    cid = rows[0]["conversation_id"]
    r2 = requests.get(f"{BASE_URL}/api/admin/chats/{cid}?chat_type=clone", headers={"Authorization": f"Bearer {admin_token}"})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["chat_type"] == "clone"
    assert isinstance(body["thread"], list)


def test_redaction_applied(admin_token):
    """End-to-end: post a message with sensitive content, verify admin sees it redacted."""
    # Use anonymous chat path — simplest API surface that routes through admin chats view.
    device = f"redact-{uuid.uuid4().hex[:14]}"
    h = {"X-Device-Id": device}
    requests.post(f"{BASE_URL}/api/anonymous/session", headers=h)
    requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/join", headers=h)
    sensitive = "lonely tonight, contact me at testredact@example.com or +1 (555) 234-7891 anytime"
    r = requests.post(
        f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
        headers=h,
        json={"content": sensitive},
    )
    assert r.status_code in (200, 202), r.text

    # Admin lists chats — find the message and assert redaction
    ah = {"Authorization": f"Bearer {admin_token}"}
    r2 = requests.get(f"{BASE_URL}/api/admin/chats?days=1&chat_type=anonymous&limit=50", headers=ah)
    assert r2.status_code == 200
    chats = r2.json().get("chats", [])
    found = next((c for c in chats if "lonely tonight" in (c.get("last_message_preview") or "")), None)
    assert found is not None, "message not found in admin chats list"
    preview = found["last_message_preview"]
    assert "testredact@example.com" not in preview
    assert "555" not in preview or "[redacted:phone]" in preview
    redactions = found.get("last_message_redactions") or []
    assert "email" in redactions
    assert "phone" in redactions


def test_export_admin_only(admin_token, normal_token):
    r1 = requests.get(f"{BASE_URL}/api/admin/chats/export/all?days=14", headers={"Authorization": f"Bearer {admin_token}"})
    assert r1.status_code == 200
    r2 = requests.get(f"{BASE_URL}/api/admin/chats/export/all?days=14", headers={"Authorization": f"Bearer {normal_token}"})
    assert r2.status_code == 403


def test_flag_then_hide(admin_token):
    """Flag/hide a NEW dedicated anonymous message so we don't disturb other tests."""
    device = f"flag-test-{uuid.uuid4().hex[:14]}"
    h = {"X-Device-Id": device}
    requests.post(f"{BASE_URL}/api/anonymous/session", headers=h)
    requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/join", headers=h)
    posted = requests.post(
        f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
        headers=h,
        json={"content": "flag-and-hide test message do not delete"},
    )
    assert posted.status_code in (200, 202), posted.text

    ah = {"Authorization": f"Bearer {admin_token}"}
    listing = requests.get(f"{BASE_URL}/api/admin/chats?days=1&chat_type=anonymous&limit=50", headers=ah)
    chats = listing.json()["chats"]
    target = next((c for c in chats if "flag-and-hide test" in (c.get("last_message_preview") or "")), None)
    assert target is not None
    cid = target["conversation_id"]
    r1 = requests.patch(f"{BASE_URL}/api/admin/chats/{cid}/flag", json={"chat_type": "anonymous", "reason": "test"}, headers=ah)
    assert r1.status_code == 200
    r2 = requests.patch(f"{BASE_URL}/api/admin/chats/{cid}/hide", json={"chat_type": "anonymous", "hide": True, "reason": "test"}, headers=ah)
    assert r2.status_code == 200
    listing2 = requests.get(f"{BASE_URL}/api/admin/chats?days=1&chat_type=anonymous&limit=50", headers=ah)
    found = next((c for c in listing2.json()["chats"] if c["conversation_id"] == cid), None)
    assert found is not None
    assert found["is_hidden"] is True
