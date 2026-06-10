"""
Instamojo provider unit tests — MAC verification, idempotency, amount-mismatch
guard, success-path credit grant. Pure unit tests; no live network calls.
"""
from __future__ import annotations

import os
import sys
import asyncio
import hmac
import hashlib
import uuid
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_database")
os.environ["INSTAMOJO_API_KEY"] = "test_api_key"
os.environ["INSTAMOJO_AUTH_TOKEN"] = "test_auth_token"
os.environ["INSTAMOJO_WEBHOOK_SECRET"] = "test_salt_xyz"
os.environ["INSTAMOJO_ENV"] = "test"

from payments.providers.instamojo import InstamojoProvider, _compute_mac  # noqa: E402
from db import db  # noqa: E402

_LOOP = asyncio.get_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


def _signed_payload(fields: dict, salt: str = "test_salt_xyz") -> dict:
    fields = {**fields}
    fields["mac"] = _compute_mac(fields, salt)
    return fields


def _body(fields: dict) -> bytes:
    return urlencode(fields).encode("utf-8")


def test_compute_mac_matches_instamojo_spec():
    """Spec: HMAC-SHA1 of pipe-joined sorted values, excluding `mac`."""
    payload = {"a": "1", "b": "2", "c": "3"}
    msg = "1|2|3"  # values sorted by key (a, b, c)
    expected = hmac.new(b"saltX", msg.encode(), hashlib.sha1).hexdigest()
    assert _compute_mac(payload, "saltX") == expected


def test_compute_mac_excludes_mac_field():
    payload_a = {"a": "1", "b": "2"}
    payload_b = {"a": "1", "b": "2", "mac": "ignored"}
    assert _compute_mac(payload_a, "saltX") == _compute_mac(payload_b, "saltX")


def test_status_unconfigured_when_creds_missing():
    saved = (os.environ.get("INSTAMOJO_API_KEY"), os.environ.get("INSTAMOJO_AUTH_TOKEN"), os.environ.get("INSTAMOJO_WEBHOOK_SECRET"))
    os.environ.pop("INSTAMOJO_API_KEY", None)
    try:
        prov = InstamojoProvider()
        st = prov.status()
        assert st.configured is False
        assert st.provider == "instamojo"
        assert st.env == "test"
    finally:
        os.environ["INSTAMOJO_API_KEY"], os.environ["INSTAMOJO_AUTH_TOKEN"], os.environ["INSTAMOJO_WEBHOOK_SECRET"] = saved


def test_status_configured_when_all_creds_present():
    prov = InstamojoProvider()
    st = prov.status()
    assert st.configured is True
    assert st.display_name == "Instamojo"


def test_webhook_invalid_mac_does_not_credit():
    def _go():
        return _run(_inner())

    async def _inner():
        order_id = f"inst_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_{uuid.uuid4().hex[:8]}"
        pr_id = f"MOJO{uuid.uuid4().hex[:8]}"
        await db.users.insert_one({"user_id": user_id, "email": f"inst_u_{uuid.uuid4().hex[:6]}@x.com", "plan_id": "free", "credits_balance": 0})
        await db.payment_orders.insert_one({
            "order_id": order_id, "user_id": user_id, "email": f"inst_u_{uuid.uuid4().hex[:6]}@x.com",
            "kind": "subscription", "plan_id": "pro", "pack_id": None,
            "credits": 2500, "amount": 1499.00, "amount_inr": 1499.00,
            "status": "pending", "provider": "instamojo", "env": "test",
            "instamojo_payment_request_id": pr_id,
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })
        payload = {
            "payment_request_id": pr_id, "payment_id": "MOJO_PAY_1",
            "amount": "1499.00", "status": "Credit", "buyer_name": "U",
            "buyer_email": f"inst_u_{uuid.uuid4().hex[:6]}@x.com",
            "mac": "0" * 40,   # invalid
        }
        prov = InstamojoProvider()
        result = await prov.handle_webhook(raw_body=_body(payload), headers={}, content_type="application/x-www-form-urlencoded")
        assert result.ok is False and result.reason == "invalid_mac"
        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 0
        order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
        assert order["status"] == "pending"
        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": order_id})

    _go()


def test_webhook_success_grants_credits_once():
    async def _inner():
        order_id = f"inst_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_{uuid.uuid4().hex[:8]}"
        pr_id = f"MOJO{uuid.uuid4().hex[:8]}"
        await db.users.insert_one({"user_id": user_id, "email": f"inst_u2_{uuid.uuid4().hex[:6]}@x.com", "plan_id": "free", "credits_balance": 0})
        await db.payment_orders.insert_one({
            "order_id": order_id, "user_id": user_id, "email": f"inst_u2_{uuid.uuid4().hex[:6]}@x.com",
            "kind": "subscription", "plan_id": "pro", "pack_id": None,
            "credits": 2500, "amount": 1499.00, "amount_inr": 1499.00,
            "status": "pending", "provider": "instamojo", "env": "test",
            "instamojo_payment_request_id": pr_id,
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })
        payment_id = f"MOJO_PAY_{uuid.uuid4().hex[:6]}"
        payload = _signed_payload({
            "payment_request_id": pr_id, "payment_id": payment_id,
            "amount": "1499.00", "status": "Credit",
            "buyer_name": "U", "buyer_email": f"inst_u2_{uuid.uuid4().hex[:6]}@x.com",
        })
        prov = InstamojoProvider()
        result = await prov.handle_webhook(raw_body=_body(payload), headers={}, content_type="application/x-www-form-urlencoded")
        assert result.ok is True and result.credited is True
        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 2500
        assert user["plan_id"] == "pro" and user["plan_status"] == "active"
        order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
        assert order["status"] == "paid"
        assert order.get("credited_at")

        # Replay → must not double-credit
        result2 = await prov.handle_webhook(raw_body=_body(payload), headers={}, content_type="application/x-www-form-urlencoded")
        assert result2.credited is False
        user2 = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user2["credits_balance"] == 2500

        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": order_id})
        await db.webhook_dedup.delete_many({"dedup_key": {"$regex": f"instamojo:{pr_id}:"}})

    _run(_inner())


