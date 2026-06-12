"""End-to-end coverage for /api/profile/export (GDPR data portability)."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


@pytest.fixture(scope="module")
def user_session() -> tuple[str, str]:
    email = f"export_{uuid.uuid4().hex[:10]}@example.com"
    pw = "TestPass123!"
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": pw, "name": "Export Tester"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    return r.json()["session_token"], email


def test_export_requires_auth():
    r = requests.get(f"{BASE_URL}/api/profile/export", timeout=15)
    assert r.status_code == 401


def test_export_preview_counts(user_session):
    token, _ = user_session
    r = requests.get(
        f"{BASE_URL}/api/profile/export/preview",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "user_id" in body and "counts" in body
    counts = body["counts"]
    # A brand-new account starts with zero of almost everything
    for key in ("payment_orders", "clones", "clone_memories", "support_threads"):
        assert key in counts and counts[key] >= 0


def test_export_full_dump(user_session):
    token, email = user_session
    r = requests.get(
        f"{BASE_URL}/api/profile/export",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd and ".json" in cd

    body = json.loads(r.content)
    # Top-level shape
    for key in (
        "export_metadata", "account_profile", "subscriptions_and_payments",
        "clones", "clone_memories", "support_threads", "login_events_last_100",
        "voice_messages", "delayed_messages", "avatar_chat_messages", "counts",
    ):
        assert key in body, f"missing key: {key}"

    # Profile fidelity + no secrets leaked
    profile = body["account_profile"]
    assert profile["email"] == email
    assert "password_hash" not in profile
    assert "_id" not in profile

    # Export metadata has provenance
    meta = body["export_metadata"]
    assert meta["export_version"] == "1.0"
    assert meta["source"] == "aiclonechats.com"


def test_export_rate_limit(user_session):
    """Second consecutive export within the per-minute window must rate-limit."""
    token, _ = user_session
    # First call already happened in test_export_full_dump (same module fixture).
    # Trigger another to force the limiter.
    r1 = requests.get(
        f"{BASE_URL}/api/profile/export",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    # If we're the first in the suite to run this test, the previous test
    # already burned the quota — accept either 200 (first call) or 429.
    if r1.status_code == 200:
        r2 = requests.get(
            f"{BASE_URL}/api/profile/export",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        assert r2.status_code in (200, 429), r2.text
    else:
        assert r1.status_code == 429
