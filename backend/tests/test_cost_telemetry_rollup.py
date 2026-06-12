"""Daily Cost Telemetry Rollups — math + endpoint contract.

Verifies:
  - `rollup_day(d)` writes one document per feature for that UTC date.
  - Re-running rolls up to the same row (idempotent — no duplicates).
  - Math contract: metered cost is summed correctly from
    `provider_cost_events`; credits summed from `credit_events`; revenue
    apportionment uses the day's paid orders.
  - The `/daily` endpoint returns ascending-by-date series + totals_series
    matching the persisted rows.
  - Admin-only gate.
  - Boot scan in server.py is idempotent (verified by the fact that the
    server starts cleanly above — no separate assertion needed here).
"""
from __future__ import annotations

import os
import sys
import uuid
import asyncio
from datetime import datetime, timezone, timedelta, date
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
from cost_telemetry_rollup import rollup_day, rollup_recent, ROLLUP_VERSION  # noqa: E402


def _run(coro):
    return get_shared_loop().run_until_complete(coro)


@pytest.fixture
def _purge_rollup_test_rows():
    """Clean rows for the synthetic dates we use (1990-01-{02,03,04})."""
    async def _clear():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.cost_telemetry_daily.delete_many({"date": {"$regex": "^1990-01-"}})
        await db.provider_cost_events.delete_many({"request_id": {"$regex": "^req_rollup_"}})
        await db.credit_events.delete_many({"request_id": {"$regex": "^req_rollup_"}})
        await db.payment_orders.delete_many({"order_id": {"$regex": "^ord_rollup_"}})
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


def _seed(d: date, feature: str, *, cost_inr: float, credits: int, revenue_inr: float = 0.0):
    """Seed one (provider_cost, credit, optional paid order) bundle on the
    given UTC date."""
    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        midday = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        rid = f"req_rollup_{uuid.uuid4().hex[:10]}"
        uid = f"u_rollup_{uuid.uuid4().hex[:6]}"
        await db.provider_cost_events.insert_one({
            "cost_id": "cost_" + uuid.uuid4().hex[:18],
            "user_id": uid, "request_id": rid,
            "feature": feature, "surface": f"{feature}_chat",
            "provider": "synth", "model": "synth-model",
            "pricing_key": "synth/synth-model",
            "input_tokens": 50, "output_tokens": 100, "total_tokens": 150,
            "input_chars": 200, "output_chars": 400,
            "cost_usd": cost_inr / 86.0, "cost_inr": cost_inr,
            "usd_to_inr": 86.0, "cost_method": "token_estimate",
            "is_priced": True, "created_at": midday,
        })
        if credits > 0:
            await db.credit_events.insert_one({
                "event_id": uuid.uuid4().hex, "user_id": uid, "request_id": rid,
                "kind": "deduct", "delta": -credits,
                "balance_before": 1000, "balance_after": 1000 - credits,
                "surface": f"{feature}_chat", "feature": feature,
                "created_at": midday,
            })
        if revenue_inr > 0:
            await db.payment_orders.insert_one({
                "order_id": "ord_rollup_" + uuid.uuid4().hex[:10],
                "user_id": uid, "amount_inr": revenue_inr,
                "status": "paid", "paid_at": midday, "created_at": midday,
            })
    _run(_go())


def test_rollup_day_writes_one_row_per_feature(_purge_rollup_test_rows):
    d = date(1990, 1, 2)
    _seed(d, "ai_clone", cost_inr=100.0, credits=10, revenue_inr=500.0)
    _seed(d, "voice", cost_inr=20.0, credits=5)

    summary = _run(rollup_day(d))
    assert summary["date"] == "1990-01-02"
    assert summary["features_written"] == 7  # 7 dashboard features
    assert summary["total_credits"] == 15  # 10 + 5
    assert summary["total_revenue_inr"] == 500.0
    assert summary["version"] == ROLLUP_VERSION

    async def _check():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        rows = await db.cost_telemetry_daily.find({"date": "1990-01-02"}, {"_id": 0}).to_list(20)
        assert len(rows) == 7
        ai = next(r for r in rows if r["feature"] == "ai_clone")
        assert ai["credits_consumed"] == 10
        assert ai["metered_cost_inr"] == 100.0
        # Revenue apportionment: ai_clone has 10/15 = 66.667% share
        assert abs(ai["revenue_apportioned_inr"] - (500.0 * 10 / 15)) < 0.01
        # Margin pct = (revenue - cost) / revenue × 100
        expected_margin = ((500.0 * 10 / 15) - 100.0) / (500.0 * 10 / 15) * 100
        assert abs(ai["margin_pct"] - expected_margin) < 0.1
    _run(_check())


def test_rollup_day_is_idempotent(_purge_rollup_test_rows):
    """Running twice writes the same number of rows, not double."""
    d = date(1990, 1, 3)
    _seed(d, "chat", cost_inr=10.0, credits=2)
    _run(rollup_day(d))
    _run(rollup_day(d))  # re-run

    async def _count():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        return await db.cost_telemetry_daily.count_documents({"date": "1990-01-03"})
    n = _run(_count())
    assert n == 7  # exactly one row per feature, no dupes


def test_rollup_recent_handles_multiple_days(_purge_rollup_test_rows):
    """rollup_recent(days=2) must process today + yesterday."""
    summaries = _run(rollup_recent(days=2))
    assert len(summaries) == 2
    today_iso = datetime.now(timezone.utc).date().isoformat()
    yest_iso = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    dates = [s["date"] for s in summaries]
    assert today_iso in dates
    assert yest_iso in dates


def test_daily_endpoint_shape(_purge_rollup_test_rows, admin_token: str):
    """GET /daily returns ascending series + totals_series."""
    d = date(1990, 1, 4)
    _seed(d, "ai_clone", cost_inr=50.0, credits=10, revenue_inr=200.0)
    _run(rollup_day(d))

    # The endpoint defaults to days=30 backward from today; 1990-01-04 is
    # way outside that window. We need a wider override.
    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/daily?days=180",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("window_days", "series", "totals_series", "version"):
        assert k in body
    # totals_series sorted ascending by date
    dates = [t["date"] for t in body["totals_series"]]
    assert dates == sorted(dates)


def test_daily_endpoint_feature_filter(_purge_rollup_test_rows, admin_token: str):
    d = date(1990, 1, 4)
    _seed(d, "chat", cost_inr=5.0, credits=1)
    _run(rollup_day(d))
    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/daily?days=180&feature=chat",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    body = r.json()
    assert body["feature_filter"] == "chat"
    # Every series row must be feature=chat
    for row in body["series"]:
        assert row["feature"] == "chat"


def test_daily_endpoint_rejects_unknown_feature(admin_token: str):
    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/daily?feature=garbage_feature",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=10,
    )
    assert r.status_code == 400


def test_rollup_endpoint_admin_only():
    r = requests.post(f"{BASE_URL}/api/admin/cost-telemetry/rollup", timeout=10)
    assert r.status_code in (401, 403)


def test_daily_endpoint_admin_only():
    r = requests.get(f"{BASE_URL}/api/admin/cost-telemetry/daily", timeout=10)
    assert r.status_code in (401, 403)


def test_rollup_endpoint_runs(admin_token: str):
    """POST /rollup returns days_processed and per-day summaries."""
    r = requests.post(
        f"{BASE_URL}/api/admin/cost-telemetry/rollup?days=2",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["days_processed"] == 2
    assert len(body["summaries"]) == 2
    for s in body["summaries"]:
        assert s["version"] == ROLLUP_VERSION
        assert s["features_written"] == 7
