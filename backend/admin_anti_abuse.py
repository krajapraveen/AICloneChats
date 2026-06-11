"""
admin_anti_abuse.py — Admin observability + control for the anti-abuse layer.

Endpoints:
  GET  /api/admin/anti-abuse/summary             aggregate metrics
  GET  /api/admin/anti-abuse/recent              latest abuse events
  GET  /api/admin/anti-abuse/suspicious-users    users approaching limits
  GET  /api/admin/anti-abuse/blocked-users       currently limited/blocked
  POST /api/admin/anti-abuse/set-status          set abuse_status for a user
  POST /api/admin/anti-abuse/reset-counters      wipe counters for a user

All endpoints require admin role.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth import get_current_user
from db import db
from admin import get_admin_user
from anti_abuse import (
    set_user_abuse_status,
    reset_abuse_counters,
    is_anti_abuse_exempt_user,
)

router = APIRouter(prefix="/api/admin/anti-abuse", tags=["admin"])
logger = logging.getLogger(__name__)


# ---------------- Models ----------------

class SetStatusReq(BaseModel):
    user_id: str = Field(..., min_length=1)
    status: str = Field(..., pattern=r"^(normal|limited|blocked)$")
    reason: str = Field(..., min_length=3, max_length=400)


class ResetCountersReq(BaseModel):
    user_id: str = Field(..., min_length=1)


# ---------------- Endpoints ----------------

@router.get("/summary")
async def summary(
    user: dict = Depends(get_admin_user),
    hours: int = Query(default=24, ge=1, le=168),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    # Counts per event type from login_events
    pipeline = [
        {"$match": {
            "event": {"$in": [
                "anti_abuse_rate_limited",
                "anti_abuse_user_limited",
                "anti_abuse_user_blocked",
                "anti_abuse_user_unblocked",
                "anti_abuse_exempt_bypassed",
                "anti_abuse_blocked_user_attempt",
                "anti_abuse_counters_reset",
            ]},
            "created_at": {"$gte": since.isoformat()},
        }},
        {"$group": {"_id": "$event", "count": {"$sum": 1}}},
    ]
    rows = await db.login_events.aggregate(pipeline).to_list(length=100)
    by_event = {r["_id"]: r["count"] for r in rows}
    total_users_blocked = await db.users.count_documents({"abuse_status": "blocked"})
    total_users_limited = await db.users.count_documents({"abuse_status": "limited"})
    return {
        "hours": hours,
        "by_event": by_event,
        "users_blocked": total_users_blocked,
        "users_limited": total_users_limited,
    }


@router.get("/recent")
async def recent(
    user: dict = Depends(get_admin_user),
    limit: int = Query(default=50, ge=1, le=500),
    event: Optional[str] = Query(default=None),
):
    q: dict = {"event": {"$regex": r"^anti_abuse_"}}
    if event:
        q["event"] = event
    cursor = db.login_events.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    items = await cursor.to_list(length=limit)
    return {"items": items, "count": len(items)}


@router.get("/suspicious-users")
async def suspicious_users(
    user: dict = Depends(get_admin_user),
    hours: int = Query(default=1, ge=1, le=24),
    min_events: int = Query(default=20, ge=1, le=1000),
):
    """Users with the most anti_abuse_events in the last N hours, ranked.
    Useful for spotting normal users about to be hit by limits."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    pipeline = [
        {"$match": {"created_at": {"$gte": since}, "exempt": False, "user_id": {"$ne": None}}},
        {"$group": {
            "_id": {"user_id": "$user_id", "email": "$email"},
            "count": {"$sum": 1},
            "scopes": {"$addToSet": "$scope"},
            "last_seen": {"$max": "$created_at"},
        }},
        {"$match": {"count": {"$gte": min_events}}},
        {"$sort": {"count": -1}},
        {"$limit": 100},
    ]
    rows = await db.anti_abuse_events.aggregate(pipeline).to_list(length=100)
    out = []
    for r in rows:
        out.append({
            "user_id": r["_id"].get("user_id"),
            "email": r["_id"].get("email"),
            "events": r["count"],
            "scopes": r["scopes"],
            "last_seen": r["last_seen"].isoformat() if hasattr(r["last_seen"], "isoformat") else str(r["last_seen"]),
        })
    return {"hours": hours, "min_events": min_events, "users": out, "count": len(out)}


@router.get("/blocked-users")
async def blocked_users(user: dict = Depends(get_admin_user)):
    cursor = db.users.find(
        {"abuse_status": {"$in": ["limited", "blocked"]}},
        {"_id": 0, "user_id": 1, "email": 1, "abuse_status": 1, "abuse_status_reason": 1,
         "abuse_status_set_at": 1, "abuse_status_set_by": 1},
    ).sort("abuse_status_set_at", -1).limit(200)
    items = await cursor.to_list(length=200)
    return {"items": items, "count": len(items)}


@router.post("/set-status")
async def set_status(req: SetStatusReq, user: dict = Depends(get_admin_user)):
    return await set_user_abuse_status(
        req.user_id, req.status, req.reason,
        by_admin_email=user.get("email") or "",
    )


@router.post("/reset-counters")
async def reset_counters(req: ResetCountersReq, user: dict = Depends(get_admin_user)):
    return await reset_abuse_counters(req.user_id, by_admin_email=user.get("email") or "")