def test_webhook_failed_status_does_not_credit():
    async def _inner():
        order_id = f"inst_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_{uuid.uuid4().hex[:8]}"
        pr_id = f"MOJO{uuid.uuid4().hex[:8]}"
        await db.users.insert_one({"user_id": user_id, "email": f"inst_u3_{uuid.uuid4().hex[:6]}@x.com", "plan_id": "free", "credits_balance": 0})
        await db.payment_orders.insert_one({
            "order_id": order_id, "user_id": user_id, "email": f"inst_u3_{uuid.uuid4().hex[:6]}@x.com",
            "kind": "topup", "plan_id": None, "pack_id": "topup_small",
            "credits": 300, "amount": 299.00, "amount_inr": 299.00,
            "status": "pending", "provider": "instamojo", "env": "test",
            "instamojo_payment_request_id": pr_id,
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })
        payload = _signed_payload({
            "payment_request_id": pr_id, "payment_id": f"MOJO_PAY_{uuid.uuid4().hex[:6]}",
            "amount": "299.00", "status": "Failed",
            "buyer_name": "U", "buyer_email": f"inst_u3_{uuid.uuid4().hex[:6]}@x.com",
            "failure_reason": "Card declined",
        })
        prov = InstamojoProvider()
        result = await prov.handle_webhook(raw_body=_body(payload), headers={}, content_type="application/x-www-form-urlencoded")
        assert result.ok is True and result.status == "failed" and result.credited is False
        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 0
        order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
        assert order["status"] == "failed"
        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": order_id})
        await db.webhook_dedup.delete_many({"dedup_key": {"$regex": f"instamojo:{pr_id}:"}})

    _run(_inner())


def test_webhook_amount_mismatch_blocks_credit():
    async def _inner():
        order_id = f"inst_test_{uuid.uuid4().hex[:10]}"
        user_id = f"user_{uuid.uuid4().hex[:8]}"
        pr_id = f"MOJO{uuid.uuid4().hex[:8]}"
        await db.users.insert_one({"user_id": user_id, "email": f"inst_u4_{uuid.uuid4().hex[:6]}@x.com", "plan_id": "free", "credits_balance": 0})
        await db.payment_orders.insert_one({
            "order_id": order_id, "user_id": user_id, "email": f"inst_u4_{uuid.uuid4().hex[:6]}@x.com",
            "kind": "subscription", "plan_id": "pro", "pack_id": None,
            "credits": 2500, "amount": 1499.00, "amount_inr": 1499.00,
            "status": "pending", "provider": "instamojo", "env": "test",
            "instamojo_payment_request_id": pr_id,
            "created_at": "1970-01-01", "updated_at": "1970-01-01",
        })
        # Attacker submits Credit with amount=1 — MAC is valid but amount is wrong
        payload = _signed_payload({
            "payment_request_id": pr_id, "payment_id": f"MOJO_PAY_{uuid.uuid4().hex[:6]}",
            "amount": "1.00", "status": "Credit",
            "buyer_name": "U", "buyer_email": f"inst_u4_{uuid.uuid4().hex[:6]}@x.com",
        })
        prov = InstamojoProvider()
        result = await prov.handle_webhook(raw_body=_body(payload), headers={}, content_type="application/x-www-form-urlencoded")
        assert result.ok is False and result.reason == "amount_mismatch"
        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        assert user["credits_balance"] == 0
        await db.users.delete_one({"user_id": user_id})
        await db.payment_orders.delete_one({"order_id": order_id})
        await db.webhook_dedup.delete_many({"dedup_key": {"$regex": f"instamojo:{pr_id}:"}})

    _run(_inner())


def test_webhook_unknown_payment_request_id_audit_only():
    async def _inner():
        unknown_pr = f"NEVER_{uuid.uuid4().hex[:8]}"
        payload = _signed_payload({
            "payment_request_id": unknown_pr, "payment_id": "X",
            "amount": "100.00", "status": "Credit",
            "buyer_name": "U", "buyer_email": f"inst_u_{uuid.uuid4().hex[:6]}@x.com",
        })
        prov = InstamojoProvider()
        result = await prov.handle_webhook(raw_body=_body(payload), headers={}, content_type="application/x-www-form-urlencoded")
        assert result.ok is False and result.reason == "order_not_found"
        # Audit log should have an entry for this arrival
        log = await db.webhook_logs.find_one({"payment_request_id": unknown_pr})
        assert log is not None
        assert log.get("mac_valid") is True
        await db.webhook_logs.delete_many({"payment_request_id": unknown_pr})

    _run(_inner())


if __name__ == "__main__":
    print("Run: pytest tests/test_payments_instamojo.py -v")
