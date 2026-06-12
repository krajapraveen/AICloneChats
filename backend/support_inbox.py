"""
support_inbox.py — User ↔ Admin inbox for concerns and recommendations.

Data model
----------
support_threads: one document per conversation between a user and admins.
  - thread_id, user_id, user_email, kind ('concern'|'recommendation'),
    subject, status ('open'|'awaiting_user'|'resolved'|'closed'),
    last_message_at, unread_for_user (bool), unread_for_admins (bool),
    created_at, messages: [
        {message_id, sender ('user'|'admin'), sender_email, body, created_at}
    ]

Endpoints (user side)
---------------------
POST   /api/support/threads                        create a new thread
GET    /api/support/threads                        list my threads
GET    /api/support/threads/{thread_id}            full thread, marks user-side read
POST   /api/support/threads/{thread_id}/messages   user replies to existing thread

Endpoints (admin side)
----------------------
GET    /api/admin/support/threads                  all threads with filter
POST   /api/admin/support/threads/{id}/reply       admin reply, sets unread_for_user
POST   /api/admin/support/threads/{id}/status      mark resolved / closed / open

Notes
-----
- Inline anti-abuse via guard_expensive_action (3/min user-create, 10/min replies).
- Empty/spammy 1-char bodies blocked at the model layer.
- Admin replies go from whichever admin email is authenticated.
- We never delete threads; admins can close them.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field

from auth import get_current_user
from db import db
from admin import get_admin_user
from anti_abuse import guard_expensive_action

router = APIRouter(prefix="/api/support", tags=["support"])
admin_router = APIRouter(prefix="/api/admin/support", tags=["admin"])
logger = logging.getLogger(__name__)

VALID_KINDS = ("concern", "recommendation")
VALID_STATUSES = ("open", "awaiting_user", "resolved", "closed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────── Pydantic models ───────────────

class ThreadCreate(BaseModel):
    kind: str = Field(..., pattern=r"^(concern|recommendation)$")
    subject: str = Field(..., min_length=3, max_length=120)
    body: str = Field(..., min_length=10, max_length=4000)


class ThreadReply(BaseModel):
    body: str = Field(..., min_length=2, max_length=4000)


class ThreadStatusUpdate(BaseModel):
    status: str = Field(..., pattern=r"^(open|awaiting_user|resolved|closed)$")


def _serialize_thread(t: dict) -> dict:
    return {
        "thread_id": t.get("thread_id"),
        "user_id": t.get("user_id"),
        "user_email": t.get("user_email"),
        "kind": t.get("kind"),
        "subject": t.get("subject"),
        "status": t.get("status"),
        "last_message_at": t.get("last_message_at"),
        "unread_for_user": bool(t.get("unread_for_user")),
        "unread_for_admins": bool(t.get("unread_for_admins")),
        "created_at": t.get("created_at"),
        "message_count": len(t.get("messages") or []),
    }


def _serialize_thread_full(t: dict) -> dict:
    out = _serialize_thread(t)
    out["messages"] = t.get("messages") or []
    return out


# ─────────────── User endpoints ───────────────

@router.post("/threads")
async def create_thread(payload: ThreadCreate, request: Request, user: dict = Depends(get_current_user)):
    await guard_expensive_action(
        user=user, scope="support.thread_create", request=request,
        max_per_user_per_min=3, max_per_user_per_hour=15,
        endpoint="POST /api/support/threads",
    )
    thread_id = "th_" + uuid.uuid4().hex[:18]
    now = _now()
    first_msg = {
        "message_id": uuid.uuid4().hex,
        "sender": "user",
        "sender_email": user.get("email"),
        "body": payload.body.strip(),
        "created_at": now,
    }
    doc = {
        "thread_id": thread_id,
        "user_id": user["user_id"],
        "user_email": user.get("email"),
        "kind": payload.kind,
        "subject": payload.subject.strip(),
        "status": "open",
        "messages": [first_msg],
        "last_message_at": now,
        "unread_for_user": False,
        "unread_for_admins": True,
        "created_at": now,
    }
    await db.support_threads.insert_one(doc)
    return _serialize_thread(doc)


@router.get("/threads")
async def list_my_threads(user: dict = Depends(get_current_user), limit: int = Query(default=50, ge=1, le=200)):
    cursor = db.support_threads.find(
        {"user_id": user["user_id"]},
        {"_id": 0},
    ).sort("last_message_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    unread = sum(1 for t in docs if t.get("unread_for_user"))
    return {
        "items": [_serialize_thread(t) for t in docs],
        "count": len(docs),
        "unread": unread,
    }


@router.get("/threads/{thread_id}")
async def get_my_thread(thread_id: str, user: dict = Depends(get_current_user)):
    doc = await db.support_threads.find_one({"thread_id": thread_id, "user_id": user["user_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Thread not found."})
    # Mark read for user
    if doc.get("unread_for_user"):
        await db.support_threads.update_one({"thread_id": thread_id}, {"$set": {"unread_for_user": False}})
        doc["unread_for_user"] = False
    return _serialize_thread_full(doc)


@router.post("/threads/{thread_id}/messages")
async def reply_to_thread(thread_id: str, payload: ThreadReply, request: Request, user: dict = Depends(get_current_user)):
    await guard_expensive_action(
        user=user, scope="support.thread_reply", request=request,
        max_per_user_per_min=10, max_per_user_per_hour=60,
        endpoint="POST /api/support/threads/{id}/messages",
    )
    doc = await db.support_threads.find_one({"thread_id": thread_id, "user_id": user["user_id"]}, {"_id": 0, "thread_id": 1, "status": 1})
    if not doc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Thread not found."})
    if doc.get("status") == "closed":
        raise HTTPException(status_code=400, detail={"code": "thread_closed", "message": "This thread is closed. Open a new one if needed."})
    now = _now()
    msg = {
        "message_id": uuid.uuid4().hex,
        "sender": "user",
        "sender_email": user.get("email"),
        "body": payload.body.strip(),
        "created_at": now,
    }
    await db.support_threads.update_one(
        {"thread_id": thread_id},
        {"$push": {"messages": msg},
         "$set": {"last_message_at": now, "unread_for_admins": True, "status": "open"}},
    )
    return {"ok": True, "message": msg}


# ─────────────── Admin endpoints ───────────────

@admin_router.get("/threads")
async def admin_list_threads(
    admin: dict = Depends(get_admin_user),
    status: Optional[str] = Query(default=None),
    unread_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
):
    q: dict = {}
    if status and status in VALID_STATUSES:
        q["status"] = status
    if unread_only:
        q["unread_for_admins"] = True
    cursor = db.support_threads.find(q, {"_id": 0}).sort("last_message_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return {
        "items": [_serialize_thread(t) for t in docs],
        "count": len(docs),
        "unread_total": await db.support_threads.count_documents({"unread_for_admins": True}),
    }


@admin_router.get("/threads/{thread_id}")
async def admin_get_thread(thread_id: str, admin: dict = Depends(get_admin_user)):
    doc = await db.support_threads.find_one({"thread_id": thread_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Thread not found."})
    if doc.get("unread_for_admins"):
        await db.support_threads.update_one({"thread_id": thread_id}, {"$set": {"unread_for_admins": False}})
        doc["unread_for_admins"] = False
    return _serialize_thread_full(doc)


@admin_router.post("/threads/{thread_id}/reply")
async def admin_reply(thread_id: str, payload: ThreadReply, admin: dict = Depends(get_admin_user)):
    doc = await db.support_threads.find_one({"thread_id": thread_id}, {"_id": 0, "thread_id": 1, "status": 1})
    if not doc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Thread not found."})
    now = _now()
    msg = {
        "message_id": uuid.uuid4().hex,
        "sender": "admin",
        "sender_email": admin.get("email"),
        "body": payload.body.strip(),
        "created_at": now,
    }
    new_status = "awaiting_user" if doc.get("status") not in ("resolved", "closed") else doc.get("status")
    await db.support_threads.update_one(
        {"thread_id": thread_id},
        {"$push": {"messages": msg},
         "$set": {"last_message_at": now, "unread_for_user": True, "unread_for_admins": False,
                  "status": new_status}},
    )
    return {"ok": True, "message": msg}


@admin_router.post("/threads/{thread_id}/status")
async def admin_set_status(thread_id: str, payload: ThreadStatusUpdate, admin: dict = Depends(get_admin_user)):
    if payload.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail={"code": "invalid_status"})
    res = await db.support_threads.update_one(
        {"thread_id": thread_id},
        {"$set": {"status": payload.status}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    return {"ok": True, "thread_id": thread_id, "status": payload.status}


async def ensure_indexes() -> None:
    try:
        await db.support_threads.create_index("user_id")
        await db.support_threads.create_index("status")
        await db.support_threads.create_index("unread_for_admins")
        await db.support_threads.create_index("last_message_at")
        logger.info("support_inbox: indexes ensured")
    except Exception as e:
        logger.warning("support_inbox: index creation failed: %s", e)
