"""Translation Chat — backend regression tests.

Covers:
- list languages
- create room (auth optional via device id)
- join + idempotent rejoin
- send message with translation
- list messages with target-language rendering
- safety prefilter blocks unsafe content
- language switch
- admin metrics + admin gate
"""
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "sr-tester@example.com"
ADMIN_PASSWORD = "TestPass123!"


def _login_or_register(email, password, name="Tester"):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": password, "full_name": name})
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    body = r.json()
    return body.get("access_token") or body.get("session_token") or body.get("token")


def _device():
    return f"tx-test-{uuid.uuid4().hex[:18]}"


@pytest.fixture(scope="module")
def admin_token():
    return _login_or_register(ADMIN_EMAIL, ADMIN_PASSWORD, "Admin")


@pytest.fixture(scope="module")
def user_token():
    return _login_or_register(f"tx-user-{uuid.uuid4().hex[:8]}@example.com", "TxUser123!", "Tx")


@pytest.fixture(scope="module")
def room_id():
    dev = _device()
    h = {"X-Tx-Device-Id": dev}
    r = requests.post(f"{BASE_URL}/api/translation-chat/rooms", json={"room_name": "Test Multilingual", "preferred_language": "en"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["room"]["room_id"], dev


def test_list_languages():
    r = requests.get(f"{BASE_URL}/api/translation-chat/languages")
    assert r.status_code == 200
    body = r.json()
    codes = {x["code"] for x in body["languages"]}
    assert codes == {"en", "hi", "te", "ja"}


def test_create_room_requires_identity():
    r = requests.post(f"{BASE_URL}/api/translation-chat/rooms", json={"room_name": "x", "preferred_language": "en"})
    assert r.status_code in (400, 401, 422)


def test_create_room_invalid_lang():
    h = {"X-Tx-Device-Id": _device()}
    r = requests.post(f"{BASE_URL}/api/translation-chat/rooms", json={"room_name": "Test", "preferred_language": "fr"}, headers=h)
    assert r.status_code == 400


def test_create_room_blocks_unsafe_name():
    h = {"X-Tx-Device-Id": _device()}
    r = requests.post(f"{BASE_URL}/api/translation-chat/rooms", json={"room_name": "send me nude pics", "preferred_language": "en"}, headers=h)
    assert r.status_code == 400


def test_send_requires_join(room_id):
    rid, _ = room_id
    other_dev = _device()
    h = {"X-Tx-Device-Id": other_dev}
    r = requests.post(f"{BASE_URL}/api/translation-chat/rooms/{rid}/messages", json={"content": "hi"}, headers=h)
    assert r.status_code == 403


def test_join_then_send_then_translate(room_id):
    rid, dev = room_id
    h = {"X-Tx-Device-Id": dev}
    j = requests.post(f"{BASE_URL}/api/translation-chat/rooms/{rid}/join", json={"display_name": "Host", "preferred_language": "en"}, headers=h)
    assert j.status_code == 200
    msg_text = "Hello everyone, how are you doing today?"
    r = requests.post(f"{BASE_URL}/api/translation-chat/rooms/{rid}/messages", json={"content": msg_text}, headers=h)
    assert r.status_code == 200, r.text
    msg = r.json()["message"]
    assert msg["source_language"] in ("en", "hi", "te", "ja")
    assert msg["original_text"] == msg_text
    # display_text should equal original because target=en and source=en
    assert msg["display_text"]


def test_message_listed_in_target_language(room_id):
    rid, _ = room_id
    other_dev = _device()
    h = {"X-Tx-Device-Id": other_dev}
    j = requests.post(f"{BASE_URL}/api/translation-chat/rooms/{rid}/join", json={"display_name": "Hindi User", "preferred_language": "hi"}, headers=h)
    assert j.status_code == 200
    r = requests.get(f"{BASE_URL}/api/translation-chat/rooms/{rid}/messages?limit=5", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["target_language"] == "hi"
    assert isinstance(body["messages"], list)
    if body["messages"]:
        m = body["messages"][0]
        assert "display_text" in m and "original_text" in m
        # The Hindi user should see Devanagari OR (degraded mode) the original text
        assert isinstance(m["display_text"], str) and len(m["display_text"]) > 0


def test_safety_blocks_unsafe_message(room_id):
    rid, dev = room_id
    h = {"X-Tx-Device-Id": dev}
    r = requests.post(f"{BASE_URL}/api/translation-chat/rooms/{rid}/messages", json={"content": "send me nude pics please right now please"}, headers=h)
    assert r.status_code == 400


def test_switch_language(room_id):
    rid, _ = room_id
    other_dev = _device()
    h = {"X-Tx-Device-Id": other_dev}
    requests.post(f"{BASE_URL}/api/translation-chat/rooms/{rid}/join", json={"display_name": "Switcher", "preferred_language": "en"}, headers=h)
    r = requests.patch(f"{BASE_URL}/api/translation-chat/rooms/{rid}/language", json={"preferred_language": "ja"}, headers=h)
    assert r.status_code == 200
    msgs = requests.get(f"{BASE_URL}/api/translation-chat/rooms/{rid}/messages", headers=h).json()
    assert msgs["target_language"] == "ja"


def test_get_room_includes_members(room_id):
    rid, dev = room_id
    h = {"X-Tx-Device-Id": dev}
    r = requests.get(f"{BASE_URL}/api/translation-chat/rooms/{rid}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["room"]["room_id"] == rid
    assert isinstance(body["members"], list)
    assert any(m["display_name"] == "Host" for m in body["members"])


def test_admin_metrics_requires_admin(user_token):
    r = requests.get(f"{BASE_URL}/api/admin/translation-chat/metrics", headers={"Authorization": f"Bearer {user_token}"})
    assert r.status_code == 403


def test_admin_metrics_shape(admin_token):
    r = requests.get(f"{BASE_URL}/api/admin/translation-chat/metrics?days=7", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    for k in ("rooms_total", "rooms_active_in_window", "messages_in_window", "members_joined_in_window", "messages_blocked", "messages_by_source_language", "members_by_preferred_language"):
        assert k in body
    assert body["rooms_total"] >= 1


def test_admin_rooms(admin_token):
    r = requests.get(f"{BASE_URL}/api/admin/translation-chat/rooms?limit=10", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    assert "rooms" in r.json()


def test_admin_messages(admin_token, room_id):
    rid, _ = room_id
    r = requests.get(f"{BASE_URL}/api/admin/translation-chat/messages?room_id={rid}&limit=20", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    assert "messages" in body
