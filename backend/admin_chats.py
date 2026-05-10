"""
Admin chat monitoring — unified read-only view across all chat surfaces.

Architecture:
- Reads from existing collections (clone_messages, anonymous_messages,
  debate_arguments, smart_reply_sessions). Single source of truth — no data
  duplication, no drift.
- Admin actions (flag / hide) write to `chat_audit_logs` collection so admin
  decisions are themselves auditable.
- Sensitive content redaction at READ time (regex-based): emails, phone
  numbers, credit cards, API keys, "password is X" phrases, addresses are
  masked before being shown to the admin UI.
- Privacy disclosure is mandatory and surfaced in the UI + privacy notice.

Endpoints:
- GET  /api/admin/chats                       — unified list
- GET  /api/admin/chats/{conversation_id}     — full thread
- GET  /api/admin/chats/user/{user_id}        — by user
- GET  /api/admin/chats/export                — JSON export
- PATCH /api/admin/chats/{conversation_id}/flag
- PATCH /api/admin/chats/{conversation_id}/hide
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db import db
from auth import get_current_user
from models import now_iso

admin_router = APIRouter(prefix="/api/admin/chats", tags=["chats-admin"])


async def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


# ---- Sensitive-data redaction (read-side) ----------------------------------
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_RE_PHONE = re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}\b")
_RE_CC = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_RE_KEY = re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}|pk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{35}|ghp_[A-Za-z0-9]{36}|xox[abp]-[A-Za-z0-9-]{10,})\b")
_RE_PASSWORD_PHRASE = re.compile(r"\b(?:my\s+)?password\s*(?:is|=|:)\s*\S+", re.IGNORECASE)
_RE_ADDRESS_NUM = re.compile(r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\s+(?:Street|St|Road|Rd|Ave|Avenue|Lane|Ln|Drive|Dr|Blvd|Boulevard)\b")


def redact(text: Optional[str]) -> tuple[str, list[str]]:
    """Return (redacted_text, list_of_categories_redacted). Never log raw value."""
    if not text:
        return ("", [])
    flags: list[str] = []
    out = text
    if _RE_KEY.search(out):
        flags.append("api_key")
        out = _RE_KEY.sub("[redacted:key]", out)
    if _RE_CC.search(out):
        flags.append("credit_card")
        out = _RE_CC.sub("[redacted:cc]", out)
    if _RE_PASSWORD_PHRASE.search(out):
        flags.append("password")
        out = _RE_PASSWORD_PHRASE.sub("[redacted:password]", out)
    if _RE_EMAIL.search(out):
        flags.append("email")
        out = _RE_EMAIL.sub("[redacted:email]", out)
    if _RE_PHONE.search(out):
        flags.append("phone")
        out = _RE_PHONE.sub("[redacted:phone]", out)
    if _RE_ADDRESS_NUM.search(out):
        flags.append("address")
        out = _RE_ADDRESS_NUM.sub("[redacted:address]", out)
    return (out, flags)


# ---- Action models --------------------------------------------------------
class FlagBody(BaseModel):
    chat_type: str  # clone | anonymous | debate | smart_reply
    reason: Optional[str] = None


class HideBody(BaseModel):
    chat_type: str
    hide: bool = True
    reason: Optional[str] = None


# ---- User lookup helper ----------------------------------------------------
async def _user_view(user_id: Optional[str]) -> dict:
    if not user_id:
        return {"user_id": None, "email": None, "name": None}
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0, "user_id": 1, "email": 1, "name": 1})
    return u or {"user_id": user_id, "email": None, "name": None}


# ---- Unified row builders -------------------------------------------------
def _redact_dict(text: Optional[str]) -> dict:
    redacted, flags = redact(text or "")
    return {"text": redacted, "redacted": flags}


async def _list_clone_chats(since: str, limit: int, search: Optional[str], user_filter: Optional[str], hidden_set: set) -> list[dict]:
    q: dict = {"updated_at": {"$gte": since}}
    convs = await db.clone_conversations.find(q, {"_id": 0}).sort("updated_at", -1).limit(limit * 2).to_list(limit * 2)
    rows: list[dict] = []
    for c in convs:
        clone = await db.clones.find_one({"clone_id": c.get("clone_id")}, {"_id": 0, "user_id": 1, "display_name": 1, "slug": 1})
        owner_user = await _user_view((clone or {}).get("user_id")) if clone else {}
        last = await db.clone_messages.find_one({"conversation_id": c["conversation_id"]}, {"_id": 0}, sort=[("created_at", -1)])
        msg_count = await db.clone_messages.count_documents({"conversation_id": c["conversation_id"]})
        last_text = (last or {}).get("text", "")
        if search and search.lower() not in (last_text or "").lower() and search.lower() not in (owner_user.get("email") or "").lower() and search.lower() not in ((clone or {}).get("display_name") or "").lower():
            continue
        if user_filter and owner_user.get("user_id") != user_filter and c.get("visitor_id") != user_filter:
            continue
        last_redacted = _redact_dict(last_text)
        rows.append({
            "chat_type": "clone",
            "conversation_id": c["conversation_id"],
            "user": owner_user,                    # clone owner
            "visitor_id": c.get("visitor_id"),     # the chatter (may be anonymous)
            "channel": c.get("channel"),
            "clone_id": c.get("clone_id"),
            "clone_name": (clone or {}).get("display_name"),
            "clone_slug": (clone or {}).get("slug"),
            "message_count": msg_count,
            "last_message_at": c.get("updated_at"),
            "last_message_preview": (last_redacted["text"] or "")[:160],
            "last_message_redactions": last_redacted["redacted"],
            "is_hidden": c["conversation_id"] in hidden_set,
            "is_flagged": (c.get("admin_flagged") is True),
        })
    return rows


async def _list_anonymous(since: str, limit: int, search: Optional[str], user_filter: Optional[str], hidden_set: set) -> list[dict]:
    q: dict = {"created_at": {"$gte": since}}
    if search:
        q["content"] = {"$regex": re.escape(search), "$options": "i"}
    msgs = await db.anonymous_messages.find(q, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    rows: list[dict] = []
    for m in msgs:
        red = _redact_dict(m.get("content", ""))
        rows.append({
            "chat_type": "anonymous",
            "conversation_id": m["message_id"],   # one row per message — no thread
            "user": {"user_id": m.get("session_id"), "email": None, "name": m.get("anonymous_handle")},
            "room_slug": m.get("room_slug"),
            "moderation_status": m.get("moderation_status"),
            "message_count": 1,
            "last_message_at": m.get("created_at"),
            "last_message_preview": (red["text"] or "")[:160],
            "last_message_redactions": red["redacted"],
            "is_hidden": m["message_id"] in hidden_set or m.get("moderation_status") == "blocked",
            "is_flagged": (m.get("admin_flagged") is True) or m.get("moderation_status") == "flagged",
        })
    return rows


async def _list_debate(since: str, limit: int, search: Optional[str], user_filter: Optional[str], hidden_set: set) -> list[dict]:
    q: dict = {"created_at": {"$gte": since}}
    if search:
        q["content"] = {"$regex": re.escape(search), "$options": "i"}
    if user_filter:
        q["user_id"] = user_filter
    args = await db.debate_arguments.find(q, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    rows: list[dict] = []
    for a in args:
        usr = await _user_view(a.get("user_id"))
        red = _redact_dict(a.get("content", ""))
        rows.append({
            "chat_type": "debate",
            "conversation_id": a["argument_id"],
            "user": usr,
            "debate_id": a.get("debate_id"),
            "side": a.get("side"),
            "ai_score": a.get("ai_score"),
            "moderation_status": a.get("moderation_status"),
            "message_count": 1,
            "last_message_at": a.get("created_at"),
            "last_message_preview": (red["text"] or "")[:160],
            "last_message_redactions": red["redacted"],
            "is_hidden": a["argument_id"] in hidden_set or a.get("moderation_status") == "hidden",
            "is_flagged": (a.get("admin_flagged") is True) or a.get("moderation_status") == "flagged",
        })
    return rows


async def _list_smart_reply(since: str, limit: int, search: Optional[str], user_filter: Optional[str], hidden_set: set) -> list[dict]:
    q: dict = {"created_at": {"$gte": since}}
    if user_filter:
        q["user_id"] = user_filter
    sessions = await db.smart_reply_sessions.find(q, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    rows: list[dict] = []
    for s in sessions:
        usr = await _user_view(s.get("user_id"))
        haystack = " ".join([s.get("incoming_message") or "", s.get("user_goal") or "", s.get("what_i_want_to_say") or ""])
        if search and search.lower() not in haystack.lower():
            continue
        preview_red = _redact_dict(s.get("incoming_message") or "")
        rows.append({
            "chat_type": "smart_reply",
            "conversation_id": s["session_id"],
            "user": usr,
            "mode": s.get("mode"),
            "tone": s.get("desired_tone"),
            "safety_flags": s.get("safety_flags") or {},
            "message_count": len(s.get("generated_replies") or []) + 1,
            "last_message_at": s.get("created_at"),
            "last_message_preview": (preview_red["text"] or "")[:160],
            "last_message_redactions": preview_red["redacted"],
            "is_hidden": s["session_id"] in hidden_set,
            "is_flagged": (s.get("admin_flagged") is True),
        })
    return rows


async def _hidden_set_for(chat_type: str) -> set:
    rows = await db.chat_audit_logs.find({"chat_type": chat_type, "action": "hide", "is_hidden": True}, {"_id": 0, "conversation_id": 1}).to_list(5000)
    return {r["conversation_id"] for r in rows}


# ---- Endpoints ------------------------------------------------------------
@admin_router.get("")
async def list_chats(
    _admin: dict = Depends(_require_admin),
    chat_type: str = Query(default="all", regex="^(all|clone|anonymous|debate|smart_reply)$"),
    search: Optional[str] = Query(default=None, max_length=200),
    user_id: Optional[str] = Query(default=None, max_length=64),
    days: int = Query(default=14, ge=1, le=90),
    limit: int = Query(default=80, ge=1, le=200),
    safety: Optional[str] = Query(default=None, regex="^(all|flagged|hidden|blocked)$"),
):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    types = ["clone", "anonymous", "debate", "smart_reply"] if chat_type == "all" else [chat_type]
    rows: list[dict] = []
    for t in types:
        hidden = await _hidden_set_for(t)
        if t == "clone":
            rows += await _list_clone_chats(since, limit, search, user_id, hidden)
        elif t == "anonymous":
            rows += await _list_anonymous(since, limit, search, user_id, hidden)
        elif t == "debate":
            rows += await _list_debate(since, limit, search, user_id, hidden)
        elif t == "smart_reply":
            rows += await _list_smart_reply(since, limit, search, user_id, hidden)
    rows.sort(key=lambda r: r.get("last_message_at") or "", reverse=True)
    if safety == "flagged":
        rows = [r for r in rows if r.get("is_flagged")]
    elif safety == "hidden":
        rows = [r for r in rows if r.get("is_hidden")]
    elif safety == "blocked":
        rows = [r for r in rows if r.get("moderation_status") in ("blocked", "hidden", "flagged")]
    return {"window_days": days, "count": len(rows[:limit]), "chats": rows[:limit]}


@admin_router.get("/{conversation_id}")
async def get_conversation(conversation_id: str, _admin: dict = Depends(_require_admin), chat_type: str = Query(...)):
    """Return the full thread + redacted text + safety meta."""
    if chat_type == "clone":
        conv = await db.clone_conversations.find_one({"conversation_id": conversation_id}, {"_id": 0})
        if not conv:
            raise HTTPException(404, "Conversation not found")
        clone = await db.clones.find_one({"clone_id": conv.get("clone_id")}, {"_id": 0})
        owner = await _user_view((clone or {}).get("user_id"))
        msgs = await db.clone_messages.find({"conversation_id": conversation_id}, {"_id": 0}).sort("created_at", 1).to_list(500)
        thread = []
        for m in msgs:
            red = _redact_dict(m.get("text"))
            thread.append({
                "role": m.get("sender"),
                "text": red["text"],
                "redacted": red["redacted"],
                "created_at": m.get("created_at"),
                "message_id": m.get("message_id"),
            })
        return {
            "chat_type": "clone",
            "conversation_id": conversation_id,
            "user": owner,
            "visitor_id": conv.get("visitor_id"),
            "channel": conv.get("channel"),
            "clone": {"clone_id": (clone or {}).get("clone_id"), "display_name": (clone or {}).get("display_name"), "slug": (clone or {}).get("slug")},
            "thread": thread,
            "is_hidden": conversation_id in await _hidden_set_for("clone"),
        }
    if chat_type == "anonymous":
        m = await db.anonymous_messages.find_one({"message_id": conversation_id}, {"_id": 0})
        if not m:
            raise HTTPException(404, "Message not found")
        # Show 5 surrounding messages for context
        surrounding = await db.anonymous_messages.find({"room_slug": m["room_slug"]}, {"_id": 0}).sort("created_at", -1).limit(20).to_list(20)
        thread = []
        for s in reversed(surrounding):
            red = _redact_dict(s.get("content"))
            thread.append({
                "role": "user" if s.get("message_type") == "user" else s.get("message_type", "system"),
                "text": red["text"],
                "redacted": red["redacted"],
                "created_at": s.get("created_at"),
                "message_id": s.get("message_id"),
                "anonymous_handle": s.get("anonymous_handle"),
                "moderation_status": s.get("moderation_status"),
            })
        return {"chat_type": "anonymous", "conversation_id": conversation_id, "room_slug": m["room_slug"], "thread": thread}
    if chat_type == "debate":
        a = await db.debate_arguments.find_one({"argument_id": conversation_id}, {"_id": 0, "raw_model_response": 0})
        if not a:
            raise HTTPException(404, "Argument not found")
        usr = await _user_view(a.get("user_id"))
        red = _redact_dict(a.get("content", ""))
        return {"chat_type": "debate", "conversation_id": conversation_id, "user": usr, "debate_id": a.get("debate_id"), "side": a.get("side"), "ai_score": a.get("ai_score"), "ai_score_breakdown": a.get("ai_score_breakdown"), "ai_feedback": a.get("ai_feedback"), "moderation_status": a.get("moderation_status"), "thread": [{"role": "user", "text": red["text"], "redacted": red["redacted"], "created_at": a.get("created_at"), "message_id": conversation_id}]}
    if chat_type == "smart_reply":
        s = await db.smart_reply_sessions.find_one({"session_id": conversation_id}, {"_id": 0})
        if not s:
            raise HTTPException(404, "Session not found")
        usr = await _user_view(s.get("user_id"))
        thread = []
        for field in ("incoming_message", "relationship_context", "user_goal", "what_i_want_to_say"):
            val = s.get(field) or ""
            if not val:
                continue
            red = _redact_dict(val)
            thread.append({"role": f"user:{field}", "text": red["text"], "redacted": red["redacted"], "created_at": s.get("created_at")})
        for r in (s.get("generated_replies") or []):
            red = _redact_dict(r.get("reply") or "")
            thread.append({"role": f"ai:{r.get('label')}", "text": red["text"], "redacted": red["redacted"], "created_at": s.get("created_at"), "risk_level": r.get("risk_level"), "why_it_works": r.get("why_it_works")})
        return {"chat_type": "smart_reply", "conversation_id": conversation_id, "user": usr, "mode": s.get("mode"), "tone": s.get("desired_tone"), "safety_flags": s.get("safety_flags") or {}, "thread": thread}
    raise HTTPException(400, "Unknown chat_type")


@admin_router.get("/user/{user_id}")
async def by_user(user_id: str, _admin: dict = Depends(_require_admin), days: int = Query(default=30, ge=1, le=180)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out: list[dict] = []
    for t in ["clone", "anonymous", "debate", "smart_reply"]:
        hidden = await _hidden_set_for(t)
        if t == "clone":
            out += await _list_clone_chats(since, 200, None, user_id, hidden)
        elif t == "debate":
            out += await _list_debate(since, 200, None, user_id, hidden)
        elif t == "smart_reply":
            out += await _list_smart_reply(since, 200, None, user_id, hidden)
        # anonymous chats are by session_id, not user_id
    out.sort(key=lambda r: r.get("last_message_at") or "", reverse=True)
    return {"user_id": user_id, "count": len(out), "chats": out}


@admin_router.get("/export/all")
async def export_all(_admin: dict = Depends(_require_admin), days: int = Query(default=14, ge=1, le=90), chat_type: str = Query(default="all")):
    """JSON export of all chat rows (preview metadata + redacted previews)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    types = ["clone", "anonymous", "debate", "smart_reply"] if chat_type == "all" else [chat_type]
    rows: list[dict] = []
    for t in types:
        hidden = await _hidden_set_for(t)
        if t == "clone":
            rows += await _list_clone_chats(since, 200, None, None, hidden)
        elif t == "anonymous":
            rows += await _list_anonymous(since, 200, None, None, hidden)
        elif t == "debate":
            rows += await _list_debate(since, 200, None, None, hidden)
        elif t == "smart_reply":
            rows += await _list_smart_reply(since, 200, None, None, hidden)
    rows.sort(key=lambda r: r.get("last_message_at") or "", reverse=True)
    return {"window_days": days, "chat_type": chat_type, "count": len(rows), "chats": rows}


