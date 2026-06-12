"""
account_lifecycle.py — User-initiated account deletion (Apple/Google compliance).

Why this file
-------------
Apple Guideline 5.1.1(v) and Google Play User Data policy require in-app
account deletion with no support-ticket workaround. This module implements
the canonical delete flow:

  1. Authenticated POST /api/profile/delete-account
  2. Password re-confirmation for email/password users
  3. Explicit confirmation flag for Google OAuth users
  4. Immediate hard-anonymize of the user document + cascade cleanup
  5. All sessions invalidated, future logins impossible
  6. Confirmation email sent (best-effort) for an audit trail
  7. Admin-visible event row in `account_deletion_events`

Design choices (deliberate)
---------------------------
- We anonymize-in-place instead of full row delete. Reason: foreign-key-like
  references exist across `support_threads`, `payment_orders`, `webhook_logs`,
  `login_events`. Hard-deleting rows would break audit history we are legally
  required to keep for payment reconciliation. Personal identifiers (email,
  name, picture, password hash) ARE wiped; financial/abuse audit identifiers
  (user_id, hashed IPs, order IDs) stay so a court order can still be served.
- Clones owned by the user are unpublished (visibility=private, is_deleted=True)
  but not row-deleted, because public clones may be chatting with other users
  whose conversation history would be corrupted by a hard delete.
- No grace-period / undo. We surface a "this cannot be undone" confirmation in
  the UI. Adding undo later requires only flipping `is_deleted` and restoring
  a backup of the personal fields, which we deliberately do NOT keep (zero
  ghost-PII rule).
"""
from __future__ import annotations

import os
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user, verify_password
from db import db
from anti_abuse import guard_expensive_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])

