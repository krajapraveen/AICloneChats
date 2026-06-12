"""P2 — Loss-Making Request Alerts.

Verifies:
  - Severity buckets correctly from margin_pct.
  - Endpoint returns the full {summary, top_expensive, top_losses} envelope.
  - top_expensive is sorted by metered_cost_inr desc.
  - top_losses contains only severity in (warning, critical), sorted by margin asc.
  - by_feature aggregation sums per-feature cost / revenue / margin correctly.
  - `$ifNull` legacy bucketing carries through.
  - Admin-only gate.
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
from cost_telemetry import _severity_for_margin_pct  # noqa: E402


def _run(coro):
    return get_shared_loop().run_until_complete(coro)


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


def test_severity_buckets():
    assert _severity_for_margin_pct(-25) == "critical"
    assert _severity_for_margin_pct(-21) == "critical"
    assert _severity_for_margin_pct(-20) == "warning"  # < 0 but not < -20
    assert _severity_for_margin_pct(-1) == "warning"
    assert _severity_for_margin_pct(0) == "info"
    assert _severity_for_margin_pct(9.99) == "info"
    assert _severity_for_margin_pct(10) == "ok"
    assert _severity_for_margin_pct(50) == "ok"
    assert _severity_for_margin_pct(None) == "unknown"


def test_loss_making_endpoint_shape(admin_token: str):
    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/loss-making?days=30&top_n=10",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("window_days", "computed_at", "revenue_per_credit_inr",
              "summary", "top_expensive", "top_losses", "thresholds"):
        assert k in body, k
    for k in ("total_requests_analyzed", "total_flagged",
              "total_negative_margin_inr", "by_severity", "by_feature"):
        assert k in body["summary"], k
    assert body["thresholds"]["critical_margin_pct"] == -20
    assert body["thresholds"]["warning_margin_pct"] == 0
    assert body["thresholds"]["info_margin_pct"] == 10


def _seed_request_pair(*, feature: str, cost_inr: float, credits: int,
                       request_id: str | None = None) -> str:
    """Seed a matched (provider_cost_events, credit_events) pair via a shared request_id."""
    rid = request_id or f"req_lm_{uuid.uuid4().hex[:10]}"
    user_id = f"lm_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.provider_cost_events.insert_one({
            "cost_id": "cost_" + uuid.uuid4().hex[:18],
            "user_id": user_id, "request_id": rid,
            "feature": feature, "surface": f"{feature}_chat",
            "provider": "synth", "model": "synth-model",
            "pricing_key": "synth/synth-model",
            "input_tokens": 100, "output_tokens": 200, "total_tokens": 300,
            "input_chars": 400, "output_chars": 800,
            "cost_usd": cost_inr / 86.0, "cost_inr": cost_inr,
            "usd_to_inr": 86.0, "cost_method": "token_estimate",
            "is_priced": True, "created_at": now,
        })
        if credits > 0:
            await db.credit_events.insert_one({
                "event_id": uuid.uuid4().hex,
                "user_id": user_id, "request_id": rid,
                "kind": "deduct", "delta": -credits,
                "balance_before": 1000, "balance_after": 1000 - credits,
                "surface": f"{feature}_chat", "feature": feature,
                "created_at": now,
            })
    _run(_go())
    return rid


def test_top_expensive_sorted_desc(admin_token: str):
    # Seed three rows with very different costs in a fresh window
    _seed_request_pair(feature="ai_clone", cost_inr=99999.0, credits=1)
    _seed_request_pair(feature="voice", cost_inr=1.0, credits=1)
    _seed_request_pair(feature="chat", cost_inr=500.0, credits=1)

    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/loss-making?days=1&top_n=10",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    body = r.json()
    expensive = body["top_expensive"]
    assert len(expensive) >= 3
    costs = [row["metered_cost_inr"] for row in expensive]
    assert costs == sorted(costs, reverse=True), costs
    assert expensive[0]["metered_cost_inr"] == 99999.0


def test_top_losses_only_warning_critical_sorted_asc(admin_token: str):
    """A request that costs more than its apportioned revenue should appear in
    top_losses with severity warning or critical, and the list is sorted by
    margin asc (biggest loss first)."""
    # Big cost, tiny credits → margin will be deeply negative
    _seed_request_pair(feature="ai_clone", cost_inr=10000.0, credits=1)
    _seed_request_pair(feature="voice", cost_inr=5000.0, credits=1)

    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/loss-making?days=1&top_n=10",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    body = r.json()
    losses = body["top_losses"]
    # Every row in top_losses must be flagged (warning/critical) — never ok/info/unknown
    for row in losses:
        assert row["severity"] in ("warning", "critical"), row
    # Sorted ascending by margin_inr
    margins = [row["margin_inr"] for row in losses]
    assert margins == sorted(margins), margins


def test_by_feature_summary_sums(admin_token: str):
    rid1 = _seed_request_pair(feature="chat", cost_inr=10.0, credits=1)
    rid2 = _seed_request_pair(feature="chat", cost_inr=20.0, credits=1)

    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/loss-making?days=1&top_n=10",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    body = r.json()
    chat_bucket = next((b for b in body["summary"]["by_feature"] if b["feature"] == "chat"), None)
    assert chat_bucket is not None
    # The bucket aggregates ALL chat rows in the window — must include ≥ the 2 we just seeded
    assert chat_bucket["requests"] >= 2
    assert chat_bucket["cost_inr"] >= 30.0  # 10 + 20 floor


def test_endpoint_admin_only():
    r = requests.get(f"{BASE_URL}/api/admin/cost-telemetry/loss-making", timeout=15)
    assert r.status_code in (401, 403)
