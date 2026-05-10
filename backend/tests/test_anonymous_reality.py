"""Anonymous Reality Phase 1 backend tests."""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "sr-tester@example.com"
ADMIN_PASSWORD = "TestPass123!"


def _new_device():
    return f"test-device-{uuid.uuid4().hex[:16]}"


@pytest.fixture(scope="module")
def device_a():
    return _new_device()


@pytest.fixture(scope="module")
def device_b():
    return _new_device()


@pytest.fixture(scope="module")
def admin_token():
    # Try login; if fails, register
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r.status_code != 200:
        requests.post(f"{BASE_URL}/api/auth/register", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "full_name": "SR Tester"})
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    body = r.json()
    return body.get("access_token") or body.get("session_token") or body.get("token")


# ---------- Sessions ----------
class TestSession:
    def test_create_session_idempotent(self, device_a):
        h = {"X-Device-Id": device_a}
        r1 = requests.post(f"{BASE_URL}/api/anonymous/session", headers=h)
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert "session_id" in d1 and "anonymous_handle" in d1 and "expires_at" in d1
        r2 = requests.post(f"{BASE_URL}/api/anonymous/session", headers=h)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["session_id"] == d1["session_id"]
        assert d2["anonymous_handle"] == d1["anonymous_handle"]

    def test_different_device_different_handle(self, device_a, device_b):
        r1 = requests.post(f"{BASE_URL}/api/anonymous/session", headers={"X-Device-Id": device_a}).json()
        r2 = requests.post(f"{BASE_URL}/api/anonymous/session", headers={"X-Device-Id": device_b}).json()
        assert r1["session_id"] != r2["session_id"]

    def test_me(self, device_a):
        r = requests.get(f"{BASE_URL}/api/anonymous/me", headers={"X-Device-Id": device_a})
        assert r.status_code == 200
        assert "anonymous_handle" in r.json()


# ---------- Rooms ----------
class TestRooms:
    def test_list_rooms(self, device_a):
        r = requests.get(f"{BASE_URL}/api/anonymous/rooms", headers={"X-Device-Id": device_a})
        assert r.status_code == 200
        data = r.json()
        slugs = {x["slug"] for x in data["rooms"]}
        expected = {"loneliness", "family-pressure", "money-reality", "mental-load",
                    "relationships", "startup-struggle", "student-life", "general-reality"}
        assert expected.issubset(slugs), f"missing rooms: {expected - slugs}"
        # last_message_preview should exist for at least one room (seeded)
        assert any(x.get("last_message_preview") for x in data["rooms"])

    def test_get_room(self, device_a):
        r = requests.get(f"{BASE_URL}/api/anonymous/rooms/loneliness", headers={"X-Device-Id": device_a})
        assert r.status_code == 200
        data = r.json()
        assert data["slug"] == "loneliness"
        assert isinstance(data.get("rules"), list) and len(data["rules"]) >= 1

    def test_seeded_messages(self, device_a):
        r = requests.get(f"{BASE_URL}/api/anonymous/rooms/loneliness/messages", headers={"X-Device-Id": device_a})
        assert r.status_code == 200
        msgs = r.json()["messages"]
        seeds = [m for m in msgs if m.get("message_type") == "seed"]
        assert len(seeds) >= 5, f"expected ≥5 seeds, got {len(seeds)}"


