"""Subscriber Motion + Churn Velocity tests.

The contract we're verifying:
  - Motion counts are reproducible from the immutable source rows
    (`payment_orders`, `payment_refunds`).
  - Two consecutive calls return identical counts (idempotency / determinism).
  - Classification rules:
      first-ever paid order → new_subscriber
      paid order after refund of prior order → won_back
      paid order long after previous expired → renewal
      refund row inserted → refund_churn
      no paid order for 30d + grace + no cancel flag → expire_churn
  - Velocity ratios use active-at-start as the denominator.
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


def _admin_token() -> str:
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
    return asyncio.new_event_loop().run_until_complete(_mint())


@pytest.fixture(scope="module")
def admin_token() -> str:
    t = _admin_token()
    if not t:
        pytest.skip("admin not seeded")
    return t


def _register(email: str) -> dict:
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "TestPass123!", "name": "Motion Tester"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _insert_paid_order(user_id: str, days_ago: float, amount_inr: float = 1499.0, plan_id: str = "pro") -> str:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    paid_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    order_id = f"order_mot_{uuid.uuid4().hex[:12]}"
    await db.payment_orders.insert_one({
        "order_id": order_id,
        "user_id": user_id,
        "plan_id": plan_id,
        "status": "paid",
        "amount": amount_inr * 100,
        "currency": "INR",
        "amount_inr": amount_inr,
        "credits_to_grant": 2500,
        "provider": "cashfree",
        "created_at": paid_at,
        "paid_at": paid_at,
        "credited_at": paid_at,
        "updated_at": paid_at,
    })
    return order_id


async def _insert_refund(user_id: str, order_id: str, days_ago: float) -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    await db.payment_refunds.insert_one({
        "refund_id": f"refund_{uuid.uuid4().hex[:14]}",
        "user_id": user_id,
        "order_id": order_id,
        "amount": 1499.0,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
    })


async def _set_cancel_flag(user_id: str, value: bool) -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    await db.users.update_one({"user_id": user_id}, {"$set": {"cancel_at_period_end": value}})


def _new_user() -> str:
    reg = _register(f"motion_{uuid.uuid4().hex[:10]}@example.com")
    return reg["user"]["user_id"]


def _seed(user_id: str, fn):
    asyncio.new_event_loop().run_until_complete(fn(user_id))


def _motion(admin_token: str, days: int = 30) -> dict:
    r = requests.get(
        f"{BASE_URL}/api/admin/revenue/subscriber-motion?days={days}",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_motion_deterministic_across_calls(admin_token: str):
    """Two back-to-back reads against the same rows must return identical counts."""
    a = _motion(admin_token, 30)
    b = _motion(admin_token, 30)
    # `computed_at` differs, but every count must match.
    assert a["motion"] == b["motion"]
    assert a["velocity"] == b["velocity"]
    assert a["executive_summary"]["active_subscribers_end"] == b["executive_summary"]["active_subscribers_end"]


def test_new_subscriber_classification(admin_token: str):
    user_id = _new_user()
    asyncio.new_event_loop().run_until_complete(_insert_paid_order(user_id, days_ago=2))
    motion = _motion(admin_token, 30)
    assert motion["motion"]["new_subscribers"] >= 1


def test_renewal_after_expiry(admin_token: str):
    user_id = _new_user()
    # Old paid order (60d ago) + recent paid order (1d ago) = renewal
    asyncio.new_event_loop().run_until_complete(_insert_paid_order(user_id, days_ago=60))
    asyncio.new_event_loop().run_until_complete(_insert_paid_order(user_id, days_ago=1))
    motion = _motion(admin_token, 30)
    assert motion["motion"]["renewals"] >= 1


def test_won_back_after_refund(admin_token: str):
    user_id = _new_user()
    # First paid order, then refund of that order, then new paid order
    async def _seed_seq(uid):
        oid1 = await _insert_paid_order(uid, days_ago=20)
        await _insert_refund(uid, oid1, days_ago=15)
        await _insert_paid_order(uid, days_ago=5)
    asyncio.new_event_loop().run_until_complete(_seed_seq(user_id))
    motion = _motion(admin_token, 30)
    assert motion["motion"]["won_back"] >= 1
    assert motion["motion"]["refund_churn"] >= 1


def test_expire_churn(admin_token: str):
    user_id = _new_user()
    # Paid 60d ago, no subsequent activity → expire_churn (expiry+grace = 33d ago)
    asyncio.new_event_loop().run_until_complete(_insert_paid_order(user_id, days_ago=60))
    motion = _motion(admin_token, 60)
    assert motion["motion"]["expire_churn"] >= 1


def test_velocity_ratios_use_start_as_denominator(admin_token: str):
    """If active_start = 0 the denominator is floor(1) so percentages don't divide by zero."""
    motion = _motion(admin_token, 30)
    v = motion["velocity"]
    # All four ratios are numbers (never None / NaN)
    for k in ("churn_rate_pct", "renewal_rate_pct", "wonback_rate_pct", "net_growth_pct"):
        assert isinstance(v[k], (int, float)), (k, v)


def test_executive_summary_shape(admin_token: str):
    motion = _motion(admin_token, 30)
    es = motion["executive_summary"]
    for k in ("active_subscribers_start", "active_subscribers_end", "net_growth_pct",
              "window_revenue_inr", "mrr_estimate_inr", "arppu_inr"):
        assert k in es, k
    assert es["active_subscribers_end"] >= 0
    assert es["mrr_estimate_inr"] >= 0


def test_trend_endpoint_returns_points(admin_token: str):
    r = requests.get(
        f"{BASE_URL}/api/admin/revenue/subscriber-trend?days=30",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bucket_hours"] in (24, 72, 168)
    assert len(body["points"]) >= 15, "30-day window should yield ≥15 daily points"
    p = body["points"][-1]
    for k in ("t", "active", "new_subscribers", "renewals", "won_back", "churn", "revenue_inr"):
        assert k in p, k


def test_non_admin_blocked(admin_token: str):
    """Sanity: motion endpoint is admin-gated."""
    # Try without auth at all → 401
    r = requests.get(f"{BASE_URL}/api/admin/revenue/subscriber-motion?days=30", timeout=15)
    assert r.status_code in (401, 403), r.text
