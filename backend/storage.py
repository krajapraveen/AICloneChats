import os
import uuid
import logging
import requests
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Response
from urllib.parse import quote

from auth import get_current_user
from db import db

router = APIRouter(prefix="/api/storage", tags=["storage"])
logger = logging.getLogger(__name__)

STORAGE_URL = "https://integrations.emergentagent.com/objstore/api/v1/storage"
EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
APP_NAME = os.environ.get("APP_NAME", "cloneme")

_storage_key = None

ALLOWED_IMG = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_BYTES = 5 * 1024 * 1024  # 5 MB


def _init_storage() -> str:
    global _storage_key
    if _storage_key:
        return _storage_key
    if not EMERGENT_KEY:
        raise HTTPException(status_code=500, detail="EMERGENT_LLM_KEY not configured")
    try:
        r = requests.post(f"{STORAGE_URL}/init", json={"emergent_key": EMERGENT_KEY}, timeout=30)
        r.raise_for_status()
        _storage_key = r.json()["storage_key"]
        return _storage_key
    except Exception as e:
        logger.exception("storage init failed")
        raise HTTPException(status_code=502, detail=f"Storage init failed: {e}")


def _put(path: str, data: bytes, content_type: str) -> dict:
    key = _init_storage()
    r = requests.put(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key, "Content-Type": content_type},
        data=data,
        timeout=120,
    )
    if r.status_code == 403:
        # refresh key once
        global _storage_key
        _storage_key = None
        key = _init_storage()
        r = requests.put(
            f"{STORAGE_URL}/objects/{path}",
            headers={"X-Storage-Key": key, "Content-Type": content_type},
            data=data,
            timeout=120,
        )
    r.raise_for_status()
    return r.json()


def _get(path: str) -> tuple[bytes, str]:
    key = _init_storage()
    r = requests.get(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key},
        timeout=60,
    )
    if r.status_code == 403:
        global _storage_key
        _storage_key = None
        key = _init_storage()
        r = requests.get(
            f"{STORAGE_URL}/objects/{path}",
            headers={"X-Storage-Key": key},
            timeout=60,
        )
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "application/octet-stream")


@router.post("/upload-avatar")
async def upload_avatar(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    if file.content_type not in ALLOWED_IMG:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, WebP, or GIF allowed")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "png"
    if ext not in {"jpg", "jpeg", "png", "webp", "gif"}:
        ext = "png"
    path = f"{APP_NAME}/avatars/{user['user_id']}/{uuid.uuid4().hex}.{ext}"

    try:
        result = _put(path, data, file.content_type)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("avatar upload failed")
        raise HTTPException(status_code=502, detail=f"Upload failed: {e}")

    storage_path = result.get("path", path)

    await db.files.insert_one({
        "file_id": uuid.uuid4().hex,
        "user_id": user["user_id"],
        "storage_path": storage_path,
        "content_type": file.content_type,
        "size": result.get("size", len(data)),
        "purpose": "avatar",
        "is_deleted": False,
    })

    public_url = f"/api/storage/files/{quote(storage_path, safe='')}"
    return {"avatar_url": public_url, "storage_path": storage_path}


@router.get("/files/{path:path}")
async def serve_file(path: str):
    """Public read endpoint — no auth (avatars are publicly viewable)."""
    record = await db.files.find_one({"storage_path": path, "is_deleted": False}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        data, ct = _get(path)
    except Exception:
        raise HTTPException(status_code=502, detail="Storage fetch failed")
    return Response(content=data, media_type=record.get("content_type") or ct, headers={"Cache-Control": "public, max-age=3600"})
