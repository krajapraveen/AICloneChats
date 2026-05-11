"""
Email OTP verification — gates the 50 free credit grant.

Flow:
  1. /api/auth/register creates the user with `email_verified=false` and
     `credits_balance=0`. NO credits granted yet.
  2. Client calls POST /api/auth/verify-email/send → we mint a 6-digit code,
     store its hash + expiry, send via Resend (or no-op if RESEND_API_KEY missing).
  3. Client calls POST /api/auth/verify-email/confirm with the code → we verify,
     mark user verified, then call grant_signup_credits_if_eligible() to issue
     the 50 free credits (subject to all anti-abuse checks).

We send the OTP at most once per 60 seconds and at most 5 times per 24h per user.
If RESEND_API_KEY is not configured, the endpoint succeeds but logs that the
email send was a no-op — useful during preview testing where credits can be
granted via the admin/QA escape hatch documented in test_credentials.md.
"""
from __future__ import annotations

import os
import re
import uuid
import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from db import db
from auth import get_current_user
from models import now_iso
from credits import grant_signup_credits_if_eligible
from email_sender import send_email as multi_send_email

router = APIRouter(prefix="/api/auth", tags=["auth-email-verify"])
logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "aiclonechats.com <admin@aiclonechats.com>")
OTP_TTL_SECONDS = 600   # 10 minutes
OTP_RESEND_COOLDOWN_SEC = 60
OTP_MAX_PER_DAY = 5

_OTP_RE = re.compile(r"^\d{6}$")


class ConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)
    device_id: Optional[str] = Field(default=None, max_length=128)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _client_ip(request: Request) -> Optional[str]:
    # Cloudflare / standard proxy headers, falling back to direct client.
    for h in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        v = request.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    return request.client.host if request.client else None


async def _send_otp_email(to_email: str, code: str) -> bool:
    html = f"""<div style="font-family:system-ui;max-width:480px;margin:0 auto;padding:24px;color:#111">
<h2 style="margin:0 0 8px">Confirm your email</h2>
<p style="color:#555;font-size:14px;margin:0 0 18px">Enter this code on aiclonechats.com to verify your account and unlock subscriptions.</p>
<div style="font-size:28px;font-weight:700;letter-spacing:0.18em;font-family:ui-monospace,monospace;padding:14px 18px;background:#f4f4f5;border-radius:10px;text-align:center">{code}</div>
<p style="color:#888;font-size:12px;margin:18px 0 0">Code expires in 10 minutes. If you didn't request this, ignore the email.</p>
</div>"""
    text = f"Your aiclonechats.com verification code is {code}. It expires in 10 minutes."
    ok, _provider = await multi_send_email(
        to_email=to_email,
        subject="Your verification code",
        html=html,
        text=text,
        purpose="email_otp",
    )
    return ok


@router.post("/verify-email/send")
async def send_verification_code(request: Request, user: dict = Depends(get_current_user)):
    """Mint an OTP for the authenticated user's email and dispatch it."""
    if user.get("email_verified"):
        return {"ok": True, "already_verified": True}

    # Rate limit: max 5/day, min 60s between sends.
    now_dt = datetime.now(timezone.utc)
    today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    cnt = await db.email_otp_codes.count_documents({
        "user_id": user["user_id"],
        "created_at": {"$gte": today},
    })
    if cnt >= OTP_MAX_PER_DAY:
        raise HTTPException(429, "Too many verification attempts today. Try again tomorrow.")
    last = await db.email_otp_codes.find_one(
        {"user_id": user["user_id"]},
        {"_id": 0, "created_at": 1},
        sort=[("created_at", -1)],
    )
    if last:
        try:
            last_dt = datetime.fromisoformat(last["created_at"].replace("Z", "+00:00"))
            if (now_dt - last_dt).total_seconds() < OTP_RESEND_COOLDOWN_SEC:
                raise HTTPException(429, f"Wait {OTP_RESEND_COOLDOWN_SEC}s between code requests.")
        except HTTPException:
            raise
        except Exception:
            pass

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = (now_dt + timedelta(seconds=OTP_TTL_SECONDS)).isoformat()
    await db.email_otp_codes.insert_one({
        "otp_id": uuid.uuid4().hex,
        "user_id": user["user_id"],
        "email": user["email"],
        "code_hash": _hash_code(code),
        "ip_address": _client_ip(request),
        "expires_at": expires_at,
        "used": False,
        "attempts": 0,
        "created_at": now_iso(),
    })
    sent = await _send_otp_email(user["email"], code)
    return {"ok": True, "sent": sent, "expires_in_seconds": OTP_TTL_SECONDS, "email_send_configured": bool(RESEND_API_KEY)}


@router.post("/verify-email/confirm")
async def confirm_verification_code(payload: ConfirmRequest, request: Request, user: dict = Depends(get_current_user)):
    if user.get("email_verified"):
        state = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0, "credits_balance": 1})
        return {"ok": True, "already_verified": True, "credits_balance": (state or {}).get("credits_balance", 0)}

    if not _OTP_RE.match(payload.code):
        raise HTTPException(400, "Code must be 6 digits")

    code_hash = _hash_code(payload.code)
    now_iso_str = now_iso()
    otp = await db.email_otp_codes.find_one_and_update(
        {
            "user_id": user["user_id"],
            "code_hash": code_hash,
            "used": False,
            "expires_at": {"$gt": now_iso_str},
        },
        {"$set": {"used": True, "used_at": now_iso_str}, "$inc": {"attempts": 1}},
        projection={"_id": 0},
    )
    if not otp:
        # Increment attempt counter on the most recent live code (anti-bruteforce)
        await db.email_otp_codes.update_one(
            {"user_id": user["user_id"], "used": False, "expires_at": {"$gt": now_iso_str}},
            {"$inc": {"attempts": 1}},
        )
        raise HTTPException(400, "Invalid or expired code")

    # Mark user verified
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"email_verified": True, "email_verified_at": now_iso_str}},
    )

    # Grant free credits (subject to anti-abuse rules)
    grant = await grant_signup_credits_if_eligible(
        user_id=user["user_id"],
        email=user["email"],
        ip_address=_client_ip(request),
        device_id=payload.device_id,
    )
    return {"ok": True, "verified": True, **grant}
