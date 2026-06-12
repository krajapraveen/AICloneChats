"""Subscription lifecycle state machine + admin subscription history.

We cover:
  - Read-side state derivation for the common transitions:
      free → active → grace_period → expired
      active → pending_cancellation → cancelled (after period end)
      active → refunded
  - The user-facing /api/profile/subscription/state endpoint surfaces the
    derived state without any DB writes.
  - Admin endpoint returns lifetime totals + order history.
"""
from __future__ import annotations

import os
import sys
import uuid
import asyncio
import importlib
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


def _new_email() -> str:
    return f"sub_state_{uuid.uuid4().hex[:10]}@example.com"


def _register(email: str, password: str = "TestPass123!") -> dict:
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": password, "name": "State Tester"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _get_state(token: str) -> dict:
    r = requests.get(
        f"{BASE_URL}/api/profile/subscription/state",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _seed_order(user_id: str, plan_id: str, paid_at_offset_days: int, status: str = "paid") -> str:
    """Insert a synthetic payment_orders row directly."""
    order_id = f"order_test_{uuid.uuid4().hex[:14]}"
    paid_at = (datetime.now(timezone.utc) + timedelta(days=paid_at_offset_days)).isoformat()

    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.payment_orders.insert_one({
            "order_id": order_id,
            "user_id": user_id,
            "plan_id": plan_id,
            "status": status,
            "amount": 1499.0,
            "currency": "INR",
            "amount_inr": 1499.0,
            "credits_to_grant": 2500,
            "provider": "cashfree",
            "created_at": paid_at,
            "paid_at": paid_at if status == "paid" else None,
            "credited_at": paid_at if status == "paid" else None,
            "updated_at": paid_at,
        })
        if status == "paid":
            await db.users.update_one(
                {"user_id": user_id},
                {"$set": {"plan_id": plan_id, "plan_status": "active", "credits_balance": 2500}},
            )

    asyncio.new_event_loop().run_until_complete(_go())
    return order_id


def _set_cancel_flag(user_id: str, value: bool) -> None:
    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.users.update_one({"user_id": user_id}, {"$set": {"cancel_at_period_end": value}})
    asyncio.new_event_loop().run_until_complete(_go())


def _seed_refund(user_id: str, order_id: str) -> None:
    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.payment_refunds.insert_one({
            "refund_id": f"refund_{uuid.uuid4().hex[:14]}",
            "user_id": user_id, "order_id": order_id,
            "amount": 1499.0, "created_at": datetime.now(timezone.utc).isoformat(),
        })
    asyncio.new_event_loop().run_until_complete(_go())


@pytest.fixture(scope="module")
def admin_token() -> str:
    async def _mint():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        admin = await db.users.find_one({"email": ADMIN_EMAIL}, {"user_id": 1})
        if not admin:
            return None
        token = f"st_{uuid.uuid4().hex}{uuid.uuid4().hex}"
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        await db.user_sessions.insert_one({
            "session_token": token,
            "user_id": admin["user_id"],
            "source": "test-mint-admin",
            "created_at": _dt.now(_tz.utc).isoformat(),
            "expires_at": (_dt.now(_tz.utc) + _td(hours=1)).isoformat(),
        })
        return token
    t = asyncio.new_event_loop().run_until_complete(_mint())
    if not t:
        pytest.skip("admin user not seeded")
    return t


def test_state_free_for_new_account():
    reg = _register(_new_email())
    state = _get_state(reg["session_token"])
    # Fresh signup with REQUIRE_EMAIL_VERIFICATION_FOR_CHECKOUT off → "free";
    # otherwise → "pending_verification". Either is correct.
    assert state["state"] in ("free", "pending_verification"), state
    assert state["admin_unlimited"] is False


def test_state_active_after_recent_paid_order():
    reg = _register(_new_email())
    token = reg["session_token"]
    user_id = reg["user"]["user_id"]
    _seed_order(user_id, "pro", paid_at_offset_days=-2)
    state = _get_state(token)
    assert state["state"] == "active", state
    assert state["current_plan_id"] == "pro"
    assert state["expires_at"] is not None


def test_state_pending_cancellation_then_cancelled():
    # Pending cancellation while inside the paid window
    reg = _register(_new_email())
    token = reg["session_token"]
    user_id = reg["user"]["user_id"]
    _seed_order(user_id, "pro", paid_at_offset_days=-5)
    _set_cancel_flag(user_id, True)
    state = _get_state(token)
    assert state["state"] == "pending_cancellation", state

    # Separate user: paid 40 days ago + cancel flag → past expiry + grace → cancelled
    reg2 = _register(_new_email())
    token2 = reg2["session_token"]
    user_id2 = reg2["user"]["user_id"]
    _seed_order(user_id2, "pro", paid_at_offset_days=-40)
    _set_cancel_flag(user_id2, True)
    state2 = _get_state(token2)
    assert state2["state"] == "cancelled", state2


def test_state_grace_period():
    reg = _register(_new_email())
    token = reg["session_token"]
    user_id = reg["user"]["user_id"]
    # 31 days ago — just past 30d expiry, inside 3-day grace
    _seed_order(user_id, "pro", paid_at_offset_days=-31)
    state = _get_state(token)
    assert state["state"] == "grace_period", state
    assert state["grace_period_until"] is not None


def test_state_expired_after_grace():
    reg = _register(_new_email())
    token = reg["session_token"]
    user_id = reg["user"]["user_id"]
    _seed_order(user_id, "starter", paid_at_offset_days=-45)
    state = _get_state(token)
    assert state["state"] == "expired", state


def test_state_refunded():
    reg = _register(_new_email())
    token = reg["session_token"]
    user_id = reg["user"]["user_id"]
    order_id = _seed_order(user_id, "premium", paid_at_offset_days=-2)
    _seed_refund(user_id, order_id)
    state = _get_state(token)
    assert state["state"] == "refunded", state


def test_cancel_then_resume_flow():
    reg = _register(_new_email())
    token = reg["session_token"]
    user_id = reg["user"]["user_id"]
    _seed_order(user_id, "pro", paid_at_offset_days=-3)

    # Cancel
    r = requests.post(
        f"{BASE_URL}/api/profile/subscription/cancel",
        json={"confirm": True, "reason": "test"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"]["state"] == "pending_cancellation"

    # Resume
    r2 = requests.post(
        f"{BASE_URL}/api/profile/subscription/resume",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["state"]["state"] == "active"


def test_cancel_blocked_when_no_active_subscription():
    reg = _register(_new_email())
    token = reg["session_token"]
    r = requests.post(
        f"{BASE_URL}/api/profile/subscription/cancel",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "no_active_subscription"


def test_admin_user_subscription_summary(admin_token: str):
    # Build a user with one paid order
    reg = _register(_new_email())
    user_id = reg["user"]["user_id"]
    _seed_order(user_id, "ultimate", paid_at_offset_days=-1)

    r = requests.get(
        f"{BASE_URL}/api/admin/billing/users/{user_id}/subscription-summary",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["user_id"] == user_id
    assert body["state"]["state"] == "active"
    assert body["state"]["current_plan_id"] == "ultimate"
    assert body["lifetime"]["total_paid_orders"] == 1
    assert body["lifetime"]["total_revenue_inr"] == 1499.0
    assert body["lifetime"]["total_credits_purchased"] == 2500
    assert len(body["orders"]) == 1


def test_admin_user_search(admin_token: str):
    reg = _register(_new_email())
    email = reg["user"]["email"]
    r = requests.get(
        f"{BASE_URL}/api/admin/billing/users/search",
        params={"q": email.split("@")[0]},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 1
    assert any(u["email"] == email for u in body["users"])
