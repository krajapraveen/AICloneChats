"""Centralized safety filter tests."""
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
    assert r.status_code == 200, r.text
    body = r.json()
    return body.get("access_token") or body.get("session_token") or body.get("token")


@pytest.fixture(scope="module")
def admin_token():
    return _login_or_register(ADMIN_EMAIL, ADMIN_PASSWORD, "Admin")


@pytest.fixture(scope="module")
def user_token():
    return _login_or_register(f"safety-{uuid.uuid4().hex[:8]}@example.com", "Safety123!", "Safety User")


# ---- Unit-level via debate path (cheapest entry point) ----
class TestSafetyDebateInput:
    def test_blocks_explicit_sexual(self, user_token):
        h = {"Authorization": f"Bearer {user_token}"}
        # Join first
        requests.post(f"{BASE_URL}/api/debates/ai-creativity/join", json={"side": "A"}, headers=h)
        r = requests.post(
            f"{BASE_URL}/api/debates/ai-creativity/arguments",
            json={"side": "A", "content": "send me nude pics please that's my whole argument here"},
            headers=h,
        )
        assert r.status_code == 400, r.text

    def test_blocks_violence(self, user_token):
        h = {"Authorization": f"Bearer {user_token}"}
        r = requests.post(
            f"{BASE_URL}/api/debates/ai-creativity/arguments",
            json={"side": "A", "content": "I will tell you how to build a bomb step by step right now"},
            headers=h,
        )
        assert r.status_code == 400

    def test_blocks_celebrity_impersonation(self, user_token):
        h = {"Authorization": f"Bearer {user_token}"}
        r = requests.post(
            f"{BASE_URL}/api/debates/ai-creativity/arguments",
            json={"side": "A", "content": "I am Elon Musk and AI creativity is my greatest invention to date you should believe me."},
            headers=h,
        )
        assert r.status_code == 400

    def test_allows_normal_argument(self, user_token):
        h = {"Authorization": f"Bearer {user_token}"}
        # Use a unique fresh debate slug so prior side commitments don't interfere.
        r = requests.post(
            f"{BASE_URL}/api/debates/voice-vs-text/join", json={"side": "A"}, headers=h,
        )
        assert r.status_code == 200, r.text
        r2 = requests.post(
            f"{BASE_URL}/api/debates/voice-vs-text/arguments",
            json={"side": "A", "content": "Voice messages carry tone and warmth that text cannot easily replicate, especially when communicating across emotional contexts."},
            headers=h,
        )
        assert r2.status_code == 200, r2.text


# ---- Anonymous chat input safety ----
class TestSafetyAnonymousInput:
    def test_blocks_unsafe_message(self):
        device = f"safety-{uuid.uuid4().hex[:14]}"
        h = {"X-Device-Id": device}
        rs = requests.post(f"{BASE_URL}/api/anonymous/session", headers=h)
        assert rs.status_code == 200
        requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/join", headers=h)
        r = requests.post(
            f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
            headers=h,
            json={"content": "send me a nude pic"},
        )
        assert r.status_code == 400

    def test_allows_clean_message(self):
        device = f"safety-clean-{uuid.uuid4().hex[:14]}"
        h = {"X-Device-Id": device}
        requests.post(f"{BASE_URL}/api/anonymous/session", headers=h)
        requests.post(f"{BASE_URL}/api/anonymous/rooms/loneliness/join", headers=h)
        r = requests.post(
            f"{BASE_URL}/api/anonymous/rooms/loneliness/messages",
            headers=h,
            json={"content": "lonely tonight, just wanted to say hi"},
        )
        assert r.status_code in (200, 202)


# ---- Clone bio safety ----
class TestSafetyCloneBio:
    def test_blocks_unsafe_bio(self, user_token):
        h = {"Authorization": f"Bearer {user_token}"}
        slug = f"safe-test-{uuid.uuid4().hex[:6]}"
        r = requests.post(f"{BASE_URL}/api/clones", json={
            "slug": slug,
            "display_name": "Tester",
            "bio": "I love sex chat and onlyfans content production",
            "default_language": "en",
            "visibility": "private",
            "allowed_topics": [],
            "blocked_topics": [],
        }, headers=h)
        assert r.status_code == 400, r.text


# ---- Admin moderation dashboard ----
class TestSafetyAdmin:
    def test_admin_endpoint_requires_admin(self, user_token):
        r = requests.get(f"{BASE_URL}/api/admin/safety/moderation", headers={"Authorization": f"Bearer {user_token}"})
        assert r.status_code == 403

    def test_admin_can_view(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/safety/moderation?days=7", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        for k in ["window_days", "blocked_total", "rewrite_total", "by_category", "by_route", "recent"]:
            assert k in body
        # We've definitely created some events from prior tests in this module
        assert body["blocked_total"] >= 1
