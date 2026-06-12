import uuid
import hashlib
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from pydantic import BaseModel

from db import db
from auth import get_optional_user, get_current_user
from models import now_iso

router = APIRouter(prefix="/api", tags=["analytics"])

SHARE_EVENTS = ["share_card_downloaded", "share_card_copied", "clone_shared", "share_link_clicked"]
MOOD_CATEGORIES = {"funny", "deep", "savage", "quote"}


def _daily_rotation_boost(clone_id: str) -> float:
    """Deterministic per-day pseudo-random boost in [0, 1].

    Same clone, same day → same value. Same clone, next day → different value.
    Used to gently re-rank DEMO clones so a fresh set surfaces every day
    without touching the DB. Real (non-demo) user clones get boost=0.0 so
    their ranking is purely organic.
    """
    seed = f"{date.today().isoformat()}:{clone_id}"
    h = hashlib.sha256(seed.encode()).hexdigest()
    # Take 8 hex chars → int → normalize to [0, 1]
    return int(h[:8], 16) / 0xFFFFFFFF


class EventRequest(BaseModel):
    event_name: str
    clone_id: Optional[str] = None
    metadata: Optional[dict] = None


@router.post("/analytics/event")
async def track_event(payload: EventRequest, user: Optional[dict] = Depends(get_optional_user)):
    if not payload.event_name or len(payload.event_name) > 64:
        raise HTTPException(status_code=400, detail="Invalid event_name")
    await db.clone_analytics.insert_one({
        "event_id": uuid.uuid4().hex,
        "event_name": payload.event_name,
        "clone_id": payload.clone_id,
        "user_id": user["user_id"] if user else None,
        "metadata": payload.metadata or {},
        "created_at": now_iso(),
    })
    return {"ok": True}


@router.get("/analytics/clone/{clone_id}")
async def clone_analytics(clone_id: str, user: Optional[dict] = Depends(get_optional_user)):
    if not user:
        return {"events": {}}
    clone = await db.clones.find_one({"clone_id": clone_id, "user_id": user["user_id"]}, {"_id": 0})
    if not clone:
        return {"events": {}}
    pipeline = [
        {"$match": {"clone_id": clone_id}},
        {"$group": {"_id": "$event_name", "count": {"$sum": 1}}},
    ]
    counts = {}
    async for row in db.clone_analytics.aggregate(pipeline):
        counts[row["_id"]] = row["count"]
    return {"events": counts}


@router.get("/analytics/stats/{clone_id_or_slug}")
async def clone_stats(clone_id_or_slug: str):
    """Public stats for a single clone — used by share counter on PublicClone."""
    key = clone_id_or_slug.lower()
    clone = await db.clones.find_one(
        {"$or": [{"slug": key}, {"clone_id": clone_id_or_slug}]},
        {"_id": 0, "clone_id": 1, "visibility": 1},
    )
    if not clone:
        raise HTTPException(status_code=404, detail="Clone not found")
    cid = clone["clone_id"]
    share_count = await db.clone_analytics.count_documents(
        {"clone_id": cid, "event_name": {"$in": SHARE_EVENTS}}
    )
    message_count = await db.clone_messages.count_documents({"clone_id": cid})
    visitor_ids = await db.clone_conversations.distinct("visitor_id", {"clone_id": cid})
    return {
        "share_count": share_count,
        "message_count": message_count,
        "visitor_count": len(visitor_ids),
    }


@router.get("/explore")
async def explore(
    category: str = Query("trending"),
    limit: int = Query(20, ge=1, le=50),
):
    """
    Public discovery feed. category in {trending, funny, deep, savage, quote, active, recent}.
    Score = shares*0.5 + messages*0.3 + unique_visitors*0.2
    """
    pipeline = [
        {"$match": {"visibility": "public", "status": {"$ne": "paused"}}},
        {"$lookup": {"from": "clone_analytics", "localField": "clone_id", "foreignField": "clone_id", "as": "events"}},
        {"$lookup": {"from": "clone_messages", "localField": "clone_id", "foreignField": "clone_id", "as": "msgs"}},
        {"$lookup": {"from": "clone_conversations", "localField": "clone_id", "foreignField": "clone_id", "as": "convs"}},
        {"$project": {
            "_id": 0,
            "clone_id": 1, "slug": 1, "display_name": 1, "bio": 1, "avatar_url": 1, "created_at": 1, "personality": 1,
            "is_demo": {"$ifNull": ["$is_demo", False]},
            "demo_category": {"$ifNull": ["$demo_category", None]},
            "event_names": "$events.event_name",
            "moods": "$events.metadata.mood",
            "message_count": {"$size": "$msgs"},
            "visitor_ids": "$convs.visitor_id",
        }},
        {"$limit": 500},
    ]
    docs = await db.clones.aggregate(pipeline).to_list(500)

    enriched = []
    for c in docs:
        events = c.pop("event_names", []) or []
        moods = c.pop("moods", []) or []
        visitor_ids = c.pop("visitor_ids", []) or []
        share_count = sum(1 for e in events if e in SHARE_EVENTS)
        unique_visitors = len(set(v for v in visitor_ids if v))
        score = share_count * 0.5 + (c.get("message_count") or 0) * 0.3 + unique_visitors * 0.2

        # Daily rotation: demo clones get a small (0-15%) per-day boost so the
        # set that surfaces varies day to day. Organic clones are untouched.
        if c.get("is_demo"):
            boost = _daily_rotation_boost(c["clone_id"])
            score = score * (1.0 + boost * 0.15)

        mood_counts = {}
        for m in moods:
            if m:
                mood_counts[m] = mood_counts.get(m, 0) + 1
        primary_mood = max(mood_counts, key=mood_counts.get) if mood_counts else None
        c.update({
            "share_count": share_count,
            "visitor_count": unique_visitors,
            "score": round(score, 2),
            "primary_mood": primary_mood,
            "mood_counts": mood_counts,
        })
        enriched.append(c)

    cat = (category or "trending").lower()
    if cat in MOOD_CATEGORIES:
        enriched = [c for c in enriched if c["mood_counts"].get(cat, 0) > 0]
        enriched.sort(key=lambda c: (c["mood_counts"].get(cat, 0), c["score"]), reverse=True)
    elif cat == "active":
        enriched.sort(key=lambda c: (c["message_count"], c["score"]), reverse=True)
    elif cat == "recent":
        enriched.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    else:  # trending
        enriched.sort(key=lambda c: (c["score"], c.get("created_at") or ""), reverse=True)

    return {
        "category": cat,
        "total": len(enriched),
        "clones": enriched[:limit],
    }
