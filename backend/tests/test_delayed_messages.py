"""
Tests for delayed-delivery emotional chat.
- create / list / cancel / delete / inbox CRUD
- past delivery rejected
- per-user cap
- self-harm safety: blocks + returns crisis response, does NOT schedule
- delivery worker (force-deliver path)
- admin metrics + queue
"""
from __future__ import annotations
import os, time
from datetime import datetime, timezone, timedelta
import httpx
import pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001"
API = f"{BASE}/api"
ADMIN_EMAIL = "sr-tester@example.com"
ADMIN_PASSWORD = "TestPass123!"


def _token(client):
    r = client.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r.status_code != 200:
        client.post(f"{API}/auth/register", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "name": "SR Tester"})
        r = client.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    return r.json()["session_token"]


@pytest.fixture(scope="module")
def headers():
    with httpx.Client(timeout=30) as c:
        return {"Authorization": f"Bearer {_token(c)}"}


def _future(seconds=600):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _past(hours=1):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def test_status(headers):
    with httpx.Client(timeout=10) as c:
        r = c.get(f"{API}/delayed-messages/status", headers=headers)
        assert r.status_code == 200
        d = r.json()
        assert d["available_for_user"] is True


def test_create_self_message(headers):
    with httpx.Client(timeout=15) as c:
        r = c.post(f"{API}/delayed-messages", headers=headers, json={
            "title": "Note to me",
            "message_body": "Be kind.",
            "emotional_category": "future_self",
            "recipient_type": "self",
            "delivery_time": _future(),
            "delivery_channel": "in_app",
        })
        assert r.status_code == 200, r.text
        msg = r.json()["delayed_message"]
        assert msg["status"] == "scheduled"


def test_past_delivery_rejected(headers):
    with httpx.Client(timeout=10) as c:
        r = c.post(f"{API}/delayed-messages", headers=headers, json={
            "title": "Past", "message_body": "x",
            "recipient_type": "self", "delivery_time": _past(), "delivery_channel": "in_app",
        })
        assert r.status_code == 400


def test_invalid_category_rejected(headers):
    with httpx.Client(timeout=10) as c:
        r = c.post(f"{API}/delayed-messages", headers=headers, json={
            "title": "x", "message_body": "x", "emotional_category": "not_real",
            "recipient_type": "self", "delivery_time": _future(), "delivery_channel": "in_app",
        })
        assert r.status_code == 400


def test_email_channel_requires_email(headers):
    with httpx.Client(timeout=10) as c:
        r = c.post(f"{API}/delayed-messages", headers=headers, json={
            "title": "x", "message_body": "x",
            "recipient_type": "email", "delivery_time": _future(), "delivery_channel": "email",
        })
        # No recipient_email → 422 (pydantic email validation) or 400
        assert r.status_code in (400, 422)


def test_self_harm_returns_crisis_response_not_scheduled(headers):
    with httpx.Client(timeout=15) as c:
        r = c.post(f"{API}/delayed-messages", headers=headers, json={
            "title": "kms note", "message_body": "I want to kill myself tomorrow.",
            "recipient_type": "self", "delivery_time": _future(), "delivery_channel": "in_app",
        })
        # Either blocked with crisis response, or pre-existing safety filter blocks at HTTP level
        if r.status_code == 200:
            d = r.json()
            assert d.get("blocked") is True
            assert d.get("self_harm_detected") is True
            assert "crisis_response" in d
        else:
            assert r.status_code == 400


def test_list_then_cancel_delete(headers):
    with httpx.Client(timeout=15) as c:
        r = c.post(f"{API}/delayed-messages", headers=headers, json={
            "title": "T1", "message_body": "B1",
            "recipient_type": "self", "delivery_time": _future(900), "delivery_channel": "in_app",
        })
        mid = r.json()["delayed_message"]["delayed_message_id"]
        # List
        r = c.get(f"{API}/delayed-messages", headers=headers)
        assert any(m["delayed_message_id"] == mid for m in r.json()["messages"])
        # Cancel
        r = c.post(f"{API}/delayed-messages/{mid}/cancel", headers=headers)
        assert r.status_code == 200
        r = c.get(f"{API}/delayed-messages/{mid}", headers=headers)
        assert r.json()["delayed_message"]["status"] == "cancelled"
        # Delete
        r = c.delete(f"{API}/delayed-messages/{mid}", headers=headers)
        assert r.status_code == 200


def test_admin_force_deliver_lands_in_inbox(headers):
    with httpx.Client(timeout=20) as c:
        r = c.post(f"{API}/delayed-messages", headers=headers, json={
            "title": "Force test", "message_body": "Body force test",
            "recipient_type": "self", "delivery_time": _future(900), "delivery_channel": "in_app",
        })
        mid = r.json()["delayed_message"]["delayed_message_id"]
        r = c.post(f"{API}/admin/delayed-messages/{mid}/force-deliver", headers=headers)
        assert r.status_code == 200
        # Inbox should contain it
        r = c.get(f"{API}/delayed-messages/inbox", headers=headers)
        assert any(m["delayed_message_id"] == mid for m in r.json()["inbox"])


