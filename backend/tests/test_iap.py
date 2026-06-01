"""
Tests for backend/iap.py — Apple/Google IAP verification + push token endpoints.

Uses httpx.AsyncClient + ASGITransport (NOT starlette.TestClient) so the
motor MongoDB client and the FastAPI app share a single event loop. The
TestClient approach hits a "Future attached to a different loop" error with
motor because TestClient spawns each request on a per-call anyio loop.
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI, Request, HTTPException

os.environ.setdefault("APPLE_BUNDLE_ID", "com.aiclonechats.app")
os.environ.setdefault("GOOGLE_PACKAGE_NAME", "com.aiclonechats.app")
os.environ.setdefault("APPLE_ALLOW_SANDBOX", "true")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("MONGO_URL", os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
os.environ.setdefault("DB_NAME", os.environ.get("DB_NAME", "aiclone_iap_test"))

import iap  # noqa: E402
from db import db  # noqa: E402
from auth import get_current_user  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for the whole session so motor's cached loop stays alive."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


async def _test_auth(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "unauthenticated", "message": "Missing bearer"})
    token = auth.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail={"code": "unauthenticated", "message": "Empty token"})
    return {"user_id": f"test-user-{token}"}


app = FastAPI()
app.include_router(iap.router)
app.dependency_overrides[get_current_user] = _test_auth


GOOD_APPLE_SUB_SKU = "com.aiclonechats.app.sub.pro"
GOOD_APPLE_CONSUMABLE = "com.aiclonechats.app.credits.medium"
GOOD_GOOGLE_SUB_SKU = "com.aiclonechats.app.sub.starter"
GOOD_GOOGLE_CONSUMABLE = "com.aiclonechats.app.credits.small"


@pytest_asyncio.fixture
async def client():
    """Fresh AsyncClient per test, sharing the same loop as the test."""
    await db.iap_transactions.delete_many({})
    await db.push_tokens.delete_many({})
    await db.users.delete_many({"user_id": {"$regex": "^test-user-"}})
    await iap.ensure_iap_indexes()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Apple
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_apple_verify_success_subscription(client, monkeypatch):
    import time
    token = uuid.uuid4().hex[:8]

    async def fake_jws(jws, product_id):
        return {
            "transaction_id": "apple-txn-1",
            "original_transaction_id": "apple-txn-1",
            "environment": "Sandbox",
            "expires_date_ms": int((time.time() + 86400) * 1000),
        }
    monkeypatch.setattr(iap, "_verify_apple_jws", fake_jws)

    r = await client.post(
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


@pytest.mark.asyncio
async def test_apple_invalid_bundle(client):
    r = await client.post(
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


@pytest.mark.asyncio
async def test_apple_duplicate_transaction_no_double_credit(client, monkeypatch):
    import time
    token = uuid.uuid4().hex[:8]

    async def fake_jws(jws, product_id):
        return {
            "transaction_id": "apple-txn-dup",
            "original_transaction_id": "apple-txn-dup",
            "environment": "Sandbox",
            "expires_date_ms": int((time.time() + 86400) * 1000),
        }
    monkeypatch.setattr(iap, "_verify_apple_jws", fake_jws)

    payload = {
        "productId": GOOD_APPLE_CONSUMABLE,
        "bundleId": "com.aiclonechats.app",
        "jws": "fake",
        "kind": "consumable",
    }
    r1 = await client.post("/api/iap/apple/verify", json=payload, headers=_auth(token))
    assert r1.status_code == 200, r1.text
    b1 = r1.json()["balance"]

    r2 = await client.post("/api/iap/apple/verify", json=payload, headers=_auth(token))
    assert r2.status_code == 200, r2.text

    # Critical: the DB must have EXACTLY one row for this transaction. Balance
    # equality is NOT a reliable idempotency proof (balance can legitimately
    # change between calls due to unrelated grants).
    count = await db.iap_transactions.count_documents({"transaction_id": "apple-txn-dup"})
    assert count == 1, f"Expected 1 iap_transactions row, found {count}"


@pytest.mark.asyncio
async def test_apple_unknown_sku(client):
    r = await client.post(
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
    assert r.json()["error"] == "unknown_sku"


@pytest.mark.asyncio
async def test_apple_expired_subscription_rejected(client, monkeypatch):
    import time

    async def fake_jws(jws, product_id):
        return {
            "transaction_id": "apple-exp",
            "original_transaction_id": "apple-exp",
            "environment": "Sandbox",
            "expires_date_ms": int((time.time() - 86400) * 1000),
        }
    monkeypatch.setattr(iap, "_verify_apple_jws", fake_jws)

    r = await client.post(
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
@pytest.mark.asyncio
async def test_google_verify_success_consumable(client, monkeypatch):
    token = uuid.uuid4().hex[:8]

    async def fake_product(product_id, purchase_token):
        return {"transaction_id": f"google-{purchase_token}"}
    monkeypatch.setattr(iap, "_verify_google_product", fake_product)
    monkeypatch.setattr(iap, "_google_consume", AsyncMock(return_value=None))

    r = await client.post(
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


@pytest.mark.asyncio
async def test_google_invalid_package(client):
    r = await client.post(
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


@pytest.mark.asyncio
async def test_google_duplicate_purchase_no_double_credit(client, monkeypatch):
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
    r1 = await client.post("/api/iap/google/verify", json=payload, headers=_auth(token))
    assert r1.status_code == 200, r1.text
    b1 = r1.json()["balance"]

    r2 = await client.post("/api/iap/google/verify", json=payload, headers=_auth(token))
    assert r2.status_code == 200, r2.text

    count = await db.iap_transactions.count_documents({"transaction_id": "google-dup-g-tok-dup"})
    assert count == 1, f"Expected 1 iap_transactions row, found {count}"


@pytest.mark.asyncio
async def test_restore_purchases_mixed(client, monkeypatch):
    token = uuid.uuid4().hex[:8]

    async def fake_product(product_id, purchase_token):
        return {"transaction_id": f"google-restore-{purchase_token}"}
    monkeypatch.setattr(iap, "_verify_google_product", fake_product)
    monkeypatch.setattr(iap, "_google_consume", AsyncMock(return_value=None))

    r0 = await client.post(
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

    r = await client.post(
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


@pytest.mark.asyncio
async def test_push_token_register_and_revoke(client):
    push_token = "ExponentPushToken[abcDEF123_-]"
    headers = _auth("push-user")

    r = await client.post(
        "/api/me/push-token",
        json={"expo_push_token": push_token, "platform": "ios", "device_id": "dev_1"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    r2 = await client.post(
        "/api/me/push-token",
        json={"expo_push_token": push_token, "platform": "ios", "device_id": "dev_1"},
        headers=headers,
    )
    assert r2.status_code == 200

    r3 = await client.post(
        "/api/me/push-token/revoke",
        json={"expo_push_token": push_token},
        headers=headers,
    )
    assert r3.status_code == 200


@pytest.mark.asyncio
async def test_push_token_bad_format_rejected(client):
    r = await client.post(
        "/api/me/push-token",
        json={"expo_push_token": "not-a-real-token", "platform": "ios"},
        headers=_auth("u"),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bad_token_format"


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
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
async def test_unauthenticated_requests_rejected(client, path, body):
    r = await client.post(path, json=body)
    assert r.status_code in (401, 403, 422), r.text
