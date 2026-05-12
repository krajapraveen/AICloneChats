"""
Regression: /api/payments/order/:id status resolution & PaymentReturn UX contract.

Bug history (2026-05-11):
  When a user clicked Leave/Cancel on Cashfree's checkout, the order at
  Cashfree remained `ACTIVE` (open) with zero payment attempts. The backend
  did not distinguish this from "payment in flight", so the frontend showed
  "Confirming your payment…" for 30s of polling — confusing & misleading.

  Fix: `get_order_status` now also queries `/orders/:id/payments`. When
  Cashfree reports `order_status=ACTIVE` AND there are zero payment
  attempts, the backend transitions the local order to `unpaid` so the
  frontend can immediately bounce the user back to /pricing.

These tests directly exercise the backend logic with the requests layer
mocked — no Cashfree sandbox calls, fast & deterministic.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import payments_cashfree  # noqa: E402
from db import db  # noqa: E402


@pytest.fixture
def fresh_order():
    """Seed a fake order in the DB, return its id, then clean up afterwards."""
    user_id = f"user_test_{uuid.uuid4().hex[:8]}"
    order_id = f"order_test_{uuid.uuid4().hex[:8]}"
    order = {
        "order_id": order_id,
        "user_id": user_id,
        "plan_id": "starter",
        "kind": "subscription",
        "amount": 499.0,
        "currency": "INR",
        "credits": 600,
        "status": "active",
        "created_at": "2026-05-11T00:00:00+00:00",
        "updated_at": "2026-05-11T00:00:00+00:00",
    }

    async def setup():
        await db.payment_orders.insert_one(dict(order))

    async def teardown():
        await db.payment_orders.delete_one({"order_id": order_id})

    asyncio.get_event_loop().run_until_complete(setup())
    yield order_id, user_id
    asyncio.get_event_loop().run_until_complete(teardown())


def _mock_response(status: int, json_body):
    m = MagicMock()
    m.status_code = status
    m.json = lambda: json_body
    return m


def test_user_dropped_marks_order_unpaid(fresh_order):
    """When Cashfree returns order_status=ACTIVE with empty payments list,
    the backend must transition the local order to 'unpaid'."""
    order_id, user_id = fresh_order
    fake_user = {"user_id": user_id, "role": "user", "email": "x@example.com"}

    def fake_requests_get(url, **kwargs):
        if f"/orders/{order_id}/payments" in url:
            return _mock_response(200, [])  # zero payment attempts
        if f"/orders/{order_id}" in url:
            return _mock_response(200, {"order_id": order_id, "order_status": "ACTIVE"})
        return _mock_response(404, {})

    with patch.object(payments_cashfree, "requests") as mock_req:
        mock_req.get.side_effect = fake_requests_get
        result = asyncio.get_event_loop().run_until_complete(
            payments_cashfree.get_order_status(order_id, fake_user)
        )
    assert result["order"]["status"] == "unpaid", (
        f"Expected status 'unpaid' after user dropped without paying, got {result['order']['status']!r}"
    )


def test_active_with_payment_attempt_remains_active(fresh_order):
    """When Cashfree returns ACTIVE but a payment attempt exists, the order
    must REMAIN in flight (not be marked unpaid) — user might still complete."""
    order_id, user_id = fresh_order
    fake_user = {"user_id": user_id, "role": "user", "email": "x@example.com"}

    def fake_requests_get(url, **kwargs):
        if f"/orders/{order_id}/payments" in url:
            # One attempt exists, possibly still settling
            return _mock_response(200, [{"cf_payment_id": "p1", "payment_status": "PENDING"}])
        if f"/orders/{order_id}" in url:
            return _mock_response(200, {"order_id": order_id, "order_status": "ACTIVE"})
        return _mock_response(404, {})

    with patch.object(payments_cashfree, "requests") as mock_req:
        mock_req.get.side_effect = fake_requests_get
        result = asyncio.get_event_loop().run_until_complete(
            payments_cashfree.get_order_status(order_id, fake_user)
        )
    # active or created — anything BUT unpaid is acceptable; frontend will keep polling
    assert result["order"]["status"] in ("active", "created"), (
        f"Expected order to remain in-flight, got {result['order']['status']!r}"
    )


def test_expired_terminal_status(fresh_order):
    """Cashfree EXPIRED → local status 'expired' (already worked, regression guard)."""
    order_id, user_id = fresh_order
    fake_user = {"user_id": user_id, "role": "user", "email": "x@example.com"}

    def fake_requests_get(url, **kwargs):
        if f"/orders/{order_id}" in url and "/payments" not in url:
            return _mock_response(200, {"order_id": order_id, "order_status": "EXPIRED"})
        return _mock_response(404, {})

    with patch.object(payments_cashfree, "requests") as mock_req:
        mock_req.get.side_effect = fake_requests_get
        result = asyncio.get_event_loop().run_until_complete(
            payments_cashfree.get_order_status(order_id, fake_user)
        )
    assert result["order"]["status"] == "expired"


def test_terminated_terminal_status(fresh_order):
    """Cashfree TERMINATED → local status 'terminated'."""
    order_id, user_id = fresh_order
    fake_user = {"user_id": user_id, "role": "user", "email": "x@example.com"}

    def fake_requests_get(url, **kwargs):
        if f"/orders/{order_id}" in url and "/payments" not in url:
            return _mock_response(200, {"order_id": order_id, "order_status": "TERMINATED"})
        return _mock_response(404, {})

    with patch.object(payments_cashfree, "requests") as mock_req:
        mock_req.get.side_effect = fake_requests_get
        result = asyncio.get_event_loop().run_until_complete(
            payments_cashfree.get_order_status(order_id, fake_user)
        )
    assert result["order"]["status"] == "terminated"
