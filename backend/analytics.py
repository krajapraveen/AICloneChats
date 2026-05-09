import uuid
from fastapi import APIRouter, Depends
from typing import Optional
from pydantic import BaseModel

from db import db
from auth import get_optional_user
from models import now_iso

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class EventRequest(BaseModel):
    event_name: str
    clone_id: Optional[str] = None
    metadata: Optional[dict] = None


@router.post("/event")
async def track_event(payload: EventRequest, user: Optional[dict] = Depends(get_optional_user)):
    await db.clone_analytics.insert_one({
        "event_id": uuid.uuid4().hex,
        "event_name": payload.event_name,
        "clone_id": payload.clone_id,
        "user_id": user["user_id"] if user else None,
        "metadata": payload.metadata or {},
        "created_at": now_iso(),
    })
    return {"ok": True}


@router.get("/clone/{clone_id}")
async def clone_analytics(clone_id: str, user: dict = Depends(get_optional_user)):
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
