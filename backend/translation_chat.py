"""
Translation Chat — Phase 1.

Real-time multilingual chat. Same-domain route-based architecture mirroring
Anonymous Reality + Debates:
- Polling-based realtime (5s) — preview WS handshakes are unreliable.
- Translation via Claude Sonnet 4.5 / Emergent LLM key.
- Strict analytics separation: every event tagged experience_variant=translation_v1.
- Reuses centralized safety_filter for pre-flight blocks.
- Languages (Phase 1): en, hi, te, ja.

Auth model:
- Authenticated users get a stable user_id.
- Anonymous users get a per-device cookie/header `X-Tx-Device-Id`.
This matches Anonymous Reality's pragmatic anon-but-identified pattern.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from db import db
from auth import get_current_user, get_optional_user
from models import now_iso
from safety_filter import moderate_user_input, log_moderation_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/translation-chat", tags=["translation-chat"])
admin_router = APIRouter(prefix="/api/admin/translation-chat", tags=["translation-chat-admin"])

EXPERIENCE_VARIANT = "translation_v1"
SUPPORTED_LANGS = ["en", "hi", "te", "ja"]
LANG_NAMES = {"en": "English", "hi": "Hindi", "te": "Telugu", "ja": "Japanese"}
MAX_MESSAGE_LEN = 800
RATE_LIMIT_MSGS_PER_MIN = 20

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")


# ---- Models ----
class CreateRoomRequest(BaseModel):
    room_name: str = Field(min_length=2, max_length=80)
    preferred_language: str = Field(default="en")


class JoinRoomRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)
    preferred_language: str = Field(default="en")


class SetLanguageRequest(BaseModel):
    preferred_language: str


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LEN)
    source_language: Optional[str] = None  # None → auto-detect


class TrackRequest(BaseModel):
    event_name: str
    metadata: Optional[dict] = None


# ---- Helpers ----
def _validate_lang(lang: str) -> str:
    if lang not in SUPPORTED_LANGS:
        raise HTTPException(400, f"Unsupported language: {lang}. Supported: {SUPPORTED_LANGS}")
    return lang


async def _identify(user: Optional[dict], device_id: Optional[str]) -> tuple[str, str]:
    """Return (member_id, kind) — kind is 'user' or 'anon'."""
    if user and user.get("user_id"):
        return user["user_id"], "user"
    if not device_id or len(device_id) < 8:
        raise HTTPException(401, "Authentication or X-Tx-Device-Id header required.")
    return f"anon:{device_id}", "anon"


async def _emit(event_name: str, *, room_id: Optional[str] = None, message_id: Optional[str] = None, member_id: Optional[str] = None, metadata: Optional[dict] = None) -> None:
    await db.translation_chat_events.insert_one({
        "event_id": uuid.uuid4().hex,
        "event_name": event_name,
        "room_id": room_id,
        "message_id": message_id,
        "member_id": member_id,
        "metadata": {**(metadata or {}), "experience_variant": EXPERIENCE_VARIANT},
        "created_at": now_iso(),
    })


def _public_room(r: dict) -> dict:
    return {
        "room_id": r.get("room_id"),
        "room_name": r.get("room_name"),
        "created_at": r.get("created_at"),
        "is_active": bool(r.get("is_active", True)),
        "supported_languages": r.get("supported_languages") or SUPPORTED_LANGS,
        "message_count": int(r.get("message_count") or 0),
        "last_message_at": r.get("last_message_at"),
    }


def _public_message(m: dict, target_lang: str) -> dict:
    translations = m.get("translations") or {}
    src = m.get("source_language", "en")
    display = translations.get(target_lang) or m.get("original_text") or ""
    return {
        "message_id": m.get("message_id"),
        "room_id": m.get("room_id"),
        "sender_name": m.get("sender_name"),
        "sender_id": m.get("sender_id"),
        "source_language": src,
        "original_text": m.get("original_text"),
        "display_text": display,
        "is_same_language": src == target_lang,
        "moderation_status": m.get("moderation_status", "clean"),
        "created_at": m.get("created_at"),
    }


# ---- Language detection (lightweight; LLM handles edge cases) ----
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_TELUGU = re.compile(r"[\u0C00-\u0C7F]")
_KANA_OR_KANJI = re.compile(r"[\u3040-\u30FF\u4E00-\u9FAF]")


def detect_language_heuristic(text: str) -> Optional[str]:
    if not text:
        return None
    if _DEVANAGARI.search(text):
        return "hi"
    if _TELUGU.search(text):
        return "te"
    if _KANA_OR_KANJI.search(text):
        return "ja"
    # Latin alphabet → assume English (LLM can override)
    if re.search(r"[A-Za-z]", text):
        return "en"
    return None


# ---- Translation service ----
async def translate_message(text: str, source_lang: str, targets: list[str]) -> dict[str, str]:
    """Returns dict of {lang: translated_text}. Same-language returns text as-is.
    Falls back to original text on LLM failure (degraded mode — never blocks send)."""
    out: dict[str, str] = {source_lang: text}
    needed = [t for t in targets if t != source_lang]
    if not needed:
        return {t: text for t in targets}

    if not EMERGENT_LLM_KEY:
        logger.error("EMERGENT_LLM_KEY missing — translation degraded")
        for t in needed:
            out[t] = text
        return {t: out.get(t, text) for t in targets}

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        sys_prompt = (
            "You are a precise translator. Translate the user's message into the requested target languages. "
            "Rules:\n"
            "- Preserve meaning exactly. Do not add extra content. Do not summarize. Do not moralize.\n"
            "- Keep tone natural to the target language (casual stays casual; formal stays formal).\n"
            "- Preserve emojis and proper nouns (names, brands, places).\n"
            "- Translate slang into a natural equivalent rather than literally.\n"
            "- If the message is already in a target language, return it unchanged for that language.\n"
            f"- Supported language codes: {', '.join(SUPPORTED_LANGS)} ({', '.join(LANG_NAMES.values())}).\n"
            "Return STRICT JSON ONLY (no markdown fences). Schema:\n"
            '{"translations": {"<lang_code>": "<translated_text>", ...}}'
        )
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"tx_{abs(hash(text)) % 10**8}",
            system_message=sys_prompt,
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")
        user_msg = (
            f"Source language: {source_lang} ({LANG_NAMES.get(source_lang, source_lang)})\n"
            f"Target languages: {', '.join(needed)} ({', '.join(LANG_NAMES.get(t, t) for t in needed)})\n"
            f"Message:\n{text}"
        )
        raw = await chat.send_message(UserMessage(text=user_msg))
        raw_str = (raw or "").strip().strip("`")
        if raw_str.startswith("json"):
            raw_str = raw_str[4:].strip()
        parsed = json.loads(raw_str)
        translations = parsed.get("translations") or {}
        for t in needed:
            v = translations.get(t)
            if isinstance(v, str) and v.strip():
                out[t] = v.strip()
            else:
                out[t] = text  # fallback
    except Exception as e:
        logger.exception("Translation LLM failed: %s", e)
        for t in needed:
            out[t] = text  # degraded: send original to all

    return {t: out.get(t, text) for t in targets}


# ---- Public endpoints ----
@router.get("/languages")
async def list_languages():
    return {"languages": [{"code": c, "name": LANG_NAMES[c]} for c in SUPPORTED_LANGS]}


@router.post("/rooms")
async def create_room(payload: CreateRoomRequest, user: Optional[dict] = Depends(get_optional_user), x_tx_device_id: Optional[str] = Header(default=None)):
    _validate_lang(payload.preferred_language)
    member_id, _kind = await _identify(user, x_tx_device_id)
    safety_check = moderate_user_input(payload.room_name)
    if safety_check["action"] == "block":
        await log_moderation_event(db, user_id=member_id, route="translation_room_create", source="user_input", result=safety_check, action_taken="block_input")
        raise HTTPException(400, "Please choose a respectful room name.")
    room_id = f"tx_{uuid.uuid4().hex[:14]}"
    now = now_iso()
    doc = {
        "room_id": room_id,
        "room_name": payload.room_name.strip(),
        "created_by": member_id,
        "created_at": now,
        "updated_at": now,
        "is_active": True,
        "supported_languages": SUPPORTED_LANGS,
        "message_count": 0,
        "last_message_at": None,
    }
    await db.translation_rooms.insert_one(doc)
    await db.translation_room_members.insert_one({
        "member_doc_id": uuid.uuid4().hex,
        "room_id": room_id,
        "member_id": member_id,
        "display_name": (user or {}).get("name") or "Host",
        "preferred_language": payload.preferred_language,
        "joined_at": now,
        "last_seen_at": now,
        "is_online": True,
    })
    await _emit("translation_room_created", room_id=room_id, member_id=member_id, metadata={"preferred_language": payload.preferred_language})
    return {"room": _public_room(doc), "join_url": f"/translation-chat/{room_id}"}


@router.get("/rooms/{room_id}")
async def get_room(room_id: str, user: Optional[dict] = Depends(get_optional_user), x_tx_device_id: Optional[str] = Header(default=None)):
    r = await db.translation_rooms.find_one({"room_id": room_id}, {"_id": 0})
    if not r:
        raise HTTPException(404, "Room not found")
    member_id = None
    me = None
    try:
        member_id, _ = await _identify(user, x_tx_device_id)
        me = await db.translation_room_members.find_one({"room_id": room_id, "member_id": member_id}, {"_id": 0})
    except HTTPException:
        pass
    members = await db.translation_room_members.find({"room_id": room_id}, {"_id": 0, "display_name": 1, "preferred_language": 1, "is_online": 1, "last_seen_at": 1, "member_id": 1}).to_list(50)
    online_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    for m in members:
        m["is_online"] = bool(m.get("last_seen_at") and m["last_seen_at"] >= online_cutoff)
    return {
        "room": _public_room(r),
        "me": ({"display_name": me.get("display_name"), "preferred_language": me.get("preferred_language")} if me else None),
        "members": members,
    }


@router.post("/rooms/{room_id}/join")
async def join_room(room_id: str, payload: JoinRoomRequest, user: Optional[dict] = Depends(get_optional_user), x_tx_device_id: Optional[str] = Header(default=None)):
    _validate_lang(payload.preferred_language)
    member_id, _kind = await _identify(user, x_tx_device_id)
    r = await db.translation_rooms.find_one({"room_id": room_id}, {"_id": 0, "room_id": 1, "is_active": 1})
    if not r or not r.get("is_active"):
        raise HTTPException(404, "Room not active")
    safety_check = moderate_user_input(payload.display_name)
    if safety_check["action"] == "block":
        raise HTTPException(400, "Please choose a respectful display name.")
    now = now_iso()
    existing = await db.translation_room_members.find_one({"room_id": room_id, "member_id": member_id}, {"_id": 0})
    if existing:
        await db.translation_room_members.update_one(
            {"room_id": room_id, "member_id": member_id},
            {"$set": {"display_name": payload.display_name, "preferred_language": payload.preferred_language, "last_seen_at": now, "is_online": True}},
        )
    else:
        await db.translation_room_members.insert_one({
            "member_doc_id": uuid.uuid4().hex,
            "room_id": room_id,
            "member_id": member_id,
            "display_name": payload.display_name.strip(),
            "preferred_language": payload.preferred_language,
            "joined_at": now,
            "last_seen_at": now,
            "is_online": True,
        })
        await _emit("translation_room_joined", room_id=room_id, member_id=member_id, metadata={"preferred_language": payload.preferred_language})
    return {"ok": True, "room_id": room_id, "preferred_language": payload.preferred_language}


@router.patch("/rooms/{room_id}/language")
async def switch_language(room_id: str, payload: SetLanguageRequest, user: Optional[dict] = Depends(get_optional_user), x_tx_device_id: Optional[str] = Header(default=None)):
    _validate_lang(payload.preferred_language)
    member_id, _ = await _identify(user, x_tx_device_id)
    res = await db.translation_room_members.update_one(
        {"room_id": room_id, "member_id": member_id},
        {"$set": {"preferred_language": payload.preferred_language, "last_seen_at": now_iso()}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Not a member of this room")
    await _emit("translation_language_switched", room_id=room_id, member_id=member_id, metadata={"to": payload.preferred_language})
    return {"ok": True}


async def _check_rate_limit(member_id: str) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    n = await db.translation_messages.count_documents({"sender_id": member_id, "created_at": {"$gte": cutoff}})
    if n >= RATE_LIMIT_MSGS_PER_MIN:
        raise HTTPException(429, "Slow down — too many messages in the last minute.")


@router.post("/rooms/{room_id}/messages")
async def send_message(room_id: str, payload: SendMessageRequest, user: Optional[dict] = Depends(get_optional_user), x_tx_device_id: Optional[str] = Header(default=None)):
    member_id, _ = await _identify(user, x_tx_device_id)
    member = await db.translation_room_members.find_one({"room_id": room_id, "member_id": member_id}, {"_id": 0})
    if not member:
        raise HTTPException(403, "Join this room before sending messages.")

    text = (payload.content or "").strip()
    if not text:
        raise HTTPException(400, "Message cannot be empty.")

    safety = moderate_user_input(text)
    if safety["action"] == "block":
        await log_moderation_event(db, user_id=member_id, route="translation_chat", source="user_input", result=safety, action_taken="block_input")
        await _emit("translation_message_blocked", room_id=room_id, member_id=member_id, metadata={"category": safety.get("category"), "severity": safety.get("severity")})
        raise HTTPException(400, "This message could not be sent because it may violate safety rules.")

    await _check_rate_limit(member_id)

    src_lang = (payload.source_language or "").lower()
    if src_lang not in SUPPORTED_LANGS:
        src_lang = detect_language_heuristic(text) or member.get("preferred_language") or "en"

    translations = await translate_message(text, src_lang, SUPPORTED_LANGS)
    msg_id = f"txm_{uuid.uuid4().hex[:16]}"
    now = now_iso()
    msg_doc = {
        "message_id": msg_id,
        "room_id": room_id,
        "sender_id": member_id,
        "sender_name": member.get("display_name"),
        "source_language": src_lang,
        "original_text": text,
        "translations": translations,
        "moderation_status": "clean",
        "created_at": now,
    }
    await db.translation_messages.insert_one(msg_doc)
    await db.translation_rooms.update_one(
        {"room_id": room_id},
        {"$set": {"updated_at": now, "last_message_at": now}, "$inc": {"message_count": 1}},
    )
    await db.translation_room_members.update_one(
        {"room_id": room_id, "member_id": member_id},
        {"$set": {"last_seen_at": now, "is_online": True}},
    )
    await _emit("translation_message_sent", room_id=room_id, message_id=msg_id, member_id=member_id, metadata={"source_language": src_lang, "len": len(text)})
    await _emit("translation_message_translated", room_id=room_id, message_id=msg_id, member_id=member_id, metadata={"langs": list(translations.keys())})

    return {"message": _public_message(msg_doc, member.get("preferred_language") or "en")}


@router.get("/rooms/{room_id}/messages")
async def list_messages(room_id: str, since: Optional[str] = Query(default=None), limit: int = Query(default=80, ge=1, le=200), user: Optional[dict] = Depends(get_optional_user), x_tx_device_id: Optional[str] = Header(default=None)):
    member_id, _ = await _identify(user, x_tx_device_id)
    member = await db.translation_room_members.find_one({"room_id": room_id, "member_id": member_id}, {"_id": 0, "preferred_language": 1, "display_name": 1})
    if not member:
        raise HTTPException(403, "Join this room first.")
    target = member.get("preferred_language") or "en"

    q: dict = {"room_id": room_id, "moderation_status": {"$ne": "blocked"}}
    if since:
        q["created_at"] = {"$gt": since}
    cursor = db.translation_messages.find(q, {"_id": 0}).sort("created_at", 1).limit(limit)
    rows = await cursor.to_list(limit)

    # Mark presence
    await db.translation_room_members.update_one(
        {"room_id": room_id, "member_id": member_id},
        {"$set": {"last_seen_at": now_iso(), "is_online": True}},
    )
    return {"messages": [_public_message(m, target) for m in rows], "target_language": target}


@router.post("/rooms/{room_id}/track")
async def track(room_id: str, payload: TrackRequest, user: Optional[dict] = Depends(get_optional_user), x_tx_device_id: Optional[str] = Header(default=None)):
    member_id = None
    try:
        member_id, _ = await _identify(user, x_tx_device_id)
    except HTTPException:
        pass
    await _emit(payload.event_name, room_id=room_id, member_id=member_id, metadata=payload.metadata or {})
    return {"ok": True}


# ---- Admin ----
async def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


@admin_router.get("/metrics")
async def admin_metrics(_admin: dict = Depends(_require_admin), days: int = Query(default=7, ge=1, le=90)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rooms_total = await db.translation_rooms.count_documents({})
    rooms_active = await db.translation_rooms.count_documents({"is_active": True, "last_message_at": {"$gte": since}})
    msgs_total = await db.translation_messages.count_documents({"created_at": {"$gte": since}})
    members = await db.translation_room_members.count_documents({"joined_at": {"$gte": since}})
    blocks = await db.translation_chat_events.count_documents({"event_name": "translation_message_blocked", "created_at": {"$gte": since}})
    copies = await db.translation_chat_events.count_documents({"event_name": "translation_message_copied", "created_at": {"$gte": since}})
    by_lang_rows = await db.translation_messages.aggregate([
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": "$source_language", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]).to_list(20)
    by_lang = [{"language": r["_id"] or "unknown", "count": r["n"]} for r in by_lang_rows]
    pref_rows = await db.translation_room_members.aggregate([
        {"$group": {"_id": "$preferred_language", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]).to_list(20)
    by_pref = [{"language": r["_id"] or "unknown", "count": r["n"]} for r in pref_rows]
    return {
        "window_days": days,
        "rooms_total": rooms_total,
        "rooms_active_in_window": rooms_active,
        "messages_in_window": msgs_total,
        "members_joined_in_window": members,
        "messages_blocked": blocks,
        "copy_events": copies,
        "messages_by_source_language": by_lang,
        "members_by_preferred_language": by_pref,
    }


@admin_router.get("/rooms")
async def admin_rooms(_admin: dict = Depends(_require_admin), limit: int = Query(default=100, ge=1, le=500)):
    rows = await db.translation_rooms.find({}, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(limit)
    return {"rooms": [_public_room(r) for r in rows]}


@admin_router.get("/messages")
async def admin_messages(_admin: dict = Depends(_require_admin), room_id: Optional[str] = None, limit: int = Query(default=200, ge=1, le=1000)):
    q = {}
    if room_id:
        q["room_id"] = room_id
    rows = await db.translation_messages.find(q, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return {"messages": rows}
