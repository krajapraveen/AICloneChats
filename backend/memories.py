import uuid
from fastapi import APIRouter, Depends, HTTPException
from typing import List

from db import db
from auth import get_current_user
from models import MemoryCreate, MemoryUpdate, now_iso

router = APIRouter(prefix="/api/clones", tags=["memories"])


async def _ensure_owner(clone_id: str, user_id: str) -> dict:
    clone = await db.clones.find_one({"clone_id": clone_id, "user_id": user_id}, {"_id": 0})
    if not clone:
        raise HTTPException(status_code=404, detail="Clone not found")
    return clone


@router.get("/{clone_id}/memories")
async def list_memories(clone_id: str, user: dict = Depends(get_current_user)):
    await _ensure_owner(clone_id, user["user_id"])
    cursor = db.clone_memories.find({"clone_id": clone_id}, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(500)


@router.post("/{clone_id}/memories")
async def add_memory(clone_id: str, payload: MemoryCreate, user: dict = Depends(get_current_user)):
    await _ensure_owner(clone_id, user["user_id"])
    memory_id = f"mem_{uuid.uuid4().hex[:14]}"
    doc = {
        "memory_id": memory_id,
        "clone_id": clone_id,
        "user_id": user["user_id"],
        "content": payload.content,
        "memory_type": payload.memory_type,
        "importance": float(payload.importance),
        "visibility": payload.visibility,
        "can_use_for_reply": payload.can_use_for_reply,
        "created_at": now_iso(),
    }
    await db.clone_memories.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


@router.patch("/{clone_id}/memories/{memory_id}")
async def update_memory(clone_id: str, memory_id: str, payload: MemoryUpdate, user: dict = Depends(get_current_user)):
    await _ensure_owner(clone_id, user["user_id"])
    update = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    res = await db.clone_memories.update_one(
        {"memory_id": memory_id, "clone_id": clone_id},
        {"$set": update},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Memory not found")
    updated = await db.clone_memories.find_one({"memory_id": memory_id}, {"_id": 0})
    return updated


@router.delete("/{clone_id}/memories/{memory_id}")
async def delete_memory(clone_id: str, memory_id: str, user: dict = Depends(get_current_user)):
    await _ensure_owner(clone_id, user["user_id"])
    res = await db.clone_memories.delete_one({"memory_id": memory_id, "clone_id": clone_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"ok": True}
