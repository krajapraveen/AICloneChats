"""
Multi-provider email sender with automatic failover.

Architecture:
- Configured provider chain (env: EMAIL_PROVIDER_ORDER=resend,smtp).
- Each provider has its own send() with a hard timeout.
- send_email() walks the chain in order; first OK wins. All attempts logged
  to db.email_send_events for observability.
- Zero user-facing exposure of provider state. Callers receive a simple
  (ok, used_provider) tuple.

Why two providers minimum:
- Resend can fail for any of: API key revoked, domain not verified,
  rate-limit, transient network blip, DNS issue on Resend's side.
- SMTP via Zoho/Gmail Workspace/custom mailbox is a separate failure
  domain and uses different infrastructure end-to-end.

Skipped intentionally for scale:
- Circuit breaker — the chain itself is the retry; in-memory failure counters
  add complexity without measurable benefit at our volume.
- Quota prediction — Resend has no quota API. Dashboard-based monitoring is
  sufficient for now.
"""
from __future__ import annotations

import os
import asyncio
import smtplib
import logging
import time
import uuid
from email.message import EmailMessage
from typing import Optional, Tuple, List
from dataclasses import dataclass

import httpx

from db import db
from models import now_iso

logger = logging.getLogger(__name__)

# ----- Config -----
EMAIL_PROVIDER_ORDER = [
    p.strip().lower()
    for p in os.environ.get("EMAIL_PROVIDER_ORDER", "resend,smtp").split(",")
    if p.strip()
]

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
RESEND_FROM = os.environ.get("RESEND_FROM", "aiclonechats.com <admin@aiclonechats.com>")
RESEND_TIMEOUT_S = 20.0

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or "587")
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "").strip() or SMTP_USER
SMTP_USE_TLS = (os.environ.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes"))
SMTP_TIMEOUT_S = 15.0


@dataclass
class SendResult:
    ok: bool
    provider: str
    latency_ms: int
    error_code: Optional[str] = None


# ----- Provider implementations -----
async def _send_via_resend(to_email: str, subject: str, html: str, text: str) -> SendResult:
    started = time.monotonic()
    if not RESEND_API_KEY:
        return SendResult(False, "resend", 0, "not_configured")
    try:
        async with httpx.AsyncClient(timeout=RESEND_TIMEOUT_S) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={"from": RESEND_FROM, "to": [to_email], "subject": subject, "text": text, "html": html},
            )
        latency = int((time.monotonic() - started) * 1000)
        if 200 <= r.status_code < 300:
            return SendResult(True, "resend", latency, None)
        # Capture short non-200 reason without leaking provider internals to users
        return SendResult(False, "resend", latency, f"http_{r.status_code}")
    except Exception as e:
        latency = int((time.monotonic() - started) * 1000)
        return SendResult(False, "resend", latency, f"exc_{type(e).__name__}")


def _send_via_smtp_blocking(to_email: str, subject: str, html: str, text: str) -> Tuple[bool, Optional[str]]:
    """Plain smtplib in a worker thread. SSL/TLS based on SMTP_USE_TLS + port."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD and SMTP_FROM):
        return False, "not_configured"
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        if SMTP_PORT == 465:
            # Implicit SSL
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_S) as s:
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_S) as s:
                s.ehlo()
                if SMTP_USE_TLS:
                    s.starttls()
                    s.ehlo()
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.send_message(msg)
        return True, None
    except smtplib.SMTPResponseException as e:
        return False, f"smtp_{e.smtp_code}"
    except smtplib.SMTPException as e:
        return False, f"smtp_exc_{type(e).__name__}"
    except Exception as e:
        return False, f"exc_{type(e).__name__}"


async def _send_via_smtp(to_email: str, subject: str, html: str, text: str) -> SendResult:
    started = time.monotonic()
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        return SendResult(False, "smtp", 0, "not_configured")
    try:
        ok, err = await asyncio.to_thread(_send_via_smtp_blocking, to_email, subject, html, text)
        latency = int((time.monotonic() - started) * 1000)
        return SendResult(ok, "smtp", latency, None if ok else err)
    except Exception as e:
        latency = int((time.monotonic() - started) * 1000)
        return SendResult(False, "smtp", latency, f"exc_{type(e).__name__}")


PROVIDERS = {
    "resend": _send_via_resend,
    "smtp": _send_via_smtp,
}


# ----- Public API -----
async def send_email(
    to_email: str,
    subject: str,
    html: str,
    text: str,
    purpose: str = "transactional",
) -> Tuple[bool, Optional[str]]:
    """Try each configured provider in order. Logs every attempt. Returns
    (ok, used_provider_name). On total failure, used_provider_name is None.
    """
    chain = [p for p in EMAIL_PROVIDER_ORDER if p in PROVIDERS] or ["resend"]
    event_group = uuid.uuid4().hex[:12]
    attempted: List[SendResult] = []
    for provider_name in chain:
        sender = PROVIDERS[provider_name]
        result = await sender(to_email, subject, html, text)
        attempted.append(result)
        # Log every attempt
        try:
            await db.email_send_events.insert_one({
                "event_id": uuid.uuid4().hex,
                "event_group": event_group,
                "timestamp": now_iso(),
                "provider": result.provider,
                "purpose": purpose,
                "recipient_domain": (to_email.split("@", 1)[1] if "@" in to_email else ""),
                "ok": result.ok,
                "error_code": result.error_code,
                "latency_ms": result.latency_ms,
            })
        except Exception:
            # Never fail send on logging failure
            logger.warning("email_send_events log failed", exc_info=True)
        if result.ok:
            return True, result.provider
        logger.warning("email send via %s failed: %s", result.provider, result.error_code)
    logger.error("email send failed across all providers for %s: %s", to_email,
                 [(r.provider, r.error_code) for r in attempted])
    return False, None


def configured_providers() -> dict:
    """Read-only snapshot of provider configuration (no secrets)."""
    return {
        "order": EMAIL_PROVIDER_ORDER,
        "resend": {
            "configured": bool(RESEND_API_KEY),
            "from": RESEND_FROM if RESEND_API_KEY else None,
        },
        "smtp": {
            "configured": bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD),
            "host": SMTP_HOST or None,
            "port": SMTP_PORT,
            "from": SMTP_FROM or None,
            "use_tls": SMTP_USE_TLS,
        },
    }
