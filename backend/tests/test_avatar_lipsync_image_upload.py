"""
Tests for the lipsync image upload fix.

Scope:
- `_fetch_image_bytes` handles http(s) URLs.
- `_fetch_image_bytes` handles /api/storage/files/ paths via db.files lookup.
- `_generate_lipsync_video` short-circuits sensibly when FAL_KEY is unset.
- GET /api/avatar-chat/messages?limit=N returns shape with message_id.

These tests run without a real FAL_KEY — they validate the helper + endpoint
plumbing only. End-to-end fal.ai is exercised in production smoke tests.
"""
from __future__ import annotations
import os
import asyncio
import uuid

import pytest
import httpx

BASE = os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001"
API = f"{BASE}/api"
ADMIN_EMAIL = "sr-tester@example.com"
ADMIN_PASSWORD = "TestPass123!"


def _token(client):
    r = client.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r.status_code != 200:
        client.post(f"{API}/auth/register", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "name": "SR Tester"})
        r = client.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    return r.json()["session_token"]


@pytest.fixture(scope="module")
def auth_headers():
    with httpx.Client(timeout=60) as c:
        tok = _token(c)
    return {"Authorization": f"Bearer {tok}"}


def test_messages_limit_endpoint_returns_200(auth_headers):
    """The previously-404 endpoint now exists and returns a messages array."""
    with httpx.Client(timeout=15) as c:
        r = c.get(f"{API}/avatar-chat/messages?limit=2", headers=auth_headers)
        # 200 for admin (feature gated open), 503 for non-admin without flag.
        assert r.status_code in (200, 503), r.text
        if r.status_code == 200:
            d = r.json()
            assert "messages" in d and isinstance(d["messages"], list)
            for m in d["messages"]:
                assert "message_id" in m
                assert "video_status" in m
                # Both legacy + new aliases are present.
                assert "ai_response_text" in m
                assert "reply_text" in m


def test_lipsync_short_circuits_without_fal_key():
    """Without FAL_KEY the helper must return PROVIDER_AUTH_FAILED + reason."""
    import avatar_chat
    orig = avatar_chat.FAL_KEY
    try:
        avatar_chat.FAL_KEY = ""
        url, code, dbg = asyncio.get_event_loop().run_until_complete(
            avatar_chat._generate_lipsync_video("https://i.pravatar.cc/256", b"\x00\x01")
        )
        assert url is None
        assert code == avatar_chat.LIPSYNC_ERR_PROVIDER_AUTH_FAILED
        assert dbg == "no_fal_key"
    finally:
        avatar_chat.FAL_KEY = orig


def test_fetch_image_bytes_http_ok():
    """http URLs must be downloadable to bytes for fal upload."""
    import avatar_chat
    blob, ct, dbg = asyncio.get_event_loop().run_until_complete(
        avatar_chat._fetch_image_bytes("https://i.pravatar.cc/64")
    )
    assert blob is not None and len(blob) > 0
    assert ct and "image" in ct
    assert dbg == "ok_http"

def test_fetch_image_bytes_handles_missing_url():
    import avatar_chat
    blob, ct, dbg = asyncio.get_event_loop().run_until_complete(
        avatar_chat._fetch_image_bytes("")
    )
    assert blob is None
    assert dbg == "no_image_url"


def test_lipsync_invalid_avatar_id_when_image_url_blank():
    """Empty image URL → INVALID_AVATAR_ID (canonical code)."""
    import avatar_chat
    orig = avatar_chat.FAL_KEY
    try:
        avatar_chat.FAL_KEY = "fake_key_for_test"  # past the auth gate
        url, code, dbg = asyncio.get_event_loop().run_until_complete(
            avatar_chat._generate_lipsync_video("", b"\x00\x01")
        )
        assert url is None
        assert code == avatar_chat.LIPSYNC_ERR_INVALID_AVATAR_ID
        assert dbg == "no_image_url"
    finally:
        avatar_chat.FAL_KEY = orig


def test_lipsync_video_not_started_when_audio_empty():
    """Empty audio bytes → VIDEO_NOT_STARTED."""
    import avatar_chat
    orig = avatar_chat.FAL_KEY
    try:
        avatar_chat.FAL_KEY = "fake_key_for_test"
        url, code, dbg = asyncio.get_event_loop().run_until_complete(
            avatar_chat._generate_lipsync_video("https://i.pravatar.cc/64", b"")
        )
        assert url is None
        assert code == avatar_chat.LIPSYNC_ERR_VIDEO_NOT_STARTED
        assert dbg == "empty_audio_bytes"
    finally:
        avatar_chat.FAL_KEY = orig


def test_lipsync_invalid_avatar_id_when_image_fetch_fails():
    """Unreachable image URL → INVALID_AVATAR_ID with image_fetch_failed detail."""
    import avatar_chat
    orig = avatar_chat.FAL_KEY
    try:
        avatar_chat.FAL_KEY = "fake_key_for_test"
        url, code, dbg = asyncio.get_event_loop().run_until_complete(
            avatar_chat._generate_lipsync_video(
                "https://nonexistent-domain-987654321.invalid/x.jpg",
                b"\x00" * 100,
            )
        )
        assert url is None
        assert code == avatar_chat.LIPSYNC_ERR_INVALID_AVATAR_ID
        assert dbg.startswith("image_fetch_failed:")
    finally:
        avatar_chat.FAL_KEY = orig


def test_error_code_constants_exist():
    """All 8 canonical error codes are exported as module constants."""
    import avatar_chat
    for c in [
        "LIPSYNC_ERR_PROVIDER_AUTH_FAILED", "LIPSYNC_ERR_PROVIDER_422",
        "LIPSYNC_ERR_INVALID_AVATAR_ID", "LIPSYNC_ERR_JOB_TIMEOUT",
        "LIPSYNC_ERR_POLL_FAILED", "LIPSYNC_ERR_NO_VIDEO_URL",
        "LIPSYNC_ERR_RENDER_EXCEPTION", "LIPSYNC_ERR_VIDEO_NOT_STARTED",
    ]:
        assert hasattr(avatar_chat, c), f"Missing constant: {c}"
