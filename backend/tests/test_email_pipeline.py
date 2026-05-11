"""
Tests for the multi-provider email send pipeline reliability layer.
Covers:
- Public /api/email/health (anonymous, leak-safe shape)
- Admin /api/admin/email/health (auth gating + payload contract)
- /api/auth/verify-email/send (OTP send writes db.email_send_events)
- Failover event_group invariant (multiple attempts share event_group)
- /api/auth/forgot-password neutral acknowledgement
- Secret-leak guard: RESEND_API_KEY / SMTP_PASSWORD must not appear in responses
"""
from __future__ import annotations

import os
import sys
import time
import uuid
import asyncio
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "krajapraveen@gmail.com"
ADMIN_PASSWORD = "TestPass123!"
USER_EMAIL = "sr-tester@example.com"
USER_PASSWORD = "TestPass123!"

# Read backend env for cross-check (no value ever sent over the wire)
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")


# ---- helpers ----
def _login(email: str, password: str):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    if r.status_code != 200:
        return None
    data = r.json()
    return data.get("session_token") or data.get("token")


@pytest.fixture(scope="module")
def admin_token():
    tok = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    if not tok:
        pytest.skip("admin login failed")
    return tok


@pytest.fixture(scope="module")
def user_token():
    tok = _login(USER_EMAIL, USER_PASSWORD)
    if not tok:
        pytest.skip("user login failed")
    return tok


def _no_secret_leak(body_text: str):
    assert "RESEND_API_KEY" not in body_text
    if RESEND_API_KEY:
        assert RESEND_API_KEY not in body_text, "RESEND_API_KEY leaked in response"
    if SMTP_PASSWORD:
        assert SMTP_PASSWORD not in body_text, "SMTP_PASSWORD leaked in response"


