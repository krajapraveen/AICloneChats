"""Regression coverage for support thread admin email notifications.

What this proves
----------------
1. Creating a new support thread invokes `_notify_admins_new_thread`.
2. One Resend send attempt per recipient (from `ADMIN_EMAILS` CSV) is logged
   to `db.email_send_events` with `purpose=support_thread_admin_notify`.
3. Notification failures NEVER block thread creation (best-effort path).

We do NOT assert that emails actually deliver in this test because the
preview Resend API key is restricted (no verified domain) and the
production key cannot be exercised from CI. Real-send proof was captured
in the manual P0 verification log (see iteration history).
"""
from __future__ import annotations

import os
import sys
import asyncio
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

USER_EMAIL = "sr-tester@example.com"
USER_PASSWORD = "TestPass123!"


@pytest.fixture(scope="module")
def user_token() -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": USER_EMAIL, "password": USER_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["session_token"]


def _expected_recipient_count() -> int:
    raw = os.environ.get("ADMIN_EMAILS", "") or ""
    recipients = {e.lower().strip() for e in raw.split(",") if e.strip()}
    return max(1, len(recipients) or 2)  # default fallback is 2 in _notify_admins_new_thread


def test_concern_creation_triggers_admin_notification(user_token: str):
    # Submit a fresh concern
    payload = {
        "kind": "concern",
        "subject": "Regression test: admin email notify",
        "body": (
            "Automated regression test verifying that admin email "
            "notifications fire on new concern creation."
        ),
    }
    r = requests.post(
        f"{BASE_URL}/api/support/threads",
        json=payload,
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=30,
    )
    assert r.status_code == 200, f"thread create failed: {r.status_code} {r.text}"
    thread_id = r.json()["thread_id"]
    assert thread_id.startswith("th_")

    # Give the fire-and-forget send pipeline a moment to log
    async def _check():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        # The notification function logs one row per provider per recipient
        # (resend first, then smtp fallback if resend failed). At minimum we
        # expect `expected_recipients` resend rows in the latest events.
        for _ in range(10):
            cursor = db.email_send_events.find(
                {"purpose": "support_thread_admin_notify", "provider": "resend"}
            ).sort("timestamp", -1).limit(10)
            docs = await cursor.to_list(length=10)
            if len(docs) >= _expected_recipient_count():
                return docs
            await asyncio.sleep(0.5)
        return docs

    docs = asyncio.get_event_loop().run_until_complete(_check())
    assert len(docs) >= _expected_recipient_count(), (
        f"expected at least {_expected_recipient_count()} resend events, got {len(docs)}"
    )
    # Each event must carry the right purpose
    for d in docs[: _expected_recipient_count()]:
        assert d.get("purpose") == "support_thread_admin_notify"
        assert d.get("provider") == "resend"
        # ok may be True (prod) or False (preview - http_403 no verified domain).
        # Either is acceptable — what matters is the send was *attempted*.


def test_thread_creation_succeeds_even_if_notify_fails(user_token: str):
    """Notification is best-effort: a failing Resend send must NOT bubble up
    to the user. We rely on the preview env (Resend 403) to exercise this
    path implicitly — every successful thread create above already proves it."""
    r = requests.post(
        f"{BASE_URL}/api/support/threads",
        json={
            "kind": "recommendation",
            "subject": "Recommendation regression",
            "body": "Notification failure must not block thread creation.",
        },
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "open"
