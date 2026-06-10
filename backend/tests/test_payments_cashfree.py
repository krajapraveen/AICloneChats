"""
Cashfree provider unit tests — signature verification (v3 spec), idempotency,
amount-mismatch attack, success/failure paths. Pure unit tests; no live network.
"""
from __future__ import annotations

import os
import sys
import asyncio
import hmac
import hashlib
import base64
import json
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_database")
os.environ["CASHFREE_APP_ID"] = "TEST_APP_ID_AAA"
os.environ["CASHFREE_SECRET_KEY"] = "TEST_SECRET_BBB"
os.environ["CASHFREE_WEBHOOK_SECRET"] = "TEST_WEBHOOK_CCC"
os.environ["CASHFREE_ENV"] = "prod"

from payments.providers.cashfree import (  # noqa: E402
    CashfreeProvider,
    _verify_webhook_signature,
    _normalize_phone,
)
from db import db  # noqa: E402

_LOOP = asyncio.get_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


def _sign(raw: bytes, timestamp: str, secret: str) -> str:
    msg = timestamp.encode("utf-8") + raw
    return base64.b64encode(hmac.new(secret.encode(), msg, hashlib.sha256).digest()).decode()


def test_signature_algorithm_matches_spec():
    """Cashfree v3: signature = base64(HMAC-SHA256(secret, timestamp + raw_body))."""
    raw = b'{"type":"PAYMENT_SUCCESS_WEBHOOK"}'
    ts = "1715000000"
    secret = "S"
    expected = base64.b64encode(hmac.new(b"S", ts.encode() + raw, hashlib.sha256).digest()).decode()
    assert _verify_webhook_signature(raw_body=raw, timestamp=ts, signature_b64=expected, secret=secret) is True


def test_signature_rejects_tampered_body():
    raw = b'{"type":"PAYMENT_SUCCESS_WEBHOOK"}'
    ts = "1715000000"
    sig = _sign(raw, ts, "S")
    # Tamper: same signature, but body has been changed
    assert _verify_webhook_signature(raw_body=b'{"type":"X"}', timestamp=ts, signature_b64=sig, secret="S") is False


def test_signature_rejects_wrong_secret():
    raw = b'{"type":"PAYMENT_SUCCESS_WEBHOOK"}'
    ts = "1715000000"
    sig = _sign(raw, ts, "S1")
    assert _verify_webhook_signature(raw_body=raw, timestamp=ts, signature_b64=sig, secret="S2") is False


def test_signature_rejects_missing_components():
    raw = b'{"x":1}'
    assert _verify_webhook_signature(raw_body=raw, timestamp="", signature_b64="abc", secret="S") is False
    assert _verify_webhook_signature(raw_body=raw, timestamp="123", signature_b64="", secret="S") is False
    assert _verify_webhook_signature(raw_body=raw, timestamp="123", signature_b64="abc", secret="") is False


def test_normalize_phone_strips_country_code_and_pads():
    assert _normalize_phone("+91 98765 43210") == "9876543210"
    assert _normalize_phone("") == "9999999999"
    assert _normalize_phone(None) == "9999999999"
    assert _normalize_phone("1234567") == "9999999999"   # too short → fallback


def test_status_unconfigured_when_secret_missing():
    saved = os.environ.get("CASHFREE_SECRET_KEY")
    os.environ.pop("CASHFREE_SECRET_KEY", None)
    try:
        st = CashfreeProvider().status()
        assert st.configured is False
        assert st.provider == "cashfree"
    finally:
        os.environ["CASHFREE_SECRET_KEY"] = saved


def test_status_configured_in_prod_mode():
    st = CashfreeProvider().status()
    assert st.configured is True
    assert st.env == "prod"
    assert st.display_name == "Cashfree"


