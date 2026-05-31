"""
Tests for backend/iap.py — Apple/Google IAP verification + push token endpoints.

Network boundary mocked (Apple JWKS/verifyReceipt, Google androidpublisher);
core grant logic & idempotency exercised against real `credit_payment`,
`PLAN_INDEX`, `TOPUP_INDEX`, `get_current_user`, and the live Mongo client
(motor) the rest of the suite uses.
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APPLE_BUNDLE_ID", "com.aiclonechats.app")
os.environ.setdefault("GOOGLE_PACKAGE_NAME", "com.aiclonechats.app")
os.environ.setdefault("APPLE_ALLOW_SANDBOX", "true")
os.environ.setdefault("JWT_SECRET", "test-secret")

import iap  # noqa: E402  (env must be set first)
from server import app  # noqa: E402
from db import db  # noqa: E402

client = TestClient(app)

GOOD_APPLE_SUB_SKU = "com.aiclonechats.app.sub.pro"
GOOD_APPLE_CONSUMABLE = "com.aiclonechats.app.credits.medium"
GOOD_GOOGLE_SUB_SKU = "com.aiclonechats.app.sub.starter"
GOOD_GOOGLE_CONSUMABLE = "com.aiclonechats.app.credits.small"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _register_and_login() -> tuple[str, str]:
    """Register a fresh user, return (user_id, session_token)."""
    email = f"iap-{uuid.uuid4().hex[:10]}@example.com"
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "TestPass123!", "name": "IAP Tester"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    return body["user"]["user_id"], body["session_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clean_iap_state():
    """Wipe iap_transactions + push_tokens between tests to keep idempotency clean."""
    import asyncio

    async def _wipe():
        await db.iap_transactions.delete_many({})
        await db.push_tokens.delete_many({})

    asyncio.get_event_loop().run_until_complete(_wipe())
    yield


# ---------------------------------------------------------------------------
# Apple
# ---------------------------------------------------------------------------
def test_apple_verify_success_subscription(monkeypatch):
    _, token = _register_and_login()

    async def fake_jws(jws, product_id):
        return {
            "transaction_id": "apple-txn-1",
            "original_transaction_id": "apple-txn-1",
            "environment": "Sandbox",
            "expires_date_ms": int((__import__("time").time() + 86400) * 1000),
        }
    monkeypatch.setattr(iap, "_verify_apple_jws", fake_jws)

    r = client.post(
        "/api/iap/apple/verify",
        json={
            "productId": GOOD_APPLE_SUB_SKU,
            "bundleId": "com.aiclonechats.app",
            "jws": "fake-jws",
            "transactionId": "apple-txn-1",
            "kind": "subscription",
        },
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is True
    assert body["plan_id"] == "pro"
    assert body["balance"] >= 2500


def test_apple_invalid_bundle(monkeypatch):
    _, token = _register_and_login()
    r = client.post(
        "/api/iap/apple/verify",
        json={
            "productId": GOOD_APPLE_SUB_SKU,
            "bundleId": "com.evil.app",
            "jws": "x",
            "kind": "subscription",
        },
        headers=_auth(token),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bad_bundle_id"


def test_apple_duplicate_transaction_no_double_credit(monkeypatch):
    user_id, token = _register_and_login()

    async def fake_jws(jws, product_id):
        return {
            "transaction_id": "apple-txn-dup",
            "original_transaction_id": "apple-txn-dup",
            "environment": "Sandbox",
            "expires_date_ms": int((__import__("time").time() + 86400) * 1000),
        }
    monkeypatch.setattr(iap, "_verify_apple_jws", fake_jws)

    payload = {
        "productId": GOOD_APPLE_CONSUMABLE,
        "bundleId": "com.aiclonechats.app",
        "jws": "fake",
        "kind": "consumable",
    }
    r1 = client.post("/api/iap/apple/verify", json=payload, headers=_auth(token))
    assert r1.status_code == 200, r1.text
    balance_after_first = r1.json()["balance"]

    r2 = client.post("/api/iap/apple/verify", json=payload, headers=_auth(token))
    assert r2.status_code == 200, r2.text
    balance_after_second = r2.json()["balance"]

    # Critical assertion: balance must NOT have grown a second time.
    assert balance_after_second == balance_after_first, (
        f"Double-credit! first={balance_after_first} second={balance_after_second}"
    )


def test_apple_unknown_sku(monkeypatch):
    _, token = _register_and_login()
    r = client.post(
        "/api/iap/apple/verify",
        json={
            "productId": "com.aiclonechats.app.sub.nonexistent",
            "bundleId": "com.aiclonechats.app",
            "jws": "x",
            "kind": "subscription",
        },
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["error"] == "unknown_sku"


def test_apple_expired_subscription_rejected(monkeypatch):
    _, token = _register_and_login()

    async def fake_jws(jws, product_id):
        return {
            "transaction_id": "apple-exp",
            "original_transaction_id": "apple-exp",
            "environment": "Sandbox",
            "expires_date_ms": int((__import__("time").time() - 86400) * 1000),
        }
    monkeypatch.setattr(iap, "_verify_apple_jws", fake_jws)

    r = client.post(
        "/api/iap/apple/verify",
        json={
            "productId": GOOD_APPLE_SUB_SKU,
            "bundleId": "com.aiclonechats.app",
            "jws": "fake",
            "kind": "subscription",
        },
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["error"] == "subscription_expired"


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------
def test_google_verify_success_consumable(monkeypatch):
    _, token = _register_and_login()

    async def fake_product(product_id, purchase_token):
        return {"transaction_id": f"google-{purchase_token}"}
    monkeypatch.setattr(iap, "_verify_google_product", fake_product)
    monkeypatch.setattr(iap, "_google_consume", AsyncMock(return_value=None))

    r = client.post(
        "/api/iap/google/verify",
        json={
            "productId": GOOD_GOOGLE_CONSUMABLE,
            "purchaseToken": "g-tok-1",
            "packageName": "com.aiclonechats.app",
            "kind": "consumable",
        },
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is True
    assert body["pack_id"] == "topup_small"
    assert body["balance"] >= 300


def test_google_invalid_package(monkeypatch):
    _, token = _register_and_login()
    r = client.post(
        "/api/iap/google/verify",
        json={
            "productId": GOOD_GOOGLE_SUB_SKU,
            "purchaseToken": "g-tok",
            "packageName": "com.evil.app",
            "kind": "subscription",
        },
        headers=_auth(token),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bad_package_name"


def test_google_duplicate_purchase_no_double_credit(monkeypatch):
    _, token = _register_and_login()

    async def fake_product(product_id, purchase_token):
        return {"transaction_id": f"google-dup-{purchase_token}"}
    monkeypatch.setattr(iap, "_verify_google_product", fake_product)
    monkeypatch.setattr(iap, "_google_consume", AsyncMock(return_value=None))

    payload = {
        "productId": GOOD_GOOGLE_CONSUMABLE,
        "purchaseToken": "g-tok-dup",
        "packageName": "com.aiclonechats.app",
        "kind": "consumable",
    }
    r1 = client.post("/api/iap/google/verify", json=payload, headers=_auth(token))
    assert r1.status_code == 200
    b1 = r1.json()["balance"]

    r2 = client.post("/api/iap/google/verify", json=payload, headers=_auth(token))
    assert r2.status_code == 200
    b2 = r2.json()["balance"]
    assert b1 == b2, f"Double credit! {b1} != {b2}"


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------
def test_restore_purchases_mixed(monkeypatch):
    _, token = _register_and_login()

    async def fake_product(product_id, purchase_token):
        return {"transaction_id": f"google-restore-{purchase_token}"}
    monkeypatch.setattr(iap, "_verify_google_product", fake_product)

    # First purchase: fresh.
    r0 = client.post(
        "/api/iap/google/verify",
        json={
            "productId": GOOD_GOOGLE_CONSUMABLE,
            "purchaseToken": "tok-A",
            "packageName": "com.aiclonechats.app",
            "kind": "consumable",
        },
        headers=_auth(token),
    )
    assert r0.status_code == 200

    # Restore including the same token (should be already_active) + an unknown SKU.
    r = client.post(
        "/api/iap/restore",
        json={
            "platform": "android",
            "purchases": [
                {"productId": GOOD_GOOGLE_CONSUMABLE, "purchaseToken": "tok-A"},
                {"productId": "com.bogus.sku", "purchaseToken": "tok-B"},
            ],
        },
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["already_active"] == 1
    assert body["failed"] == 1
    assert body["restored"] == 0


# ---------------------------------------------------------------------------
# Push token
# ---------------------------------------------------------------------------
def test_push_token_register_and_revoke():
    _, token = _register_and_login()
    push_token = "ExponentPushToken[abcDEF123_-]"

    r = client.post(
        "/api/me/push-token",
        json={"expo_push_token": push_token, "platform": "ios", "device_id": "dev_1"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # Re-registering is idempotent.
    r2 = client.post(
        "/api/me/push-token",
        json={"expo_push_token": push_token, "platform": "ios", "device_id": "dev_1"},
        headers=_auth(token),
    )
    assert r2.status_code == 200

    r3 = client.post(
        "/api/me/push-token/revoke",
        json={"expo_push_token": push_token},
        headers=_auth(token),
    )
    assert r3.status_code == 200


def test_push_token_bad_format_rejected():
    _, token = _register_and_login()
    r = client.post(
        "/api/me/push-token",
        json={"expo_push_token": "not-a-real-token", "platform": "ios"},
        headers=_auth(token),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bad_token_format"


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/iap/apple/verify", {"productId": GOOD_APPLE_SUB_SKU, "bundleId": "com.aiclonechats.app", "jws": "x", "kind": "subscription"}),
        ("/api/iap/google/verify", {"productId": GOOD_GOOGLE_SUB_SKU, "purchaseToken": "x", "packageName": "com.aiclonechats.app", "kind": "subscription"}),
        ("/api/iap/restore", {"platform": "ios", "purchases": []}),
        ("/api/me/push-token", {"expo_push_token": "ExponentPushToken[x]", "platform": "ios"}),
        ("/api/me/push-token/revoke", {"expo_push_token": "ExponentPushToken[x]"}),
    ],
)
def test_unauthenticated_requests_rejected(path, body):
    r = client.post(path, json=body)
    # Either 401 or 422 (FastAPI's auth deps -> 401; explicit auth_err -> 401)
    assert r.status_code in (401, 422), r.text