ADMIN_EMAILS = {
    e.lower().strip()
    for e in (os.environ.get("ADMIN_EMAILS", "") or "").split(",")
    if e.strip()
}
ADMIN_UNLIMITED_EMAIL = (os.environ.get("ADMIN_UNLIMITED_EMAIL", "") or "").lower().strip()
if ADMIN_UNLIMITED_EMAIL:
    ADMIN_EMAILS.add(ADMIN_UNLIMITED_EMAIL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _anon_email(user_id: str) -> str:
    """Deterministic, unique, clearly-fake replacement email so the unique
    index on `users.email` keeps working post-deletion."""
    return f"deleted_{user_id}@deleted.local"


class DeleteAccountRequest(BaseModel):
    confirm: bool = Field(..., description="Must be exactly true.")
    password: str | None = Field(default=None, description="Required for email/password users.")
    reason: str | None = Field(default=None, max_length=500)


@router.post("/delete-account")
async def delete_account(payload: DeleteAccountRequest, request: Request, user: dict = Depends(get_current_user)):
    # Admins cannot self-delete via this endpoint — protects against accidental
    # lockout of the only admin account. Admins must be removed via a Mongo
    # tool by another admin.
    email = (user.get("email") or "").lower().strip()
    if email in ADMIN_EMAILS or user.get("role") == "admin":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "admin_cannot_self_delete",
                "message": "Admin accounts cannot self-delete. Contact another administrator.",
            },
        )

    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail={"code": "confirmation_required", "message": "You must confirm to proceed."},
        )

    user_id = user["user_id"]
    auth_provider = (user.get("auth_provider") or "email").lower()

    # Cheap input validation BEFORE the rate limiter so bad-input doesn't burn
    # the per-minute budget (the test suite, and real users hitting the form,
    # may legitimately submit twice within seconds).
    if auth_provider == "email" and not payload.password:
        raise HTTPException(
            status_code=400,
            detail={"code": "password_required", "message": "Password is required to delete your account."},
        )

    # Rate-limit AFTER shape validation so a compromised session can't loop-
    # delete (e.g. with future undo). 1/min, 3/hour per user is plenty.
    await guard_expensive_action(
        user=user,
        scope="profile.delete_account",
        request=request,
        max_per_user_per_min=1,
        max_per_user_per_hour=3,
        endpoint="POST /api/profile/delete-account",
    )

    # Password re-confirmation only for email/password users. Google users
    # already proved identity at the OAuth provider in their CURRENT session.
    if auth_provider == "email":
        full_user = await db.users.find_one({"user_id": user_id}, {"password_hash": 1})
        if not full_user or not full_user.get("password_hash"):
            raise HTTPException(
                status_code=400,
                detail={"code": "password_not_set", "message": "Your account has no password set. Use the Forgot Password flow first."},
            )
        if not verify_password(payload.password, full_user["password_hash"]):
            raise HTTPException(
                status_code=401,
                detail={"code": "invalid_password", "message": "Password is incorrect."},
            )

    # ---- Perform deletion ----
    deletion_id = "del_" + uuid.uuid4().hex[:18]
    deletion_at = _now()
    original_email = email
    anon_email = _anon_email(user_id)

    # 1. Anonymize user row (in-place; preserves user_id for audit FK integrity)
    await db.users.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "email": anon_email,
                "name": "Deleted User",
                "picture": "",
                "password_hash": "",
                "is_deleted": True,
                "deleted_at": deletion_at,
                "deletion_id": deletion_id,
                "deletion_reason": (payload.reason or "")[:500],
                "auth_provider": auth_provider,  # keep so admin can see the origin
                "is_deactivated": True,
                "plan_id": "free",
                "plan_status": "deleted",
                "credits_balance": 0,
                "updated_at": deletion_at,
            }
        },
    )

    # 2. Wipe every active session — user is signed out everywhere immediately
    sessions_deleted = (await db.user_sessions.delete_many({"user_id": user_id})).deleted_count

    # 3. Delete pending password-reset tokens; they would be a re-entry vector
    pwd_tokens_deleted = (await db.password_reset_tokens.delete_many({"user_id": user_id})).deleted_count

    # 4. Delete email OTP codes
    try:
        otps_deleted = (await db.email_otp_codes.delete_many({"user_id": user_id})).deleted_count
    except Exception:
        otps_deleted = 0

    # 5. Anonymize support threads (keep for admin audit; strip PII)
    await db.support_threads.update_many(
        {"user_id": user_id},
        {"$set": {"user_email": anon_email, "is_user_deleted": True}},
    )

    # 6. Unpublish clones — keep the row for ongoing conversations of OTHER
    # users who started chatting with that clone, but make it un-discoverable.
    clones_unpublished = (await db.clones.update_many(
        {"user_id": user_id},
        {"$set": {"visibility": "private", "is_deleted": True, "deleted_at": deletion_at}},
    )).modified_count

    # 7. Delete personal memory rows the user uploaded for their own clones
    try:
        memories_deleted = (await db.clone_memories.delete_many({"user_id": user_id})).deleted_count
    except Exception:
        memories_deleted = 0

    # 8. Record an audit event row
    audit_doc = {
        "deletion_id": deletion_id,
        "user_id": user_id,
        "original_email_hash": _email_hash(original_email),  # one-way hash; lets admin verify a re-signup attempt
        "auth_provider": auth_provider,
        "reason": (payload.reason or "")[:500],
        "deleted_at": deletion_at,
        "ip_hash": _ip_hash(request),
        "user_agent": (request.headers.get("user-agent") or "")[:200],
        "cascade_summary": {
            "sessions_deleted": sessions_deleted,
            "password_reset_tokens_deleted": pwd_tokens_deleted,
            "otps_deleted": otps_deleted,
            "clones_unpublished": clones_unpublished,
            "memories_deleted": memories_deleted,
        },
    }
    await db.account_deletion_events.insert_one(audit_doc)

    # 9. Best-effort confirmation email — never blocks the response
    try:
        await _send_deletion_confirmation_email(original_email, deletion_id, deletion_at)
    except Exception as e:
        logger.warning("delete_account: confirmation email failed for %s: %s", user_id, e)

    return {
        "ok": True,
        "deletion_id": deletion_id,
        "deleted_at": deletion_at,
        "message": "Your account and personal data have been deleted. You have been signed out.",
    }