@admin_router.patch("/{conversation_id}/flag")
async def flag_chat(conversation_id: str, payload: FlagBody, admin: dict = Depends(_require_admin)):
    await db.chat_audit_logs.insert_one({
        "audit_id": uuid.uuid4().hex,
        "conversation_id": conversation_id,
        "chat_type": payload.chat_type,
        "action": "flag",
        "reason": (payload.reason or "")[:500],
        "admin_email": admin.get("email"),
        "created_at": now_iso(),
    })
    # Best-effort: also tag the source row
    if payload.chat_type == "clone":
        await db.clone_conversations.update_one({"conversation_id": conversation_id}, {"$set": {"admin_flagged": True}})
    elif payload.chat_type == "anonymous":
        await db.anonymous_messages.update_one({"message_id": conversation_id}, {"$set": {"admin_flagged": True}})
    elif payload.chat_type == "debate":
        await db.debate_arguments.update_one({"argument_id": conversation_id}, {"$set": {"admin_flagged": True}})
    elif payload.chat_type == "smart_reply":
        await db.smart_reply_sessions.update_one({"session_id": conversation_id}, {"$set": {"admin_flagged": True}})
    return {"ok": True}


@admin_router.patch("/{conversation_id}/hide")
async def hide_chat(conversation_id: str, payload: HideBody, admin: dict = Depends(_require_admin)):
    await db.chat_audit_logs.insert_one({
        "audit_id": uuid.uuid4().hex,
        "conversation_id": conversation_id,
        "chat_type": payload.chat_type,
        "action": "hide",
        "is_hidden": bool(payload.hide),
        "reason": (payload.reason or "")[:500],
        "admin_email": admin.get("email"),
        "created_at": now_iso(),
    })
    return {"ok": True, "is_hidden": payload.hide}
