"""Admin endpoints for the centralized safety filter."""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query

from db import db
from auth import get_current_user

admin_router = APIRouter(prefix="/api/admin/safety", tags=["safety-admin"])


async def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


@admin_router.get("/moderation")
async def safety_moderation(_admin: dict = Depends(_require_admin), days: int = Query(default=7, ge=1, le=90), limit: int = Query(default=200, ge=1, le=1000)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    blocked_total = await db.safety_moderation_events.count_documents({"created_at": {"$gte": since}, "action_taken": {"$in": ["block_input", "block_output"]}})
    rewrite_total = await db.safety_moderation_events.count_documents({"created_at": {"$gte": since}, "action_taken": "rewrite_output"})
    by_category = []
    rows = await db.safety_moderation_events.aggregate([
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": "$category", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]).to_list(50)
    by_category = [{"category": r["_id"] or "unknown", "count": r["n"]} for r in rows]

    by_route_rows = await db.safety_moderation_events.aggregate([
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": "$route", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]).to_list(50)
    by_route = [{"route": r["_id"] or "unknown", "count": r["n"]} for r in by_route_rows]

    recent = await db.safety_moderation_events.find({"created_at": {"$gte": since}}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)

    return {
        "window_days": days,
        "blocked_total": blocked_total,
        "rewrite_total": rewrite_total,
        "by_category": by_category,
        "by_route": by_route,
        "recent": recent,
    }
