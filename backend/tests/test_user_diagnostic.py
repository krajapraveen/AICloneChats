"""User diagnostic endpoint — admin-only ground-truth for the
"paid but UI says free" report category."""
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
    tok = _run(_mint())
    if not tok:
        pytest.skip("admin not seeded")
    return tok


def test_diagnostic_admin_only():
    r = requests.get(f"{BASE_URL}/api/admin/billing/diagnostic/by-email/anyone@example.com", timeout=10)
    assert r.status_code in (401, 403)


def test_diagnostic_unknown_email_404(admin_token: str):
    r = requests.get(
        f"{BASE_URL}/api/admin/billing/diagnostic/by-email/nope-{uuid.uuid4().hex[:6]}@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=10,
    )
    assert r.status_code == 404
    detail = r.json().get("detail")
    # FastAPI HTTPException wraps the dict — accept either shape
    if isinstance(detail, dict):
        assert detail.get("code") == "user_not_found"


def test_diagnostic_envelope_shape(admin_token: str):
    r = requests.get(
        f"{BASE_URL}/api/admin/billing/diagnostic/by-email/{ADMIN_EMAIL}",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("user", "derived_credit_state", "orders", "credit_events",
              "funnel_events", "paywall_events", "subscription_transitions",
              "last_paid_order_webhook_arrivals", "consistency_check", "computed_at"):
        assert k in body, f"missing key {k}"
    # No password hash leaks
    assert "password_hash" not in body["user"]
    assert "reset_token_hash" not in body["user"]
    # Consistency check always has at least one entry
    assert isinstance(body["consistency_check"], list)
    assert len(body["consistency_check"]) >= 1
    for c in body["consistency_check"]:
        assert c["level"] in ("ok", "info", "warning", "critical")
        assert c["name"] and c["message"]


def test_diagnostic_detects_paid_but_free_drift(admin_token: str):
    """Seed the exact bug pattern: a paid order exists, but the user doc
    still has plan_id='free'. The endpoint must flag this with a 'critical'
    consistency check."""
    test_email = f"diag_drift_{uuid.uuid4().hex[:8]}@example.com"
    uid = f"u_diag_{uuid.uuid4().hex[:8]}"

    async def _seed():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        now = datetime.now(timezone.utc).isoformat()
        await db.users.insert_one({
            "user_id": uid, "email": test_email,
            "plan_id": "free", "plan_status": "",
            "credits_balance": 0,
            "email_verified": True,
            "created_at": now,
        })
        await db.payment_orders.insert_one({
            "order_id": f"ord_diag_{uuid.uuid4().hex[:10]}",
            "user_id": uid, "status": "paid",
            "plan_id": "starter_chat", "amount_inr": 499,
            "amount": 499, "currency": "INR",
            "paid_at": now, "created_at": now,
        })

    async def _cleanup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.users.delete_one({"user_id": uid})
        await db.payment_orders.delete_many({"user_id": uid})

    _run(_seed())
    try:
        r = requests.get(
            f"{BASE_URL}/api/admin/billing/diagnostic/by-email/{test_email}",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        check_names = [c["name"] for c in body["consistency_check"]]
        assert "paid_order_but_user_plan_free" in check_names
        # Should be critical
        critical = next(c for c in body["consistency_check"] if c["name"] == "paid_order_but_user_plan_free")
        assert critical["level"] == "critical"
        # No grant rows either → should also flag paid_order_but_no_credit_grant
        assert "paid_order_but_no_credit_grant" in check_names
    finally:
        _run(_cleanup())
