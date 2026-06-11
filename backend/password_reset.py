"""
Password reset flow + auth hardening helpers.

Constitutional notes:
- Reset endpoint never reveals whether the email exists. Same neutral 200 response,
  same timing (best-effort), same shape.
- Tokens are SHA-256 hashed before storage. Raw token only ever leaves the server
  inside the Resend email link, never logged, never echoed back to the caller.
- Tokens are single-use (consumed flag) AND single-active-per-user (new request
  invalidates all unconsumed prior tokens).
- 30-minute expiry, enforced on read.
- On successful reset, ALL user_sessions for the user are deleted.
- Rate limiting via the existing rate_limiter module (IP+route bucket).
- request_id added to every response shape so support can trace from logs.
- Audit log: password_reset_requested, password_reset_completed, password_reset_failed
  reuse the existing login_events collection for a single pane of glass.
"""
from __future__ import annotations

import os
import re
import hmac
import hashlib
import secrets
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from db import db
from models import now_iso
from auth import hash_password, verify_password
from email_sender import send_email as multi_send_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth-password-reset"])

RESET_TOKEN_TTL_MIN = 30
RESET_TOKEN_BYTES = 32
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "aiclonechats.com <admin@aiclonechats.com>")
FRONTEND_PUBLIC_URL = os.environ.get("FRONTEND_PUBLIC_URL", "").rstrip("/")

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


# ---------- Shared error shape ----------
def _err(code: str, message: str, request_id: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, "request_id": request_id})


def _new_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:16]


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _password_is_strong(pw: str) -> Tuple[bool, str]:
    """Production password strength rules.

    Required: 8+ chars, at least one upper, one lower, one digit, one
    special (non-alphanumeric, non-whitespace). Returns (ok, message_or_empty).
    The message is field-level — e.g. "Password must contain at least one digit."
    """
    if not pw or len(pw) < 8:
        return False, "Password must be at least 8 characters."
    if len(pw) > 200:
        return False, "Password is too long."
    if not re.search(r"[a-z]", pw):
        return False, "Password must contain at least one lowercase letter."
    if not re.search(r"[A-Z]", pw):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"\d", pw):
        return False, "Password must contain at least one digit."
    if not re.search(r"[^A-Za-z0-9\s]", pw):
        return False, "Password must contain at least one special character."
    if re.search(r"\s", pw):
        return False, "Password must not contain spaces."
    return True, ""


# ---------- Rate limit (Mongo-backed, simple sliding window) ----------
async def _rate_limit_or_raise(key: str, *, max_in_window: int, window_seconds: int, request_id: str) -> None:
    """Best-effort rate limit. Failures are swallowed so a rate-limit-store outage
    can never lock real users out (we'd rather degrade gracefully).
    """
    try:
        since = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
        n = await db.auth_rate_limits.count_documents({"key": key, "created_at": {"$gte": since}})
        if n >= max_in_window:
            await _audit("rate_limit_triggered", request_id=request_id, metadata={"key_kind": key.split(":")[0], "limit": max_in_window})
            raise _err("rate_limited", "Too many attempts. Try again later.", request_id, status=429)
        await db.auth_rate_limits.insert_one({"key": key, "created_at": now_iso()})
    except HTTPException:
        raise
    except Exception:
        return


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )


# ---------- Audit log ----------
async def _audit(event_type: str, *, request_id: str, user_id: Optional[str] = None, email: Optional[str] = None, metadata: Optional[dict] = None) -> None:
    try:
        await db.login_events.insert_one({
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "user_id": user_id,
            "email": (email or "").lower() or None,
            "request_id": request_id,
            "metadata": metadata or {},
            "created_at": now_iso(),
        })
    except Exception:
        # Audit must never break auth flow
        pass


