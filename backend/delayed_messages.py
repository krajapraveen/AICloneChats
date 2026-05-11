"""
Delayed-Delivery Emotional Chat — admin/QA-gated.

Lets a user write an emotional message and schedule it for future delivery to:
  - their future self (in-app inbox)
  - any email recipient (requires RESEND_API_KEY)
  - another aiclonechats.com user (in-app, looked up by user_id)

Delivery channels:
  - in_app: writes to delayed_message_inbox + emits event
  - email: Resend transactional send (no-op when RESEND_API_KEY missing → records failure)
  - both

Background scheduler:
  - Single asyncio loop (started by server.py at startup)
  - Polls every 30s for status="scheduled" AND delivery_time <= now
  - Marks queued → delivered/failed
  - Idempotent (status guards)

Safety:
  - Past delivery time rejected at write
  - Per-user cap: MAX_DELAYED_MESSAGES_PER_USER active scheduled
  - Self-harm content triggers crisis-safe response, NOT scheduled
  - Email rate limit per sender: 5 per 24h
  - Recipient email validated against simple regex; admin can disable email channel

Strict analytics separation: experience_variant="delayed_emotional_v1".
"""
from __future__ import annotations

import os
import re
import uuid
import logging
import asyncio
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, EmailStr

from db import db
from auth import get_current_user
from credit_guard import charge_credits_or_402, fresh_user
from models import now_iso
from safety_filter import moderate_user_input, log_moderation_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/delayed-messages", tags=["delayed-messages"])
admin_router = APIRouter(prefix="/api/admin/delayed-messages", tags=["delayed-messages-admin"])

EXPERIENCE_VARIANT = "delayed_emotional_v1"
DELAYED_EMOTIONAL_CHAT_ENABLED = os.environ.get("DELAYED_EMOTIONAL_CHAT_ENABLED", "false").lower() == "true"
DELAYED_DELIVERY_CRON_ENABLED = os.environ.get("DELAYED_DELIVERY_CRON_ENABLED", "true").lower() == "true"
MAX_DELAYED_MESSAGES_PER_USER = int(os.environ.get("MAX_DELAYED_MESSAGES_PER_USER", "50"))
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "aiclonechats.com <hello@aiclonechats.com>")

EMOTIONAL_CATEGORIES = {"future_self", "apology", "memory", "motivation", "love", "grief", "custom"}
RECIPIENT_TYPES = {"self", "email", "clone_user", "clone"}
DELIVERY_CHANNELS = {"in_app", "email", "both"}
SCHEDULER_POLL_SEC = int(os.environ.get("DELAYED_SCHEDULER_POLL_SEC", "30"))
EMAIL_RATE_LIMIT_PER_DAY = 5
MAX_DELIVERY_ATTEMPTS = 3

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _make_open_token() -> str:
    """Returns an unguessable capability token. Looked up by exact match."""
    return secrets.token_urlsafe(32)


# ---- Models ----
class CreateDelayedMessageRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    message_body: str = Field(min_length=1, max_length=4000)
    emotional_category: str = Field(default="future_self")
    recipient_type: str = Field(default="self")
    recipient_email: Optional[EmailStr] = None
    recipient_user_id: Optional[str] = None
    clone_id: Optional[str] = None  # required when recipient_type == "clone"
    source_conversation_id: Optional[str] = None  # link back to the originating clone conversation
    delivery_time: str  # ISO8601 future timestamp
    timezone: Optional[str] = Field(default="UTC")
    delivery_channel: str = Field(default="in_app")


class UpdateDelayedMessageRequest(BaseModel):
    title: Optional[str] = None
    message_body: Optional[str] = None
    delivery_time: Optional[str] = None


# ---- Feature gate ----
def _is_feature_available(user: Optional[dict]) -> bool:
    if DELAYED_EMOTIONAL_CHAT_ENABLED:
        return True
    return bool(user and user.get("role") == "admin")


def _require_feature(user: Optional[dict]) -> None:
    if not _is_feature_available(user):
        raise HTTPException(503, "delayed_messages_unavailable")


def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


