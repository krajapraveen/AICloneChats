"""
Anonymous Reality — Phase 1.

Anonymous emotional-topic rooms with WebSocket realtime + long-polling fallback.
Moderation runs BEFORE broadcast. No audio. No private messages.

Operator constraints:
- 8 seeded topic rooms (no user-created rooms in Phase 1)
- Anonymous handle generated server-side, persisted by device_id
- Moderation: BLOCK on uncertainty, ESCALATE self-harm with supportive response
- No fake-flex / authenticity / trust scores. Phase 1 is honest chat with safety.
- No likes / follows / leaderboards / public profiles.
"""
import asyncio
import hashlib
import logging
import os
import random
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from db import db
from auth import get_current_user
from credit_guard import charge_credits_or_402, fresh_user as _cg_fresh_user
from models import now_iso
import anonymous_moderation as mod
from anonymous_seed import ROOMS, SEED_CONVERSATIONS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/anonymous", tags=["anonymous"])

EXPERIENCE_VARIANT = "anonymous_v1"
SESSION_TTL_DAYS = 30
MAX_MESSAGE_LEN = 1500
RATE_LIMIT_MESSAGES_PER_MIN = 12

# Handle generation
_ADJECTIVES = [
    "Quiet", "Honest", "Silent", "Patient", "Kind", "Calm", "Soft", "Brave", "Wild", "Steady",
    "Mossy", "Hazel", "Drift", "Ember", "Hollow", "Linen", "Ashen", "Dawn", "Fog", "Mist",
    "Salt", "Pine", "Stone", "Cloud", "Rugged", "Olive", "Heavy", "Charlock", "Meadow", "Bright",
]
_NOUNS = [
    "River", "Moon", "Fox", "Owl", "Pine", "Stone", "Heron", "Falcon", "Sparrow", "Hawk",
    "Maple", "Birch", "Elm", "Wisp", "Tide", "Storm", "Dawn", "Gale", "Lake", "Grove",
    "Canyon", "Wind", "Pebble", "Kite", "Fern", "Horse", "Ember", "Haven", "Flint", "Sky",
]


# -------- Models --------
class CreateSessionResponse(BaseModel):
    session_id: str
    anonymous_handle: str
    created_at: str
    expires_at: str


class RoomSummary(BaseModel):
    slug: str
    title: str
    description: str
    rules: list[str]
    active_count: int
    status: str
    last_message_preview: Optional[str] = None
    last_message_at: Optional[str] = None


class RoomDetail(RoomSummary):
    pass


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LEN)


class MessagePublic(BaseModel):
    message_id: str
    room_slug: str
    anonymous_handle: str
    content: str
    message_type: str  # "user" | "system" | "seed"
    moderation_status: str  # "allowed" | "blocked" | "escalated"
    created_at: str


class ReportRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class TrackRequest(BaseModel):
    event_name: str
    metadata: Optional[dict] = None


# -------- Helpers --------
def _gen_handle(seed_str: str) -> str:
    rng = random.Random(seed_str)
    return f"{rng.choice(_ADJECTIVES)}{rng.choice(_NOUNS)}{rng.randint(10, 99)}"


def _hash_ip(request: Request) -> str:
    ip = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )
    secret = os.environ.get("JWT_SECRET", "dev-secret")
    return hashlib.sha256(f"{ip}|{secret}".encode("utf-8")).hexdigest()[:24]


