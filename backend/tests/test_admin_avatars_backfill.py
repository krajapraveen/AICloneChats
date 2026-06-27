"""End-to-end coverage for the one-shot
`POST /api/admin/avatars/backfill-clones` endpoint.

We spec-pin the operator contract: who can call it, what gets touched on the
first run, and what re-running does (no-op when not forced)."""
from __future__ import annotations

import os
import sys
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
ADMIN_EMAIL = "krajapraveen@gmail.com"

PRAVEEN_AVATAR_URL = "https://aiclonechats.com/founder.jpg"


def _register(email: str, password: str = "TestPass123!") -> dict:
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": password, "name": "Backfill Tester"},
        timeout=30,
    )
    assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"
    body = r.json()
    return {"user_id": body["user"]["user_id"], "session_token": body["session_token"]}


async def _mint_admin_token() -> str | None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    u = await db.users.find_one({"email": ADMIN_EMAIL}, {"user_id": 1})
    if not u:
        return None
    token = f"st_{uuid.uuid4().hex}{uuid.uuid4().hex}"
    await db.user_sessions.insert_one({
        "session_token": token,
        "user_id": u["user_id"],
        "source": "test-mint-admin-backfill",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    })
    return token


def _admin_token() -> str:
    token = asyncio.new_event_loop().run_until_complete(_mint_admin_token())
    if not token:
        pytest.skip("admin user not seeded")
    return token


def _post(token: str | None, params: dict | None = None) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.post(
        f"{BASE_URL}/api/admin/avatars/backfill-clones",
        headers=headers,
        params=params or {},
        timeout=60,
    )


def test_unauth_returns_401_or_403():
    r = _post(None)
    assert r.status_code in (401, 403)


def test_non_admin_user_blocked():
    reg = _register(f"non_admin_{uuid.uuid4().hex[:6]}@example.com")
    r = _post(reg["session_token"])
    assert r.status_code == 403


