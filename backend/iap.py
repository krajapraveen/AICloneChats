"""
Mobile In-App Purchase + Push token endpoints.

Implements the contract spec from /app/IAP_PORT_PLAN.md.

Endpoints
---------
- POST /api/iap/apple/verify
- POST /api/iap/google/verify
- POST /api/iap/restore
- POST /api/me/push-token
- POST /api/me/push-token/revoke

Verification flow
-----------------
- Apple StoreKit 2 JWS (preferred) signature-verified against Apple's JWKS.
- Apple StoreKit 1 receipt verified via /verifyReceipt with sandbox-21007 fallback.
- Google Play `subscriptionsv2.get` / `products.get` + mandatory consume/ack.
- Credits/subscriptions are granted *only* after the provider call returns success.
- Idempotency is enforced at the DB layer by a unique key on iap_transactions._id.

This module deliberately keeps the network-boundary helpers as module-level
functions (`_verify_apple_jws`, `_verify_apple_receipt`, `_verify_google_*`)
so unit tests can monkeypatch them without touching grant logic.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user
from credits import PLAN_INDEX, TOPUP_INDEX, credit_payment
from db import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# Configuration (env-driven, no defaults that could leak to prod)
# ---------------------------------------------------------------------------
APPLE_BUNDLE_ID = os.environ.get("APPLE_BUNDLE_ID", "")
APPLE_SHARED_SECRET = os.environ.get("APPLE_SHARED_SECRET", "")
APPLE_ALLOW_SANDBOX = os.environ.get("APPLE_ALLOW_SANDBOX", "false").lower() == "true"
APPLE_PROD_VERIFY_URL = "https://buy.itunes.apple.com/verifyReceipt"
APPLE_SANDBOX_VERIFY_URL = "https://sandbox.itunes.apple.com/verifyReceipt"

GOOGLE_PACKAGE_NAME = os.environ.get("GOOGLE_PACKAGE_NAME", "")
GOOGLE_PLAY_SA_JSON_PATH = os.environ.get("GOOGLE_PLAY_SA_JSON_PATH", "")

EXPO_PUSH_TOKEN_RE = re.compile(r"^ExponentPushToken\[[A-Za-z0-9_\-]+\]$")

# ---------------------------------------------------------------------------
# SKU -> entitlement mapping. MUST match
# /app/frontend/src/iap/index.ts -> SKU_TO_PLAN.
# Drift = silent grant failures.
# ---------------------------------------------------------------------------
SKU_TO_ENTITLEMENT: dict[str, dict[str, Any]] = {
    # Subscriptions
    "com.aiclonechats.app.sub.starter":   {"kind": "subscription", "plan_id": "starter"},
    "com.aiclonechats.app.sub.pro":       {"kind": "subscription", "plan_id": "pro"},
    "com.aiclonechats.app.sub.premium":   {"kind": "subscription", "plan_id": "premium"},
    "com.aiclonechats.app.sub.ultimate":  {"kind": "subscription", "plan_id": "ultimate"},
    # Consumable credit packs
    "com.aiclonechats.app.credits.small":  {"kind": "consumable", "pack_id": "topup_small"},
    "com.aiclonechats.app.credits.medium": {"kind": "consumable", "pack_id": "topup_medium"},
    "com.aiclonechats.app.credits.large":  {"kind": "consumable", "pack_id": "topup_large"},
    "com.aiclonechats.app.credits.mega":   {"kind": "consumable", "pack_id": "topup_mega"},
}


def _entitlement_credits(entry: dict) -> int:
    if entry.get("plan_id"):
        return int(PLAN_INDEX[entry["plan_id"]]["monthly_credits"])
    return int(TOPUP_INDEX[entry["pack_id"]]["credits"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def _err(code: str, message: str, request_id: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, "request_id": request_id})


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class AppleVerifyRequest(BaseModel):
    productId: str = Field(min_length=1, max_length=200)
    bundleId: str = Field(min_length=1, max_length=200)
    transactionId: Optional[str] = None
    transactionReceipt: Optional[str] = None
    jws: Optional[str] = None
    kind: Literal["subscription", "consumable"]


class GoogleVerifyRequest(BaseModel):
    productId: str = Field(min_length=1, max_length=200)
    purchaseToken: str = Field(min_length=1)
    packageName: str = Field(min_length=1, max_length=200)
    kind: Literal["subscription", "consumable"]


class RestoreRequest(BaseModel):
    platform: Literal["ios", "android"]
    purchases: list[dict[str, Any]] = Field(default_factory=list)


class PushTokenRequest(BaseModel):
    expo_push_token: str
    platform: Literal["ios", "android"]
    device_id: Optional[str] = None


class PushTokenRevokeRequest(BaseModel):
    expo_push_token: str


class VerifyResponse(BaseModel):
    valid: bool
    plan_id: Optional[str] = None
    pack_id: Optional[str] = None
    balance: Optional[int] = None
    expires_at: Optional[str] = None
    error: Optional[str] = None
    request_id: str


# ---------------------------------------------------------------------------
# Provider verifiers (network boundary — tests monkeypatch these)
# ---------------------------------------------------------------------------
class VerificationFailure(Exception):
    """Soft failure: provider rejected the receipt. Return valid:false to client."""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class UpstreamError(Exception):
    """Hard failure: provider unavailable. Return 502 — client should retry."""
    def __init__(self, code: str = "upstream_unavailable"):
        super().__init__(code)
        self.code = code


async def _verify_apple_jws(jws: str, product_id: str) -> dict:
    """
    Verify a StoreKit 2 JWS. Returns a normalized dict:
      {transaction_id, original_transaction_id, environment, expires_date_ms?}
    Raises VerificationFailure on signature/contents mismatch.
    Raises UpstreamError on JWKS unreachable.

    Production implementation must:
      1. Decode JWS header to read `kid` and `alg` (must be "ES256").
      2. Fetch & cache https://appleid.apple.com/auth/keys (24h TTL).
      3. Verify signature with that key using PyJWT.
      4. Assert payload.bundleId == APPLE_BUNDLE_ID,
                payload.productId == product_id,
                payload.environment in {"Production", "Sandbox"}.
      5. Reject "Sandbox" unless APPLE_ALLOW_SANDBOX=true.

    Implementation kept thin here; full crypto wired in the PR but factored
    out so unit tests can monkeypatch this function entirely.
    """
    try:
        import jwt  # PyJWT; requires `cryptography` for ES256
    except ImportError as exc:  # pragma: no cover
        raise UpstreamError("pyjwt_missing") from exc

    # Decode header without verification to find `kid`.
    try:
        unverified_header = jwt.get_unverified_header(jws)
    except Exception:
        raise VerificationFailure("invalid_jws_header")

    kid = unverified_header.get("kid")
    alg = unverified_header.get("alg")
    if alg != "ES256" or not kid:
        raise VerificationFailure("bad_jws_alg_or_kid")

    # Fetch JWKS (production would cache for 24h).
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://appleid.apple.com/auth/keys")
            r.raise_for_status()
            keys = r.json().get("keys", [])
    except httpx.HTTPError:
        raise UpstreamError("apple_jwks_unavailable")

    key_dict = next((k for k in keys if k.get("kid") == kid), None)
    if not key_dict:
        raise VerificationFailure("unknown_kid")

    try:
        from jwt.algorithms import ECAlgorithm

        public_key = ECAlgorithm.from_jwk(json.dumps(key_dict))
        payload = jwt.decode(jws, public_key, algorithms=["ES256"], options={"verify_aud": False})
    except Exception:
        raise VerificationFailure("invalid_jws_signature")

    if payload.get("bundleId") != APPLE_BUNDLE_ID:
        raise VerificationFailure("bundle_mismatch")
    if payload.get("productId") != product_id:
        raise VerificationFailure("product_mismatch")
    env = payload.get("environment", "Production")
    if env == "Sandbox" and not APPLE_ALLOW_SANDBOX:
        raise VerificationFailure("sandbox_in_production")

    return {
        "transaction_id": str(payload.get("transactionId") or payload.get("originalTransactionId")),
        "original_transaction_id": str(payload.get("originalTransactionId") or payload.get("transactionId")),
        "environment": env,
        "expires_date_ms": payload.get("expiresDate"),
    }


async def _verify_apple_receipt(receipt: str, product_id: str) -> dict:
    """StoreKit 1 fallback. Returns same normalized shape as _verify_apple_jws."""
    body = {
        "receipt-data": receipt,
        "password": APPLE_SHARED_SECRET,
        "exclude-old-transactions": True,
    }

    async def _post(url: str) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=body)
            r.raise_for_status()
            return r.json()

    try:
        result = await _post(APPLE_PROD_VERIFY_URL)
    except httpx.HTTPError:
        raise UpstreamError("apple_verify_unavailable")

    # 21007 = sandbox receipt sent to production endpoint.
    if result.get("status") == 21007:
        if not APPLE_ALLOW_SANDBOX:
            raise VerificationFailure("sandbox_in_production")
        try:
            result = await _post(APPLE_SANDBOX_VERIFY_URL)
        except httpx.HTTPError:
            raise UpstreamError("apple_verify_unavailable")

    if result.get("status") != 0:
        raise VerificationFailure(f"apple_status_{result.get('status')}")

    in_apps = (result.get("receipt") or {}).get("in_app") or []
    latest = (result.get("latest_receipt_info") or [])
    matches = [t for t in (in_apps + latest) if t.get("product_id") == product_id]
    if not matches:
        raise VerificationFailure("transaction_not_in_receipt")

    # Pick the newest by purchase_date_ms.
    matches.sort(key=lambda t: int(t.get("purchase_date_ms", 0)), reverse=True)
    txn = matches[0]
    return {
        "transaction_id": str(txn.get("transaction_id")),
        "original_transaction_id": str(txn.get("original_transaction_id") or txn.get("transaction_id")),
        "environment": (result.get("environment") or "Production"),
        "expires_date_ms": int(txn["expires_date_ms"]) if txn.get("expires_date_ms") else None,
    }


def _google_play_client():
    """Builds an androidpublisher client. Cached at module load in production."""
    if not GOOGLE_PLAY_SA_JSON_PATH:
        raise UpstreamError("google_sa_not_configured")
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover
        raise UpstreamError("google_libs_missing") from exc

    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_PLAY_SA_JSON_PATH,
        scopes=["https://www.googleapis.com/auth/androidpublisher"],
    )
    return build("androidpublisher", "v3", credentials=creds, cache_discovery=False)


async def _verify_google_subscription(product_id: str, purchase_token: str) -> dict:
    """Returns {transaction_id, expires_ms, acknowledged}."""
    def _do() -> dict:
        svc = _google_play_client()
        try:
            res = svc.purchases().subscriptionsv2().get(
                packageName=GOOGLE_PACKAGE_NAME,
                token=purchase_token,
            ).execute()
        except Exception as exc:  # pragma: no cover
            raise UpstreamError(f"google_api_error:{exc}") from exc

        state = res.get("subscriptionState")
        if state != "SUBSCRIPTION_STATE_ACTIVE":
            raise VerificationFailure(f"subscription_state_{state}")
        line_items = res.get("lineItems") or []
        if not any(li.get("productId") == product_id for li in line_items):
            raise VerificationFailure("product_mismatch")
        ack = res.get("acknowledgementState") == "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED"
        return {
            "transaction_id": res.get("latestOrderId") or purchase_token,
            "expires_ms": int(res.get("lineItems", [{}])[0].get("expiryTime", 0) or 0),
            "acknowledged": ack,
        }
    return await asyncio.to_thread(_do)


async def _verify_google_product(product_id: str, purchase_token: str) -> dict:
    """Returns {transaction_id} for a consumable. Caller must call _consume after grant."""
    def _do() -> dict:
        svc = _google_play_client()
        try:
            res = svc.purchases().products().get(
                packageName=GOOGLE_PACKAGE_NAME,
                productId=product_id,
                token=purchase_token,
            ).execute()
        except Exception as exc:  # pragma: no cover
            raise UpstreamError(f"google_api_error:{exc}") from exc

        if int(res.get("purchaseState", 1)) != 0:
            raise VerificationFailure("not_purchased")
        if int(res.get("consumptionState", 0)) != 0:
            raise VerificationFailure("already_consumed")
        return {"transaction_id": res.get("orderId") or purchase_token}
    return await asyncio.to_thread(_do)


async def _google_consume(product_id: str, purchase_token: str) -> None:
    def _do() -> None:
        svc = _google_play_client()
        svc.purchases().products().consume(
            packageName=GOOGLE_PACKAGE_NAME, productId=product_id, token=purchase_token
        ).execute()
    await asyncio.to_thread(_do)


async def _google_acknowledge_subscription(purchase_token: str) -> None:
    def _do() -> None:
        svc = _google_play_client()
        # subscriptionsv2 acknowledge is via the v1 acknowledge method.
        svc.purchases().subscriptions().acknowledge(
            packageName=GOOGLE_PACKAGE_NAME,
            subscriptionId="",  # not required for sv2 ack
            token=purchase_token,
            body={},
        ).execute()
    try:
        await asyncio.to_thread(_do)
    except Exception:
        logger.warning("google_ack_failed token=%s", purchase_token[:12])


# ---------------------------------------------------------------------------
# Idempotent grant
# ---------------------------------------------------------------------------
async def _grant_idempotent(
    provider: Literal["apple", "google"],
    transaction_id: str,
    user_id: str,
    product_id: str,
    raw_payload: dict,
    expires_at: Optional[str] = None,
) -> dict:
    """
    Insert into iap_transactions atomically. If the doc already exists, do NOT
    re-grant. Returns the canonical record either way.

    Relies on a unique index on `_id`. The caller (verify endpoints) MUST
    ensure that index exists at startup.
    """
    entry = SKU_TO_ENTITLEMENT[product_id]
    credits = _entitlement_credits(entry)
    plan_id = entry.get("plan_id")
    pack_id = entry.get("pack_id")
    kind = entry["kind"]

    txn_id = f"{provider}:{transaction_id}"
    doc = {
        "_id": txn_id,
        "provider": provider,
        "transaction_id": transaction_id,
        "user_id": user_id,
        "product_id": product_id,
        "kind": kind,
        "plan_id": plan_id,
        "pack_id": pack_id,
        "credits_granted": credits,
        "expires_at": expires_at,
        "raw_provider_payload": {k: v for k, v in raw_payload.items() if k not in ("transactionReceipt", "jws", "purchaseToken")},
        "created_at": _now_iso(),
        "status": "granted",
    }

    try:
        await db.iap_transactions.insert_one(doc)
        # First time we've seen this transaction. Grant entitlement.
        new_balance = await credit_payment(
            user_id=user_id,
            credits=credits,
            order_id=txn_id,
            plan_id=plan_id,
            kind="subscription" if kind == "subscription" else "topup",
            pack_id=pack_id,
        )
        return {
            "fresh": True,
            "credits_granted": credits,
            "plan_id": plan_id,
            "pack_id": pack_id,
            "balance": new_balance,
            "expires_at": expires_at,
        }
    except Exception as exc:
        # Most likely DuplicateKeyError. Look up the prior record and return its
        # info — without re-granting credits.
        cls = exc.__class__.__name__
        if cls not in ("DuplicateKeyError",):
            logger.exception("iap_transactions insert failed: %s", exc)
            raise
        prior = await db.iap_transactions.find_one({"_id": txn_id}, {"_id": 0})
        prior = prior or {}
        user_row = await db.users.find_one({"user_id": user_id}, {"_id": 0, "credits_balance": 1})
        return {
            "fresh": False,
            "credits_granted": prior.get("credits_granted", 0),
            "plan_id": prior.get("plan_id"),
            "pack_id": prior.get("pack_id"),
            "balance": (user_row or {}).get("credits_balance"),
            "expires_at": prior.get("expires_at"),
        }


# ---------------------------------------------------------------------------
# DB index bootstrap (call once at app startup)
# ---------------------------------------------------------------------------
async def ensure_iap_indexes() -> None:
    """Idempotent. Safe to call on every boot."""
    await db.iap_transactions.create_index("user_id")
    await db.iap_transactions.create_index([("created_at", -1)])
    # _id is implicit unique
    await db.push_tokens.create_index("user_id")
    await db.push_tokens.create_index("expo_push_token")
    await db.push_tokens.create_index("revoked")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/iap/apple/verify", response_model=VerifyResponse)
async def apple_verify(payload: AppleVerifyRequest, user: dict = Depends(get_current_user)):
    rid = _new_request_id()
    if not APPLE_BUNDLE_ID:
        raise _err("server_misconfigured", "APPLE_BUNDLE_ID is not set", rid, status=500)
    if payload.bundleId != APPLE_BUNDLE_ID:
        raise _err("bad_bundle_id", "Unknown bundle", rid, status=400)
    if payload.productId not in SKU_TO_ENTITLEMENT:
        return VerifyResponse(valid=False, error="unknown_sku", request_id=rid)
    if not payload.jws and not payload.transactionReceipt:
        return VerifyResponse(valid=False, error="missing_token", request_id=rid)

    try:
        if payload.jws:
            v = await _verify_apple_jws(payload.jws, payload.productId)
        else:
            v = await _verify_apple_receipt(payload.transactionReceipt or "", payload.productId)
    except VerificationFailure as f:
        return VerifyResponse(valid=False, error=f.code, request_id=rid)
    except UpstreamError as u:
        raise _err(u.code, "Provider unavailable. Please retry.", rid, status=502)

    # Subscription expiry check.
    expires_at: Optional[str] = None
    if SKU_TO_ENTITLEMENT[payload.productId]["kind"] == "subscription":
        exp_ms = v.get("expires_date_ms")
        if exp_ms:
            exp_dt = datetime.fromtimestamp(int(exp_ms) / 1000, tz=timezone.utc)
            if exp_dt < datetime.now(timezone.utc):
                return VerifyResponse(valid=False, error="subscription_expired", request_id=rid)
            expires_at = exp_dt.isoformat()

    res = await _grant_idempotent(
        provider="apple",
        transaction_id=v["original_transaction_id"],
        user_id=user["user_id"],
        product_id=payload.productId,
        raw_payload={"environment": v["environment"]},
        expires_at=expires_at,
    )
    return VerifyResponse(
        valid=True,
        plan_id=res.get("plan_id"),
        pack_id=res.get("pack_id"),
        balance=res.get("balance"),
        expires_at=res.get("expires_at"),
        request_id=rid,
    )


@router.post("/iap/google/verify", response_model=VerifyResponse)
async def google_verify(payload: GoogleVerifyRequest, user: dict = Depends(get_current_user)):
    rid = _new_request_id()
    if not GOOGLE_PACKAGE_NAME:
        raise _err("server_misconfigured", "GOOGLE_PACKAGE_NAME is not set", rid, status=500)
    if payload.packageName != GOOGLE_PACKAGE_NAME:
        raise _err("bad_package_name", "Unknown package", rid, status=400)
    if payload.productId not in SKU_TO_ENTITLEMENT:
        return VerifyResponse(valid=False, error="unknown_sku", request_id=rid)

    entry = SKU_TO_ENTITLEMENT[payload.productId]
    if entry["kind"] != payload.kind:
        return VerifyResponse(valid=False, error="kind_mismatch", request_id=rid)

    try:
        if payload.kind == "subscription":
            v = await _verify_google_subscription(payload.productId, payload.purchaseToken)
            expires_at: Optional[str] = None
            if v.get("expires_ms"):
                expires_at = datetime.fromtimestamp(v["expires_ms"] / 1000, tz=timezone.utc).isoformat()
        else:
            v = await _verify_google_product(payload.productId, payload.purchaseToken)
            expires_at = None
    except VerificationFailure as f:
        return VerifyResponse(valid=False, error=f.code, request_id=rid)
    except UpstreamError as u:
        raise _err(u.code, "Provider unavailable. Please retry.", rid, status=502)

    res = await _grant_idempotent(
        provider="google",
        transaction_id=v["transaction_id"],
        user_id=user["user_id"],
        product_id=payload.productId,
        raw_payload={},
        expires_at=expires_at,
    )

    # Post-grant store-side housekeeping (best-effort; doesn't affect grant).
    if res.get("fresh"):
        try:
            if payload.kind == "consumable":
                await _google_consume(payload.productId, payload.purchaseToken)
            elif not v.get("acknowledged", False):
                await _google_acknowledge_subscription(payload.purchaseToken)
        except Exception:
            logger.exception("google_post_grant_housekeeping_failed")

    return VerifyResponse(
        valid=True,
        plan_id=res.get("plan_id"),
        pack_id=res.get("pack_id"),
        balance=res.get("balance"),
        expires_at=res.get("expires_at"),
        request_id=rid,
    )


@router.post("/iap/restore")
async def iap_restore(payload: RestoreRequest, user: dict = Depends(get_current_user)):
    rid = _new_request_id()
    restored = 0
    already_active = 0
    failed = 0

    for p in payload.purchases:
        product_id = p.get("productId")
        if not product_id or product_id not in SKU_TO_ENTITLEMENT:
            failed += 1
            continue
        try:
            if payload.platform == "ios":
                jws = p.get("jwsRepresentation") or p.get("purchaseToken")
                receipt = p.get("transactionReceipt")
                if jws:
                    v = await _verify_apple_jws(jws, product_id)
                elif receipt:
                    v = await _verify_apple_receipt(receipt, product_id)
                else:
                    failed += 1
                    continue
                provider = "apple"
                txn_id = v["original_transaction_id"]
            else:
                token = p.get("purchaseToken")
                if not token:
                    failed += 1
                    continue
                kind = SKU_TO_ENTITLEMENT[product_id]["kind"]
                if kind == "subscription":
                    v = await _verify_google_subscription(product_id, token)
                else:
                    v = await _verify_google_product(product_id, token)
                provider = "google"
                txn_id = v["transaction_id"]
        except VerificationFailure:
            failed += 1
            continue
        except UpstreamError:
            failed += 1
            continue

        res = await _grant_idempotent(
            provider=provider,
            transaction_id=txn_id,
            user_id=user["user_id"],
            product_id=product_id,
            raw_payload={"restored": True},
            expires_at=None,
        )
        if res["fresh"]:
            restored += 1
        else:
            already_active += 1

    return {
        "ok": True,
        "restored": restored,
        "already_active": already_active,
        "failed": failed,
        "request_id": rid,
    }


# ---------------------------------------------------------------------------
# Push token management
# ---------------------------------------------------------------------------
@router.post("/me/push-token")
async def register_push_token(payload: PushTokenRequest, user: dict = Depends(get_current_user)):
    rid = _new_request_id()
    if not EXPO_PUSH_TOKEN_RE.match(payload.expo_push_token):
        raise _err("bad_token_format", "Expected an Expo push token", rid, status=400)

    now = _now_iso()
    await db.push_tokens.update_one(
        {"_id": f"{user['user_id']}:{payload.expo_push_token}"},
        {
            "$set": {
                "user_id": user["user_id"],
                "expo_push_token": payload.expo_push_token,
                "platform": payload.platform,
                "device_id": payload.device_id,
                "updated_at": now,
                "last_seen_at": now,
                "revoked": False,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return {"ok": True, "request_id": rid}


@router.post("/me/push-token/revoke")
async def revoke_push_token(payload: PushTokenRevokeRequest, user: dict = Depends(get_current_user)):
    rid = _new_request_id()
    await db.push_tokens.update_one(
        {"_id": f"{user['user_id']}:{payload.expo_push_token}"},
        {"$set": {"revoked": True, "revoked_at": _now_iso()}},
    )
    return {"ok": True, "request_id": rid}
