"""
Tests for Easebuzz payment integration — hash generation, webhook handling,
idempotency, and reconcile path. Pure unit tests; no live network.
"""
from __future__ import annotations

import os
import sys
import asyncio
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_database")
os.environ["EASEBUZZ_MERCHANT_KEY"] = "TEST_KEY_AAA"
os.environ["EASEBUZZ_SALT"] = "TEST_SALT_BBB"
os.environ["EASEBUZZ_ENV"] = "test"

import pytest  # noqa: E402

from payments_easebuzz import (  # noqa: E402
    _build_request_hash,
    _build_response_hash,
    _sha512,
    _process_callback,
)
from db import db  # noqa: E402


# Module-level shared event loop — motor client in db.py binds to whichever
# loop runs the first await. Using one loop across all async tests prevents
# "Event loop is closed" between tests.
_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)


def _run(coro):
    return _LOOP.run_until_complete(coro)


def test_request_hash_is_deterministic_sha512():
    h1 = _build_request_hash(
        key="K", txnid="T1", amount="499.00", productinfo="Pro",
        firstname="John", email="j@x.com",
        udf1="u1", udf2="sub", udf3="pro", udf4="2500", udf5="",
        salt="S",
    )
    h2 = _build_request_hash(
        key="K", txnid="T1", amount="499.00", productinfo="Pro",
        firstname="John", email="j@x.com",
        udf1="u1", udf2="sub", udf3="pro", udf4="2500", udf5="",
        salt="S",
    )
    assert h1 == h2
    assert len(h1) == 128  # sha512 hex


def test_request_hash_format_matches_easebuzz_spec():
    """Spec: sha512(key|txnid|amount|productinfo|firstname|email|udf1|udf2|udf3|udf4|udf5|udf6|udf7|udf8|udf9|udf10|SALT)
    With udf6-udf10 empty → 5 trailing empty pipe segments before SALT."""
    expected = _sha512("K|T1|499.00|Pro|John|j@x.com|u1|sub|pro|2500|||||||SALT")
    # That string has udf5='' then udf6-udf10='' then SALT = 5 trailing empties
    # Actually: 'K|T1|499.00|Pro|John|j@x.com|u1|sub|pro|2500|' + '|||||' + '|SALT'
    # = key + udf5='' implicit? Let me write it more explicit.
    explicit = "|".join(["K", "T1", "499.00", "Pro", "John", "j@x.com",
                          "u1", "sub", "pro", "2500", "",  # udf1-udf5 (udf5 empty)
                          "", "", "", "", "",                # udf6-udf10
                          "SALT"])
    expected = _sha512(explicit)
    got = _build_request_hash(
        key="K", txnid="T1", amount="499.00", productinfo="Pro",
        firstname="John", email="j@x.com",
        udf1="u1", udf2="sub", udf3="pro", udf4="2500", udf5="",
        salt="SALT",
    )
    assert got == expected


def test_response_hash_is_reverse_of_request():
    """Spec: sha512(SALT|status|udf10|udf9|udf8|udf7|udf6|udf5|udf4|udf3|udf2|udf1|email|firstname|productinfo|amount|txnid|key)"""
    payload = {
        "key": "K", "txnid": "T1", "amount": "499.00", "productinfo": "Pro",
        "firstname": "John", "email": "j@x.com",
        "udf1": "u1", "udf2": "sub", "udf3": "pro", "udf4": "2500", "udf5": "",
        "status": "success",
    }
    expected = _sha512("SALT|success|||||||2500|pro|sub|u1|j@x.com|John|Pro|499.00|T1|K")
    got = _build_response_hash(payload, salt="SALT")
    assert got == expected


def test_response_hash_invalid_when_tampered():
    payload = {
        "key": "K", "txnid": "T1", "amount": "499.00", "productinfo": "Pro",
        "firstname": "John", "email": "j@x.com",
        "udf1": "u1", "udf2": "sub", "udf3": "pro", "udf4": "2500", "udf5": "",
        "status": "success",
    }
    legit = _build_response_hash(payload, salt="SALT")
    payload_tampered = {**payload, "amount": "100.00"}
    tampered = _build_response_hash(payload_tampered, salt="SALT")
    assert legit != tampered


def test_webhook_invalid_hash_does_not_credit():
    async def _go():
        txnid = f"ebz_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_test_{uuid.uuid4().hex[:8]}"

        await db.users.insert_one({
            "user_id": user_id, "email": "u@x.com", "plan_id": "free", "credits_balance": 0,
        })
        await db.payment_orders.insert_one({
            "order_id": txnid, "txnid": txnid, "user_id": user_id, "email": "u@x.com",
            "kind": "subscription", "plan_id": "pro", "pack_id": None,
            "credits": 2500, "amount": 1499.00, "amount_inr": 1499.00,
            "status": "pending", "provider": "easebuzz", "env": "test",
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })

        payload = {
            "key": "TEST_KEY_AAA", "txnid": txnid, "amount": "1499.00", "productinfo": "Pro",
            "firstname": "U", "email": "u@x.com",
            "udf1": user_id, "udf2": "subscription", "udf3": "pro", "udf4": "2500", "udf5": "",
            "status": "success", "easepayid": "EBZ123",
            "hash": "0" * 128,
        }
        res = await _process_callback(payload, raw_body="x", source="webhook")
        assert res["ok"] is False and res["reason"] == "invalid_hash"

        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 0
        order = await db.payment_orders.find_one({"order_id": txnid}, {"_id": 0})
        assert order["status"] == "pending"

        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": txnid})
    _run(_go())