def test_dry_run_does_not_write():
    """dry_run=true reports counts but writes nothing.

    We don't depend on the DB pre-state — we just verify that after a dry
    run, the `updated` count is 0 and the response shape is correct.
    """
    token = _admin_token()
    r = _post(token, params={"dry_run": "true"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["dry_run"] is True
    assert data["updated"] == 0
    assert "scanned" in data
    assert "eligible" in data
    assert "sample" in data
    assert isinstance(data["sample"], list)


def test_real_run_writes_then_idempotent_second_run():
    """First call backfills any clones with empty avatar_url. Second call
    finds nothing left to do (no force flag)."""
    token = _admin_token()

    # Force-empty one clone so we have a known target to write.
    async def _stage():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        target = await d.clones.find_one({}, {"_id": 0, "clone_id": 1, "avatar_url": 1})
        if not target:
            return None
        await d.clones.update_one({"clone_id": target["clone_id"]}, {"$set": {"avatar_url": ""}})
        return target["clone_id"]
    target_id = asyncio.new_event_loop().run_until_complete(_stage())
    if not target_id:
        pytest.skip("no clones to test against")

    r1 = _post(token)
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["ok"] is True
    assert d1["dry_run"] is False
    assert d1["updated"] >= 1, f"expected at least 1 update, got {d1}"

    # Verify the target now has a non-empty avatar_url.
    async def _read():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        return await d.clones.find_one({"clone_id": target_id}, {"_id": 0, "avatar_url": 1})
    after = asyncio.new_event_loop().run_until_complete(_read())
    assert after is not None
    assert (after.get("avatar_url") or "").startswith("http"), after

    # Second run with no force flag should NOT touch this target again.
    r2 = _post(token)
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["ok"] is True
    # `updated` may still be >0 if other clones had empties between runs
    # (other tests), but the *previously* updated clone must not flip.
    after2 = asyncio.new_event_loop().run_until_complete(_read())
    assert after2["avatar_url"] == after["avatar_url"], "non-force run mutated already-filled clone"


def test_force_flag_overwrites_everything():
    """force=true sets every clone's avatar_url to the deterministic target,
    even if it was already filled."""
    token = _admin_token()

    # Set one clone to a known sentinel so we can detect overwrite.
    sentinel = "https://example.invalid/SENTINEL.png"

    async def _stage():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        target = await d.clones.find_one({"slug": {"$ne": "praveen"}}, {"_id": 0, "clone_id": 1})
        if not target:
            return None
        await d.clones.update_one({"clone_id": target["clone_id"]}, {"$set": {"avatar_url": sentinel}})
        return target["clone_id"]
    target_id = asyncio.new_event_loop().run_until_complete(_stage())
    if not target_id:
        pytest.skip("no non-praveen clones to test against")

    r = _post(token, params={"force": "true"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["force"] is True

    async def _read():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        return await d.clones.find_one({"clone_id": target_id}, {"_id": 0, "avatar_url": 1})
    after = asyncio.new_event_loop().run_until_complete(_read())
    assert after["avatar_url"] != sentinel, "force=true did not overwrite the sentinel"
    assert after["avatar_url"].startswith("https://"), after


def test_praveen_clone_gets_founder_image():
    """If a clone with slug=praveen exists, it gets the canonical founder URL,
    not a pravatar placeholder."""
    token = _admin_token()

    # Seed a Praveen clone (or reuse if one exists), clear its avatar_url.
    test_clone_id = f"clone_test_praveen_{uuid.uuid4().hex[:6]}"

    async def _stage():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        await d.clones.insert_one({
            "clone_id": test_clone_id,
            "slug": "praveen",
            "display_name": "Praveen",
            "user_id": "user_test_seed",
            "avatar_url": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    try:
        asyncio.new_event_loop().run_until_complete(_stage())
    except Exception:
        # Duplicate slug — fine, another test may have seeded it. Skip cleanly.
        pytest.skip("praveen slug already in use by another test row")

    try:
        r = _post(token)
        assert r.status_code == 200, r.text

        async def _read():
            c = AsyncIOMotorClient(MONGO_URL)
            d = c[DB_NAME]
            return await d.clones.find_one({"clone_id": test_clone_id}, {"_id": 0, "avatar_url": 1})
        after = asyncio.new_event_loop().run_until_complete(_read())
        assert after is not None
        assert after["avatar_url"] == PRAVEEN_AVATAR_URL, (
            f"Praveen clone got {after['avatar_url']!r}, expected {PRAVEEN_AVATAR_URL!r}"
        )
    finally:
        async def _cleanup():
            c = AsyncIOMotorClient(MONGO_URL)
            d = c[DB_NAME]
            await d.clones.delete_one({"clone_id": test_clone_id})
        asyncio.new_event_loop().run_until_complete(_cleanup())


def test_placeholder_is_deterministic_per_clone_id():
    """Re-running with force=true assigns the SAME placeholder for the same
    clone_id (so users don't see their clone's face shuffle on every run)."""
    token = _admin_token()
    seed_clone_id = f"clone_det_{uuid.uuid4().hex[:8]}"

    async def _stage():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        await d.clones.insert_one({
            "clone_id": seed_clone_id,
            "slug": f"determinism-{seed_clone_id[-6:]}",
            "display_name": "Determinism Tester",
            "user_id": "user_test_seed",
            "avatar_url": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    asyncio.new_event_loop().run_until_complete(_stage())
    try:
        r1 = _post(token, params={"force": "true"})
        assert r1.status_code == 200

        async def _read():
            c = AsyncIOMotorClient(MONGO_URL)
            d = c[DB_NAME]
            return await d.clones.find_one({"clone_id": seed_clone_id}, {"_id": 0, "avatar_url": 1})
        url1 = asyncio.new_event_loop().run_until_complete(_read())["avatar_url"]

        # Re-set to empty then force again — the placeholder URL must match.
        async def _reset():
            c = AsyncIOMotorClient(MONGO_URL)
            d = c[DB_NAME]
            await d.clones.update_one({"clone_id": seed_clone_id}, {"$set": {"avatar_url": ""}})
        asyncio.new_event_loop().run_until_complete(_reset())
        r2 = _post(token, params={"force": "true"})
        assert r2.status_code == 200

        url2 = asyncio.new_event_loop().run_until_complete(_read())["avatar_url"]
        assert url1 == url2, f"placeholder changed across runs: {url1!r} != {url2!r}"
    finally:
        async def _cleanup():
            c = AsyncIOMotorClient(MONGO_URL)
            d = c[DB_NAME]
            await d.clones.delete_one({"clone_id": seed_clone_id})
        asyncio.new_event_loop().run_until_complete(_cleanup())