# ---- Analytics ----
async def _emit(event_name: str, *, message_id: Optional[str] = None, user_id: Optional[str] = None, metadata: Optional[dict] = None) -> None:
    await db.delayed_message_events.insert_one({
        "event_id": uuid.uuid4().hex,
        "event_type": event_name,
        "delayed_message_id": message_id,
        "user_id": user_id,
        "metadata": {**(metadata or {}), "experience_variant": EXPERIENCE_VARIANT},
        "created_at": now_iso(),
    })


# ---- Vendor: Resend (email) ----
async def _send_email(to_email: str, subject: str, body: str, open_url: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Returns (success, error_message). No-op success=False when key missing.
    If open_url is provided, the message body is plain context only — the
    email surfaces a clear CTA to open the full sealed message on the web
    (where the noindex reveal page lives). Recipients without an account
    can therefore still read what was written for them."""
    if not RESEND_API_KEY:
        return False, "resend_api_key_missing"
    try:
        import requests
        # Build a tiny HTML version. Plain text first, then minimally wrapped.
        safe_body = body.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        cta_html = ""
        cta_text = ""
        if open_url:
            cta_html = f"""<div style="margin:24px 0">
<a href="{open_url}" style="display:inline-block;padding:12px 22px;border:2px solid #111;border-radius:999px;background:#111;color:#fff;text-decoration:none;font-family:ui-monospace,monospace;font-size:13px;letter-spacing:0.08em;text-transform:uppercase">Open the message</a>
</div>
<div style="font-size:12px;color:#666;word-break:break-all">If the button doesn't work, paste this link in your browser:<br/>{open_url}</div>"""
            cta_text = f"\n\nOpen the full message:\n{open_url}\n"
        html = f"""<div style="font-family:system-ui,-apple-system,sans-serif;max-width:560px;margin:0 auto;padding:24px;line-height:1.55;color:#111;">
<h2 style="margin:0 0 12px">{subject}</h2>
<div style="white-space:pre-wrap">{safe_body}</div>
{cta_html}
<hr style="margin:24px 0;border:none;border-top:1px solid #e5e5e5"/>
<div style="font-size:12px;color:#666">Delivered via aiclonechats.com — Delayed Emotional Chat. The system delivers; it does not chase.</div>
</div>"""
        text_body = body + cta_text

        def _do() -> tuple[bool, Optional[str]]:
            try:
                r = requests.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                    json={"from": RESEND_FROM, "to": [to_email], "subject": subject, "text": text_body, "html": html},
                    timeout=20,
                )
                if r.status_code in (200, 201, 202):
                    return True, None
                return False, f"resend_{r.status_code}: {r.text[:200]}"
            except Exception as inner:
                return False, str(inner)[:300]

        return await asyncio.to_thread(_do)
    except Exception as e:
        return False, str(e)[:300]