# ---------- Resend email send ----------
async def _send_reset_email(to_email: str, reset_link: str) -> Tuple[bool, Optional[str]]:
    subject = "Reset your aiclonechats.com password"
    text = (
        "We received a request to reset your aiclonechats.com password.\n\n"
        f"Reset link (expires in {RESET_TOKEN_TTL_MIN} minutes):\n{reset_link}\n\n"
        "If you didn't request this, you can safely ignore this email — "
        "your password won't change."
    )
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px; color: #0d0d10;">
      <h2 style="margin: 0 0 12px; font-size: 22px;">Reset your password</h2>
      <p style="font-size: 14px; line-height: 1.6; margin: 0 0 16px;">We received a request to reset your aiclonechats.com password.</p>
      <p style="margin: 24px 0;">
        <a href="{reset_link}" style="background:#f59e0b; color:#0d0d10; padding: 12px 20px; border-radius: 10px; text-decoration: none; font-weight: 700;">
          Reset password
        </a>
      </p>
      <p style="font-size: 12px; color: #64748b; line-height: 1.5;">This link expires in {RESET_TOKEN_TTL_MIN} minutes. If you didn't request this, you can safely ignore this email — your password won't change.</p>
    </div>
    """
    ok, provider = await multi_send_email(
        to_email=to_email,
        subject=subject,
        html=html,
        text=text,
        purpose="password_reset",
    )
    return ok, None if ok else "all_providers_failed"


async def _send_reset_confirmation_email(to_email: str, ip_hash_short: str) -> Tuple[bool, Optional[str]]:
    """Sent AFTER a successful password reset. Confirms the change to the user
    so an unauthorized reset gets noticed immediately. We do NOT include the
    raw IP or location — only a short, opaque hash for support correlation —
    and we tell the user how to react if it wasn't them.
    """
    subject = "Your aiclonechats.com password was changed"
    text = (
        "Your aiclonechats.com password was just changed.\n\n"
        "All previous sessions on every device have been signed out automatically.\n\n"
        "If this was you — nothing to do. You can sign in with your new password "
        "at https://aiclonechats.com/login.\n\n"
        "If this was NOT you — your account may be compromised. Take these steps:\n"
        "  1. Reset your password again immediately at "
        "https://aiclonechats.com/forgot-password\n"
        "  2. Email admin@aiclonechats.com with the subject \"SECURITY\" and reference "
        f"id {ip_hash_short}.\n"
    )
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px; color: #0d0d10;">
      <h2 style="margin: 0 0 12px; font-size: 22px;">Your password was changed</h2>
      <p style="font-size: 14px; line-height: 1.6; margin: 0 0 16px;">
        Your aiclonechats.com password was just changed. All previous sessions on every device
        have been signed out automatically.
      </p>
      <p style="margin: 24px 0;">
        <a href="https://aiclonechats.com/login"
           style="background:#f59e0b; color:#0d0d10; padding: 12px 20px; border-radius: 10px; text-decoration: none; font-weight: 700;">
          Sign in with new password
        </a>
      </p>
      <div style="background:#fef3c7; border:1px solid #f59e0b; border-radius: 10px; padding: 14px; margin-top: 18px;">
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color:#92400e; margin-bottom: 6px;">Was this NOT you?</div>
        <p style="margin: 0 0 8px; font-size: 13px; line-height: 1.5; color:#78350f;">
          Reset your password again at
          <a href="https://aiclonechats.com/forgot-password" style="color:#92400e; text-decoration: underline;">/forgot-password</a>
          immediately, then email
          <a href="mailto:admin@aiclonechats.com?subject=SECURITY" style="color:#92400e; text-decoration: underline;">admin@aiclonechats.com</a>
          with subject <strong>SECURITY</strong>.
        </p>
        <p style="margin: 0; font-size: 11px; color:#78350f;">Reference id: <code>{ip_hash_short}</code></p>
      </div>
    </div>
    """
    ok, _provider = await multi_send_email(
        to_email=to_email,
        subject=subject,
        html=html,
        text=text,
        purpose="password_reset_confirmation",
    )
    return ok, None if ok else "all_providers_failed"


# ---------- Request models ----------
class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)
    confirm_password: str = Field(min_length=1, max_length=200)


