import uuid
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional

from db import db
from auth import get_current_user, get_optional_user
from models import CloneCreate, CloneUpdate, Clone, now_iso, PERSONALITY_DEFAULT

router = APIRouter(prefix="/api/clones", tags=["clones"])

RESERVED_SLUGS = {"api", "admin", "auth", "login", "register", "dashboard", "settings", "new", "create"}


def _serialize(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


@router.post("")
async def create_clone(payload: CloneCreate, user: dict = Depends(get_current_user)):
    slug = payload.slug.lower()
    if slug in RESERVED_SLUGS:
        raise HTTPException(status_code=400, detail="Slug is reserved")

    existing = await db.clones.find_one({"slug": slug}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=400, detail="Slug already taken")

    # Limit free users to 1 clone
    count = await db.clones.count_documents({"user_id": user["user_id"]})
    if count >= 5:
        raise HTTPException(status_code=400, detail="Clone limit reached")

    clone_id = f"clone_{uuid.uuid4().hex[:14]}"
    now = now_iso()
    doc = {
        "clone_id": clone_id,
        "user_id": user["user_id"],
        "slug": slug,
        "display_name": payload.display_name,
        "bio": payload.bio,
        "avatar_url": payload.avatar_url,
        "default_language": payload.default_language,
        "visibility": payload.visibility,
        "status": "ready",
        "allowed_topics": payload.allowed_topics,
        "blocked_topics": payload.blocked_topics,
        "personality": {**PERSONALITY_DEFAULT, **(payload.personality or {})},
        "created_at": now,
        "updated_at": now,
    }
    insert_doc = dict(doc)
    await db.clones.insert_one(insert_doc)
    return doc


@router.get("/mine")
async def list_my_clones(user: dict = Depends(get_current_user)):
    cursor = db.clones.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(100)


@router.get("/by-slug/{slug}")
async def get_clone_by_slug(slug: str, user: Optional[dict] = Depends(get_optional_user)):
    clone = await db.clones.find_one({"slug": slug.lower()}, {"_id": 0})
    if not clone:
        raise HTTPException(status_code=404, detail="Clone not found")
    if clone["visibility"] == "private":
        if not user or user["user_id"] != clone["user_id"]:
            raise HTTPException(status_code=403, detail="This clone is private")
    return clone


@router.get("/{clone_id}")
async def get_clone(clone_id: str, user: dict = Depends(get_current_user)):
    clone = await db.clones.find_one({"clone_id": clone_id, "user_id": user["user_id"]}, {"_id": 0})
    if not clone:
        raise HTTPException(status_code=404, detail="Clone not found")
    return clone


@router.patch("/{clone_id}")
async def update_clone(clone_id: str, payload: CloneUpdate, user: dict = Depends(get_current_user)):
    clone = await db.clones.find_one({"clone_id": clone_id, "user_id": user["user_id"]}, {"_id": 0})
    if not clone:
        raise HTTPException(status_code=404, detail="Clone not found")

    update = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if "personality" in update:
        update["personality"] = {**clone.get("personality", PERSONALITY_DEFAULT), **update["personality"]}
    update["updated_at"] = now_iso()

    await db.clones.update_one({"clone_id": clone_id}, {"$set": update})
    updated = await db.clones.find_one({"clone_id": clone_id}, {"_id": 0})
    return updated


@router.delete("/{clone_id}")
async def delete_clone(clone_id: str, user: dict = Depends(get_current_user)):
    res = await db.clones.delete_one({"clone_id": clone_id, "user_id": user["user_id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Clone not found")
    # Cascade
    await db.clone_memories.delete_many({"clone_id": clone_id})
    await db.clone_messages.delete_many({"clone_id": clone_id})
    await db.clone_conversations.delete_many({"clone_id": clone_id})
    return {"ok": True}


@router.get("/check-slug/{slug}")
async def check_slug(slug: str):
    slug = slug.lower()
    if slug in RESERVED_SLUGS:
        return {"available": False, "reason": "reserved"}
    existing = await db.clones.find_one({"slug": slug}, {"_id": 0, "slug": 1})
    return {"available": existing is None}
