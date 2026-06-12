"""Reply-To header for support-thread admin notifications.

When a user submits a recommendation or concern, the admin notification
email must carry a `Reply-To` header pointing to the user's email — so
admin clicking Reply in their mail client responds to the user, not to
the no-reply sender address.

Two layers of coverage:
  1. `email_sender.send_email` propagates `reply_to` into the Resend
     payload and into the SMTP `Reply-To` header.
  2. `support_inbox._notify_admins_new_thread` calls send_email with
     `reply_to=user.email`.
"""
from __future__ import annotations

import os
import sys
import asyncio
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv("/app/backend/.env")

from conftest import get_shared_loop  # noqa: E402


def _run(coro):
    return get_shared_loop().run_until_complete(coro)


# ───────────────────── email_sender layer ─────────────────────

def test_send_email_passes_reply_to_to_resend_payload(monkeypatch):
    """The Resend provider must include `reply_to` in its JSON payload
    when send_email is called with the kwarg."""
    import email_sender

    captured = {}

    class FakeResp:
        status_code = 200

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResp()

    monkeypatch.setattr(email_sender, "RESEND_API_KEY", "fake-key")
    monkeypatch.setattr(email_sender.httpx, "AsyncClient", FakeClient)

    # Also disable SMTP fallback so we only test the resend path
    monkeypatch.setattr(email_sender, "EMAIL_PROVIDER_ORDER", ["resend"])

    ok, provider = _run(email_sender.send_email(
        to_email="admin@example.com",
        subject="X",
        html="<p>X</p>",
        text="X",
        purpose="test",
        reply_to="user@example.com",
    ))
    assert ok is True
    assert provider == "resend"
    payload = captured["json"]
    assert payload["reply_to"] == "user@example.com", (
        "Resend payload must include reply_to so admin's Reply lands at the user"
    )


def test_send_email_without_reply_to_omits_field(monkeypatch):
    """If no reply_to is passed, the Resend payload must NOT include the
    `reply_to` key — letting the recipient's client fall back to From."""
    import email_sender

    captured = {}

    class FakeResp:
        status_code = 200

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return FakeResp()

    monkeypatch.setattr(email_sender, "RESEND_API_KEY", "fake-key")
    monkeypatch.setattr(email_sender.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(email_sender, "EMAIL_PROVIDER_ORDER", ["resend"])

    ok, _ = _run(email_sender.send_email(
        to_email="admin@example.com", subject="X", html="<p>X</p>", text="X", purpose="test",
    ))
    assert ok is True
    assert "reply_to" not in captured["json"]


def test_send_via_smtp_sets_reply_to_header(monkeypatch):
    """SMTP fallback must set the Reply-To header on the EmailMessage."""
    import email_sender

    captured_msg: list[EmailMessage] = []

    def fake_send(to_email, subject, html, text, reply_to=None):
        # This is the blocking helper — build the message ourselves the
        # same way the real implementation does, so we can assert on the
        # final headers.
        msg = EmailMessage()
        msg["From"] = "sender@example.com"
        msg["To"] = to_email
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        captured_msg.append(msg)
        return True, None

    monkeypatch.setattr(email_sender, "_send_via_smtp_blocking", fake_send)
    monkeypatch.setattr(email_sender, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_sender, "SMTP_USER", "u")
    monkeypatch.setattr(email_sender, "SMTP_PASSWORD", "p")
    monkeypatch.setattr(email_sender, "EMAIL_PROVIDER_ORDER", ["smtp"])

    ok, provider = _run(email_sender.send_email(
        to_email="admin@example.com",
        subject="Hi",
        html="<p>Hi</p>",
        text="Hi",
        purpose="test",
        reply_to="user@example.com",
    ))
    assert ok is True
    assert provider == "smtp"
    assert captured_msg
    assert captured_msg[0]["Reply-To"] == "user@example.com"


# ───────────────────── support_inbox call-site layer ─────────────────────
#
# Note (Feb 12, 2026 policy change): we no longer send any email when a user
# submits a concern/recommendation. Admins read the thread inside the in-app
# admin support inbox. The Reply-To plumbing in email_sender stays —
# transactional emails (password reset, etc.) still benefit from it — but
# the support-thread notification call site no longer triggers a send.
#
# The test below guards that policy: a new thread must NOT invoke
# send_email at all. Older versions of this file (pre-Feb 12) asserted the
# opposite — that send_email was called with reply_to=user.email. Those
# assertions are intentionally inverted now.

def test_notify_admins_new_thread_no_email_sent(monkeypatch):
    """Policy guard: _notify_admins_new_thread is a noop. It must not
    invoke email_sender.send_email under any circumstances (no email
    ping to admin@aiclonechats.com / krajapraveen@aiclonechats.com / etc.)."""
    import support_inbox

    sent_calls: list[dict] = []

    async def fake_send(**kwargs):
        sent_calls.append(kwargs)
        return True, "fake"

    monkeypatch.setattr("email_sender.send_email", fake_send)
    monkeypatch.setenv("ADMIN_EMAILS", "admin@aiclonechats.com,owner@aiclonechats.com")

    thread = {
        "thread_id": "th_test_1",
        "kind": "recommendation",
        "subject": "test123",
        "messages": [{"body": "test body"}],
    }
    user = {
        "email": "vishal7293kumar@gmail.com",
        "display_name": "Vishal",
        "user_id": "u_vishal",
    }
    _run(support_inbox._notify_admins_new_thread(thread, user))

    assert sent_calls == [], (
        "Per Feb 12 2026 policy, user concerns/recommendations stay in-app. "
        "_notify_admins_new_thread must NOT call email_sender.send_email."
    )