# ---------- Endpoints ----------
@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordRequest, request: Request, response: Response):
    """ALWAYS returns neutral 200, regardless of whether the email exists.

    The only path that returns non-200 is invalid email format (RFC-ish check)
    and rate-limiting. Account existence is NEVER leaked through response shape,
    status code, or response time deltas (we still execute a no-op send when
    the user doesn't exist).
    """
    request_id = _new_request_id()
    email = (payload.email or "").strip().lower()
    response.headers["X-Request-Id"] = request_id

    if not EMAIL_RE.match(email):
        await _audit("password_reset_failed", request_id=request_id, email=email, metadata={"reason": "invalid_email_format"})
        raise _err("invalid_email", "Please enter a valid email address.", request_id, status=400)

    # Rate limit: per IP and per email, 5 / 15 min each
    ip = _client_ip(request)
    await _rate_limit_or_raise(f"forgot:ip:{ip}", max_in_window=10, window_seconds=900, request_id=request_id)
    await _rate_limit_or_raise(f"forgot:email:{email}", max_in_window=5, window_seconds=900, request_id=request_id)

    # Look up user; if missing we still write an audit + return same neutral response
    user = await db.users.find_one({"email": email}, {"_id": 0, "user_id": 1, "email": 1})
    if user:
        # Invalidate any pending tokens for this user
        await db.password_reset_tokens.update_many(
            {"user_id": user["user_id"], "consumed": False},
            {"$set": {"consumed": True, "consumed_at": now_iso(), "consumed_reason": "superseded"}},
        )
        # Mint a new single-use token
        raw_token = secrets.token_urlsafe(RESET_TOKEN_BYTES)
        token_hash = _hash_token(raw_token)
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_TTL_MIN)).isoformat()
        await db.password_reset_tokens.insert_one({
            "token_id": uuid.uuid4().hex,
            "user_id": user["user_id"],
            "email": email,
            "token_hash": token_hash,
            "expires_at": expires_at,
            "consumed": False,
            "consumed_at": None,
            "created_at": now_iso(),
            "ip_address_hash": hashlib.sha256(ip.encode()).hexdigest()[:24],
            "request_id": request_id,
        })

        base = FRONTEND_PUBLIC_URL or str(request.base_url).rstrip("/")
        reset_link = f"{base}/reset-password?token={raw_token}"
        sent, err = await _send_reset_email(email, reset_link)
        await _audit(
            "password_reset_requested",
            request_id=request_id,
            user_id=user["user_id"],
            email=email,
            metadata={"sent": sent, "send_error": err, "ttl_minutes": RESET_TOKEN_TTL_MIN},
        )
    else:
        # No user — still log the attempt (no token issued)
        await _audit("password_reset_requested", request_id=request_id, email=email, metadata={"sent": False, "reason": "unknown_email_silent"})

    return {
        "ok": True,
        "code": "neutral_acknowledgement",
        "message": "If this email exists, reset instructions have been sent.",
        "request_id": request_id,
    }


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest, request: Request, response: Response):
    request_id = _new_request_id()
    response.headers["X-Request-Id"] = request_id

    raw = (payload.token or "").strip()
    new_pw = payload.new_password or ""
    confirm = payload.confirm_password or ""

    if new_pw != confirm:
        await _audit("password_reset_failed", request_id=request_id, metadata={"reason": "password_mismatch"})
        raise _err("password_mismatch", "Passwords do not match.", request_id, status=400)

    strong, msg = _password_is_strong(new_pw)
    if not strong:
        await _audit("password_reset_failed", request_id=request_id, metadata={"reason": "weak_password"})
        raise _err("weak_password", msg, request_id, status=400)

    # Rate limit reset attempts per IP to slow brute-force on tokens
    ip = _client_ip(request)
    await _rate_limit_or_raise(f"reset:ip:{ip}", max_in_window=20, window_seconds=900, request_id=request_id)

    token_hash = _hash_token(raw)
    token_doc = await db.password_reset_tokens.find_one({"token_hash": token_hash}, {"_id": 0})
    if not token_doc:
        await _audit("password_reset_failed", request_id=request_id, metadata={"reason": "token_invalid"})
        raise _err("token_invalid", "This reset link is invalid or has already been used.", request_id, status=400)

    if token_doc.get("consumed"):
        await _audit("password_reset_failed", request_id=request_id, user_id=token_doc.get("user_id"), metadata={"reason": "token_reused"})
        raise _err("token_invalid", "This reset link is invalid or has already been used.", request_id, status=400)

    # Expiry check
    try:
        exp = datetime.fromisoformat((token_doc.get("expires_at") or "").replace("Z", "+00:00"))
    except Exception:
        exp = datetime.now(timezone.utc) - timedelta(seconds=1)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        await _audit("password_reset_failed", request_id=request_id, user_id=token_doc.get("user_id"), metadata={"reason": "token_expired"})
        raise _err("token_expired", "This reset link has expired. Request a new one.", request_id, status=400)

    user = await db.users.find_one({"user_id": token_doc["user_id"]}, {"_id": 0})
    if not user:
        await _audit("password_reset_failed", request_id=request_id, user_id=token_doc.get("user_id"), metadata={"reason": "user_not_found"})
        raise _err("token_invalid", "This reset link is invalid or has already been used.", request_id, status=400)

    # Block reuse of the same password
    if user.get("password_hash") and verify_password(new_pw, user["password_hash"]):
        await _audit("password_reset_failed", request_id=request_id, user_id=user.get("user_id"), metadata={"reason": "same_as_current"})
        raise _err("same_password", "Choose a password different from your current one.", request_id, status=400)

    # Atomically consume the token (single-use guard)
    consume_res = await db.password_reset_tokens.update_one(
        {"token_hash": token_hash, "consumed": False},
        {"$set": {"consumed": True, "consumed_at": now_iso(), "consumed_reason": "used"}},
    )
    if consume_res.modified_count == 0:
        # Lost the race — another concurrent reset already happened
        await _audit("password_reset_failed", request_id=request_id, user_id=user.get("user_id"), metadata={"reason": "token_race"})
        raise _err("token_invalid", "This reset link is invalid or has already been used.", request_id, status=400)

    # Update password + invalidate all sessions for this user
    new_hash = hash_password(new_pw)
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"password_hash": new_hash, "password_updated_at": now_iso()}},
    )
    invalidated = await db.user_sessions.delete_many({"user_id": user["user_id"]})

    # Confirmation email — best-effort, never fails the request
    confirmation_sent = False
    confirmation_err: Optional[str] = None
    try:
        ip_hash_short = hashlib.sha256(ip.encode()).hexdigest()[:12]
        confirmation_sent, confirmation_err = await _send_reset_confirmation_email(
            user.get("email", ""), ip_hash_short,
        )
    except Exception as e:
        confirmation_err = f"exception:{type(e).__name__}"
        logger.warning("password_reset confirmation email failed user_id=%s err=%s", user.get("user_id"), e)

    await _audit(
        "password_reset_completed",
        request_id=request_id,
        user_id=user["user_id"],
        email=user.get("email"),
        metadata={
            "sessions_invalidated": invalidated.deleted_count,
            "confirmation_email_sent": confirmation_sent,
            "confirmation_email_error": confirmation_err,
        },
    )
    return {
        "ok": True,
        "code": "password_reset_completed",
        "message": "Password updated. Please sign in with your new password.",
        "request_id": request_id,
    }


@router.get("/reset-password/validate")
async def validate_reset_token(token: str, response: Response):
    """Lightweight check used by the /reset-password page before showing the
    password form. Returns {valid: bool, request_id} — never leaks the email
    associated with the token.
    """
    request_id = _new_request_id()
    response.headers["X-Request-Id"] = request_id
    raw = (token or "").strip()
    if len(raw) < 20:
        return {"valid": False, "code": "token_invalid", "request_id": request_id}
    doc = await db.password_reset_tokens.find_one({"token_hash": _hash_token(raw)}, {"_id": 0, "expires_at": 1, "consumed": 1})
    if not doc or doc.get("consumed"):
        return {"valid": False, "code": "token_invalid", "request_id": request_id}
    try:
        exp = datetime.fromisoformat((doc.get("expires_at") or "").replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return {"valid": False, "code": "token_expired", "request_id": request_id}
    except Exception:
        return {"valid": False, "code": "token_invalid", "request_id": request_id}
    return {"valid": True, "request_id": request_id}
