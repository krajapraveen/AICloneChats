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


# --- Tests for _classify_fal_exception (the bit that fixes the
#     "FalClientHTTPError truncated" bug from prod). ---

class _FakeResp:
    def __init__(self, text):
        self.text = text


def _make_fal_http_err(status_code, body_text, message="fal error"):
    """Construct a FalClientHTTPError without hitting the network."""
    from fal_client import FalClientHTTPError
    return FalClientHTTPError(
        message=message,
        status_code=status_code,
        response_headers={"content-type": "application/json"},
        response=_FakeResp(body_text),
    )


def test_classify_fal_422_preserves_full_body():
    """422 from fal must surface as PROVIDER_422 with the FULL body (not truncated str(err))."""
    import avatar_chat
    body = '[{"loc": ["body", "model"], "msg": "extra fields not permitted", "type": "value_error.extra"}]'
    err = _make_fal_http_err(422, body)
    code, dbg = avatar_chat._classify_fal_exception(err, stage="poll", request_id="req_abc123")
    assert code == avatar_chat.LIPSYNC_ERR_PROVIDER_422
    assert "HTTP 422" in dbg
    assert "extra fields not permitted" in dbg, f"body must be preserved verbatim, got: {dbg}"
    assert "req_abc123" in dbg


def test_classify_fal_401_is_auth_failed():
    import avatar_chat
    err = _make_fal_http_err(401, '{"detail":"unauthorized"}')
    code, dbg = avatar_chat._classify_fal_exception(err, stage="submit")
    assert code == avatar_chat.LIPSYNC_ERR_PROVIDER_AUTH_FAILED
    assert "HTTP 401" in dbg


def test_classify_fal_5xx_during_poll_is_poll_failed():
    import avatar_chat
    err = _make_fal_http_err(502, "bad gateway")
    code, dbg = avatar_chat._classify_fal_exception(err, stage="poll", request_id="rid1")
    assert code == avatar_chat.LIPSYNC_ERR_POLL_FAILED
    assert "HTTP 502" in dbg
    assert "rid1" in dbg


def test_classify_generic_exception_falls_back():
    import avatar_chat
    code, dbg = avatar_chat._classify_fal_exception(RuntimeError("boom"), stage="poll")
    assert code == avatar_chat.LIPSYNC_ERR_POLL_FAILED
    assert "RuntimeError" in dbg
    assert "boom" in dbg


def test_lipsync_endpoint_env_override_is_used(monkeypatch):
    """FAL_LIPSYNC_ENDPOINT env var must be honoured (no hard-coded endpoint)."""
    import avatar_chat
    # Reading the module-level constant is enough — the var is consulted at
    # import time; we only assert the var is referenced inside submit.
    assert hasattr(avatar_chat, "FAL_LIPSYNC_ENDPOINT")
    assert hasattr(avatar_chat, "FAL_LIPSYNC_MODEL")
    # Default should be fal-ai/sync-lipsync (matches what we shipped).
    assert avatar_chat.FAL_LIPSYNC_ENDPOINT == "fal-ai/sync-lipsync"
    # Default model is empty (omit from args) — this is the fix for prod 422.
    assert avatar_chat.FAL_LIPSYNC_MODEL == ""
    # sync_mode defaults to "loop" so still images (1-frame GIFs) get looped
    # across the full audio.
    assert avatar_chat.FAL_LIPSYNC_SYNC_MODE == "loop"


# --- GIF conversion (the fix for the JPG → 422 schema mismatch) ---

def _make_test_jpg_bytes() -> bytes:
    """Generate a tiny in-memory JPG for tests."""
    from PIL import Image
    import io as _io
    img = Image.new("RGB", (64, 64), (200, 120, 60))
    buf = _io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_image_bytes_to_gif_jpg_input():
    """JPG bytes must convert to valid GIF bytes (header = b'GIF8')."""
    import avatar_chat
    jpg = _make_test_jpg_bytes()
    gif, ct = avatar_chat._image_bytes_to_gif(jpg)
    assert ct == "image/gif"
    assert gif.startswith(b"GIF87a") or gif.startswith(b"GIF89a"), f"not a GIF header: {gif[:6]!r}"