# ---- Public probe ----
class TestPublicEmailHealth:
    def test_anonymous_shape_and_no_leak(self):
        r = requests.get(f"{API}/email/health", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        # Shape contract
        assert set(data.keys()) == {"healthy", "last_24h_attempts"}, f"unexpected keys: {list(data.keys())}"
        assert isinstance(data["healthy"], bool)
        assert isinstance(data["last_24h_attempts"], int)
        # Leak guards
        body = r.text.lower()
        for forbidden in ("resend", "smtp", "provider", "error_code", "recipient", "@"):
            assert forbidden not in body, f"public probe leaks '{forbidden}': {r.text}"
        _no_secret_leak(r.text)


# ---- Admin endpoint ----
class TestAdminEmailHealth:
    def test_requires_auth_401(self):
        r = requests.get(f"{API}/admin/email/health", timeout=15)
        # Some apps use 403 for missing-auth on admin routes — accept either gate.
        assert r.status_code in (401, 403), r.text

    def test_non_admin_forbidden_403(self, user_token):
        r = requests.get(
            f"{API}/admin/email/health",
            headers={"Authorization": f"Bearer {user_token}"},
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_admin_payload_contract(self, admin_token):
        r = requests.get(
            f"{API}/admin/email/health",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        # Top-level keys
        for key in ("configured", "totals_24h", "per_provider_24h", "recent"):
            assert key in d, f"missing {key}"
        cfg = d["configured"]
        assert "order" in cfg and isinstance(cfg["order"], list)
        assert "resend" in cfg and "configured" in cfg["resend"]
        assert "smtp" in cfg and "configured" in cfg["smtp"]
        # No raw secrets in admin response either
        _no_secret_leak(r.text)
        # Recent rows should not carry full recipient emails — only domain
        for ev in d.get("recent", []):
            for k, v in ev.items():
                if isinstance(v, str):
                    assert "@" not in v, f"recipient email leak in recent event field {k}: {v}"


# ---- OTP send writes email_send_events ----
class TestOtpSendWritesEvent:
    def test_otp_send_logs_event(self, user_token):
        # Snapshot count before
        admin_tok = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
        if not admin_tok:
            pytest.skip("admin login failed for snapshot")
        before = requests.get(
            f"{API}/admin/email/health",
            headers={"Authorization": f"Bearer {admin_tok}"},
            timeout=20,
        ).json()
        before_total = (before.get("totals_24h") or {}).get("total", 0)

        # Trigger send (sr-tester may already be verified — accept both branches)
        r = requests.post(
            f"{API}/auth/verify-email/send",
            headers={"Authorization": f"Bearer {user_token}"},
            timeout=30,
        )
        # Either OK with sent payload, already_verified, or 429 cooldown — all acceptable
        assert r.status_code in (200, 429), r.text
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        _no_secret_leak(r.text)

        if r.status_code == 200 and body.get("already_verified"):
            pytest.skip("user already verified; OTP send path is short-circuited.")
        if r.status_code == 429:
            pytest.skip("cooldown active — last test ran too recently")

        # Validate response shape
        assert body.get("ok") is True
        assert "sent" in body
        assert body.get("expires_in_seconds") == 600
        assert "email_send_configured" in body

        # Allow event log to be written
        time.sleep(1.5)

        after = requests.get(
            f"{API}/admin/email/health",
            headers={"Authorization": f"Bearer {admin_tok}"},
            timeout=20,
        ).json()
        after_total = (after.get("totals_24h") or {}).get("total", 0)
        assert after_total >= before_total + 1, "no new email_send_events row after OTP send"

        # Top recent row must be from our send: purpose=email_otp, domain matches user email
        recent = after.get("recent", [])
        assert recent, "recent list empty after send"
        top = recent[0]
        assert top.get("purpose") == "email_otp"
        assert top.get("recipient_domain") == USER_EMAIL.split("@", 1)[1]
        assert isinstance(top.get("latency_ms"), int)
        assert top.get("provider") in ("resend", "smtp")
        # Recipient full address must NOT be present
        assert USER_EMAIL not in str(top)


# ---- Failover event_group invariant ----
class TestFailoverEventGroup:
    """If multiple providers attempted for one logical send, they must share event_group.
    SMTP is intentionally unconfigured in preview (`not_configured`), and the failover
    only kicks in when Resend fails. We cannot force a Resend failure from the public API,
    so this test:
      a) verifies the event_group field is populated and reusable across rows,
      b) directly invokes the email_sender with a forced-bad chain to assert the invariant.
    """
    def test_event_group_present_on_recent(self, admin_token):
        d = requests.get(
            f"{API}/admin/email/health",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=20,
        ).json()
        recent = d.get("recent", [])
        if not recent:
            pytest.skip("no recent rows to inspect")
        for ev in recent[:10]:
            assert ev.get("event_group"), "event_group missing on a recent event"
            assert isinstance(ev["event_group"], str)
            assert len(ev["event_group"]) >= 8

    def test_forced_failover_writes_shared_event_group(self):
        """Walk the chain with both providers ⇒ resend fails (bad key), smtp not_configured.
        Both attempts MUST land in db.email_send_events under one event_group.
        """
        from motor.motor_asyncio import AsyncIOMotorClient

        async def run():
            # Force a fake resend key + ensure smtp stays unconfigured
            os.environ["RESEND_API_KEY"] = "re_invalid_forced_failover_test_key"
            os.environ["EMAIL_PROVIDER_ORDER"] = "resend,smtp"
            # Reload module so env reads land
            import importlib
            import email_sender
            importlib.reload(email_sender)

            ok, used = await email_sender.send_email(
                to_email="failover-probe@example.invalid",
                subject="failover test",
                html="<p>x</p>",
                text="x",
                purpose="failover_test",
            )
            # Both providers should have failed (resend: bad key, smtp: not_configured)
            assert ok is False
            assert used is None

            # Read the last 2 rows for this purpose
            client = AsyncIOMotorClient(os.environ["MONGO_URL"])
            dbh = client[os.environ["DB_NAME"]]
            rows = await dbh.email_send_events.find(
                {"purpose": "failover_test"},
                {"_id": 0, "event_group": 1, "provider": 1, "ok": 1, "error_code": 1},
                sort=[("timestamp", -1)],
            ).to_list(5)
            client.close()

            assert len(rows) >= 2, f"expected >=2 attempts logged, got {rows}"
            # Top two should share an event_group (one logical send)
            groups = {r["event_group"] for r in rows[:2]}
            assert len(groups) == 1, f"failover attempts did not share event_group: {rows[:2]}"
            providers = {r["provider"] for r in rows[:2]}
            assert providers == {"resend", "smtp"}, f"expected both providers in the group, got {providers}"
            for r in rows[:2]:
                assert r["ok"] is False
                assert r["error_code"]

        # restore env after
        original_key = RESEND_API_KEY
        try:
            asyncio.run(run())
        finally:
            os.environ["RESEND_API_KEY"] = original_key
            import importlib
            import email_sender
            importlib.reload(email_sender)


# ---- Forgot password still neutral ----
class TestForgotPasswordNeutral:
    def test_forgot_password_neutral_200(self):
        r = requests.post(
            f"{API}/auth/forgot-password",
            json={"email": "sr-tester@example.com"},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True
        assert d.get("code") == "neutral_acknowledgement"
        assert "request_id" in d
        _no_secret_leak(r.text)

    def test_forgot_password_unknown_email_still_neutral(self):
        r = requests.post(
            f"{API}/auth/forgot-password",
            json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com"},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True
        assert d.get("code") == "neutral_acknowledgement"
