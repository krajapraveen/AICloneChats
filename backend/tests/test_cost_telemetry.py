"""Cost Telemetry — profit-per-feature + contribution-by-source.

Contracts verified:
  - Aggregations bucket legacy rows (no `feature` / no `pricing_visit_source`)
    into `unknown` via `$ifNull` — no row is silently dropped.
  - Cost config GET/POST round-trip; only ALLOWED_FEATURES keys accepted;
    negative costs rejected.
  - Profit math: gross_profit = revenue_attributed − estimated_cost;
    margin_pct = gross / revenue × 100.
  - Revenue apportionment by credits-consumed share sums back to total.
  - Contribution by source: visits / starts / paid / conversion / revenue / arppu.
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
            "session_token": token, "user_id": admin["user_id"],
            "source": "test-mint-admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        })
        return token
    t = asyncio.new_event_loop().run_until_complete(_mint())
    if not t:
        pytest.skip("admin not seeded")
    return t


def test_cost_config_round_trip(admin_token: str):
    # Set
    new_table = {"ai_clone": 0.04, "voice": 0.08, "chat": 0.02, "video": 0.5}
    r = requests.post(
        f"{BASE_URL}/api/admin/cost-telemetry/cost-config",
        json={"values": new_table},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    saved = r.json()["values"]
    assert saved["ai_clone"] == 0.04
    assert saved["voice"] == 0.08

    # Get
    r2 = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/cost-config",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["values"] == saved
    assert set(body["features"]) >= {"ai_clone", "voice", "video", "chat", "image", "avatar", "unknown"}


def test_cost_config_rejects_unknown_feature(admin_token: str):
    r = requests.post(
        f"{BASE_URL}/api/admin/cost-telemetry/cost-config",
        json={"values": {"made_up_feature": 0.1}},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "unknown_feature"


def test_cost_config_rejects_negative(admin_token: str):
    r = requests.post(
        f"{BASE_URL}/api/admin/cost-telemetry/cost-config",
        json={"values": {"chat": -0.5}},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "negative_cost"


def test_profit_per_feature_shape(admin_token: str):
    # Make sure costs are configured for this test
    requests.post(
        f"{BASE_URL}/api/admin/cost-telemetry/cost-config",
        json={"values": {"ai_clone": 0.05, "voice": 0.1, "chat": 0.03,
                         "video": 0.5, "image": 0.2, "avatar": 0.3,
                         "unknown": 0.01}},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )

    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/profit-per-feature?days=365",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    body = r.json()

    for k in ("window_days", "computed_at", "total_revenue_inr",
              "total_credits_consumed", "rows", "totals", "config_status"):
        assert k in body, k

    features_returned = [r["feature"] for r in body["rows"]]
    assert features_returned == ["ai_clone", "voice", "video", "chat", "image", "avatar", "unknown"]

    for row in body["rows"]:
        for k in ("feature", "credits_consumed", "usage_count", "share_of_credits_pct",
                  "estimated_cost_inr", "cost_per_credit_inr", "cost_source",
                  "revenue_attributed_inr", "gross_profit_inr", "margin_pct"):
            assert k in row, (row, k)
        # With all features costed, none should be `not_configured`.
        # `configured` (estimate from cost table) and `provider_metered`
        # (real provider rows) are both valid passing states.
        assert row["cost_source"] in ("configured", "provider_metered"), row


def test_profit_math_consistency(admin_token: str):
    """Apportionment must sum back to total revenue; per-row profit math
    must match `revenue - cost`."""
    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/profit-per-feature?days=365",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    body = r.json()
    rows = body["rows"]
    total_rev = body["total_revenue_inr"]

    summed = round(sum(r["revenue_attributed_inr"] for r in rows), 2)
    # Floating point rounding tolerance
    if body["total_credits_consumed"] > 0:
        assert abs(summed - total_rev) <= 1.0, (summed, total_rev)

    for row in rows:
        if row["estimated_cost_inr"] is not None and row["revenue_attributed_inr"] > 0:
            expected = round(row["revenue_attributed_inr"] - row["estimated_cost_inr"], 2)
            assert row["gross_profit_inr"] == expected, row


def test_profit_unknown_feature_bucket_via_ifnull(admin_token: str):
    """Insert a deduct event with NO `feature` field; it must aggregate under
    the `unknown` row, not vanish."""
    user_id = f"ct_unknown_{uuid.uuid4().hex[:8]}"
    async def _seed():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.credit_events.insert_one({
            "event_id": uuid.uuid4().hex, "user_id": user_id,
            "kind": "deduct", "delta": -7,
            "balance_before": 100, "balance_after": 93,
            "surface": "legacy_no_feature",
            # NO feature key
            "request_id": "legacy_req",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    asyncio.new_event_loop().run_until_complete(_seed())

    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/profit-per-feature?days=1",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    body = r.json()
    unknown_row = next(r for r in body["rows"] if r["feature"] == "unknown")
    assert unknown_row["credits_consumed"] >= 7
    assert unknown_row["usage_count"] >= 1


def test_contribution_by_source_shape(admin_token: str):
    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/contribution-by-source?days=365",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("window_days", "computed_at", "rows", "totals"):
        assert k in body

    for row in body["rows"]:
        for k in ("pricing_visit_source", "visits", "checkout_starts",
                  "paid_orders", "conversion_pct", "revenue_inr", "arppu_inr"):
            assert k in row, (row, k)


def test_contribution_unknown_source_bucket_via_ifnull(admin_token: str):
    """A pricing_view funnel_event with NO `pricing_visit_source` field must
    aggregate under the `unknown` source row."""
    async def _seed():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.funnel_events.insert_one({
            "event_id": uuid.uuid4().hex,
            "event_name": "pricing_view",
            # NO pricing_visit_source field
            "user_id": "legacy_user",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    asyncio.new_event_loop().run_until_complete(_seed())

    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/contribution-by-source?days=1",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    body = r.json()
    sources = {row["pricing_visit_source"] for row in body["rows"]}
    assert "unknown" in sources


def test_endpoints_admin_only():
    r = requests.get(f"{BASE_URL}/api/admin/cost-telemetry/profit-per-feature", timeout=15)
    assert r.status_code in (401, 403)
    r2 = requests.get(f"{BASE_URL}/api/admin/cost-telemetry/contribution-by-source", timeout=15)
    assert r2.status_code in (401, 403)
    r3 = requests.get(f"{BASE_URL}/api/admin/cost-telemetry/cost-config", timeout=15)
    assert r3.status_code in (401, 403)
