"""
Tests for backend/iap.py — Apple/Google IAP verification + push token endpoints.

These tests run against a minimal FastAPI app that mounts ONLY iap.router so
they don't drag in the full server.py dependency tree (emergentintegrations,
admin modules, etc.). The grant logic, idempotency, and DB writes still run
against the real `credit_payment`, `PLAN_INDEX`, `TOPUP_INDEX`.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

# Env MUST be set before importing iap (it reads env at module load).
os.environ.setdefault("APPLE_BUNDLE_ID", "com.aiclonechats.app")
os.environ.setdefault("GOOGLE_PACKAGE_NAME", "com.aiclonechats.app")
os.environ.setdefault("APPLE_ALLOW_SANDBOX", "true")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("MONGO_URL", os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
os.environ.setdefault("DB_NAME", os.environ.get("DB_NAME", "aiclone_iap_test"))

import iap  # noqa: E402
from db import db  # noqa: E402

# Override the auth dependency for tests: produce a stable, unique user per
# Authorization header so we can simulate multiple users.
async def _override_get_current_user(authorization: str | None = None):
    """Test-only auth: 'Bearer test-<id>' -> {"user_id": "test-<id>"}."""
    from fastapi import Header, HTTPException
    # FastAPI will inject the Authorization header via the real Depends chain
    # only if we declare it; for the override, we read it from the Request.
    raise NotImplementedError  # replaced below


# Build a minimal app with just iap.router mounted.
app = FastAPI()
app.include_router(iap.router)

# Simple header-based test auth override.
from fastapi import Request

async def _test_auth(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail={"code": "unauthenticated", "message": "Missing bearer"})
    token = auth.split(" ", 1)[1].strip()
    if not token:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail={"code": "unauthenticated", "message": "Empty token"})
    return {"user_id": f"test-user-{token}"}

# Override get_current_user dependency wherever it appears in iap.router.
from auth import get_current_user
app.dependency_overrides[get_current_user] = _test_auth


@pytest.fixture(scope="session", autouse=True)
def _ensure_indexes():
    """Ensure the unique index exists on iap_transactions._id (it's _id so it
    is implicitly unique, but we create the secondary indexes too)."""
    async def _do():
        await iap.ensure_iap_indexes()
    asyncio.get_event_loop().run_until_complete(_do())
    yield


@pytest.fixture(autouse=True)
def _clean_state():
    """Wipe iap_transactions, push_tokens, users between tests."""
    async def _wipe():
        await db.iap_transactions.delete_many({})
        await db.push_tokens.delete_many({})
        await db.users.delete_many({"user_id": {"$regex": "^test-user-"}})
    asyncio.get_event_loop().run_until_complete(_wipe())
    yield


client = TestClient(app)

GOOD_APPLE_SUB_SKU = "com.aiclonechats.app.sub.pro"
GOOD_APPLE_CONSUMABLE = "com.aiclonechats.app.credits.medium"
GOOD_GOOGLE_SUB_SKU = "com.aiclonechats.app.sub.starter"
GOOD_GOOGLE_CONSUMABLE = "com.aiclonechats.app.credits.small"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Apple
# ---------------------------------------------------------------------------
def test_apple_verify_success_subscription(monkeypatch):
    token = uuid.uuid4().hex[:8]

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
    assert body["balance"] is not None


def test_apple_invalid_bundle():
    r = client.post(
        "/api/iap/apple/verify",
        json={
            "productId": GOOD_APPLE_SUB_SKU,
            "bundleId": "com.evil.app",
            "jws": "x",
            "kind": "subscription",
        },
        headers=_auth("u"),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bad_bundle_id"


def test_apple_duplicate_transaction_no_double_credit(monkeypatch):
    token = uuid.uuid4().hex[:8]

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
    b1 = r1.json()["balance"]

    r2 = client.post("/api/iap/apple/verify", json=payload, headers=_auth(token))
    assert r2.status_code == 200, r2.text
    b2 = r2.json()["balance"]

    # Critical: same transaction must NOT credit twice.
    assert b1 == b2, f"Double credit! first={b1} second={b2}"

    # And there must be exactly ONE iap_transactions row for this txn.
    async def _count():
        return await db.iap_transactions.count_documents({"transaction_id": "apple-txn-dup"})
    count = asyncio.get_event_loop().run_until_complete(_count())
    assert count == 1, f"Expected 1 iap_transactions row, found {count}"


def test_apple_unknown_sku():
    r = client.post(
        "/api/iap/apple/verify",
        json={
            "productId": "com.aiclonechats.app.sub.nonexistent",
            "bundleId": "com.aiclonechats.app",
            "jws": "x",
            "kind": "subscription",
        },
        headers=_auth("u"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["error"] == "unknown_sku"


def test_apple_expired_subscription_rejected(monkeypatch):
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
        headers=_auth("u"),
    )
    assert r.status_code == 200
    assert r.json()["error"] == "subscription_expired"


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------
def test_google_verify_success_consumable(monkeypatch):
    token = uuid.uuid4().hex[:8]

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


def test_google_invalid_package():
    r = client.post(
        "/api/iap/google/verify",
        json={
            "productId": GOOD_GOOGLE_SUB_SKU,
            "purchaseToken": "g-tok",
            "packageName": "com.evil.app",
            "kind": "subscription",
        },
        headers=_auth("u"),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bad_package_name"


def test_google_duplicate_purchase_no_double_credit(monkeypatch):
    token = uuid.uuid4().hex[:8]

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
    assert r1.status_code == 200, r1.text
    b1 = r1.json()["balance"]

    r2 = client.post("/api/iap/google/verify", json=payload, headers=_auth(token))
    assert r2.status_code == 200, r2.text
    b2 = r2.json()["balance"]
    assert b1 == b2, f"Double credit! {b1} != {b2}"


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------
def test_restore_purchases_mixed(monkeypatch):
    token = uuid.uuid4().hex[:8]

    async def fake_product(product_id, purchase_token):
        return {"transaction_id": f"google-restore-{purchase_token}"}
    monkeypatch.setattr(iap, "_verify_google_product", fake_product)
    monkeypatch.setattr(iap, "_google_consume", AsyncMock(return_value=None))

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


# ---------------------------------------------------------------------------
# Push token
# ---------------------------------------------------------------------------
def test_push_token_register_and_revoke():
    push_token = "ExponentPushToken[abcDEF123_-]"
    headers = _auth("push-user")

    r = client.post(
        "/api/me/push-token",
        json={"expo_push_token": push_token, "platform": "ios", "device_id": "dev_1"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    r2 = client.post(
        "/api/me/push-token",
        json={"expo_push_token": push_token, "platform": "ios", "device_id": "dev_1"},
        headers=headers,
    )
    assert r2.status_code == 200

    r3 = client.post(
        "/api/me/push-token/revoke",
        json={"expo_push_token": push_token},
        headers=headers,
    )
    assert r3.status_code == 200


def test_push_token_bad_format_rejected():
    r = client.post(
        "/api/me/push-token",
        json={"expo_push_token": "not-a-real-token", "platform": "ios"},
        headers=_auth("u"),
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
    assert r.status_code in (401, 403, 422), r.text