def test_webhook_invalid_signature_does_not_credit():
    async def _go():
        order_id = f"cf_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_{uuid.uuid4().hex[:8]}"
        await db.users.insert_one({"user_id": user_id, "email": f"cf_u_{uuid.uuid4().hex[:6]}@x.com", "plan_id": "free", "credits_balance": 0})
        await db.payment_orders.insert_one({
            "order_id": order_id, "user_id": user_id, "email": f"cf_u_{uuid.uuid4().hex[:6]}@x.com",
            "kind": "subscription", "plan_id": "pro", "pack_id": None,
            "credits": 2500, "amount": 1499.00, "amount_inr": 1499.00,
            "status": "pending", "provider": "cashfree", "env": "prod",
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })
        raw = json.dumps({
            "type": "PAYMENT_SUCCESS_WEBHOOK",
            "data": {
                "order": {"order_id": order_id, "order_amount": 1499.0},
                "payment": {"cf_payment_id": "CF_PAY_1", "payment_status": "SUCCESS", "payment_amount": 1499.0},
            },
        }).encode()
        prov = CashfreeProvider()
        result = await prov.handle_webhook(
            raw_body=raw,
            headers={"x-webhook-signature": "0000", "x-webhook-timestamp": "12345"},
            content_type="application/json",
        )
        assert result.ok is False and result.reason == "invalid_signature"
        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 0
        order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
        assert order["status"] == "pending"
        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": order_id})

    _run(_go())


def test_webhook_success_grants_credits_once():
    async def _go():
        order_id = f"cf_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_{uuid.uuid4().hex[:8]}"
        await db.users.insert_one({"user_id": user_id, "email": f"cf_u2_{uuid.uuid4().hex[:6]}@x.com", "plan_id": "free", "credits_balance": 0})
        await db.payment_orders.insert_one({
            "order_id": order_id, "user_id": user_id, "email": f"cf_u2_{uuid.uuid4().hex[:6]}@x.com",
            "kind": "subscription", "plan_id": "pro", "pack_id": None,
            "credits": 2500, "amount": 1499.00, "amount_inr": 1499.00,
            "status": "pending", "provider": "cashfree", "env": "prod",
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })
        raw = json.dumps({
            "type": "PAYMENT_SUCCESS_WEBHOOK",
            "data": {
                "order": {"order_id": order_id, "order_amount": 1499.0},
                "payment": {"cf_payment_id": "CF_PAY_2", "payment_status": "SUCCESS", "payment_amount": 1499.0, "payment_group": "upi"},
            },
        }).encode()
        ts = "1715000000"
        sig = _sign(raw, ts, "TEST_WEBHOOK_CCC")
        prov = CashfreeProvider()
        result = await prov.handle_webhook(
            raw_body=raw,
            headers={"x-webhook-signature": sig, "x-webhook-timestamp": ts},
            content_type="application/json",
        )
        assert result.ok is True and result.credited is True
        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 2500
        assert user["plan_id"] == "pro" and user["plan_status"] == "active"
        order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
        assert order["status"] == "paid"
        assert order.get("credited_at")

        # Replay → must not double-credit
        result2 = await prov.handle_webhook(
            raw_body=raw,
            headers={"x-webhook-signature": sig, "x-webhook-timestamp": ts},
            content_type="application/json",
        )
        assert result2.credited is False
        user2 = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user2["credits_balance"] == 2500

        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": order_id})
        await db.webhook_dedup.delete_many({"dedup_key": {"$regex": f"cashfree:{order_id}:"}})

    _run(_go())