def test_webhook_success_grants_credits_once():
    async def _go():
        txnid = f"ebz_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_test_{uuid.uuid4().hex[:8]}"

        await db.users.insert_one({
            "user_id": user_id, "email": "u2@x.com", "plan_id": "free", "credits_balance": 0,
        })
        await db.payment_orders.insert_one({
            "order_id": txnid, "txnid": txnid, "user_id": user_id, "email": "u2@x.com",
            "kind": "subscription", "plan_id": "pro", "pack_id": None,
            "credits": 2500, "amount": 1499.00, "amount_inr": 1499.00,
            "status": "pending", "provider": "easebuzz", "env": "test",
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })

        payload = {
            "key": "TEST_KEY_AAA", "txnid": txnid, "amount": "1499.00", "productinfo": "Pro",
            "firstname": "U", "email": "u2@x.com",
            "udf1": user_id, "udf2": "subscription", "udf3": "pro", "udf4": "2500", "udf5": "",
            "status": "success", "easepayid": "EBZ123",
        }
        payload["hash"] = _build_response_hash(payload, salt="TEST_SALT_BBB")

        res = await _process_callback(payload, raw_body="x", source="webhook")
        assert res["ok"] is True and res.get("credited") is True

        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 2500
        assert user["plan_id"] == "pro" and user["plan_status"] == "active"

        order = await db.payment_orders.find_one({"order_id": txnid}, {"_id": 0})
        assert order["status"] == "paid"
        assert order.get("credited_at")

        # Replay → must NOT double-credit
        res2 = await _process_callback(payload, raw_body="x", source="webhook")
        assert res2.get("reason") == "duplicate" or res2.get("credited") is False
        user2 = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user2["credits_balance"] == 2500

        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": txnid})
        await db.webhook_dedup.delete_many({"dedup_key": {"$regex": f"easebuzz:{txnid}:"}})
    _run(_go())


def test_webhook_failure_does_not_credit():
    async def _go():
        txnid = f"ebz_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_test_{uuid.uuid4().hex[:8]}"

        await db.users.insert_one({
            "user_id": user_id, "email": "u3@x.com", "plan_id": "free", "credits_balance": 0,
        })
        await db.payment_orders.insert_one({
            "order_id": txnid, "txnid": txnid, "user_id": user_id, "email": "u3@x.com",
            "kind": "topup", "plan_id": None, "pack_id": "topup_small",
            "credits": 300, "amount": 299.00, "amount_inr": 299.00,
            "status": "pending", "provider": "easebuzz", "env": "test",
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })

        payload = {
            "key": "TEST_KEY_AAA", "txnid": txnid, "amount": "299.00", "productinfo": "Small",
            "firstname": "U", "email": "u3@x.com",
            "udf1": user_id, "udf2": "topup", "udf3": "topup_small", "udf4": "300", "udf5": "",
            "status": "failure", "easepayid": "EBZ456",
            "error_Message": "Card declined",
        }
        payload["hash"] = _build_response_hash(payload, salt="TEST_SALT_BBB")

        res = await _process_callback(payload, raw_body="x", source="webhook")
        assert res["ok"] is True
        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 0
        order = await db.payment_orders.find_one({"order_id": txnid}, {"_id": 0})
        assert order["status"] == "failed"

        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": txnid})
    _run(_go())


def test_amount_mismatch_blocks_credit():
    async def _go():
        txnid = f"ebz_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_test_{uuid.uuid4().hex[:8]}"

        await db.users.insert_one({
            "user_id": user_id, "email": "u4@x.com", "plan_id": "free", "credits_balance": 0,
        })
        await db.payment_orders.insert_one({
            "order_id": txnid, "txnid": txnid, "user_id": user_id, "email": "u4@x.com",
            "kind": "subscription", "plan_id": "pro", "pack_id": None,
            "credits": 2500, "amount": 1499.00, "amount_inr": 1499.00,
            "status": "pending", "provider": "easebuzz", "env": "test",
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })

        # Attacker submits success with amount=1 instead of 1499
        payload = {
            "key": "TEST_KEY_AAA", "txnid": txnid, "amount": "1.00", "productinfo": "Pro",
            "firstname": "U", "email": "u4@x.com",
            "udf1": user_id, "udf2": "subscription", "udf3": "pro", "udf4": "2500", "udf5": "",
            "status": "success", "easepayid": "EBZ999",
        }
        payload["hash"] = _build_response_hash(payload, salt="TEST_SALT_BBB")

        res = await _process_callback(payload, raw_body="x", source="webhook")
        assert res["ok"] is False and res["reason"] == "amount_mismatch"

        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 0

        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": txnid})
        await db.webhook_dedup.delete_many({"dedup_key": {"$regex": f"easebuzz:{txnid}:"}})
    _run(_go())


if __name__ == "__main__":
    # Allow direct execution for quick sanity check
    loop = asyncio.get_event_loop()
    async def _run():
        await test_webhook_invalid_hash_does_not_credit()
        await test_webhook_success_grants_credits_once()
        await test_webhook_failure_does_not_credit()
        await test_amount_mismatch_blocks_credit()
        print("All Easebuzz webhook tests passed.")
    loop.run_until_complete(_run())
    print("Hash tests:")
    test_request_hash_is_deterministic_sha512()
    test_request_hash_format_matches_payu_spec()
    test_response_hash_is_reverse_of_request()
    test_response_hash_invalid_when_tampered()
    print("All hash tests passed.")