async def get_or_create_session(device_id: str, request: Request) -> dict:
    if not device_id or len(device_id) < 8:
        raise HTTPException(400, "Invalid device_id")
    device_id = device_id[:64]
    existing = await db.anonymous_sessions.find_one({"device_id": device_id}, {"_id": 0})
    if existing:
        if existing.get("is_banned"):
            raise HTTPException(403, "This session is banned.")
        # Refresh expiry
        new_expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()
        await db.anonymous_sessions.update_one(
            {"session_id": existing["session_id"]},
            {"$set": {"expires_at": new_expires, "last_seen_at": now_iso()}},
        )
        existing["expires_at"] = new_expires
        return existing

    session_id = f"as_{uuid.uuid4().hex[:16]}"
    handle = _gen_handle(device_id)
    # collision-resistant handle
    for _ in range(5):
        if not await db.anonymous_sessions.find_one({"anonymous_handle": handle}, {"_id": 1}):
            break
        handle = _gen_handle(device_id + str(random.random()))
    doc = {
        "session_id": session_id,
        "device_id": device_id,
        "anonymous_handle": handle,
        "ip_hash": _hash_ip(request),
        "is_banned": False,
        "ban_reason": None,
        "abuse_score": 0,
        "report_count": 0,
        "created_at": now_iso(),
        "last_seen_at": now_iso(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat(),
    }
    try:
        await db.anonymous_sessions.insert_one(doc)
    except Exception as e:
        # Race condition: a concurrent request created the session. Re-find.
        if "duplicate key" in str(e).lower() or "E11000" in str(e):
            existing = await db.anonymous_sessions.find_one({"device_id": device_id}, {"_id": 0})
            if existing:
                if existing.get("is_banned"):
                    raise HTTPException(403, "This session is banned.")
                return existing
        raise
    doc.pop("_id", None)
    return doc


async def session_dep(
    request: Request,
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-Id"),
) -> dict:
    return await get_or_create_session(x_device_id or "", request)


async def _emit(session: dict, event_name: str, room_slug: Optional[str] = None, metadata: Optional[dict] = None):
    await db.anonymous_analytics.insert_one({
        "event_id": uuid.uuid4().hex,
        "session_id": session.get("session_id"),
        "anonymous_handle": session.get("anonymous_handle"),
        "room_slug": room_slug,
        "event_name": event_name,
        "metadata": {**(metadata or {}), "experience_variant": EXPERIENCE_VARIANT},
        "created_at": now_iso(),
    })


# -------- Connection manager (in-memory; single-instance MVP) --------
class _RoomManager:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, room_slug: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms[room_slug].add(ws)

    async def disconnect(self, room_slug: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms[room_slug].discard(ws)

    async def active_count(self, room_slug: str) -> int:
        async with self._lock:
            return len(self._rooms[room_slug])

    async def broadcast(self, room_slug: str, payload: dict) -> None:
        async with self._lock:
            conns = list(self._rooms[room_slug])
        for ws in conns:
            try:
                await ws.send_json(payload)
            except Exception:
                # Will be cleaned up on next disconnect cycle
                pass


manager = _RoomManager()


# -------- Seed bootstrap --------
async def ensure_rooms_and_seed() -> None:
    """Idempotent: creates rooms and seeds starter conversations once."""
    for r in ROOMS:
        await db.anonymous_rooms.update_one(
            {"slug": r["slug"]},
            {
                "$setOnInsert": {
                    "slug": r["slug"],
                    "title": r["title"],
                    "description": r["description"],
                    "rules": r["rules"],
                    "status": "active",
                    "created_at": now_iso(),
                },
                "$set": {"updated_at": now_iso()},
            },
            upsert=True,
        )
    seeded = await db.anonymous_messages.count_documents({"message_type": "seed"})
    if seeded > 0:
        return
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    for slug, conv in SEED_CONVERSATIONS.items():
        for i, (handle, content) in enumerate(conv):
            await db.anonymous_messages.insert_one({
                "message_id": f"seed_{uuid.uuid4().hex[:14]}",
                "room_slug": slug,
                "session_id": None,
                "anonymous_handle": handle,
                "content": content,
                "message_type": "seed",
                "moderation_status": "allowed",
                "moderation_reason": "seed",
                "created_at": (base + timedelta(minutes=i * 9)).isoformat(),
            })
    logger.info("Anonymous Reality: seeded %d rooms with starter conversations", len(SEED_CONVERSATIONS))


# -------- Endpoints --------
@router.post("/session", response_model=CreateSessionResponse)
async def create_session(session: dict = Depends(session_dep)):
    await _emit(session, "anonymous_session_created" if session.get("created_at") == session.get("last_seen_at") else "anonymous_session_resumed")
    return CreateSessionResponse(
        session_id=session["session_id"],
        anonymous_handle=session["anonymous_handle"],
        created_at=session["created_at"],
        expires_at=session["expires_at"],
    )


@router.get("/me", response_model=CreateSessionResponse)
async def me(session: dict = Depends(session_dep)):
    return CreateSessionResponse(
        session_id=session["session_id"],
        anonymous_handle=session["anonymous_handle"],
        created_at=session["created_at"],
        expires_at=session["expires_at"],
    )


@router.get("/rooms")
async def list_rooms(session: dict = Depends(session_dep)):
    rooms_db = await db.anonymous_rooms.find({"status": {"$ne": "archived"}}, {"_id": 0}).to_list(50)
    out = []
    for r in rooms_db:
        active = await manager.active_count(r["slug"])
        last = await db.anonymous_messages.find_one(
            {"room_slug": r["slug"], "moderation_status": "allowed"},
            {"_id": 0, "content": 1, "anonymous_handle": 1, "created_at": 1, "message_type": 1},
            sort=[("created_at", -1)],
        )
        out.append({
            **r,
            "active_count": active,
            "last_message_preview": (last or {}).get("content", "")[:120] if last else None,
            "last_message_at": (last or {}).get("created_at") if last else None,
        })
    return {"rooms": out, "session": {"anonymous_handle": session["anonymous_handle"], "session_id": session["session_id"]}}


@router.get("/rooms/{slug}")
async def get_room(slug: str, session: dict = Depends(session_dep)):
    room = await db.anonymous_rooms.find_one({"slug": slug, "status": {"$ne": "archived"}}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room not found")
    active = await manager.active_count(slug)
    return {**room, "active_count": active, "session_handle": session["anonymous_handle"]}


@router.get("/rooms/{slug}/messages")
async def get_messages(slug: str, since: Optional[str] = Query(default=None), limit: int = Query(default=80, ge=1, le=200), session: dict = Depends(session_dep)):
    """Long-polling fallback. Returns messages newer than `since` (ISO ts)."""
    room = await db.anonymous_rooms.find_one({"slug": slug}, {"_id": 0, "slug": 1, "status": 1})
    if not room:
        raise HTTPException(404, "Room not found")
    q: dict = {"room_slug": slug, "moderation_status": {"$in": ["allowed"]}}
    if since:
        q["created_at"] = {"$gt": since}
    cursor = db.anonymous_messages.find(q, {"_id": 0, "moderation_reason": 0}).sort("created_at", 1).limit(limit)
    msgs = await cursor.to_list(limit)
    if not since and not msgs:
        # return last 80 historical messages including seeds
        cursor = db.anonymous_messages.find(
            {"room_slug": slug, "moderation_status": "allowed"},
            {"_id": 0, "moderation_reason": 0},
        ).sort("created_at", -1).limit(80)
        msgs = list(reversed(await cursor.to_list(80)))
    return {"messages": msgs, "room_status": room.get("status", "active")}


async def _check_rate_limit(session_id: str) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    count = await db.anonymous_messages.count_documents({
        "session_id": session_id,
        "created_at": {"$gt": cutoff},
    })
    if count >= RATE_LIMIT_MESSAGES_PER_MIN:
        raise HTTPException(429, "You're sending messages too quickly. Slow down.")


@router.post("/rooms/{slug}/messages")
async def send_message(slug: str, payload: SendMessageRequest, session: dict = Depends(session_dep), auth_user: dict = Depends(get_current_user)):
    if session.get("is_banned"):
        raise HTTPException(403, "This session is banned from posting.")
    room = await db.anonymous_rooms.find_one({"slug": slug}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room not found")
    if room.get("status") == "frozen":
        raise HTTPException(423, "Room is read-only right now.")
    await _check_rate_limit(session["session_id"])

    content = payload.content.strip()
    # Safety regex floor (cheap & fast). LLM moderation below remains the ceiling.
    from safety_filter import moderate_user_input as _safety_check, log_moderation_event as _safety_log
    pre = _safety_check(content)
    if pre["action"] == "block":
        await _safety_log(db, user_id=session["session_id"], route="anonymous_chat", source="user_input", result=pre, action_taken="block_input")
        raise HTTPException(400, "Please keep messages safe and respectful.")

    # ---- Credit gate (anonymous_chat = 3 credits) ----
    user_doc = await _cg_fresh_user(auth_user)
    credit_handle = await charge_credits_or_402(user_doc, surface="anonymous_chat")

    try:
        moderation = await mod.moderate_message(content)
    except Exception:
        await credit_handle.refund(reason="moderation_failed")
        raise HTTPException(502, "Moderation service failed, try again.")
    decision = moderation["decision"]
    message_id = f"am_{uuid.uuid4().hex[:14]}"

    # Self-harm: NEVER block. Allow post + supportive system reply + escalate silently.
    if decision == "escalate" and moderation["category"] == "self_harm":
        doc = {
            "message_id": message_id,
            "room_slug": slug,
            "session_id": session["session_id"],
            "anonymous_handle": session["anonymous_handle"],
            "content": content,
            "message_type": "user",
            "moderation_status": "allowed",
            "moderation_reason": "self_harm_supported",
            "created_at": now_iso(),
        }
        await db.anonymous_messages.insert_one(doc)
        await db.anonymous_moderation_logs.insert_one({**moderation, "message_id": message_id, "session_id": session["session_id"], "room_slug": slug, "created_at": now_iso()})
        await _emit(session, "anonymous_message_sent", slug, {"category": "self_harm"})
        await manager.broadcast(slug, {"type": "new_message", "message": {**doc, "_id": None}})

        # Supportive system message — operator rule: never shaming, kind, brief
        supportive = moderation.get("supportive_response") or "What you're feeling is real. If it's getting heavier, reaching out to someone who can help — a trusted friend, or a hotline — is a strong move, not a weak one. You're not alone in this room."
        sys_id = f"as_msg_{uuid.uuid4().hex[:14]}"
        sys_doc = {
            "message_id": sys_id,
            "room_slug": slug,
            "session_id": None,
            "anonymous_handle": "Room",
            "content": supportive,
            "message_type": "system",
            "moderation_status": "allowed",
            "moderation_reason": "self_harm_response",
            "created_at": now_iso(),
        }
        await db.anonymous_messages.insert_one(sys_doc)
        await manager.broadcast(slug, {"type": "new_message", "message": {**sys_doc, "_id": None}})
        return {"status": "allowed", "message": _strip_id(doc), "system_message": _strip_id(sys_doc)}

    if decision == "block":
        # Persist as blocked for admin audit, but don't broadcast
        await db.anonymous_messages.insert_one({
            "message_id": message_id,
            "room_slug": slug,
            "session_id": session["session_id"],
            "anonymous_handle": session["anonymous_handle"],
            "content": content,
            "message_type": "user",
            "moderation_status": "blocked",
            "moderation_reason": moderation["reason"],
            "moderation_category": moderation["category"],
            "created_at": now_iso(),
        })
        await db.anonymous_moderation_logs.insert_one({**moderation, "message_id": message_id, "session_id": session["session_id"], "room_slug": slug, "created_at": now_iso()})
        await db.anonymous_sessions.update_one(
            {"session_id": session["session_id"]},
            {"$inc": {"abuse_score": int(moderation["severity"])}, "$set": {"last_seen_at": now_iso()}},
        )
        await _emit(session, "anonymous_message_blocked", slug, {"category": moderation["category"], "severity": moderation["severity"]})
        # Human-toned reason
        human_reasons = {
            "toxicity": "We blocked this to protect the room. Try saying it differently.",
            "hate": "This sounds like targeted hate. We blocked it.",
            "harassment": "This reads as a personal attack. Talk about the feeling instead.",
            "threats": "We blocked this to keep the room safe.",
            "doxxing": "We removed contact info to protect everyone here.",
            "spam": "This looked like spam. If it wasn't, try again with more context.",
            "sexual_abuse": "We blocked this. This room isn't a safe place for that content.",
        }
        return {
            "status": "blocked",
            "category": moderation["category"],
            "human_reason": human_reasons.get(moderation["category"], "We blocked this to protect the room."),
        }

    # Allow
    doc = {
        "message_id": message_id,
        "room_slug": slug,
        "session_id": session["session_id"],
        "anonymous_handle": session["anonymous_handle"],
        "content": content,
        "message_type": "user",
        "moderation_status": "allowed",
        "moderation_reason": "clean",
        "created_at": now_iso(),
    }
    await db.anonymous_messages.insert_one(doc)
    await db.anonymous_sessions.update_one({"session_id": session["session_id"]}, {"$set": {"last_seen_at": now_iso()}})
    await _emit(session, "anonymous_message_sent", slug)
    await manager.broadcast(slug, {"type": "new_message", "message": _strip_id(doc)})
    return {"status": "allowed", "message": _strip_id(doc)}


def _strip_id(d: dict) -> dict:
    out = dict(d)
    out.pop("_id", None)
    return out


@router.post("/rooms/{slug}/join")
async def join_room(slug: str, session: dict = Depends(session_dep)):
    room = await db.anonymous_rooms.find_one({"slug": slug, "status": {"$ne": "archived"}}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room not found")
    await _emit(session, "anonymous_room_joined", slug)
    return {"ok": True, "room": room, "session_handle": session["anonymous_handle"]}


@router.post("/rooms/{slug}/leave")
async def leave_room(slug: str, session: dict = Depends(session_dep)):
    await _emit(session, "anonymous_room_left", slug)
    return {"ok": True}


@router.post("/messages/{message_id}/report")
async def report_message(message_id: str, payload: ReportRequest, session: dict = Depends(session_dep)):
    msg = await db.anonymous_messages.find_one({"message_id": message_id}, {"_id": 0})
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg.get("session_id") == session["session_id"]:
        raise HTTPException(400, "You cannot report your own message")
    report_id = f"ar_{uuid.uuid4().hex[:14]}"
    await db.anonymous_reports.insert_one({
        "report_id": report_id,
        "message_id": message_id,
        "room_slug": msg["room_slug"],
        "reported_session_id": msg.get("session_id"),
        "reporter_session_id": session["session_id"],
        "reason": payload.reason.strip(),
        "status": "open",
        "created_at": now_iso(),
    })
    if msg.get("session_id"):
        await db.anonymous_sessions.update_one({"session_id": msg["session_id"]}, {"$inc": {"report_count": 1, "abuse_score": 2}})
    await _emit(session, "anonymous_report_created", msg["room_slug"], {"message_id": message_id})
    return {"ok": True, "report_id": report_id}


@router.post("/track")
async def track(payload: TrackRequest, session: dict = Depends(session_dep)):
    allowed = {
        "anonymous_page_opened", "anonymous_room_opened", "anonymous_typing_started",
        "anonymous_reconnect_attempted", "anonymous_polling_fallback_engaged",
        "anonymous_room_abandoned", "anonymous_message_blocked_seen", "anonymous_message_reported_clicked",
    }
    if payload.event_name not in allowed:
        raise HTTPException(400, "Unsupported event")
    await _emit(session, payload.event_name, (payload.metadata or {}).get("room_slug"), payload.metadata)
    return {"ok": True}


# -------- WebSocket --------
@router.websocket("/ws/{slug}")
async def websocket_room(websocket: WebSocket, slug: str, device_id: Optional[str] = Query(default=None, alias="device_id")):
    """Realtime room channel. Auth via ?device_id=... query param (anon model)."""
    if not device_id:
        await websocket.close(code=4401)
        return
    fake_request_headers = {}

    class _FakeReq:
        client = type("C", (), {"host": websocket.client.host if websocket.client else "ws"})()
        headers = fake_request_headers

    try:
        session = await get_or_create_session(device_id, _FakeReq())
    except HTTPException:
        await websocket.close(code=4400)
        return
    if session.get("is_banned"):
        await websocket.close(code=4403)
        return
    room = await db.anonymous_rooms.find_one({"slug": slug, "status": {"$ne": "archived"}}, {"_id": 0})
    if not room:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    await manager.connect(slug, websocket)
    try:
        # Send hello with handle + recent messages
        recent = await db.anonymous_messages.find(
            {"room_slug": slug, "moderation_status": "allowed"},
            {"_id": 0, "moderation_reason": 0},
        ).sort("created_at", -1).limit(80).to_list(80)
        recent.reverse()
        await websocket.send_json({"type": "hello", "handle": session["anonymous_handle"], "messages": recent, "active_count": await manager.active_count(slug)})
        await _emit(session, "anonymous_ws_connected", slug)
        # Broadcast active count
        await manager.broadcast(slug, {"type": "active_count", "count": await manager.active_count(slug)})

        while True:
            data = await websocket.receive_json()
            t = data.get("type")
            if t == "ping":
                await websocket.send_json({"type": "pong"})
            elif t == "typing":
                await manager.broadcast(slug, {"type": "typing", "handle": session["anonymous_handle"]})
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        logger.exception("WS error: %s", e)
    finally:
        await manager.disconnect(slug, websocket)
        try:
            await manager.broadcast(slug, {"type": "active_count", "count": await manager.active_count(slug)})
        except Exception:
            pass


# -------- Admin --------
admin_router = APIRouter(prefix="/api/admin/anonymous", tags=["anonymous-admin"])


async def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


@admin_router.get("/metrics")
async def admin_metrics(_admin: dict = Depends(_require_admin), days: int = Query(default=7, ge=1, le=90)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    total_messages_user = await db.anonymous_messages.count_documents({"created_at": {"$gte": since}, "message_type": "user"})
    blocked = await db.anonymous_messages.count_documents({"created_at": {"$gte": since}, "moderation_status": "blocked"})
    reports = await db.anonymous_reports.count_documents({"created_at": {"$gte": since}})
    sessions_created = await db.anonymous_sessions.count_documents({"created_at": {"$gte": since}})
    rooms = await db.anonymous_rooms.find({}, {"_id": 0, "slug": 1, "title": 1, "status": 1}).to_list(50)
    room_rows = []
    for r in rooms:
        n = await db.anonymous_messages.count_documents({"room_slug": r["slug"], "moderation_status": "allowed", "message_type": "user", "created_at": {"$gte": since}})
        last = await db.anonymous_messages.find_one({"room_slug": r["slug"], "moderation_status": "allowed"}, {"_id": 0, "created_at": 1}, sort=[("created_at", -1)])
        room_rows.append({**r, "messages": n, "last_message_at": (last or {}).get("created_at"), "active_count": await manager.active_count(r["slug"])})
    return {
        "window_days": days,
        "total_user_messages": total_messages_user,
        "blocked_messages": blocked,
        "block_rate_pct": round(100 * blocked / max(1, total_messages_user), 1),
        "reports": reports,
        "sessions_created": sessions_created,
        "rooms": room_rows,
    }


@admin_router.get("/reports")
async def admin_reports(_admin: dict = Depends(_require_admin), status: str = "open", limit: int = 100):
    cursor = db.anonymous_reports.find({"status": status}, {"_id": 0}).sort("created_at", -1).limit(limit)
    reports = await cursor.to_list(limit)
    # Hydrate with messages
    out = []
    for r in reports:
        msg = await db.anonymous_messages.find_one({"message_id": r["message_id"]}, {"_id": 0})
        out.append({**r, "message": msg})
    return {"reports": out}


@admin_router.get("/messages/flagged")
async def admin_flagged(_admin: dict = Depends(_require_admin), limit: int = 100):
    cursor = db.anonymous_messages.find({"moderation_status": "blocked"}, {"_id": 0}).sort("created_at", -1).limit(limit)
    return {"messages": await cursor.to_list(limit)}


@admin_router.get("/rooms/{slug}/transcript")
async def admin_transcript(slug: str, _admin: dict = Depends(_require_admin), limit: int = 500):
    cursor = db.anonymous_messages.find({"room_slug": slug}, {"_id": 0}).sort("created_at", -1).limit(limit)
    msgs = list(reversed(await cursor.to_list(limit)))
    return {"slug": slug, "messages": msgs}


class AdminAction(BaseModel):
    reason: Optional[str] = None


@admin_router.post("/messages/{message_id}/remove")
async def admin_remove_message(message_id: str, payload: AdminAction, admin: dict = Depends(_require_admin)):
    res = await db.anonymous_messages.update_one(
        {"message_id": message_id},
        {"$set": {"moderation_status": "admin_removed", "admin_removed_reason": (payload.reason or ""), "admin_removed_by": admin["email"], "admin_removed_at": now_iso()}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Message not found")
    msg = await db.anonymous_messages.find_one({"message_id": message_id}, {"_id": 0, "room_slug": 1})
    if msg:
        await manager.broadcast(msg["room_slug"], {"type": "message_removed", "message_id": message_id})
    await db.anonymous_admin_actions.insert_one({"action_id": uuid.uuid4().hex, "type": "remove_message", "target": message_id, "admin": admin["email"], "reason": payload.reason, "created_at": now_iso()})
    return {"ok": True}


@admin_router.post("/sessions/{session_id}/ban")
async def admin_ban_session(session_id: str, payload: AdminAction, admin: dict = Depends(_require_admin)):
    res = await db.anonymous_sessions.update_one({"session_id": session_id}, {"$set": {"is_banned": True, "ban_reason": payload.reason or "admin_action", "banned_at": now_iso(), "banned_by": admin["email"]}})
    if res.matched_count == 0:
        raise HTTPException(404, "Session not found")
    await db.anonymous_admin_actions.insert_one({"action_id": uuid.uuid4().hex, "type": "ban_session", "target": session_id, "admin": admin["email"], "reason": payload.reason, "created_at": now_iso()})
    return {"ok": True}


@admin_router.post("/rooms/{slug}/freeze")
async def admin_freeze_room(slug: str, payload: AdminAction, admin: dict = Depends(_require_admin)):
    res = await db.anonymous_rooms.update_one({"slug": slug}, {"$set": {"status": "frozen", "frozen_reason": payload.reason or "", "frozen_at": now_iso(), "frozen_by": admin["email"]}})
    if res.matched_count == 0:
        raise HTTPException(404, "Room not found")
    await manager.broadcast(slug, {"type": "room_frozen"})
    await db.anonymous_admin_actions.insert_one({"action_id": uuid.uuid4().hex, "type": "freeze_room", "target": slug, "admin": admin["email"], "reason": payload.reason, "created_at": now_iso()})
    return {"ok": True}


@admin_router.post("/rooms/{slug}/unfreeze")
async def admin_unfreeze_room(slug: str, admin: dict = Depends(_require_admin)):
    res = await db.anonymous_rooms.update_one({"slug": slug}, {"$set": {"status": "active"}})
    if res.matched_count == 0:
        raise HTTPException(404, "Room not found")
    await db.anonymous_admin_actions.insert_one({"action_id": uuid.uuid4().hex, "type": "unfreeze_room", "target": slug, "admin": admin["email"], "created_at": now_iso()})
    return {"ok": True}


@admin_router.post("/reports/{report_id}/resolve")
async def admin_resolve_report(report_id: str, payload: AdminAction, admin: dict = Depends(_require_admin)):
    res = await db.anonymous_reports.update_one({"report_id": report_id}, {"$set": {"status": "resolved", "resolved_by": admin["email"], "resolved_at": now_iso(), "resolution_note": payload.reason or ""}})
    if res.matched_count == 0:
        raise HTTPException(404, "Report not found")
    return {"ok": True}



# -------- Observability (read-only) --------
# Operator note: this endpoint is INSTRUMENTATION, not product expansion.
# Aggregates only over EXISTING collections. No schema changes.
# Used by /admin/anonymous-metrics dashboard for behavioral evidence
# during the measurement freeze.
def _day_key(iso_str: str) -> str:
    return (iso_str or "")[:10]  # YYYY-MM-DD


@admin_router.get("/observability")
async def admin_observability(_admin: dict = Depends(_require_admin), days: int = Query(default=7, ge=1, le=90)):
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    since_24h = (now - timedelta(days=1)).isoformat()
    since_7d = (now - timedelta(days=7)).isoformat()
    today_key = now.strftime("%Y-%m-%d")

    # ---- Activity signal: an event in anonymous_analytics OR a user message
    # We treat "active session" = any analytics event in the window.
    dau_pipeline = [
        {"$match": {"created_at": {"$gte": since_24h}, "session_id": {"$ne": None}}},
        {"$group": {"_id": "$session_id"}},
        {"$count": "n"},
    ]
    wau_pipeline = [
        {"$match": {"created_at": {"$gte": since_7d}, "session_id": {"$ne": None}}},
        {"$group": {"_id": "$session_id"}},
        {"$count": "n"},
    ]
    dau_res = await db.anonymous_analytics.aggregate(dau_pipeline).to_list(1)
    wau_res = await db.anonymous_analytics.aggregate(wau_pipeline).to_list(1)
    dau = (dau_res[0]["n"] if dau_res else 0)
    wau = (wau_res[0]["n"] if wau_res else 0)

    # DAU time-series for the requested window (per UTC day)
    daily_pipeline = [
        {"$match": {"created_at": {"$gte": since}, "session_id": {"$ne": None}}},
        {"$group": {"_id": {"day": {"$substr": ["$created_at", 0, 10]}, "session_id": "$session_id"}}},
        {"$group": {"_id": "$_id.day", "users": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    daily_rows = await db.anonymous_analytics.aggregate(daily_pipeline).to_list(120)
    daily_active = [{"day": r["_id"], "users": r["users"]} for r in daily_rows if r.get("_id")]

    # ---- Sessions in window
    sessions_in_window = await db.anonymous_sessions.count_documents({"created_at": {"$gte": since}})
    total_sessions = await db.anonymous_sessions.count_documents({})

    # ---- Messages
    msgs_user = await db.anonymous_messages.count_documents({"created_at": {"$gte": since}, "message_type": "user"})
    msgs_user_allowed = await db.anonymous_messages.count_documents({"created_at": {"$gte": since}, "message_type": "user", "moderation_status": "allowed"})
    msgs_blocked = await db.anonymous_messages.count_documents({"created_at": {"$gte": since}, "moderation_status": "blocked"})
    msgs_escalated = await db.anonymous_messages.count_documents({"created_at": {"$gte": since}, "moderation_status": "escalated"})
    msgs_system = await db.anonymous_messages.count_documents({"created_at": {"$gte": since}, "message_type": "system"})
    reports_count = await db.anonymous_reports.count_documents({"created_at": {"$gte": since}})

    # ---- Avg messages per (talking) session
    talkers_pipeline = [
        {"$match": {"created_at": {"$gte": since}, "message_type": "user", "moderation_status": "allowed"}},
        {"$group": {"_id": "$session_id", "n": {"$sum": 1}}},
    ]
    talker_rows = await db.anonymous_messages.aggregate(talkers_pipeline).to_list(10000)
    talker_count = len(talker_rows)
    avg_msgs_per_talker = round(sum(r["n"] for r in talker_rows) / talker_count, 2) if talker_count else 0.0

    # WAU-relative lurker vs talker. Talker = sent ≥1 allowed user msg in window. Lurker = active but no msg.
    active_in_window_pipeline = [
        {"$match": {"created_at": {"$gte": since}, "session_id": {"$ne": None}}},
        {"$group": {"_id": "$session_id"}},
    ]
    active_rows = await db.anonymous_analytics.aggregate(active_in_window_pipeline).to_list(20000)
    active_session_ids = {r["_id"] for r in active_rows if r.get("_id")}
    talker_ids = {r["_id"] for r in talker_rows if r.get("_id")}
    talkers = len(active_session_ids & talker_ids)
    lurkers = max(0, len(active_session_ids) - talkers)
    lurker_talker_ratio = round(lurkers / talkers, 2) if talkers else None

    # ---- Avg session duration (last_seen - created)
    duration_pipeline = [
        {"$match": {"created_at": {"$gte": since}, "last_seen_at": {"$ne": None}}},
        {"$project": {"_id": 0, "created_at": 1, "last_seen_at": 1}},
    ]
    duration_rows = await db.anonymous_sessions.aggregate(duration_pipeline).to_list(20000)
    durations_sec = []
    for r in duration_rows:
        try:
            c = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            ls = datetime.fromisoformat(r["last_seen_at"].replace("Z", "+00:00"))
            d = (ls - c).total_seconds()
            if d >= 0:
                durations_sec.append(d)
        except Exception:
            continue
    avg_session_duration_sec = round(sum(durations_sec) / len(durations_sec), 1) if durations_sec else 0.0

    # ---- Rates
    block_rate_pct = round(100 * msgs_blocked / max(1, msgs_user), 1)
    report_rate_pct = round(100 * reports_count / max(1, msgs_user_allowed), 2)
    # AI reply usage = system messages emitted (crisis/AI moderation responses) per user msg
    ai_reply_usage_pct = round(100 * msgs_system / max(1, msgs_user_allowed), 1)

    # ---- Peak concurrent (estimate from join events bucketed by 10-min)
    # Dedup per (session, 10-min bucket) so page refreshes / re-mounts of the
    # AnonymousRoom component do NOT inflate the peak. One human in one
    # 10-min window counts as one — regardless of how many `room_joined`
    # events the client emits for that session.
    join_pipeline = [
        {"$match": {"event_name": "anonymous_room_joined", "created_at": {"$gte": since}, "session_id": {"$ne": None}}},
        # Dedupe per session per 10-min bucket
        {"$group": {"_id": {"bucket": {"$substr": ["$created_at", 0, 15]}, "session_id": "$session_id"}}},
        # Count distinct sessions per bucket
        {"$group": {"_id": "$_id.bucket", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 1},
    ]
    peak_rows = await db.anonymous_analytics.aggregate(join_pipeline).to_list(1)
    peak_concurrent_estimate = peak_rows[0]["n"] if peak_rows else 0

    # Real-time current active count from in-memory manager
    rooms = await db.anonymous_rooms.find({}, {"_id": 0, "slug": 1, "title": 1, "status": 1, "created_at": 1}).to_list(100)
    rooms_total = len(rooms)
    rooms_active_now = 0
    room_rows = []
    for r in rooms:
        slug = r["slug"]
        active_now = await manager.active_count(slug)
        rooms_active_now += active_now
        msgs = await db.anonymous_messages.count_documents({"room_slug": slug, "moderation_status": "allowed", "message_type": "user", "created_at": {"$gte": since}})
        # Distinct talkers in this room
        room_talker_pipeline = [
            {"$match": {"room_slug": slug, "message_type": "user", "moderation_status": "allowed", "created_at": {"$gte": since}}},
            {"$group": {"_id": "$session_id"}},
            {"$count": "n"},
        ]
        rt = await db.anonymous_messages.aggregate(room_talker_pipeline).to_list(1)
        room_talkers = rt[0]["n"] if rt else 0
        # Distinct joiners (active sessions for this room)
        room_join_pipeline = [
            {"$match": {"event_name": "anonymous_room_joined", "room_slug": slug, "created_at": {"$gte": since}}},
            {"$group": {"_id": "$session_id"}},
            {"$count": "n"},
        ]
        rj = await db.anonymous_analytics.aggregate(room_join_pipeline).to_list(1)
        room_joiners = rj[0]["n"] if rj else 0
        abandonment = round(100 * (1 - (room_talkers / room_joiners)), 1) if room_joiners else None
        last = await db.anonymous_messages.find_one({"room_slug": slug, "moderation_status": "allowed"}, {"_id": 0, "created_at": 1}, sort=[("created_at", -1)])
        room_rows.append({
            "slug": slug,
            "title": r.get("title"),
            "status": r.get("status"),
            "active_now": active_now,
            "messages": msgs,
            "talkers": room_talkers,
            "joiners": room_joiners,
            "abandonment_pct": abandonment,
            "last_message_at": (last or {}).get("created_at"),
        })
    room_rows.sort(key=lambda x: (x["messages"], x["active_now"]), reverse=True)

    # Room creation rate: Phase 1 explicitly disables user-created rooms.
    rooms_created_in_window = await db.anonymous_rooms.count_documents({"created_at": {"$gte": since}}) if rooms_total else 0
    user_created_rooms_locked = True  # Phase 1 constraint

    # ---- Retention (D1 / D7)
    # D1: sessions created in window (excluding the latest day so they have a chance to return),
    # had at least 1 analytics event on a day strictly after their created_at day.
    retention_window_start = (now - timedelta(days=days + 1)).isoformat()
    eligible_d1 = await db.anonymous_sessions.find(
        {"created_at": {"$gte": retention_window_start, "$lt": (now - timedelta(days=1)).isoformat()}},
        {"_id": 0, "session_id": 1, "created_at": 1},
    ).to_list(20000)
    d1_returned = 0
    eligible_d7 = []
    eligible_d7_sessions = await db.anonymous_sessions.find(
        {"created_at": {"$gte": (now - timedelta(days=days + 8)).isoformat(), "$lt": (now - timedelta(days=7)).isoformat()}},
        {"_id": 0, "session_id": 1, "created_at": 1},
    ).to_list(20000)
    eligible_d7 = eligible_d7_sessions

    if eligible_d1:
        ids = [s["session_id"] for s in eligible_d1]
        # For efficiency, fetch all events for these sessions and bucket by day
        events = await db.anonymous_analytics.find(
            {"session_id": {"$in": ids}},
            {"_id": 0, "session_id": 1, "created_at": 1},
        ).to_list(200000)
        by_session: dict[str, set[str]] = defaultdict(set)
        for e in events:
            sid = e.get("session_id")
            d = _day_key(e.get("created_at", ""))
            if sid and d:
                by_session[sid].add(d)
        for s in eligible_d1:
            sid = s["session_id"]
            created_day = _day_key(s.get("created_at", ""))
            days_active = by_session.get(sid, set())
            if any(d > created_day for d in days_active):
                d1_returned += 1
    d1_retention_pct = round(100 * d1_returned / max(1, len(eligible_d1)), 1) if eligible_d1 else None

    d7_returned = 0
    if eligible_d7:
        ids7 = [s["session_id"] for s in eligible_d7]
        events7 = await db.anonymous_analytics.find(
            {"session_id": {"$in": ids7}},
            {"_id": 0, "session_id": 1, "created_at": 1},
        ).to_list(200000)
        by_session7: dict[str, set[str]] = defaultdict(set)
        for e in events7:
            sid = e.get("session_id")
            d = _day_key(e.get("created_at", ""))
            if sid and d:
                by_session7[sid].add(d)
        from datetime import date as _date
        for s in eligible_d7:
            sid = s["session_id"]
            created_day_str = _day_key(s.get("created_at", ""))
            try:
                cd = _date.fromisoformat(created_day_str)
            except Exception:
                continue
            days_active = by_session7.get(sid, set())
            for d in days_active:
                try:
                    if (_date.fromisoformat(d) - cd).days >= 7:
                        d7_returned += 1
                        break
                except Exception:
                    continue
    d7_retention_pct = round(100 * d7_returned / max(1, len(eligible_d7)), 1) if eligible_d7 else None

    # ---- Aggregate room abandonment
    room_joiners_total = sum((r["joiners"] or 0) for r in room_rows)
    room_talkers_total = sum((r["talkers"] or 0) for r in room_rows)
    overall_abandonment_pct = round(100 * (1 - (room_talkers_total / room_joiners_total)), 1) if room_joiners_total else None

    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "today_utc": today_key,
        "operator_note": "Read-only observability for the measurement freeze. No product features.",
        # Headline behavioral evidence
        "audience": {
            "dau": dau,
            "wau": wau,
            "sessions_created_in_window": sessions_in_window,
            "total_sessions_all_time": total_sessions,
            "daily_active_series": daily_active,
        },
        "engagement": {
            "messages_user_allowed": msgs_user_allowed,
            "messages_user_total": msgs_user,
            "talkers": talkers,
            "lurkers": lurkers,
            "lurker_talker_ratio": lurker_talker_ratio,
            "avg_msgs_per_talker": avg_msgs_per_talker,
            "avg_session_duration_sec": avg_session_duration_sec,
            "peak_concurrent_estimate": peak_concurrent_estimate,
            "active_now_total": rooms_active_now,
        },
        "safety": {
            "blocked": msgs_blocked,
            "escalated": msgs_escalated,
            "block_rate_pct": block_rate_pct,
            "reports": reports_count,
            "report_rate_pct": report_rate_pct,
            "ai_reply_usage_pct": ai_reply_usage_pct,
            "system_messages": msgs_system,
        },
        "retention": {
            "d1_pct": d1_retention_pct,
            "d1_eligible": len(eligible_d1),
            "d1_returned": d1_returned,
            "d7_pct": d7_retention_pct,
            "d7_eligible": len(eligible_d7),
            "d7_returned": d7_returned,
        },
        "rooms": {
            "total": rooms_total,
            "user_created_rooms_locked": user_created_rooms_locked,
            "rooms_created_in_window": rooms_created_in_window,
            "overall_abandonment_pct": overall_abandonment_pct,
            "rows": room_rows,
        },
    }