# ---------- Moderation: send messages ----------
class TestSendMessages:
    def test_clean_message_allowed(self, device_a):
        r = requests.post(
            f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
            headers={"X-Device-Id": device_a},
            json={"content": "Some quiet days I feel a heaviness I cannot name. Trying to be honest."},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "allowed"
        assert body["message"]["moderation_status"] == "allowed"

    def test_toxic_blocked(self, device_a):
        r = requests.post(
            f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
            headers={"X-Device-Id": device_a},
            json={"content": "shut up you stupid idiot nobody cares"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "blocked", body
        assert body.get("category") in ("toxicity", "harassment", "hate")
        assert body.get("human_reason")
        # Must NOT be a robotic 'blocked'
        assert body["human_reason"].lower() != "blocked"

    def test_doxxing_blocked(self, device_a):
        r = requests.post(
            f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
            headers={"X-Device-Id": device_a},
            json={"content": "Reach me at john.doe@example.com or 9876543210 anytime"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "blocked"
        assert body["category"] == "doxxing"

    def test_self_harm_allowed_with_system(self, device_a):
        r = requests.post(
            f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
            headers={"X-Device-Id": device_a},
            json={"content": "I want to end it all, I cant do this anymore"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "allowed", body
        assert body.get("system_message"), body
        sm = body["system_message"]
        assert sm["message_type"] == "system"
        assert sm["anonymous_handle"] == "Room"

        # Verify both messages appear via GET /messages
        time.sleep(1)
        msgs = requests.get(f"{BASE_URL}/api/anonymous/rooms/loneliness/messages", headers={"X-Device-Id": device_a}).json()["messages"]
        ids = {m["message_id"] for m in msgs}
        assert body["message"]["message_id"] in ids
        assert body["system_message"]["message_id"] in ids


# ---------- Reports ----------
class TestReports:
    def test_self_report_400(self, device_a):
        # send a message
        r = requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/messages", headers={"X-Device-Id": device_a},
                          json={"content": "honest gentle thought today, just wanted to share"})
        assert r.status_code == 200
        msg_id = r.json()["message"]["message_id"]
        rep = requests.post(f"{BASE_URL}/api/anonymous/messages/{msg_id}/report",
                            headers={"X-Device-Id": device_a},
                            json={"reason": "test"})
        assert rep.status_code == 400

    def test_other_can_report(self, device_a, device_b):
        r = requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/messages", headers={"X-Device-Id": device_a},
                          json={"content": "another quiet thought, hoping someone reads this"})
        msg_id = r.json()["message"]["message_id"]
        rep = requests.post(f"{BASE_URL}/api/anonymous/messages/{msg_id}/report",
                            headers={"X-Device-Id": device_b},
                            json={"reason": "abusive"})
        assert rep.status_code == 200, rep.text
        assert rep.json().get("ok") is True


# ---------- Track ----------
class TestTrack:
    def test_known_event(self, device_a):
        r = requests.post(f"{BASE_URL}/api/anonymous/track", headers={"X-Device-Id": device_a},
                          json={"event_name": "anonymous_page_opened"})
        assert r.status_code == 200

    def test_unknown_event(self, device_a):
        r = requests.post(f"{BASE_URL}/api/anonymous/track", headers={"X-Device-Id": device_a},
                          json={"event_name": "totally_made_up"})
        assert r.status_code == 400


# ---------- Long-polling fallback ----------
class TestPolling:
    def test_polling_since_returns_only_newer(self, device_a):
        # post a message
        r = requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/messages", headers={"X-Device-Id": device_a},
                          json={"content": "polling test message a, just sharing"})
        ts1 = r.json()["message"]["created_at"]
        time.sleep(0.5)
        r2 = requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/messages", headers={"X-Device-Id": device_a},
                           json={"content": "polling test message b, second one"})
        new_id = r2.json()["message"]["message_id"]
        # Now poll since ts1
        poll = requests.get(f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
                            headers={"X-Device-Id": device_a},
                            params={"since": ts1}).json()
        ids = {m["message_id"] for m in poll["messages"]}
        assert new_id in ids
        # Older seeds should NOT be there
        assert all(m["created_at"] > ts1 for m in poll["messages"])


# ---------- Admin ----------
class TestAdmin:
    def test_non_admin_forbidden(self, device_a):
        r = requests.get(f"{BASE_URL}/api/admin/anonymous/metrics")
        assert r.status_code in (401, 403)

    def test_metrics(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/anonymous/metrics",
                         headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("total_user_messages", "blocked_messages", "block_rate_pct", "reports", "sessions_created", "rooms"):
            assert k in d
        assert any("active_count" in r for r in d["rooms"])

    def test_reports_list(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/anonymous/reports?status=open",
                         headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200
        # Should be hydrated
        for rep in r.json()["reports"]:
            assert "message" in rep

    def test_flagged(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/anonymous/messages/flagged",
                         headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200
        msgs = r.json()["messages"]
        # We blocked toxic + doxxing earlier — at least one should be there
        assert len(msgs) >= 1
        assert any(m.get("moderation_status") == "blocked" for m in msgs)

    def test_transcript(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/anonymous/rooms/loneliness/transcript",
                         headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200
        msgs = r.json()["messages"]
        types = {m["message_type"] for m in msgs}
        statuses = {m["moderation_status"] for m in msgs}
        assert "seed" in types
        assert "user" in types
        assert "blocked" in statuses

    def test_remove_message(self, admin_token, device_a):
        # Post a message
        r = requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/messages", headers={"X-Device-Id": device_a},
                          json={"content": "to be removed honestly"})
        mid = r.json()["message"]["message_id"]
        rm = requests.post(f"{BASE_URL}/api/admin/anonymous/messages/{mid}/remove",
                           headers={"Authorization": f"Bearer {admin_token}"},
                           json={"reason": "test"})
        assert rm.status_code == 200
        # Verify removed in transcript
        tx = requests.get(f"{BASE_URL}/api/admin/anonymous/rooms/loneliness/transcript",
                          headers={"Authorization": f"Bearer {admin_token}"}).json()["messages"]
        target = next((m for m in tx if m["message_id"] == mid), None)
        assert target is not None
        assert target["moderation_status"] == "admin_removed"

    def test_freeze_unfreeze(self, admin_token, device_a):
        # freeze
        rf = requests.post(f"{BASE_URL}/api/admin/anonymous/rooms/general-reality/freeze",
                           headers={"Authorization": f"Bearer {admin_token}"},
                           json={"reason": "test"})
        assert rf.status_code == 200
        # post should 423
        r = requests.post(f"{BASE_URL}/api/anonymous/rooms/general-reality/messages",
                          headers={"X-Device-Id": device_a},
                          json={"content": "hello frozen"})
        assert r.status_code == 423
        # unfreeze
        ru = requests.post(f"{BASE_URL}/api/admin/anonymous/rooms/general-reality/unfreeze",
                           headers={"Authorization": f"Bearer {admin_token}"})
        assert ru.status_code == 200
        # post works
        r2 = requests.post(f"{BASE_URL}/api/anonymous/rooms/general-reality/messages",
                           headers={"X-Device-Id": device_a},
                           json={"content": "honest thoughts back online"})
        assert r2.status_code == 200

    def test_ban_session(self, admin_token):
        # Use a fresh device so we don't break other tests
        dev = _new_device()
        sess = requests.post(f"{BASE_URL}/api/anonymous/session", headers={"X-Device-Id": dev}).json()
        sid = sess["session_id"]
        # post first to confirm
        ok = requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
                           headers={"X-Device-Id": dev},
                           json={"content": "pre-ban honest message"})
        assert ok.status_code == 200
        # ban
        rb = requests.post(f"{BASE_URL}/api/admin/anonymous/sessions/{sid}/ban",
                           headers={"Authorization": f"Bearer {admin_token}"},
                           json={"reason": "test"})
        assert rb.status_code == 200
        # subsequent post → 403
        r = requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
                          headers={"X-Device-Id": dev},
                          json={"content": "after ban"})
        assert r.status_code == 403
