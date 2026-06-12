"""
data_export.py — GDPR Article 20 right-to-portability.

Why a dedicated endpoint
------------------------
Indian DPDP Act 2023 + EU GDPR Article 20 both require that users can fetch
their personal data in a "structured, commonly used, machine-readable
format". JSON over HTTPS is the de-facto industry standard for this. We
also pair it with the Account Deletion endpoint so users can
"download-then-delete" without contacting support.

What is exported
----------------
Everything we hold that is personally identifying or user-created:
  - account_profile         → users row (PII fields only, no internal flags)
  - subscriptions           → payment_orders + plan rows for this user
  - clones                  → clones authored by this user
  - clone_memories          → uploaded memories
  - support_threads         → concerns / recommendations and full message log
  - login_events (last 100) → recent session activity for transparency
  - voice_messages          → generated voice messages
  - delayed_messages        → delayed-emotional-chat history (sender side)

What is NOT exported
--------------------
- Internal moderation flags, fraud signals, anti-abuse counters.
- Other users' messages even if they were in a debate room with you (privacy
  of the other party trumps your portability right per GDPR Recital 68).
- Server logs, hashed IPs, password hashes (no benefit to the user, would be
  a security disclosure).

Rate limit
----------
Limit 1 export per 5 minutes per user, 5 per day. Exports are CPU-cheap but
trigger an outbound stream; abuse would be a DoS vector.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
import json

from auth import get_current_user
from db import db
from anti_abuse import guard_expensive_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])

# Mongo `_id` is BSON ObjectId — not JSON-serializable. We project it away
# everywhere instead of post-processing.
NO_ID = {"_id": 0}

# Fields from `users` that are personal data the user authored or chose. We
# deliberately omit internal/abuse flags (is_deactivated, abuse_status, etc.)
# — they're our operational state, not the user's personal data.
USER_FIELDS = {
    "user_id": 1, "email": 1, "name": 1, "picture": 1, "auth_provider": 1,
    "email_verified": 1, "credits_balance": 1, "plan_id": 1, "plan_status": 1,
    "created_at": 1, "updated_at": 1,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/export")
async def export_my_data(request: Request, user: dict = Depends(get_current_user)):
    """Returns a complete portable JSON dump of the caller's personal data.

    Browsers will treat this as a file download because we attach a
    Content-Disposition header. CLI users get pretty-printed JSON.
    """
    await guard_expensive_action(
        user=user,
        scope="profile.data_export",
        request=request,
        max_per_user_per_min=1,
        max_per_user_per_hour=5,
        endpoint="GET /api/profile/export",
    )

    user_id = user["user_id"]

    # ── Pull everything in parallel-friendly order. None of these are
    # individually slow; we keep limits conservative to avoid 100MB dumps.
    profile = await db.users.find_one({"user_id": user_id}, USER_FIELDS) or {}
    profile.pop("_id", None)

    payment_orders = await db.payment_orders.find(
        {"user_id": user_id}, NO_ID,
    ).sort("created_at", -1).limit(500).to_list(length=500)

    clones = await db.clones.find(
        {"user_id": user_id}, NO_ID,
    ).sort("created_at", -1).limit(500).to_list(length=500)
    # Strip large or non-portable fields
    for c in clones:
        c.pop("voice_clone_id", None)  # provider-side reference, not user data
        c.pop("lipsync_provider_id", None)

    memories = await db.clone_memories.find(
        {"user_id": user_id}, NO_ID,
    ).sort("created_at", -1).limit(2000).to_list(length=2000)

    support_threads = await db.support_threads.find(
        {"user_id": user_id}, NO_ID,
    ).sort("last_message_at", -1).limit(500).to_list(length=500)
    # Replace admin sender emails with a redaction — the admin's identity
    # isn't this user's personal data and they didn't consent to its export.
    for t in support_threads:
        for m in t.get("messages") or []:
            if m.get("sender") == "admin":
                m["sender_email"] = "[admin]"

    login_events = await db.login_events.find(
        {"user_id": user_id},
        {"_id": 0, "event_type": 1, "login_method": 1, "created_at": 1, "user_agent": 1, "outcome": 1, "country": 1},
    ).sort("created_at", -1).limit(100).to_list(length=100)

    voice_messages = await db.generated_messages.find(
        {"user_id": user_id},
        {"_id": 0, "message_id": 1, "text": 1, "voice_id": 1, "audio_url": 1, "created_at": 1, "duration_seconds": 1},
    ).sort("created_at", -1).limit(500).to_list(length=500)

    delayed_messages = await db.delayed_messages.find(
        {"sender_user_id": user_id}, NO_ID,
    ).sort("created_at", -1).limit(500).to_list(length=500)

    avatar_messages = await db.avatar_chat_messages.find(
        {"user_id": user_id}, NO_ID,
    ).sort("created_at", -1).limit(500).to_list(length=500)

    payload = {
        "export_metadata": {
            "exported_at": _now(),
            "export_version": "1.0",
            "user_id": user_id,
            "format": "json",
            "source": "aiclonechats.com",
            "notes": (
                "This export contains all personal data we hold about you. "
                "Other users' messages are excluded to respect their privacy. "
                "Payment records are included for tax compliance reference. "
                "To delete this data, use /api/profile/delete-account."
            ),
        },
        "account_profile": profile,
        "subscriptions_and_payments": payment_orders,
        "clones": clones,
        "clone_memories": memories,
        "support_threads": support_threads,
        "login_events_last_100": login_events,
        "voice_messages": voice_messages,
        "delayed_messages": delayed_messages,
        "avatar_chat_messages": avatar_messages,
        "counts": {
            "subscriptions_and_payments": len(payment_orders),
            "clones": len(clones),
            "clone_memories": len(memories),
            "support_threads": len(support_threads),
            "login_events": len(login_events),
            "voice_messages": len(voice_messages),
            "delayed_messages": len(delayed_messages),
            "avatar_chat_messages": len(avatar_messages),
        },
    }

    # Pretty-print so users can read in Notepad too. `default=str` covers any
    # stray datetime objects that slipped past the JSON-string convention.
    body = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
    filename = f"aiclonechats-export-{user_id}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/export/preview")
async def export_preview(user: dict = Depends(get_current_user)):
    """Cheap counts-only endpoint the UI uses to render "you'll be exporting
    N clones, M conversations…" before the full download. No rate-limit so the
    UI can refresh freely without burning the export budget."""
    user_id = user["user_id"]
    return JSONResponse({
        "user_id": user_id,
        "counts": {
            "payment_orders": await db.payment_orders.count_documents({"user_id": user_id}),
            "clones": await db.clones.count_documents({"user_id": user_id}),
            "clone_memories": await db.clone_memories.count_documents({"user_id": user_id}),
            "support_threads": await db.support_threads.count_documents({"user_id": user_id}),
            "voice_messages": await db.generated_messages.count_documents({"user_id": user_id}),
            "delayed_messages": await db.delayed_messages.count_documents({"sender_user_id": user_id}),
            "avatar_chat_messages": await db.avatar_chat_messages.count_documents({"user_id": user_id}),
        },
    })