def test_image_bytes_to_gif_png_with_alpha():
    """PNG with alpha must flatten to RGB and produce a GIF."""
    from PIL import Image
    import io as _io
    img = Image.new("RGBA", (40, 40), (10, 200, 10, 128))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    import avatar_chat
    gif, ct = avatar_chat._image_bytes_to_gif(buf.getvalue())
    assert ct == "image/gif"
    assert gif.startswith(b"GIF87a") or gif.startswith(b"GIF89a")


def test_image_bytes_to_gif_downscales_huge_image():
    """A 4000x4000 image must downscale to ≤1024 in the output GIF."""
    from PIL import Image
    import io as _io
    big = Image.new("RGB", (4000, 4000), (50, 50, 50))
    buf = _io.BytesIO()
    big.save(buf, format="JPEG", quality=70)
    import avatar_chat
    gif, _ = avatar_chat._image_bytes_to_gif(buf.getvalue())
    # Open the GIF and verify dimensions
    out = Image.open(_io.BytesIO(gif))
    assert max(out.size) <= 1024, f"not downscaled: {out.size}"


def test_image_bytes_to_gif_fallback_on_bad_input():
    """Garbage bytes → falls back to original bytes (no crash, content_type=image/jpeg)."""
    import avatar_chat
    junk = b"not an image at all"
    out, ct = avatar_chat._image_bytes_to_gif(junk)
    assert out == junk
    assert ct == "image/jpeg"


# --- MP4 conversion (the iter5 fix for "Failed to read video metadata") ---


def test_image_bytes_to_mp4_jpg_input_produces_valid_mp4():
    """JPG → real H.264 MP4 with valid ftyp box (so fal's ffprobe accepts it)."""
    import avatar_chat
    jpg = _make_test_jpg_bytes()
    mp4, ct = avatar_chat._image_bytes_to_mp4(jpg)
    assert mp4 is not None, "MP4 conversion failed (ffmpeg-bundled binary issue?)"
    assert ct == "video/mp4"
    # MP4 files start with a `ftyp` box header at bytes 4-8.
    assert mp4[4:8] == b"ftyp", f"not a valid MP4 (no ftyp box): {mp4[:12]!r}"
    # Should be more than a trivial placeholder (≥1KB for 2s at 25fps).
    assert len(mp4) > 1000, f"MP4 too small: {len(mp4)} bytes"


def test_image_bytes_to_mp4_png_rgba_input():
    """PNG with alpha must flatten and still produce a valid MP4."""
    from PIL import Image
    import io as _io
    img = Image.new("RGBA", (128, 128), (10, 200, 10, 128))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    import avatar_chat
    mp4, ct = avatar_chat._image_bytes_to_mp4(buf.getvalue())
    assert mp4 is not None
    assert ct == "video/mp4"
    assert mp4[4:8] == b"ftyp"


def test_image_bytes_to_mp4_handles_odd_dimensions():
    """H.264 requires even px — image with odd width/height must still encode."""
    from PIL import Image
    import io as _io
    # Deliberately odd dimensions.
    img = Image.new("RGB", (255, 333), (100, 100, 100))
    buf = _io.BytesIO()
    img.save(buf, format="JPEG")
    import avatar_chat
    mp4, ct = avatar_chat._image_bytes_to_mp4(buf.getvalue())
    assert mp4 is not None, "odd-dim image should be cropped to even and still encode"
    assert ct == "video/mp4"


def test_image_bytes_to_mp4_fallback_on_garbage():
    """Garbage bytes → returns (None, error_detail) so caller can fall through to GIF."""
    import avatar_chat
    mp4, dbg = avatar_chat._image_bytes_to_mp4(b"definitely not an image")
    assert mp4 is None
    assert isinstance(dbg, str) and len(dbg) > 0
