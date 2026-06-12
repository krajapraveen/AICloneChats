"""Admin-only shortcut banner on /account/inbox.

When an admin lands on their own personal inbox, surface a clearly-visible
banner that links them to /admin/support — where USER-submitted concerns
and recommendations actually live. Without this, an admin who got the
"new user thread" notification (or used to get an email) opens their own
inbox, sees only their own threads (likely empty), and concludes nothing
arrived. That was the original bug report.

This is a UI test driven through Playwright + the public API.
"""
from __future__ import annotations

import os
import sys
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
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
ADMIN_EMAIL = "krajapraveen@gmail.com"


def _mint_admin_token() -> str:
    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        admin = await db.users.find_one({"email": ADMIN_EMAIL}, {"user_id": 1})
        if not admin:
            return None
        token = f"st_{uuid.uuid4().hex}{uuid.uuid4().hex}"
        await db.user_sessions.insert_one({
            "session_token": token, "user_id": admin["user_id"],
            "source": "test-admin-shortcut",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        })
        return token
    return asyncio.new_event_loop().run_until_complete(_go())


def test_admin_support_endpoint_returns_user_threads():
    """The contract the new banner relies on: the admin endpoint returns
    `unread_total` and `items[]` so the banner can show the count and the
    /admin/support page can render the list."""
    tok = _mint_admin_token()
    if not tok:
        pytest.skip("admin not seeded")
    r = requests.get(
        f"{BASE_URL}/api/admin/support/threads?unread_only=true&limit=1",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    assert "unread_total" in body
    assert isinstance(body["unread_total"], int)


def test_admin_support_endpoint_blocks_non_admin():
    """Verify the contract from the other direction: a non-admin must not
    be able to query the admin support listing."""
    # Register a fresh non-admin user
    email = f"u_shortcut_{uuid.uuid4().hex[:8]}@example.com"
    reg = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "TestPass123!", "name": "S"},
        timeout=20,
    )
    if reg.status_code != 200:
        # If registration failed because email exists, try login
        reg = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": email, "password": "TestPass123!"},
            timeout=20,
        )
    assert reg.status_code == 200, reg.text
    tok = reg.json()["session_token"]

    r = requests.get(
        f"{BASE_URL}/api/admin/support/threads?unread_only=true&limit=1",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=15,
    )
    assert r.status_code in (401, 403), (
        "non-admin must NOT be able to read user-submitted threads"
    )