def _email_hash(email: str) -> str:
    import hashlib
    return hashlib.sha256((email or "").encode("utf-8")).hexdigest()


def _ip_hash(request: Request) -> str:
    import hashlib
    ip = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:32]


async def _send_deletion_confirmation_email(to_email: str, deletion_id: str, deletion_at: str) -> None:
    """Tell the user their account was deleted — addresses a common Apple
    review note: 'user must receive confirmation of deletion'."""
    from email_sender import send_email

    subject = "Your AI Clone Chats account has been deleted"
    text = (
        f"Hi,\n\n"
        f"This confirms that your account on aiclonechats.com was permanently deleted on {deletion_at}.\n\n"
        f"What we removed:\n"
        f"  - Your email address and profile\n"
        f"  - Your password\n"
        f"  - Active sessions and reset tokens\n"
        f"  - Personal clone memories you uploaded\n"
        f"  - Your clones have been unpublished\n\n"
        f"What we kept (for legal/audit only, fully anonymized to you):\n"
        f"  - Payment records (required by Indian tax + Cashfree reconciliation rules)\n"
        f"  - Anonymized support threads (for moderation history)\n\n"
        f"You can re-create an account at any time using this email — it is no\n"
        f"longer linked to the deleted user.\n\n"
        f"Deletion reference: {deletion_id}\n"
        f"\n"
        f"— AI Clone Chats\n"
    )
    html = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:540px;margin:0 auto;padding:20px;color:#0d0d10;">
      <div style="background:#0d0d10;color:#f3f3f5;padding:8px 12px;border-radius:6px;display:inline-block;
                  font-size:10px;font-family:monospace;text-transform:uppercase;letter-spacing:1px;font-weight:700;">
        ACCOUNT DELETED
      </div>
      <h2 style="margin:12px 0 8px;font-size:20px;">Your account has been permanently deleted.</h2>
      <p style="font-size:14px;line-height:1.6;color:#444;">
        This is a courtesy confirmation that your AI Clone Chats account was deleted on
        <strong>{deletion_at}</strong>.
      </p>
      <h3 style="margin-top:24px;font-size:14px;text-transform:uppercase;letter-spacing:1px;">What we removed</h3>
      <ul style="font-size:13px;color:#444;line-height:1.7;padding-left:20px;">
        <li>Email, name, profile picture, password</li>
        <li>Active sessions and password-reset tokens</li>
        <li>Personal clone memories</li>
        <li>Your published clones (unpublished + marked deleted)</li>
      </ul>
      <h3 style="margin-top:20px;font-size:14px;text-transform:uppercase;letter-spacing:1px;">What we kept (audit-only)</h3>
      <ul style="font-size:13px;color:#444;line-height:1.7;padding-left:20px;">
        <li>Payment records (Indian tax + Cashfree reconciliation)</li>
        <li>Anonymized support threads</li>
      </ul>
      <p style="font-size:11px;color:#888;margin-top:24px;">
        Deletion reference: <code>{deletion_id}</code>
      </p>
      <p style="font-size:11px;color:#888;">
        You may re-create an account with this email at any time.
      </p>
    </div>
    """
    await send_email(
        to_email=to_email,
        subject=subject,
        html=html,
        text=text,
        purpose="account_deletion_confirmation",
    )


async def ensure_indexes() -> None:
    try:
        await db.account_deletion_events.create_index("deletion_id", unique=True)
        await db.account_deletion_events.create_index([("deleted_at", -1)])
        await db.account_deletion_events.create_index("user_id")
        logger.info("account_lifecycle: indexes ensured")
    except Exception as e:
        logger.warning("account_lifecycle: index creation failed: %s", e)
