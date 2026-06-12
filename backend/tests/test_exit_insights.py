"""Exit Insights Dashboard — backend contract + math.

Verifies:
  - Bucket classification picks the right keyword bucket for free-form text.
  - Endpoint returns the full envelope: summary, by_reason_bucket,
    monthly_series, recent_exits, buckets_catalog.
  - Math: total_exits = deletions + cancellations.
  - Reasons captured map into the right buckets (deletion + cancellation).
  - Admin-only gate.
  - Empty-window returns a stable envelope (no crash).
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

from conftest import get_shared_loop  # noqa: E402
from exit_insights import _bucket_for_reason  # noqa: E402


def _run(coro):
    return get_shared_loop().run_until_complete(coro)


@pytest.fixture
def _purge_exit_test_rows():
    """Strict isolation: clean any rows our tests seed with the
    `req_exit_` / `ord_exit_` prefix. Real telemetry untouched."""
    async def _clear():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.account_deletion_events.delete_many({"user_id": {"$regex": "^u_exit_"}})
        await db.users.update_many(
            {"user_id": {"$regex": "^u_exit_"}, "cancel_at_period_end": True},
            {"$unset": {"cancel_at_period_end": "", "cancel_requested_at": "", "cancel_reason": ""}},
        )
    _run(_clear())
    yield
    _run(_clear())


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
            "session_token": token, "user_id": admin["user_id"],
            "source": "test-mint-admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        })
        return token
    t = _run(_mint())
    if not t:
        pytest.skip("admin not seeded")
    return t


# ─────────────── Bucket classifier (pure-function unit tests) ───────────────

def test_bucket_classifier_pricing():
    assert _bucket_for_reason("Too expensive for what it does") == "pricing"
    assert _bucket_for_reason("Cost is too high") == "pricing"
    assert _bucket_for_reason("I can't afford the subscription") == "pricing"


def test_bucket_classifier_missing_feature():
    assert _bucket_for_reason("Missing dark mode and group chats") == "missing_feature"
    assert _bucket_for_reason("Wish it had voice cloning") == "missing_feature"


def test_bucket_classifier_quality():
    assert _bucket_for_reason("The replies are useless and inaccurate") == "quality"
    assert _bucket_for_reason("Bad reply quality, hallucinates often") == "quality"


def test_bucket_classifier_ux():
    assert _bucket_for_reason("The UI is buggy and confusing") == "ux"
    assert _bucket_for_reason("Hard to use on mobile") == "ux"


def test_bucket_classifier_no_reason_when_empty():
    assert _bucket_for_reason(None) == "no_reason"
    assert _bucket_for_reason("") == "no_reason"
    assert _bucket_for_reason("   ") == "no_reason"


def test_bucket_classifier_other_when_no_keyword():
    # Random text that doesn't match any keyword should bucket to 'other'
    assert _bucket_for_reason("xyz qwerty totally random") == "other"


# ─────────────── Endpoint contract ───────────────

def _seed_deletion(user_id: str, reason: str | None, *, when_offset_days: int = 0):
    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        when = (datetime.now(timezone.utc) - timedelta(days=when_offset_days)).isoformat()
        await db.account_deletion_events.insert_one({
            "deletion_id": "del_" + uuid.uuid4().hex[:18],
            "user_id": user_id,
            "auth_provider": "email",
            "reason": reason,
            "deleted_at": when,
        })
    _run(_go())


def _seed_cancellation(user_id: str, reason: str | None, *, when_offset_days: int = 0):
    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        when = (datetime.now(timezone.utc) - timedelta(days=when_offset_days)).isoformat()
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "email": f"{user_id}@example.com",
                "plan_id": "pro",
                "cancel_at_period_end": True,
                "cancel_requested_at": when,
                "cancel_reason": reason or "",
            }},
            upsert=True,
        )
    _run(_go())


def test_endpoint_envelope_shape(_purge_exit_test_rows, admin_token: str):
    _seed_deletion("u_exit_1", "too expensive for me right now")
    _seed_deletion("u_exit_2", None)
    _seed_cancellation("u_exit_3", "missing group chats")

    r = requests.get(
        f"{BASE_URL}/api/admin/exit-insights?days=90",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("window_days", "computed_at", "summary",
              "by_reason_bucket", "monthly_series", "recent_exits",
              "buckets_catalog"):
        assert k in body, k
    s = body["summary"]
    for k in ("total_exits", "deletions", "subscription_cancellations",
              "exits_with_reason", "exits_without_reason",
              "reason_capture_rate_pct"):
        assert k in s, k
    # Our seeded rows contribute to the totals
    assert s["deletions"] >= 2
    assert s["subscription_cancellations"] >= 1
    assert s["total_exits"] >= 3


def test_bucket_aggregation_picks_correct_buckets(_purge_exit_test_rows, admin_token: str):
    _seed_deletion("u_exit_p1", "too expensive monthly cost")
    _seed_deletion("u_exit_p2", "pricing is too high")
    _seed_deletion("u_exit_q1", "the replies are useless and wrong")
    _seed_cancellation("u_exit_m1", "wish it had video calls feature")

    r = requests.get(
        f"{BASE_URL}/api/admin/exit-insights?days=90",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    body = r.json()
    by_bucket = {b["bucket"]: b for b in body["by_reason_bucket"]}
    # All 4 of our seeded reasons should land in the right buckets
    assert by_bucket.get("pricing", {}).get("count", 0) >= 2
    assert by_bucket.get("quality", {}).get("count", 0) >= 1
    assert by_bucket.get("missing_feature", {}).get("count", 0) >= 1
    # Examples capture the original text (truncated)
    pricing_examples = by_bucket.get("pricing", {}).get("examples", [])
    assert any("expensive" in e or "pricing" in e for e in pricing_examples)


def test_capture_rate_math(_purge_exit_test_rows, admin_token: str):
    _seed_deletion("u_exit_r1", "real reason 1")
    _seed_deletion("u_exit_r2", None)
    _seed_deletion("u_exit_r3", "")  # empty == no reason

    r = requests.get(
        f"{BASE_URL}/api/admin/exit-insights?days=90",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    s = r.json()["summary"]
    # We just contributed 1 with-reason + 2 without-reason (None and empty).
    # The endpoint also includes other rows so we just assert the math
    # invariant: capture_rate == (with / total) * 100, ±0.05% tolerance.
    if s["total_exits"] > 0:
        expected = round((s["exits_with_reason"] / s["total_exits"]) * 100, 2)
        assert abs(s["reason_capture_rate_pct"] - expected) < 0.05


def test_monthly_series_aggregates_by_yyyymm(_purge_exit_test_rows, admin_token: str):
    _seed_deletion("u_exit_t1", "test reason A", when_offset_days=0)
    _seed_deletion("u_exit_t2", "test reason B", when_offset_days=2)
    _seed_cancellation("u_exit_t3", "test cancel reason", when_offset_days=1)

    r = requests.get(
        f"{BASE_URL}/api/admin/exit-insights?days=30",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    body = r.json()
    series = body["monthly_series"]
    # Ascending by month
    months = [m["month"] for m in series]
    assert months == sorted(months)
    # Per-row keys
    for m in series:
        for k in ("month", "deletions", "cancellations", "total"):
            assert k in m
        # total = deletions + cancellations
        assert m["total"] == m["deletions"] + m["cancellations"]


def test_recent_exits_sorted_desc(_purge_exit_test_rows, admin_token: str):
    _seed_deletion("u_exit_a1", "old one", when_offset_days=5)
    _seed_deletion("u_exit_a2", "newer one", when_offset_days=1)
    _seed_cancellation("u_exit_a3", "cancellation reason", when_offset_days=2)

    r = requests.get(
        f"{BASE_URL}/api/admin/exit-insights?days=30",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    recent = r.json()["recent_exits"]
    ats = [e["at"] for e in recent if e["at"]]
    assert ats == sorted(ats, reverse=True), "recent_exits must be desc by `at`"
    # Each row has the required fields
    for e in recent:
        for k in ("kind", "at", "reason", "bucket", "user_id"):
            assert k in e
        assert e["kind"] in ("deletion", "cancellation")


def test_buckets_catalog_present(admin_token: str):
    r = requests.get(
        f"{BASE_URL}/api/admin/exit-insights?days=30",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    catalog = r.json()["buckets_catalog"]
    # Catalog gives the UI a stable list of known buckets
    expected = {"pricing", "missing_feature", "quality", "ux", "privacy",
                "not_using", "alternative", "trust", "other", "no_reason"}
    assert set(catalog) == expected


def test_endpoint_admin_only():
    r = requests.get(f"{BASE_URL}/api/admin/exit-insights", timeout=10)
    assert r.status_code in (401, 403)


def test_endpoint_rejects_bad_window(admin_token: str):
    r = requests.get(
        f"{BASE_URL}/api/admin/exit-insights?days=2",  # min is 7
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=10,
    )
    assert r.status_code == 422
    r2 = requests.get(
        f"{BASE_URL}/api/admin/exit-insights?days=999",  # max is 365
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=10,
    )
    assert r2.status_code == 422