# ---- Helpers ----
def _public(d: dict) -> dict:
    return {
        "delayed_message_id": d.get("delayed_message_id"),
        "sender_user_id": d.get("sender_user_id"),
        "recipient_type": d.get("recipient_type"),
        "recipient_user_id": d.get("recipient_user_id"),
        "recipient_email": d.get("recipient_email"),
        "clone_id": d.get("clone_id"),
        "source_conversation_id": d.get("source_conversation_id"),
        "title": d.get("title"),
        "message_body": d.get("message_body"),
        "emotional_category": d.get("emotional_category"),
        "delivery_time": d.get("delivery_time"),
        "timezone": d.get("timezone"),
        "status": d.get("status"),
        "delivery_channel": d.get("delivery_channel"),
        "delivery_attempts": d.get("delivery_attempts", 0),
        "opened_at": d.get("opened_at"),
        "delivered_at": d.get("delivered_at"),
        "cancelled_at": d.get("cancelled_at"),
        "failure_reason": d.get("failure_reason"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


def _parse_dt(iso: str) -> datetime:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception as e:
        raise HTTPException(400, f"invalid delivery_time: {e}")


# ---- Routes: feature status + CRUD ----
@router.get("/status")
async def feature_status(user: Optional[dict] = Depends(get_current_user)):
    return {
        "enabled_for_public": DELAYED_EMOTIONAL_CHAT_ENABLED,
        "available_for_user": _is_feature_available(user),
        "email_configured": bool(RESEND_API_KEY),
        "max_per_user": MAX_DELAYED_MESSAGES_PER_USER,
        "categories": sorted(EMOTIONAL_CATEGORIES),
        "recipient_types": sorted(RECIPIENT_TYPES),
        "delivery_channels": sorted(DELIVERY_CHANNELS),
    }


@router.post("")
async def create_delayed(payload: CreateDelayedMessageRequest, user: dict = Depends(get_current_user)):
    _require_feature(user)

    # Validation
    if payload.emotional_category not in EMOTIONAL_CATEGORIES:
        raise HTTPException(400, f"emotional_category must be one of {sorted(EMOTIONAL_CATEGORIES)}")
    if payload.recipient_type not in RECIPIENT_TYPES:
        raise HTTPException(400, f"recipient_type must be one of {sorted(RECIPIENT_TYPES)}")
    if payload.delivery_channel not in DELIVERY_CHANNELS:
        raise HTTPException(400, f"delivery_channel must be one of {sorted(DELIVERY_CHANNELS)}")

    dt = _parse_dt(payload.delivery_time)
    now = datetime.now(timezone.utc)
    if dt <= now + timedelta(seconds=30):
        raise HTTPException(400, "delivery_time must be at least 30 seconds in the future")
    if dt > now + timedelta(days=365 * 10):
        raise HTTPException(400, "delivery_time too far in the future (max 10 years)")

    # Recipient resolution
    recipient_email = None
    recipient_user_id = None
    clone_id_resolved: Optional[str] = None
    if payload.recipient_type == "self":
        recipient_user_id = user["user_id"]
    elif payload.recipient_type == "email":
        if not payload.recipient_email or not _EMAIL_RE.match(payload.recipient_email):
            raise HTTPException(400, "valid recipient_email required for type=email")
        recipient_email = str(payload.recipient_email)
        if payload.delivery_channel == "in_app":
            raise HTTPException(400, "type=email requires delivery_channel=email or both")
    elif payload.recipient_type == "clone_user":
        if not payload.recipient_user_id:
            raise HTTPException(400, "recipient_user_id required for type=clone_user")
        target = await db.users.find_one({"user_id": payload.recipient_user_id}, {"_id": 0, "user_id": 1})
        if not target:
            raise HTTPException(404, "Recipient user not found")
        recipient_user_id = payload.recipient_user_id
    elif payload.recipient_type == "clone":
        # Sealed message addressed to a clone — delivered to the SENDER'S inbox
        # at delivery time, tagged with the clone so they can re-enter that
        # conversation with the message visible. The clone does NOT autonomously
        # do anything with it. The sender remains the only audience.
        if not payload.clone_id:
            raise HTTPException(400, "clone_id required for type=clone")
        clone_doc = await db.clones.find_one({"clone_id": payload.clone_id}, {"_id": 0, "clone_id": 1})
        if not clone_doc:
            raise HTTPException(404, "Clone not found")
        clone_id_resolved = payload.clone_id
        recipient_user_id = user["user_id"]  # delivered into sender's inbox
        if payload.delivery_channel != "in_app":
            raise HTTPException(400, "type=clone requires delivery_channel=in_app")

    # Per-user cap (active = scheduled + queued)
    active = await db.delayed_messages.count_documents({
        "sender_user_id": user["user_id"],
        "status": {"$in": ["scheduled", "queued"]},
    })
    if active >= MAX_DELAYED_MESSAGES_PER_USER:
        raise HTTPException(429, f"max_per_user_reached ({MAX_DELAYED_MESSAGES_PER_USER})")

    # Safety: title + body
    title_check = moderate_user_input(payload.title)
    body_check = moderate_user_input(payload.message_body)
    blocked = title_check["action"] == "block" or body_check["action"] == "block"
    self_harm_detected = (
        title_check.get("category") == "self_harm" or body_check.get("category") == "self_harm"
    )
    if blocked:
        await log_moderation_event(db, user_id=user["user_id"], route="delayed_messages", source="user_input", result=body_check if body_check["action"] == "block" else title_check, action_taken="block_input")
        if self_harm_detected:
            return {
                "blocked": True,
                "self_harm_detected": True,
                "crisis_response": (
                    "We hear that something hurts right now. We won't schedule this — putting it on a future "
                    "calendar doesn't help today. If you're in crisis, please reach out: "
                    "988 (US Suicide & Crisis Lifeline), or your local equivalent. "
                    "Talking to someone now is the right move."
                ),
            }
        raise HTTPException(400, "This message could not be saved because it may violate safety rules.")

    # Email rate limit
    if payload.delivery_channel in ("email", "both"):
        since_24h = (now - timedelta(hours=24)).isoformat()
        recent_emails = await db.delayed_messages.count_documents({
            "sender_user_id": user["user_id"],
            "delivery_channel": {"$in": ["email", "both"]},
            "created_at": {"$gte": since_24h},
        })
        if recent_emails >= EMAIL_RATE_LIMIT_PER_DAY:
            raise HTTPException(429, f"email_rate_limit ({EMAIL_RATE_LIMIT_PER_DAY}/24h)")

    # ---- Credit gate (delayed_create = 4 credits, charged on creation) ----
    user_doc = await fresh_user(user)
    credit_handle = await charge_credits_or_402(user_doc, surface="delayed_create")  # noqa: F841

    delayed_id = f"dm_{uuid.uuid4().hex[:14]}"
    open_token = _make_open_token()
    doc = {
        "delayed_message_id": delayed_id,
        "sender_user_id": user["user_id"],
        "recipient_type": payload.recipient_type,
        "recipient_user_id": recipient_user_id,
        "recipient_email": recipient_email,
        "clone_id": clone_id_resolved,
        "source_conversation_id": payload.source_conversation_id,
        "title": payload.title.strip(),
        "message_body": payload.message_body.strip(),
        "emotional_category": payload.emotional_category,
        "delivery_time": dt.isoformat(),
        "timezone": payload.timezone or "UTC",
        "status": "scheduled",
        "delivery_channel": payload.delivery_channel,
        "open_token": open_token,
        "delivery_attempts": 0,
        "opened_at": None,
        "delivered_at": None,
        "cancelled_at": None,
        "failure_reason": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.delayed_messages.insert_one(dict(doc))
    await _emit("created", message_id=delayed_id, user_id=user["user_id"], metadata={"category": payload.emotional_category, "channel": payload.delivery_channel, "type": payload.recipient_type})
    public = _public(doc)
    # Raw token returned ONCE on create. Used for the email reveal URL and
    # for senders who want to forward a link manually.
    public["open_token"] = open_token
    return {"delayed_message": public}


@router.get("")
async def list_my_delayed(status: Optional[str] = None, user: dict = Depends(get_current_user)):
    _require_feature(user)
    q: dict = {"sender_user_id": user["user_id"]}
    if status:
        q["status"] = status
    rows = await db.delayed_messages.find(q, {"_id": 0}).sort("delivery_time", 1).to_list(500)
    return {"messages": [_public(r) for r in rows]}


@router.get("/inbox")
async def my_inbox(user: dict = Depends(get_current_user)):
    """Delivered in-app messages addressed to me (self or clone_user)."""
    _require_feature(user)
    rows = await db.delayed_messages.find(
        {"recipient_user_id": user["user_id"], "status": "delivered", "delivery_channel": {"$in": ["in_app", "both"]}},
        {"_id": 0},
    ).sort("delivered_at", -1).to_list(200)
    return {"inbox": [_public(r) for r in rows]}


@router.get("/{delayed_id}")
async def get_one(delayed_id: str, user: dict = Depends(get_current_user)):
    _require_feature(user)
    d = await db.delayed_messages.find_one({"delayed_message_id": delayed_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Not found")
    if d["sender_user_id"] != user["user_id"] and d.get("recipient_user_id") != user["user_id"]:
        raise HTTPException(403, "Not yours")
    # Mark opened on first read by recipient
    if d.get("recipient_user_id") == user["user_id"] and d.get("status") == "delivered" and not d.get("opened_at"):
        await db.delayed_messages.update_one({"delayed_message_id": delayed_id}, {"$set": {"opened_at": now_iso(), "updated_at": now_iso()}})
        await _emit("opened", message_id=delayed_id, user_id=user["user_id"])
        d = await db.delayed_messages.find_one({"delayed_message_id": delayed_id}, {"_id": 0})
    return {"delayed_message": _public(d or {})}


@router.put("/{delayed_id}")
async def update_one(delayed_id: str, payload: UpdateDelayedMessageRequest, user: dict = Depends(get_current_user)):
    _require_feature(user)
    d = await db.delayed_messages.find_one({"delayed_message_id": delayed_id, "sender_user_id": user["user_id"]}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Not found")
    if d["status"] != "scheduled":
        raise HTTPException(409, f"Cannot edit a message in status={d['status']}")
    update: dict = {}
    if payload.title is not None:
        c = moderate_user_input(payload.title)
        if c["action"] == "block":
            raise HTTPException(400, "Title violates safety rules")
        update["title"] = payload.title.strip()
    if payload.message_body is not None:
        c = moderate_user_input(payload.message_body)
        if c["action"] == "block":
            raise HTTPException(400, "Message body violates safety rules")
        update["message_body"] = payload.message_body.strip()
    if payload.delivery_time is not None:
        dt = _parse_dt(payload.delivery_time)
        if dt <= datetime.now(timezone.utc) + timedelta(seconds=30):
            raise HTTPException(400, "delivery_time must be at least 30 seconds in the future")
        update["delivery_time"] = dt.isoformat()
    if not update:
        raise HTTPException(400, "No changes")
    update["updated_at"] = now_iso()
    await db.delayed_messages.update_one({"delayed_message_id": delayed_id}, {"$set": update})
    fresh = await db.delayed_messages.find_one({"delayed_message_id": delayed_id}, {"_id": 0})
    return {"delayed_message": _public(fresh or {})}


@router.delete("/{delayed_id}")
async def delete_one(delayed_id: str, user: dict = Depends(get_current_user)):
    _require_feature(user)
    d = await db.delayed_messages.find_one({"delayed_message_id": delayed_id, "sender_user_id": user["user_id"]}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Not found")
    if d["status"] not in ("scheduled", "failed", "cancelled"):
        raise HTTPException(409, f"Cannot delete a message in status={d['status']}")
    await db.delayed_messages.delete_one({"delayed_message_id": delayed_id})
    return {"ok": True}


@router.post("/{delayed_id}/cancel")
async def cancel_one(delayed_id: str, user: dict = Depends(get_current_user)):
    _require_feature(user)
    d = await db.delayed_messages.find_one({"delayed_message_id": delayed_id, "sender_user_id": user["user_id"]}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Not found")
    if d["status"] not in ("scheduled", "queued"):
        raise HTTPException(409, f"Cannot cancel status={d['status']}")
    await db.delayed_messages.update_one(
        {"delayed_message_id": delayed_id},
        {"$set": {"status": "cancelled", "cancelled_at": now_iso(), "updated_at": now_iso()}},
    )
    await _emit("cancelled", message_id=delayed_id, user_id=user["user_id"])
    return {"ok": True}


# ---- Delivery worker ----
def _build_open_url(token: str) -> Optional[str]:
    base = (os.environ.get("FRONTEND_PUBLIC_URL") or os.environ.get("BACKEND_PUBLIC_URL") or "").rstrip("/")
    if not base:
        return None
    return f"{base}/open/{token}"


async def _deliver_one(d: dict) -> None:
    delayed_id = d["delayed_message_id"]
    channel = d.get("delivery_channel") or "in_app"
    failure: Optional[str] = None
    delivered_in_app = False
    delivered_email = False

    if channel in ("in_app", "both"):
        # In-app delivery is just status flip — recipient sees it via /inbox
        if d.get("recipient_user_id"):
            delivered_in_app = True
        else:
            failure = "no_in_app_recipient"

    if channel in ("email", "both"):
        if d.get("recipient_email"):
            # Embed the message's open_token in the URL so unauthenticated
            # email recipients can read the full message on the noindex
            # /open/:token reveal page.
            open_url = _build_open_url(d.get("open_token") or "") if d.get("open_token") else None
            ok, err = await _send_email(d["recipient_email"], d["title"], d["message_body"], open_url=open_url)
            if ok:
                delivered_email = True
            else:
                failure = (failure + "; " if failure else "") + (err or "email_send_failed")
        else:
            failure = (failure + "; " if failure else "") + "no_email_recipient"

    delivered_any = delivered_in_app or delivered_email
    update = {"updated_at": now_iso()}
    # Increment attempts whether or not delivery succeeded (each pass through is an attempt)
    new_attempts = (d.get("delivery_attempts") or 0) + 1
    update["delivery_attempts"] = new_attempts
    if delivered_any:
        update["status"] = "delivered"
        update["delivered_at"] = now_iso()
        if failure:
            update["failure_reason"] = failure  # partial failure recorded
        await db.delayed_messages.update_one({"delayed_message_id": delayed_id}, {"$set": update})
        await _emit("delivered", message_id=delayed_id, user_id=d.get("sender_user_id"), metadata={"channel": channel, "in_app": delivered_in_app, "email": delivered_email, "partial_failure": failure or None})
    else:
        # Retry policy: under the limit, drop back to scheduled with a small
        # backoff (current delivery_time + 60s × attempt) so the next tick picks
        # it up. At/over the limit, terminal-fail.
        if new_attempts < MAX_DELIVERY_ATTEMPTS:
            try:
                current_dt = datetime.fromisoformat((d.get("delivery_time") or "").replace("Z", "+00:00"))
            except Exception:
                current_dt = datetime.now(timezone.utc)
            backoff_dt = max(datetime.now(timezone.utc), current_dt) + timedelta(seconds=60 * new_attempts)
            update["status"] = "scheduled"
            update["delivery_time"] = backoff_dt.isoformat()
            update["failure_reason"] = (failure or "unknown_failure") + f" (attempt {new_attempts}/{MAX_DELIVERY_ATTEMPTS}, retrying)"
            await db.delayed_messages.update_one({"delayed_message_id": delayed_id}, {"$set": update})
            await _emit("delivery_attempt_failed", message_id=delayed_id, user_id=d.get("sender_user_id"), metadata={"attempt": new_attempts, "reason": failure or "unknown_failure"})
        else:
            update["status"] = "failed"
            update["failure_reason"] = (failure or "unknown_failure") + f" (terminal after {new_attempts} attempts)"
            await db.delayed_messages.update_one({"delayed_message_id": delayed_id}, {"$set": update})
            await _emit("failed", message_id=delayed_id, user_id=d.get("sender_user_id"), metadata={"channel": channel, "reason": update["failure_reason"], "attempts": new_attempts})


# ---- Token-based reveal (recipient opens via shareable link without auth) ----
@router.get("/open/{token}")
async def open_via_token(token: str, response: Response):
    """Reveal endpoint for email recipients (no auth required). The recipient
    receives the raw token in their email; the message is sealed (403) until
    its delivery time passes. Successful first-open records `opened_at`;
    subsequent reads return the same data idempotently.

    No-index headers prevent search engines from caching reveal payloads."""
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    if not token or len(token) < 16:
        raise HTTPException(404, "Not found")
    doc = await db.delayed_messages.find_one({"open_token": token}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Not found")
    if doc.get("status") != "delivered":
        # Sealed messages not openable via token before delivery.
        raise HTTPException(403, "This message is sealed until its delivery time.")
    # Record first-open if not already recorded
    if not doc.get("opened_at"):
        await db.delayed_messages.update_one(
            {"delayed_message_id": doc["delayed_message_id"]},
            {"$set": {"opened_at": now_iso(), "updated_at": now_iso()}},
        )
        await _emit("opened", message_id=doc["delayed_message_id"], user_id=doc.get("sender_user_id"), metadata={"via": "token"})
        doc["opened_at"] = now_iso()
    return {"delayed_message": _public(doc)}


async def _scheduler_tick() -> int:
    """Runs every poll. Returns count of deliveries attempted."""
    now = datetime.now(timezone.utc).isoformat()
    # Find due scheduled messages, atomically flip them to "queued" so concurrent ticks don't double-deliver.
    due = await db.delayed_messages.find(
        {"status": "scheduled", "delivery_time": {"$lte": now}},
        {"_id": 0},
    ).limit(50).to_list(50)
    if not due:
        return 0
    ids = [d["delayed_message_id"] for d in due]
    res = await db.delayed_messages.update_many(
        {"delayed_message_id": {"$in": ids}, "status": "scheduled"},
        {"$set": {"status": "queued", "updated_at": now_iso()}},
    )
    if res.matched_count == 0:
        return 0
    # Re-fetch (only those that flipped)
    queued = await db.delayed_messages.find({"delayed_message_id": {"$in": ids}, "status": "queued"}, {"_id": 0}).to_list(50)
    for d in queued:
        await _emit("queued", message_id=d["delayed_message_id"], user_id=d.get("sender_user_id"))
        try:
            await _deliver_one(d)
        except Exception as e:
            logger.exception("delivery failed for %s", d.get("delayed_message_id"))
            await db.delayed_messages.update_one(
                {"delayed_message_id": d["delayed_message_id"]},
                {"$set": {"status": "failed", "failure_reason": f"worker_exception: {type(e).__name__}", "updated_at": now_iso()}},
            )
    return len(queued)


async def _scheduler_loop() -> None:
    if not DELAYED_DELIVERY_CRON_ENABLED:
        logger.info("delayed delivery cron disabled")
        return
    logger.info("delayed delivery cron started, poll=%ss", SCHEDULER_POLL_SEC)
    while True:
        try:
            await _scheduler_tick()
        except Exception:
            logger.exception("scheduler tick failed")
        await asyncio.sleep(SCHEDULER_POLL_SEC)


# ---- Admin ----
@admin_router.get("/metrics")
async def admin_metrics(_admin: dict = Depends(_require_admin), days: int = Query(default=7, ge=1, le=90)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    scheduled = await db.delayed_messages.count_documents({"status": "scheduled"})
    queued = await db.delayed_messages.count_documents({"status": "queued"})
    delivered = await db.delayed_messages.count_documents({"status": "delivered", "delivered_at": {"$gte": since}})
    failed = await db.delayed_messages.count_documents({"status": "failed", "updated_at": {"$gte": since}})
    cancelled = await db.delayed_messages.count_documents({"status": "cancelled", "cancelled_at": {"$gte": since}})

    # Due-now queue
    now = datetime.now(timezone.utc).isoformat()
    due_now = await db.delayed_messages.count_documents({"status": "scheduled", "delivery_time": {"$lte": now}})

    # Avg delivery latency (delivered_at - delivery_time) for in-window deliveries
    pipeline = [
        {"$match": {"status": "delivered", "delivered_at": {"$gte": since}, "delivery_time": {"$ne": None}}},
        {"$project": {"_id": 0, "delivery_time": 1, "delivered_at": 1}},
    ]
    rows = await db.delayed_messages.aggregate(pipeline).to_list(5000)
    latencies: list[float] = []
    for r in rows:
        try:
            t = datetime.fromisoformat((r["delivery_time"] or "").replace("Z", "+00:00"))
            d = datetime.fromisoformat((r["delivered_at"] or "").replace("Z", "+00:00"))
            latencies.append((d - t).total_seconds())
        except Exception:
            continue
    avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0

    by_cat = await db.delayed_messages.aggregate([
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": "$emotional_category", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]).to_list(20)

    # Persistence-focused breakdowns (not engagement-focused)
    by_recipient = await db.delayed_messages.aggregate([
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": "$recipient_type", "n": {"$sum": 1}}},
    ]).to_list(10)
    recipient_counts = {r["_id"] or "unknown": r["n"] for r in by_recipient}
    future_self_count = recipient_counts.get("self", 0)
    other_user_count = recipient_counts.get("clone_user", 0)
    email_recipient_count = recipient_counts.get("email", 0)

    # D7 / D30 voluntary-open rate.
    # Cohort: messages delivered ≥7 (or ≥30) days ago. The signal is not
    # "did the user receive it" but "did the user RETURN to read it."
    # That is the persistence test.
    now_dt = datetime.now(timezone.utc)
    d7_cutoff = (now_dt - timedelta(days=7)).isoformat()
    d30_cutoff = (now_dt - timedelta(days=30)).isoformat()
    d7_eligible = await db.delayed_messages.count_documents({
        "status": "delivered",
        "delivered_at": {"$lte": d7_cutoff, "$ne": None},
    })
    d7_opened = await db.delayed_messages.count_documents({
        "status": "delivered",
        "delivered_at": {"$lte": d7_cutoff, "$ne": None},
        "opened_at": {"$ne": None},
    })
    d30_eligible = await db.delayed_messages.count_documents({
        "status": "delivered",
        "delivered_at": {"$lte": d30_cutoff, "$ne": None},
    })
    d30_opened = await db.delayed_messages.count_documents({
        "status": "delivered",
        "delivered_at": {"$lte": d30_cutoff, "$ne": None},
        "opened_at": {"$ne": None},
    })
    d7_open_rate_pct = round(100 * d7_opened / max(1, d7_eligible), 1) if d7_eligible else None
    d30_open_rate_pct = round(100 * d30_opened / max(1, d30_eligible), 1) if d30_eligible else None

    # Voluntary opens in window — the only "return" metric we track.
    voluntary_opens_in_window = await db.delayed_messages.count_documents({
        "status": "delivered",
        "opened_at": {"$gte": since, "$ne": None},
    })

    # Repeat composers — users who scheduled ≥2 messages in window. The sender-side
    # gravity signal: "is anyone using this product more than once?"
    repeat_pipeline = [
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": "$sender_user_id", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gte": 2}}},
        {"$count": "n"},
    ]
    repeat_rows = await db.delayed_messages.aggregate(repeat_pipeline).to_list(1)
    repeat_composers = repeat_rows[0]["n"] if repeat_rows else 0

    return {
        "window_days": days,
        "scheduled": scheduled,
        "queued": queued,
        "delivered_in_window": delivered,
        "failed_in_window": failed,
        "cancelled_in_window": cancelled,
        "due_now": due_now,
        "avg_delivery_latency_sec": avg_latency,
        "by_emotional_category": [{"category": r["_id"] or "unknown", "count": r["n"]} for r in by_cat],
        # Persistence signals (not engagement signals)
        "future_self_count": future_self_count,
        "other_user_count": other_user_count,
        "email_recipient_count": email_recipient_count,
        "voluntary_opens_in_window": voluntary_opens_in_window,
        "d7_open_rate": {"eligible": d7_eligible, "opened": d7_opened, "pct": d7_open_rate_pct},
        "d30_open_rate": {"eligible": d30_eligible, "opened": d30_opened, "pct": d30_open_rate_pct},
        "repeat_composers_in_window": repeat_composers,
        "email_configured": bool(RESEND_API_KEY),
        "feature_enabled_public": DELAYED_EMOTIONAL_CHAT_ENABLED,
        "scheduler_enabled": DELAYED_DELIVERY_CRON_ENABLED,
        "operator_note": "Persistence over engagement. Voluntary-open rate is the gravity signal. The system delivers; it does not chase.",
    }


@admin_router.get("/queue")
async def admin_queue(_admin: dict = Depends(_require_admin), status: Optional[str] = None, limit: int = Query(default=200, ge=1, le=1000)):
    q: dict = {}
    if status:
        q["status"] = status
    rows = await db.delayed_messages.find(q, {"_id": 0}).sort("delivery_time", 1).limit(limit).to_list(limit)
    return {"queue": [_public(r) for r in rows]}


@admin_router.post("/{delayed_id}/force-deliver")
async def admin_force_deliver(delayed_id: str, _admin: dict = Depends(_require_admin)):
    d = await db.delayed_messages.find_one({"delayed_message_id": delayed_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Not found")
    if d["status"] not in ("scheduled", "queued", "failed"):
        raise HTTPException(409, f"Cannot force-deliver status={d['status']}")
    await db.delayed_messages.update_one({"delayed_message_id": delayed_id}, {"$set": {"status": "queued", "updated_at": now_iso()}})
    fresh = await db.delayed_messages.find_one({"delayed_message_id": delayed_id}, {"_id": 0})
    await _deliver_one(fresh or d)
    return {"ok": True}


@admin_router.post("/{delayed_id}/cancel")
async def admin_cancel(delayed_id: str, _admin: dict = Depends(_require_admin)):
    res = await db.delayed_messages.update_one(
        {"delayed_message_id": delayed_id, "status": {"$in": ["scheduled", "queued"]}},
        {"$set": {"status": "cancelled", "cancelled_at": now_iso(), "updated_at": now_iso()}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Not found or already finalized")
    return {"ok": True}