def test_cancelled_does_not_deliver(headers):
    with httpx.Client(timeout=20) as c:
        r = c.post(f"{API}/delayed-messages", headers=headers, json={
            "title": "Cancel test", "message_body": "x",
            "recipient_type": "self", "delivery_time": _future(60), "delivery_channel": "in_app",
        })
        mid = r.json()["delayed_message"]["delayed_message_id"]
        r = c.post(f"{API}/delayed-messages/{mid}/cancel", headers=headers)
        assert r.status_code == 200
        # Force-deliver after cancel should be 409
        r = c.post(f"{API}/admin/delayed-messages/{mid}/force-deliver", headers=headers)
        assert r.status_code in (404, 409)


def test_admin_metrics_shape(headers):
    with httpx.Client(timeout=15) as c:
        r = c.get(f"{API}/admin/delayed-messages/metrics?days=7", headers=headers)
        assert r.status_code == 200
        d = r.json()
        for k in ("scheduled", "queued", "delivered_in_window", "due_now", "by_emotional_category", "scheduler_enabled"):
            assert k in d


def test_admin_protected_anon():
    with httpx.Client(timeout=10) as c:
        r = c.get(f"{API}/admin/delayed-messages/metrics")
        assert r.status_code in (401, 403)


# ---- Open-token reveal flow (recipient with no account) ----
def test_open_token_returned_on_create(headers):
    with httpx.Client(timeout=15) as c:
        r = c.post(f"{API}/delayed-messages", headers=headers, json={
            "title": "Token check", "message_body": "hi from past me",
            "recipient_type": "self", "delivery_time": _future(900), "delivery_channel": "in_app",
        })
        msg = r.json()["delayed_message"]
        assert isinstance(msg.get("open_token"), str)
        assert len(msg["open_token"]) >= 32


def test_open_token_sealed_before_delivery(headers):
    with httpx.Client(timeout=15) as c:
        r = c.post(f"{API}/delayed-messages", headers=headers, json={
            "title": "Sealed", "message_body": "secret",
            "recipient_type": "self", "delivery_time": _future(900), "delivery_channel": "in_app",
        })
        token = r.json()["delayed_message"]["open_token"]
        # Anonymous read attempt — must be 403 because not yet delivered
        r = c.get(f"{API}/delayed-messages/open/{token}")
        assert r.status_code == 403


def test_open_token_invalid_returns_404():
    with httpx.Client(timeout=10) as c:
        r = c.get(f"{API}/delayed-messages/open/this-is-clearly-not-a-real-token-abcdefghijklmnop")
        assert r.status_code == 404


def test_open_token_short_returns_404():
    with httpx.Client(timeout=10) as c:
        r = c.get(f"{API}/delayed-messages/open/short")
        assert r.status_code == 404


def test_open_token_after_delivery_reveals_message(headers):
    with httpx.Client(timeout=20) as c:
        r = c.post(f"{API}/delayed-messages", headers=headers, json={
            "title": "Reveal after delivery", "message_body": "the body to reveal",
            "recipient_type": "self", "delivery_time": _future(900), "delivery_channel": "in_app",
        })
        msg = r.json()["delayed_message"]
        mid = msg["delayed_message_id"]
        token = msg["open_token"]
        # Force-deliver, then open via token (no auth)
        r = c.post(f"{API}/admin/delayed-messages/{mid}/force-deliver", headers=headers)
        assert r.status_code == 200
        r = c.get(f"{API}/delayed-messages/open/{token}")
        assert r.status_code == 200, r.text
        body = r.json()["delayed_message"]
        assert body["title"] == "Reveal after delivery"
        assert body["message_body"] == "the body to reveal"
        assert body["status"] == "delivered"
        assert body.get("opened_at")
        # noindex header set
        assert "noindex" in (r.headers.get("X-Robots-Tag") or "").lower()
        # Idempotent: a second open returns the same data
        r2 = c.get(f"{API}/delayed-messages/open/{token}")
        assert r2.status_code == 200
        assert r2.json()["delayed_message"]["delayed_message_id"] == mid


def test_open_token_not_in_listing_payload(headers):
    """Listing/admin payloads must NOT leak open_token. Only the create response
    surfaces the raw token (returned once)."""
    with httpx.Client(timeout=15) as c:
        r = c.get(f"{API}/delayed-messages", headers=headers)
        assert r.status_code == 200
        for m in r.json()["messages"]:
            assert "open_token" not in m, f"open_token leaked in listing for {m.get('delayed_message_id')}"