def test_webhook_failed_status_does_not_credit():
    async def _go():
        order_id = f"cf_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_{uuid.uuid4().hex[:8]}"
        await db.users.insert_one({"user_id": user_id, "email": f"cf_u3_{uuid.uuid4().hex[:6]}@x.com", "plan_id": "free", "credits_balance": 0})
        await db.payment_orders.insert_one({
            "order_id": order_id, "user_id": user_id, "email": f"cf_u3_{uuid.uuid4().hex[:6]}@x.com",
            "kind": "topup", "plan_id": None, "pack_id": "topup_small",
            "credits": 300, "amount": 299.00, "amount_inr": 299.00,
            "status": "pending", "provider": "cashfree", "env": "prod",
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })
        raw = json.dumps({
            "type": "PAYMENT_FAILED_WEBHOOK",
            "data": {
                "order": {"order_id": order_id, "order_amount": 299.0},
                "payment": {"cf_payment_id": "CF_PAY_3", "payment_status": "FAILED", "payment_amount": 299.0, "payment_message": "Card declined"},
            },
        }).encode()
        ts = "1715000001"
        sig = _sign(raw, ts, "TEST_WEBHOOK_CCC")
        prov = CashfreeProvider()
        result = await prov.handle_webhook(
            raw_body=raw,
            headers={"x-webhook-signature": sig, "x-webhook-timestamp": ts},
            content_type="application/json",
        )
        assert result.ok is True and result.status == "failed" and result.credited is False
        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 0
        order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
        assert order["status"] == "failed"
        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": order_id})
        await db.webhook_dedup.delete_many({"dedup_key": {"$regex": f"cashfree:{order_id}:"}})

    _run(_go())


def test_webhook_amount_mismatch_blocks_credit():
    async def _go():
        order_id = f"cf_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_{uuid.uuid4().hex[:8]}"
        await db.users.insert_one({"user_id": user_id, "email": f"cf_u4_{uuid.uuid4().hex[:6]}@x.com", "plan_id": "free", "credits_balance": 0})
        await db.payment_orders.insert_one({
            "order_id": order_id, "user_id": user_id, "email": f"cf_u4_{uuid.uuid4().hex[:6]}@x.com",
            "kind": "subscription", "plan_id": "pro", "pack_id": None,
            "credits": 2500, "amount": 1499.00, "amount_inr": 1499.00,
            "status": "pending", "provider": "cashfree", "env": "prod",
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })
        # Attacker crafts a valid-signature payload with amount=1
        raw = json.dumps({
            "type": "PAYMENT_SUCCESS_WEBHOOK",
            "data": {
                "order": {"order_id": order_id, "order_amount": 1.0},
                "payment": {"cf_payment_id": "CF_PAY_4", "payment_status": "SUCCESS", "payment_amount": 1.0},
            },
        }).encode()
        ts = "1715000002"
        sig = _sign(raw, ts, "TEST_WEBHOOK_CCC")
        prov = CashfreeProvider()
        result = await prov.handle_webhook(
            raw_body=raw,
            headers={"x-webhook-signature": sig, "x-webhook-timestamp": ts},
            content_type="application/json",
        )
        assert result.ok is False and result.reason == "amount_mismatch"
        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 0
        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": order_id})
        await db.webhook_dedup.delete_many({"dedup_key": {"$regex": f"cashfree:{order_id}:"}})

    _run(_go())


def test_webhook_unknown_order_audit_only():
    async def _go():
        unknown_order_id = f"NEVER_{uuid.uuid4().hex[:10]}"
        raw = json.dumps({
            "type": "PAYMENT_SUCCESS_WEBHOOK",
            "data": {
                "order": {"order_id": unknown_order_id, "order_amount": 100.0},
                "payment": {"cf_payment_id": "CF_X", "payment_status": "SUCCESS", "payment_amount": 100.0},
            },
        }).encode()
        ts = "1715000003"
        sig = _sign(raw, ts, "TEST_WEBHOOK_CCC")
        prov = CashfreeProvider()
        result = await prov.handle_webhook(
            raw_body=raw,
            headers={"x-webhook-signature": sig, "x-webhook-timestamp": ts},
            content_type="application/json",
        )
        assert result.ok is False and result.reason == "order_not_found"
        log = await db.webhook_logs.find_one({"order_id": unknown_order_id, "provider": "cashfree"})
        assert log is not None
        assert log.get("sig_valid") is True
        await db.webhook_logs.delete_many({"order_id": unknown_order_id})

    _run(_go())


if __name__ == "__main__":
    print("Run: pytest tests/test_payments_cashfree.py -v")
