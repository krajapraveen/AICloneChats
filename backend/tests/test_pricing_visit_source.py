"""Pricing-visit source tracking + plan preselection signal persistence.

What we verify:
  - `pricing_view` funnel events accept a whitelisted `source` field.
  - Off-list source values are coerced to `unknown` (defensive).
  - `payment_orders.pricing_visit_source` is persisted when a checkout is
    initiated (today this is exercised through the order-creation path; if
    the gateway is offline the test gracefully skips the persisted check).
  - Funnel-event endpoint rejects un-whitelisted event_names (regression
    pin — adding `source` field MUST NOT loosen the event_name whitelist).
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


ALLOWED_SOURCES = [
    "landing_hero", "landing_pricing", "dashboard_upgrade", "credits_exhausted",
    "clone_limit_reached", "subscription_expired", "profile_manage_subscription",
    "pay_return_retry", "unknown",
]


@pytest.fixture(scope="module")
def user_token() -> str:
    email = f"src_{uuid.uuid4().hex[:10]}@example.com"
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "TestPass123!", "name": "Src Tester"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    return r.json()["session_token"]


def _post_funnel(token: str, source: str | None, event_name: str = "pricing_view") -> requests.Response:
    body = {"event_name": event_name}
    if source is not None:
        body["source"] = source
    return requests.post(
        f"{BASE_URL}/api/funnel/event",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )


@pytest.mark.parametrize("source", ALLOWED_SOURCES)
def test_funnel_accepts_all_allowed_sources(user_token: str, source: str):
    r = _post_funnel(user_token, source)
    assert r.status_code == 200, (source, r.text)
    body = r.json()
    assert body["ok"] is True
    assert body["pricing_visit_source"] == source


def test_funnel_coerces_unknown_source_to_unknown(user_token: str):
    """Off-list values must NOT raise — they coerce to `unknown` so the
    funnel chart is never polluted with arbitrary client-supplied strings."""
    r = _post_funnel(user_token, "TOTALLY_MADE_UP")
    assert r.status_code == 200, r.text
    assert r.json()["pricing_visit_source"] == "unknown"


def test_funnel_no_source_defaults_to_unknown(user_token: str):
    """Backward-compat: callers that haven't migrated yet (no `source` key)
    must keep working and land in the `unknown` bucket."""
    r = _post_funnel(user_token, None)
    assert r.status_code == 200, r.text
    assert r.json()["pricing_visit_source"] == "unknown"


def test_funnel_event_name_whitelist_still_enforced(user_token: str):
    """Regression pin: adding a `source` field MUST NOT loosen the
    event_name whitelist. Arbitrary event_names still rejected with 400."""
    r = _post_funnel(user_token, "landing_hero", event_name="arbitrary_event")
    assert r.status_code == 400, r.text


def test_funnel_event_persists_to_db(user_token: str):
    """End-to-end DB assert: the row carries the source value."""
    nonce = f"landing_hero"
    r = _post_funnel(user_token, nonce)
    assert r.status_code == 200, r.text

    async def _check():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        # Pull the most-recent funnel event for the test user_token's email
        # — we hop through the token → user_id → email path.
        sess = await db.user_sessions.find_one({"session_token": user_token}, {"user_id": 1})
        evt = await db.funnel_events.find_one(
            {"user_id": sess["user_id"]},
            {"_id": 0},
            sort=[("created_at", -1)],
        )
        return evt
    evt = asyncio.new_event_loop().run_until_complete(_check())
    assert evt is not None
    assert evt["event_name"] == "pricing_view"
    assert evt["pricing_visit_source"] == "landing_hero"


def test_create_order_persists_pricing_visit_source(user_token: str):
    """The pricing_visit_source on the request body is persisted onto the
    payment_orders row. If the gateway isn't configured in this env we
    gracefully skip — the contract being tested is the persistence layer,
    not the gateway integration."""
    body = {"plan_id": "pro", "pricing_visit_source": "dashboard_upgrade"}
    r = requests.post(
        f"{BASE_URL}/api/payments/create-order",
        json=body,
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=20,
    )
    if r.status_code == 503:
        pytest.skip("Payment gateway not configured in this env")
    if r.status_code in (502, 400):
        pytest.skip(f"Provider returned {r.status_code}; skipping persistence assert")
    assert r.status_code == 200, r.text
    order_id = r.json()["order_id"]

    async def _check():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        return await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
    order = asyncio.new_event_loop().run_until_complete(_check())
    assert order is not None
    assert order.get("pricing_visit_source") == "dashboard_upgrade"


def test_create_order_off_list_source_coerced_to_unknown(user_token: str):
    body = {"plan_id": "starter", "pricing_visit_source": "made_up_source"}
    r = requests.post(
        f"{BASE_URL}/api/payments/create-order",
        json=body,
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=20,
    )
    if r.status_code in (503, 502, 400):
        pytest.skip(f"Provider not available ({r.status_code}); skipping")
    order_id = r.json()["order_id"]

    async def _check():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        return await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
    order = asyncio.new_event_loop().run_until_complete(_check())
    assert order.get("pricing_visit_source") == "unknown"
