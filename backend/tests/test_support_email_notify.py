"""Support thread admin notifications — IN-APP ONLY, NO EMAIL.

Policy (Feb 12, 2026): when a user submits a concern/recommendation, admins
read it inside the in-app admin support inbox (`/admin/support`). The
backend MUST NOT send any email to admin@aiclonechats.com,
krajapraveen@aiclonechats.com, or any other ADMIN_EMAILS recipient for
this event.

What this proves
----------------
1. Creating a new support thread no longer logs any
   `support_thread_admin_notify` row in `email_send_events`.
2. The thread is still persisted with `unread_for_admins=True` so the
   admin in-app inbox renders an unread badge.
3. `_notify_admins_new_thread` is a noop — it never invokes
   `email_sender.send_email`.
"""
from __future__ import annotations

import os
import sys
import asyncio
from datetime import datetime, timezone
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


def test_thread_creation_does_not_log_any_admin_notify_email(user_token: str):
    """A new thread must NOT add any `support_thread_admin_notify` row to
    `email_send_events` — the new policy is in-app delivery only."""
    submitted_at = datetime.now(timezone.utc).isoformat()
    r = requests.post(
        f"{BASE_URL}/api/support/threads",
        json={
            "kind": "concern",
            "subject": "Regression: no admin email for new thread",
            "body": "This thread MUST NOT trigger any admin notification email.",
        },
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]
    assert thread_id.startswith("th_")

    # Wait a moment to make sure any rogue send attempt would have been
    # logged, then check the in-flight window for events.
    async def _check_no_event():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        for _ in range(4):
            count = await db.email_send_events.count_documents({
                "purpose": "support_thread_admin_notify",
                "timestamp": {"$gte": submitted_at},
            })
            if count > 0:
                return count
            await asyncio.sleep(0.5)
        return 0
    count = asyncio.new_event_loop().run_until_complete(_check_no_event())
    assert count == 0, (
        f"expected ZERO admin-notify email events after thread creation, got {count}. "
        f"Per Feb 12 2026 policy, admin notifications are in-app only."
    )


def test_thread_still_marks_unread_for_admins(user_token: str):
    """Even without email, the admin in-app inbox must light up: the new
    thread document must carry `unread_for_admins=True`."""
    r = requests.post(
        f"{BASE_URL}/api/support/threads",
        json={
            "kind": "recommendation",
            "subject": "Regression: unread_for_admins set on new thread",
            "body": "Admins read this in-app at /admin/support.",
        },
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]

    async def _fetch():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        doc = await db.support_threads.find_one({"thread_id": thread_id})
        return doc

    doc = asyncio.new_event_loop().run_until_complete(_fetch())
    assert doc is not None
    assert doc.get("unread_for_admins") is True, (
        "admin in-app inbox depends on unread_for_admins — must be True on new threads"
    )


def test_notify_admins_helper_does_not_call_send_email(monkeypatch):
    """Direct unit test on the helper: invoking it must NOT touch
    `email_sender.send_email`. This is the guarantee that even if a code
    path elsewhere starts calling the helper, no email blast happens."""
    import support_inbox

    send_calls: list[dict] = []

    async def _spy(**kwargs):
        send_calls.append(kwargs)
        return True, "fake"

    monkeypatch.setattr("email_sender.send_email", _spy)

    thread = {
        "thread_id": "th_unit_test",
        "kind": "concern",
        "subject": "subj",
        "messages": [{"body": "body"}],
    }
    user = {"email": "user@example.com", "user_id": "u_x"}

    asyncio.new_event_loop().run_until_complete(
        support_inbox._notify_admins_new_thread(thread, user)
    )
    assert send_calls == [], (
        "_notify_admins_new_thread is a noop under the in-app-only policy. "
        "If you intentionally re-enabled email, update this test consciously."
    )
