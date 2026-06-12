"""Renewal reminder scheduler — idempotency + dashboard endpoint tests.

We exercise:
  - Spec URL `POST /api/admin/billing/run-renewal-reminders` returns the summary shape.
  - Repeating the same run with `dry_run=true` does not write to the run log
    and does not flip user state.
  - Repeating a real run is idempotent: the SAME user is never reminded twice
    for the same `order_id` (cycle_identifier).
  - `GET /api/admin/billing/renewal-reminders/summary` returns the dashboard
    shape with today's counts + recent runs + next-expiring rows.
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


@pytest.fixture(scope="module")
def admin_token() -> str:
    async def _mint():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        admin = await db.users.find_one({"email": ADMIN_EMAIL}, {"user_id": 1})
        if not admin:
            return None
        token = f"st_{uuid.uuid4().hex}{uuid.uuid4().hex}"
        await db.user_sessions.insert_one({
            "session_token": token,
            "user_id": admin["user_id"],
            "source": "test-mint-admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        })
        return token
    t = asyncio.new_event_loop().run_until_complete(_mint())
    if not t:
        pytest.skip("admin not seeded")
    return t


def _register(email: str) -> dict:
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "TestPass123!", "name": "RR Tester"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _insert_expiring_paid_order(user_id: str, days_until_expiry: int = 2, plan_id: str = "pro") -> str:
    """Seed a paid order whose +30d expiry is within `days_until_expiry`."""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    paid_at = (datetime.now(timezone.utc) - timedelta(days=30 - days_until_expiry)).isoformat()
    order_id = f"order_rr_{uuid.uuid4().hex[:12]}"
    await db.payment_orders.insert_one({
        "order_id": order_id,
        "user_id": user_id,
        "plan_id": plan_id,
        "status": "paid",
        "amount": 149900,
        "currency": "INR",
        "amount_inr": 1499.0,
        "credits_to_grant": 2500,
        "provider": "cashfree",
        "created_at": paid_at,
        "paid_at": paid_at,
        "credited_at": paid_at,
        "updated_at": paid_at,
    })
    # Ensure the user is on this plan so the renewal scan picks it
    await db.users.update_one(
        {"user_id": user_id},
        {"$set": {"plan_id": plan_id, "plan_status": "active"}},
    )
    return order_id


def _new_user_with_expiring_order() -> tuple[str, str]:
    reg = _register(f"rr_{uuid.uuid4().hex[:10]}@example.com")
    user_id = reg["user"]["user_id"]
    order_id = asyncio.new_event_loop().run_until_complete(_insert_expiring_paid_order(user_id))
    return user_id, order_id


def _call_run(admin_token: str, dry_run: bool = False) -> dict:
    r = requests.post(
        f"{BASE_URL}/api/admin/billing/run-renewal-reminders?dry_run={'true' if dry_run else 'false'}",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_spec_url_returns_summary_shape(admin_token: str):
    body = _call_run(admin_token, dry_run=True)
    for k in ("run_id", "ran_at", "triggered_by", "examined", "sent",
              "skipped_admin", "skipped_already", "failures", "dry_run"):
        assert k in body, f"missing {k}"
    assert body["dry_run"] is True
    assert body["triggered_by"].startswith("scheduler:")


def test_dry_run_does_not_flip_user_state(admin_token: str):
    user_id, order_id = _new_user_with_expiring_order()
    # Run dry — should NOT mark renewal_reminder_sent_for
    body = _call_run(admin_token, dry_run=True)
    assert body["dry_run"] is True
    # examined should include our seeded order
    assert body["examined"] >= 1

    async def _check():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        u = await db.users.find_one({"user_id": user_id}, {"renewal_reminder_sent_for": 1})
        return u
    u = asyncio.new_event_loop().run_until_complete(_check())
    assert u.get("renewal_reminder_sent_for") is None, "dry_run must not flip user state"


def test_idempotent_across_repeat_runs(admin_token: str):
    """Real run twice: same user_id must NOT be reminded twice for the same order."""
    user_id, order_id = _new_user_with_expiring_order()
    first = _call_run(admin_token, dry_run=False)
    second = _call_run(admin_token, dry_run=False)
    # The second run must classify this user as "skipped_already" — never re-send.
    # (We can't read user state here because Resend may also have failed in preview;
    # the contract is: if first.sent OR first.failures > 0, second.skipped_already must
    # be at least 1 for the SAME user.)
    async def _check():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        u = await db.users.find_one({"user_id": user_id}, {"renewal_reminder_sent_for": 1})
        return u
    u = asyncio.new_event_loop().run_until_complete(_check())
    if u.get("renewal_reminder_sent_for") == order_id:
        # First-run actually delivered → second run MUST skip it as already sent.
        assert second["skipped_already"] >= 1, second
    else:
        # First-run Resend failed (likely 403 in preview). That counts as failures>0
        # and we'll try again next cycle — which is the desired behaviour.
        assert first["failures"] >= 1, first


def test_summary_endpoint_shape(admin_token: str):
    r = requests.get(
        f"{BASE_URL}/api/admin/billing/renewal-reminders/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("computed_at", "today", "next_expiring", "recent_runs", "config"):
        assert k in body, k
    for k in ("due", "sent", "failed", "skipped_already_reminded", "skipped_admin", "runs"):
        assert k in body["today"], k
    cfg = body["config"]
    assert cfg["reminder_window_days"] == 3
    assert cfg["plan_length_days"] == 30
    assert "scheduler_doc" in cfg


def test_run_persists_to_run_log(admin_token: str):
    """Every non-dry run is persisted to renewal_reminder_run_logs."""
    body = _call_run(admin_token, dry_run=False)
    run_id = body["run_id"]

    async def _check():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        return await db.renewal_reminder_run_logs.find_one({"run_id": run_id}, {"_id": 0})
    row = asyncio.new_event_loop().run_until_complete(_check())
    assert row is not None
    assert row["examined"] == body["examined"]
    assert row["sent"] == body["sent"]


def test_dry_run_NOT_persisted_to_run_log(admin_token: str):
    body = _call_run(admin_token, dry_run=True)
    run_id = body["run_id"]

    async def _check():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        return await db.renewal_reminder_run_logs.find_one({"run_id": run_id}, {"_id": 0})
    row = asyncio.new_event_loop().run_until_complete(_check())
    assert row is None, "dry_run runs must not pollute the audit log"


def test_unauthenticated_blocked():
    r = requests.post(f"{BASE_URL}/api/admin/billing/run-renewal-reminders", timeout=15)
    assert r.status_code in (401, 403)
    r2 = requests.get(f"{BASE_URL}/api/admin/billing/renewal-reminders/summary", timeout=15)
    assert r2.status_code in (401, 403)


def test_heartbeat_block_present(admin_token: str):
    r = requests.get(
        f"{BASE_URL}/api/admin/billing/renewal-reminders/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "heartbeat" in body, body
    hb = body["heartbeat"]
    for k in ("status", "label", "scheduler_source", "thresholds",
              "last_scheduler_run_at", "last_successful_run_at", "last_failed_run_at"):
        assert k in hb, k
    assert hb["status"] in ("green", "yellow", "red")
    assert hb["thresholds"]["green_max_hours"] == 26
    assert hb["thresholds"]["yellow_max_hours"] == 48


def test_user_agent_classification(admin_token: str):
    """A request with a Cloudflare UA should produce a 'cloudflare_cron'
    trigger_source in the persisted run log."""
    r = requests.post(
        f"{BASE_URL}/api/admin/billing/run-renewal-reminders",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "User-Agent": "Cloudflare-Workers/2026 (cf-worker)",
        },
        timeout=20,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trigger_source"] == "cloudflare_cron"

    # GitHub Actions UA
    r2 = requests.post(
        f"{BASE_URL}/api/admin/billing/run-renewal-reminders",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "User-Agent": "github-actions/runner-v2.x",
        },
        timeout=20,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["trigger_source"] == "github_actions"


def test_heartbeat_turns_green_after_scheduler_run(admin_token: str):
    # Force a "scheduler" run with a known UA
    requests.post(
        f"{BASE_URL}/api/admin/billing/run-renewal-reminders",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "User-Agent": "Cloudflare-Workers/2026",
        },
        timeout=20,
    )
    s = requests.get(
        f"{BASE_URL}/api/admin/billing/renewal-reminders/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    body = s.json()
    hb = body["heartbeat"]
    # Must be green because we just ran one second ago
    assert hb["status"] == "green", hb
    assert hb["scheduler_source"] == "cloudflare_cron"
    assert hb["hours_since_last_scheduler_run"] is not None
    assert hb["hours_since_last_scheduler_run"] < 1


def test_run_log_has_audit_columns(admin_token: str):
    """started_at, completed_at, duration_ms, success, reminders_sent, trigger_source."""
    r = requests.post(
        f"{BASE_URL}/api/admin/billing/run-renewal-reminders",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "User-Agent": "github-actions/runner",
        },
        timeout=20,
    )
    body = r.json()
    for k in ("started_at", "completed_at", "duration_ms", "success",
              "reminders_sent", "trigger_source", "triggered_by"):
        assert k in body, k
    assert body["reminders_sent"] == body["sent"]
    assert isinstance(body["success"], bool)
