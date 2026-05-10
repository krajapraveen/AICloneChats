"""Anonymous Reality — observability dashboard backend tests.

Verifies:
- 401/403 for non-admin
- 200 for admin with the full payload shape
- Counts are non-negative integers
- Rates fall within [0, 100]
- After seeding activity, talkers/messages reflect it
"""
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://digital-twin-119.preview.emergentagent.com",
).rstrip("/")
ADMIN_EMAIL = "sr-tester@example.com"
ADMIN_PASSWORD = "TestPass123!"


def _new_device():
    return f"obs-test-{uuid.uuid4().hex[:18]}"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r.status_code != 200:
        requests.post(f"{BASE_URL}/api/auth/register", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "full_name": "SR Tester"})
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"admin login failed: {r.text}"
    body = r.json()
    return body.get("access_token") or body.get("session_token") or body.get("token")


@pytest.fixture(scope="module")
def seeded_session():
    """Create an anonymous session and emit some activity so metrics aren't all zero."""
    device = _new_device()
    h = {"X-Device-Id": device}
    r = requests.post(f"{BASE_URL}/api/anonymous/session", headers=h)
    assert r.status_code == 200
    # Join a room (emits anonymous_room_joined event)
    rj = requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/join", headers=h)
    assert rj.status_code == 200
    # Send one benign message
    msg = requests.post(
        f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
        headers=h,
        json={"content": "honest gentle thought today, just wanted to share"},
    )
    assert msg.status_code in (200, 202), msg.text
    return device


def test_observability_requires_auth():
    r = requests.get(f"{BASE_URL}/api/admin/anonymous/observability")
    assert r.status_code in (401, 403)


def test_observability_non_admin_forbidden():
    # Create a non-admin user
    email = f"nonadmin-{uuid.uuid4().hex[:8]}@example.com"
    pw = "NonAdmin123!"
    requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": pw, "full_name": "Non Admin"})
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    token = (r.json().get("access_token") or r.json().get("session_token") or r.json().get("token"))
    assert token
    headers = {"Authorization": f"Bearer {token}"}
    r2 = requests.get(f"{BASE_URL}/api/admin/anonymous/observability", headers=headers)
    assert r2.status_code == 403, r2.text


def test_observability_admin_payload_shape(admin_token, seeded_session):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = requests.get(f"{BASE_URL}/api/admin/anonymous/observability?days=7", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # Top-level keys
    for k in ["generated_at", "window_days", "audience", "engagement", "safety", "retention", "rooms"]:
        assert k in body, f"missing key: {k}"
    assert body["window_days"] == 7

    # Audience
    a = body["audience"]
    for k in ["dau", "wau", "sessions_created_in_window", "total_sessions_all_time", "daily_active_series"]:
        assert k in a
    assert a["dau"] >= 0
    assert a["wau"] >= 0
    assert a["dau"] <= a["wau"] or a["wau"] == 0  # DAU never exceeds WAU
    assert a["total_sessions_all_time"] >= 1  # at least seeded_session
    assert isinstance(a["daily_active_series"], list)

    # Engagement
    e = body["engagement"]
    for k in ["messages_user_allowed", "messages_user_total", "talkers", "lurkers", "avg_msgs_per_talker", "avg_session_duration_sec", "peak_concurrent_estimate", "active_now_total"]:
        assert k in e
    assert e["messages_user_allowed"] >= 1
    assert e["messages_user_total"] >= e["messages_user_allowed"]
    assert e["talkers"] >= 1
    assert e["lurkers"] >= 0
    assert e["avg_msgs_per_talker"] >= 0
    assert e["peak_concurrent_estimate"] >= 1  # we joined at least once

    # Safety — rates must be in [0,100]
    s = body["safety"]
    for k in ["block_rate_pct", "report_rate_pct", "ai_reply_usage_pct"]:
        assert 0 <= s[k] <= 100, f"{k} out of range: {s[k]}"
    assert s["blocked"] >= 0
    assert s["reports"] >= 0

    # Retention — null OR pct in [0,100]
    rt = body["retention"]
    for k in ["d1_pct", "d7_pct"]:
        v = rt[k]
        assert v is None or (0 <= v <= 100), f"{k} out of range: {v}"

    # Rooms
    ro = body["rooms"]
    assert ro["user_created_rooms_locked"] is True  # Phase 1 invariant
    assert ro["total"] >= 1
    assert isinstance(ro["rows"], list)
    # Loneliness room should have ≥1 message after seeded fixture
    loneliness = next((r for r in ro["rows"] if r["slug"] == "loneliness"), None)
    assert loneliness is not None
    assert loneliness["messages"] >= 1
    assert loneliness["talkers"] >= 1
    assert loneliness["joiners"] >= 1


def test_observability_window_param(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    for d in [1, 14, 30]:
        r = requests.get(f"{BASE_URL}/api/admin/anonymous/observability?days={d}", headers=headers)
        assert r.status_code == 200
        assert r.json()["window_days"] == d


def test_observability_window_bounds(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    # below min
    r1 = requests.get(f"{BASE_URL}/api/admin/anonymous/observability?days=0", headers=headers)
    assert r1.status_code == 422
    # above max
    r2 = requests.get(f"{BASE_URL}/api/admin/anonymous/observability?days=999", headers=headers)
    assert r2.status_code == 422
