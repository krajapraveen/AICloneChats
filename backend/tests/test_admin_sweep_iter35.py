"""Horizontal admin-surface audit — iteration 35.

Probes every backend admin GET endpoint used by the 21 AdminIndex tool cards
plus a handful of write actions. Success criteria:
    * Admin request returns 200 with valid JSON.
    * Unauthenticated request returns 401 or 403 (never 500).

We DO NOT deeply validate business logic here — a companion set of feature-
specific tests already covers that. This suite is the horizontal safety net:
if the operator opens /admin/<page>, the backing endpoint must not 500.
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


async def _mint_admin_token() -> str | None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    u = await db.users.find_one({"email": ADMIN_EMAIL}, {"user_id": 1, "role": 1})
    if not u:
        return None
    if u.get("role") != "admin":
        await db.users.update_one({"email": ADMIN_EMAIL}, {"$set": {"role": "admin"}})
    token = f"st_{uuid.uuid4().hex}{uuid.uuid4().hex}"
    await db.user_sessions.insert_one({
        "session_token": token,
        "user_id": u["user_id"],
        "source": "test-mint-admin-sweep",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    })
    return token


@pytest.fixture(scope="module")
def admin_token() -> str:
    t = asyncio.new_event_loop().run_until_complete(_mint_admin_token())
    if not t:
        pytest.skip("Admin user not seeded in DB")
    return t


@pytest.fixture(scope="module")
def admin_headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


# ---- All admin GET endpoints hit by the frontend admin pages -------------
ADMIN_GETS = [
    # /admin/anonymous-reality
    "/api/admin/anonymous/metrics",
    "/api/admin/anonymous/reports",
    "/api/admin/anonymous/messages/flagged",
    "/api/admin/anonymous/observability",
    # /admin/debates
    "/api/admin/debates?days=30",
    # /admin/debates/retention
    "/api/admin/debates/retention?days=30",
    # /admin/safety
    "/api/admin/safety/moderation?days=7",
    # /admin/anti-abuse
    "/api/admin/anti-abuse/summary",
    "/api/admin/anti-abuse/recent",
    "/api/admin/anti-abuse/suspicious-users",
    "/api/admin/anti-abuse/blocked-users",
    # /admin/voice-metrics
    "/api/admin/voice/metrics?days=14",
    # /admin/anonymous-metrics (same as anonymous/metrics)
    # /admin/translation-chat
    "/api/admin/translation-chat/metrics?days=14",
    # /admin/delayed-messages
    "/api/admin/delayed-messages/metrics?days=30",
    "/api/admin/delayed-messages/queue",
    # /admin/avatar-chat
    "/api/admin/avatar-chat/metrics?days=14",
    "/api/admin/avatar-chat/jobs",
    # /admin/login-intelligence
    "/api/admin/login-events?days=7",
    "/api/admin/login-events/summary?days=7",
    # /admin/subscriber-motion
    "/api/admin/revenue/subscriber-motion?days=30",
    "/api/admin/revenue/subscriber-motion/target",
    "/api/admin/revenue/subscriber-trend?days=30",
    # /admin/cost-telemetry
    "/api/admin/cost-telemetry/cost-config",
    "/api/admin/cost-telemetry/profit-per-feature?days=30",
    "/api/admin/cost-telemetry/contribution-by-source?days=30",
    "/api/admin/cost-telemetry/loss-making?days=30",
    # /admin/exit-insights
    "/api/admin/exit-insights?days=90",
    # /admin/user-activity
    "/api/admin/user-activity?days=30&limit=20",
    # /admin/chats
    "/api/admin/chats?limit=20",
    # /admin/webhook-logs
    "/api/admin/billing/webhook-logs?limit=20",
    # /admin/revenue
    "/api/admin/revenue/funnel?days=30",
    "/api/admin/revenue/revenue?days=30",
    "/api/admin/revenue/credit-economy?days=30",
    "/api/admin/revenue/emotional-gravity?days=30",
    "/api/admin/revenue/cohorts?days=30",
    "/api/admin/revenue/operational-health",
    # /admin/users
    "/api/admin/billing/users/search?q=a",
    # /admin/email-health
    "/api/admin/email/health",
    # /admin/renewal-reminders
    "/api/admin/billing/renewal-reminders/summary",
    # /admin/support
    "/api/admin/support/threads",
    # /admin (generic admin identity)
    "/api/admin/me",
]


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_admin_get_returns_200(admin_headers, path):
    r = requests.get(f"{BASE_URL}{path}", headers=admin_headers, timeout=45)
    assert r.status_code == 200, (
        f"{path} → {r.status_code}\n"
        f"body: {r.text[:400]}"
    )
    # Best-effort: response must be JSON (or empty allowed for 204 which we don't expect).
    try:
        r.json()
    except Exception as e:
        pytest.fail(f"{path} returned non-JSON body: {e} | {r.text[:200]}")


UNAUTH_PROBES = [
    "/api/admin/anonymous/metrics",
    "/api/admin/safety/moderation",
    "/api/admin/anti-abuse/summary",
    "/api/admin/avatar-chat/metrics",
    "/api/admin/chats",
    "/api/admin/billing/webhook-logs",
    "/api/admin/revenue/funnel",
    "/api/admin/support/threads",
    "/api/admin/user-activity",
    "/api/admin/email/health",
]


@pytest.mark.parametrize("path", UNAUTH_PROBES)
def test_admin_get_unauth_returns_401_or_403(path):
    r = requests.get(f"{BASE_URL}{path}", timeout=30)
    assert r.status_code in (401, 403), (
        f"unauth {path} expected 401/403, got {r.status_code}: {r.text[:200]}"
    )


# ---- Write action probes -------------------------------------------------

def test_audit_faces_dry_run(admin_headers):
    r = requests.post(
        f"{BASE_URL}/api/admin/avatars/audit-faces",
        headers=admin_headers,
        params={"dry_run": "true"},
        timeout=90,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
    data = r.json()
    assert data.get("ok") is True
    assert "scanned" in data


def test_enforce_zero_credit_policy_dry_run(admin_headers):
    r = requests.post(
        f"{BASE_URL}/api/admin/billing/enforce-zero-credit-policy",
        headers=admin_headers,
        json={"dry_run": True},
        timeout=45,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
    data = r.json()
    assert data.get("ok") is True
    assert data.get("dry_run") is True
    assert "scanned" in data


def test_renewal_reminders_dry_run(admin_headers):
    r = requests.post(
        f"{BASE_URL}/api/admin/renewal-reminders/run",
        headers=admin_headers,
        json={"dry_run": True},
        timeout=45,
    )
    # Endpoint may not accept body kwargs — accept 200 or 422 on schema,
    # but NEVER 500.
    assert r.status_code in (200, 400, 422), f"{r.status_code}: {r.text[:400]}"


def test_non_admin_blocked_from_admin_get():
    # Register a fresh normal user then hit an admin route with their token.
    email = f"non_admin_{uuid.uuid4().hex[:8]}@example.com"
    reg = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "TestPass123!", "name": "NonAdmin"},
        timeout=30,
    )
    assert reg.status_code == 200, reg.text
    tok = reg.json()["session_token"]
    r = requests.get(
        f"{BASE_URL}/api/admin/chats",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )
    assert r.status_code == 403, f"non-admin got {r.status_code}: {r.text[:200]}"
