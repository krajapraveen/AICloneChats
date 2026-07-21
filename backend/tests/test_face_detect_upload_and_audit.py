"""Additional coverage for face-detection preflight:
   - POST /api/storage/upload-avatar returns face_check {...} regardless of face presence
   - POST /api/admin/avatars/audit-faces (real run, dry_run=false) persists face_detected
"""
from __future__ import annotations
import io, os, sys, uuid, asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import requests
from PIL import Image
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
ADMIN_EMAIL = "krajapraveen@gmail.com"


def _blank_png() -> bytes:
    img = Image.new("RGB", (400, 400), color=(255, 255, 255))
    buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()


def _register(email: str) -> dict:
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"email": email, "password": "TestPass123!", "name": "FUp"}, timeout=30)
    assert r.status_code == 200, r.text
    b = r.json()
    return {"user_id": b["user"]["user_id"], "token": b["session_token"]}


async def _mint_admin() -> str | None:
    c = AsyncIOMotorClient(MONGO_URL); db = c[DB_NAME]
    u = await db.users.find_one({"email": ADMIN_EMAIL}, {"user_id": 1})
    if not u: return None
    tok = f"st_{uuid.uuid4().hex}{uuid.uuid4().hex}"
    await db.user_sessions.insert_one({
        "session_token": tok, "user_id": u["user_id"], "source": "test-face-upload-audit",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
    })
    return tok


def test_upload_avatar_returns_face_check_with_no_face():
    reg = _register(f"upav_{uuid.uuid4().hex[:6]}@example.com")
    files = {"file": ("blank.png", _blank_png(), "image/png")}
    r = requests.post(f"{BASE_URL}/api/storage/upload-avatar",
                      headers={"Authorization": f"Bearer {reg['token']}"},
                      files=files, timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "avatar_url" in d
    assert "face_check" in d
    fc = d["face_check"]
    # Upload succeeded despite no face → soft warning path.
    assert fc.get("has_face") is False
    # Shape checks — allow either full or minimal shape from detector_error fallback.
    assert "reason" in fc


def test_upload_avatar_requires_auth():
    files = {"file": ("blank.png", _blank_png(), "image/png")}
    r = requests.post(f"{BASE_URL}/api/storage/upload-avatar", files=files, timeout=15)
    assert r.status_code in (401, 403)


def test_audit_faces_real_run_persists_face_detected():
    """Create a clone with a blank placeholder avatar_url, then run the
    admin audit with dry_run=false and only_missing=true. The clone's
    face_detected should be persisted as False afterwards."""
    reg = _register(f"aud_r_{uuid.uuid4().hex[:6]}@example.com")
    slug = f"aud-r-{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{BASE_URL}/api/clones",
                      headers={"Authorization": f"Bearer {reg['token']}"},
                      json={"slug": slug, "display_name": "Aud", "bio": "b",
                            "avatar_url": "https://via.placeholder.com/300x300.png?text=NoFace",
                            "visibility": "private"}, timeout=30)
    assert r.status_code == 200, r.text
    clone_id = r.json()["clone_id"]

    admin_tok = asyncio.new_event_loop().run_until_complete(_mint_admin())
    if not admin_tok:
        pytest.skip("admin not seeded")

    r = requests.post(f"{BASE_URL}/api/admin/avatars/audit-faces",
                      headers={"Authorization": f"Bearer {admin_tok}"},
                      params={"dry_run": "false", "only_missing": "true"},
                      timeout=180)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False
    assert body["scanned"] >= 1

    async def _read():
        c = AsyncIOMotorClient(MONGO_URL)
        return await c[DB_NAME].clones.find_one({"clone_id": clone_id},
            {"_id": 0, "face_detected": 1, "face_check": 1})
    doc = asyncio.new_event_loop().run_until_complete(_read())
    assert doc is not None
    # face_detected must now be a boolean (not None/missing) after audit
    assert doc.get("face_detected") in (True, False)
    assert doc.get("face_check") is not None


def test_validate_avatar_unauthenticated_blocked():
    """Unauth call to /clones/{id}/validate-avatar must be 401/403."""
    # Use a bogus clone_id — auth guard should fire before ownership check.
    r = requests.post(f"{BASE_URL}/api/clones/does-not-exist/validate-avatar", timeout=15)
    assert r.status_code in (401, 403)
