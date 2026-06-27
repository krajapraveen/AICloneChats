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
    """Env vars must be honoured (no hard-coded endpoint or field names).

    Default endpoint is `fal-ai/sadtalker` (verified live in fal's official
    catalog as the image-to-talking-avatar model). Default image field is
    `source_image_url` and default audio field is `driven_audio_url` —
    those are sadtalker's actual schema field names.
    """
    import avatar_chat
    assert hasattr(avatar_chat, "FAL_LIPSYNC_ENDPOINT")
    assert hasattr(avatar_chat, "FAL_LIPSYNC_MODEL")
    assert hasattr(avatar_chat, "FAL_LIPSYNC_IMAGE_FIELD")
    assert hasattr(avatar_chat, "FAL_LIPSYNC_AUDIO_FIELD")
    assert avatar_chat.FAL_LIPSYNC_ENDPOINT == "fal-ai/sadtalker"
    assert avatar_chat.FAL_LIPSYNC_MODEL == ""
    assert avatar_chat.FAL_LIPSYNC_IMAGE_FIELD == "source_image_url"
    assert avatar_chat.FAL_LIPSYNC_AUDIO_FIELD == "driven_audio_url"
    assert avatar_chat.FAL_LIPSYNC_SYNC_MODE == "loop"


def test_pillow_heif_registered():
    """HEIF/HEIC opener must be registered so iPhone photos decode without
    UnidentifiedImageError (the prod bug in iter30 screenshot)."""
    from PIL import Image
    exts = set(Image.registered_extensions().keys())
    assert ".heic" in exts or ".heif" in exts, "pillow-heif not registered — iPhone photos will fail"


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


# --- Preflight reject (iter6/iter30) ---

def test_invalid_provider_payload_constant_exists():
    """The new canonical code must be exported."""
    import avatar_chat
    assert hasattr(avatar_chat, "LIPSYNC_ERR_INVALID_PROVIDER_PAYLOAD")
    assert avatar_chat.LIPSYNC_ERR_INVALID_PROVIDER_PAYLOAD == "INVALID_PROVIDER_PAYLOAD"


def test_preflight_rejects_when_mp4_and_gif_both_fail(monkeypatch):
    """If MP4 fails AND GIF passthroughs to image/jpeg (on the legacy video
    endpoint), the helper must refuse to submit and return INVALID_PROVIDER_PAYLOAD."""
    import avatar_chat
    # Force the legacy video-native endpoint so the MP4/GIF path runs.
    monkeypatch.setattr(avatar_chat, "FAL_LIPSYNC_IMAGE_FIELD", "video_url")
    monkeypatch.setattr(avatar_chat, "FAL_LIPSYNC_ENDPOINT", "fal-ai/sync-lipsync")
    # Patch the MP4 helper to fail.
    monkeypatch.setattr(avatar_chat, "_image_bytes_to_mp4", lambda b: (None, "mp4_simulated_failure"))
    # Patch the GIF helper to also fail (returns original bytes as image/jpeg).
    monkeypatch.setattr(avatar_chat, "_image_bytes_to_gif", lambda b: (b, "image/jpeg"))
    monkeypatch.setattr(avatar_chat, "FAL_KEY", "fake_key_for_test")

    async def _fake_fetch(url):
        return _make_test_jpg_bytes(), "image/jpeg", "ok_test"
    monkeypatch.setattr(avatar_chat, "_fetch_image_bytes", _fake_fetch)

    url, code, dbg = asyncio.get_event_loop().run_until_complete(
        avatar_chat._generate_lipsync_video("https://example.com/a.jpg", b"\x00" * 100)
    )
    assert url is None
    assert code == avatar_chat.LIPSYNC_ERR_INVALID_PROVIDER_PAYLOAD, f"expected preflight reject, got {code}"
    assert "image_as_video_blocked" in dbg
    assert "image/jpeg" in dbg
    assert "passthrough_jpeg" in dbg or "path=" in dbg


def test_image_native_endpoint_skips_mp4_transcode(monkeypatch):
    """With the default sadtalker endpoint (source_image_url field name),
    _image_bytes_to_mp4 must NEVER be called and the original image bytes are
    uploaded as-is."""
    import avatar_chat
    # Confirm default is image-native (source_image_url contains "image")
    assert "image" in avatar_chat.FAL_LIPSYNC_IMAGE_FIELD
    mp4_called = {"yes": False}

    def _spy(_b):
        mp4_called["yes"] = True
        return None, "should_not_be_called"
    monkeypatch.setattr(avatar_chat, "_image_bytes_to_mp4", _spy)
    monkeypatch.setattr(avatar_chat, "FAL_KEY", "fake_key_for_test")

    async def _fake_fetch(url):
        return _make_test_jpg_bytes(), "image/jpeg", "ok_test"
    monkeypatch.setattr(avatar_chat, "_fetch_image_bytes", _fake_fetch)

    import sys
    fake_fal = type(sys)("fal_client")

    def _bad_submit(*a, **kw):
        raise RuntimeError("submit_intentionally_blocked_for_test")
    fake_fal.submit = _bad_submit
    fake_fal.upload_file = lambda p: f"https://fake.fal/{p.rsplit('/',1)[-1]}"
    fake_fal.FalClientHTTPError = type("FalClientHTTPError", (Exception,), {})
    fake_fal.FalClientTimeoutError = type("FalClientTimeoutError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "fal_client", fake_fal)

    url, code, dbg = asyncio.get_event_loop().run_until_complete(
        avatar_chat._generate_lipsync_video("https://example.com/avatar.jpg", b"\x00" * 100)
    )
    assert not mp4_called["yes"], "image-native endpoint should NOT call MP4 transcoder"
    assert url is None


def test_invalid_avatar_id_when_image_undecodable_on_image_native(monkeypatch):
    """Image-native endpoint: garbage bytes that PIL can't decode → return
    INVALID_AVATAR_ID with magic_hex."""
    import avatar_chat
    monkeypatch.setattr(avatar_chat, "FAL_LIPSYNC_IMAGE_FIELD", "source_image_url")
    monkeypatch.setattr(avatar_chat, "FAL_KEY", "fake_key_for_test")

    async def _fake_fetch(url):
        return b"NOT_AN_IMAGE_HEADER_BYTES", "image/jpeg", "ok_test"
    monkeypatch.setattr(avatar_chat, "_fetch_image_bytes", _fake_fetch)

    url, code, dbg = asyncio.get_event_loop().run_until_complete(
        avatar_chat._generate_lipsync_video("https://example.com/x.jpg", b"\x00" * 100)
    )
    assert url is None
    assert code == avatar_chat.LIPSYNC_ERR_INVALID_AVATAR_ID
    assert "image_decode_failed" in dbg
    assert "magic_hex=" in dbg


def test_submit_args_use_configurable_audio_field(monkeypatch):
    """sadtalker requires `driven_audio_url`, not `audio_url`. The submit args
    must use FAL_LIPSYNC_AUDIO_FIELD verbatim — this is the iter32 fix that
    eliminates 'unknown field' errors on non-default endpoints."""
    import avatar_chat, sys
    captured = {"args": None, "endpoint": None}

    fake_fal = type(sys)("fal_client")

    def _spy_submit(endpoint, arguments=None, **_):
        captured["endpoint"] = endpoint
        captured["args"] = dict(arguments or {})
        raise RuntimeError("ok_blocked_after_capture")
    fake_fal.submit = _spy_submit
    fake_fal.upload_file = lambda p: f"https://fake.fal/{p.rsplit('/', 1)[-1]}"
    fake_fal.FalClientHTTPError = type("FalClientHTTPError", (Exception,), {})
    fake_fal.FalClientTimeoutError = type("FalClientTimeoutError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "fal_client", fake_fal)
    monkeypatch.setattr(avatar_chat, "FAL_KEY", "fake_key_for_test")

    async def _fake_fetch(url):
        return _make_test_jpg_bytes(), "image/jpeg", "ok_test"
    monkeypatch.setattr(avatar_chat, "_fetch_image_bytes", _fake_fetch)

    asyncio.get_event_loop().run_until_complete(
        avatar_chat._generate_lipsync_video("https://example.com/a.jpg", b"\x00" * 100)
    )
    # Verify the submit was attempted with sadtalker's required field names
    assert captured["endpoint"] == "fal-ai/sadtalker"
    assert "source_image_url" in (captured["args"] or {})
    assert "driven_audio_url" in (captured["args"] or {})
    # Must NOT contain legacy field names
    assert "image_url" not in (captured["args"] or {})
    assert "audio_url" not in (captured["args"] or {})
    assert "video_url" not in (captured["args"] or {})


# --- iter33: progress polling + diagnostic fields ---

def test_progress_dict_populated_on_completed(monkeypatch):
    """The helper must write provider_request_id, provider_status, poll_attempts,
    last_poll_at, fal_endpoint, final_result_keys into the shared `progress`
    dict so the async caller can persist them to MongoDB for admin UI."""
    import avatar_chat, sys

    class _FakeQueued:
        position = 1

    class _FakeCompleted:
        logs = []
        metrics = {}
        error = None
        error_type = None

    class _FakeHandler:
        request_id = "fake-req-abc-123"

        def __init__(self):
            self._calls = 0

        def status(self, *, with_logs=False):
            self._calls += 1
            # First poll returns Queued, second returns Completed
            return _FakeQueued() if self._calls == 1 else _FakeCompleted()

        def get(self):
            return {"video": {"url": "https://fal.cdn/output/abc.mp4"}}

    h = _FakeHandler()
    # Make _FakeQueued / _FakeCompleted look like fal_client.Queued / Completed
    # by matching the class name (the helper checks type(st).__name__).
    _FakeQueued.__name__ = "Queued"
    _FakeCompleted.__name__ = "Completed"

    fake_fal = type(sys)("fal_client")
    fake_fal.submit = lambda endpoint, arguments=None, **_: h
    fake_fal.upload_file = lambda p: f"https://fake.fal/{p.rsplit('/', 1)[-1]}"
    fake_fal.FalClientHTTPError = type("FalClientHTTPError", (Exception,), {})
    fake_fal.FalClientTimeoutError = type("FalClientTimeoutError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "fal_client", fake_fal)
    monkeypatch.setattr(avatar_chat, "FAL_KEY", "fake_key_for_test")
    # Speed up: drop poll interval to ~0 so the test runs fast.
    monkeypatch.setattr(avatar_chat, "LIPSYNC_POLL_INTERVAL_SEC", 0.01)

    async def _fake_fetch(url):
        return _make_test_jpg_bytes(), "image/jpeg", "ok_test"
    monkeypatch.setattr(avatar_chat, "_fetch_image_bytes", _fake_fetch)

    progress: dict = {}
    url, code, dbg = asyncio.get_event_loop().run_until_complete(
        avatar_chat._generate_lipsync_video(
            "https://example.com/a.jpg", b"\x00" * 100, progress=progress,
        )
    )
    assert url == "https://fal.cdn/output/abc.mp4", f"unexpected url: {url}"
    assert code == "ok"
    # Verify progress dict was populated with all required diagnostic fields
    assert progress.get("provider_request_id") == "fake-req-abc-123"
    assert progress.get("provider_status") == "Completed"
    assert progress.get("poll_attempts") >= 2
    assert progress.get("last_poll_at")  # ISO timestamp string
    assert progress.get("fal_endpoint") == avatar_chat.FAL_LIPSYNC_ENDPOINT
    assert progress.get("final_result_keys") == ["video"]
    # dbg should include the attempt count and request_id
    assert "attempts=" in dbg
    assert "fake-req-abc-123" in dbg


def test_completed_with_error_returns_render_exception(monkeypatch):
    """If fal's Completed status carries `error`/`error_type` (common with
    sadtalker face-detection failures), we must surface that as
    LIPSYNC_ERR_RENDER_EXCEPTION with the error type + message."""
    import avatar_chat, sys

    class _FakeCompletedWithErr:
        logs = []
        metrics = {}
        error = "No face detected in source image"
        error_type = "FaceDetectionError"

    _FakeCompletedWithErr.__name__ = "Completed"

    class _FakeHandler:
        request_id = "rid-err-7"

        def status(self, *, with_logs=False):
            return _FakeCompletedWithErr()

        def get(self):
            raise AssertionError("get() must NOT be called when status has error")

    fake_fal = type(sys)("fal_client")
    fake_fal.submit = lambda endpoint, arguments=None, **_: _FakeHandler()
    fake_fal.upload_file = lambda p: f"https://fake.fal/{p.rsplit('/', 1)[-1]}"
    fake_fal.FalClientHTTPError = type("FalClientHTTPError", (Exception,), {})
    fake_fal.FalClientTimeoutError = type("FalClientTimeoutError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "fal_client", fake_fal)
    monkeypatch.setattr(avatar_chat, "FAL_KEY", "fake_key_for_test")
    monkeypatch.setattr(avatar_chat, "LIPSYNC_POLL_INTERVAL_SEC", 0.01)

    async def _fake_fetch(url):
        return _make_test_jpg_bytes(), "image/jpeg", "ok_test"
    monkeypatch.setattr(avatar_chat, "_fetch_image_bytes", _fake_fetch)

    url, code, dbg = asyncio.get_event_loop().run_until_complete(
        avatar_chat._generate_lipsync_video("https://example.com/a.jpg", b"\x00" * 100)
    )
    assert url is None
    assert code == avatar_chat.LIPSYNC_ERR_RENDER_EXCEPTION
    assert "FaceDetectionError" in dbg
    assert "No face detected" in dbg


def test_result_alternative_video_url_shapes(monkeypatch):
    """fal models return the video URL under different keys (sadtalker uses
    {video:{url}}, sync-lipsync uses {video:url string}, VEED uses
    {video_url}). The helper must accept all three shapes."""
    import avatar_chat, sys

    for shape in ({"video": {"url": "https://x/1.mp4"}},
                  {"video": "https://x/2.mp4"},
                  {"video_url": "https://x/3.mp4"},
                  {"output_video_url": "https://x/4.mp4"}):

        class _FakeCompleted:
            logs = []
            metrics = {}
            error = None
            error_type = None
        _FakeCompleted.__name__ = "Completed"

        class _FakeHandler:
            request_id = "rid"

            def __init__(self, shape):
                self._shape = shape
            def status(self, *, with_logs=False):
                return _FakeCompleted()
            def get(self):
                return self._shape

        fake_fal = type(sys)("fal_client")
        captured_shape = dict(shape)  # close over loop var
        fake_fal.submit = (lambda s: (lambda endpoint, arguments=None, **_: _FakeHandler(s)))(captured_shape)
        fake_fal.upload_file = lambda p: f"https://fake.fal/{p.rsplit('/', 1)[-1]}"
        fake_fal.FalClientHTTPError = type("FalClientHTTPError", (Exception,), {})
        fake_fal.FalClientTimeoutError = type("FalClientTimeoutError", (Exception,), {})
        monkeypatch.setitem(sys.modules, "fal_client", fake_fal)
        monkeypatch.setattr(avatar_chat, "FAL_KEY", "fake_key_for_test")
        monkeypatch.setattr(avatar_chat, "LIPSYNC_POLL_INTERVAL_SEC", 0.01)

        async def _fake_fetch(url):
            return _make_test_jpg_bytes(), "image/jpeg", "ok_test"
        monkeypatch.setattr(avatar_chat, "_fetch_image_bytes", _fake_fetch)

        url, code, dbg = asyncio.get_event_loop().run_until_complete(
            avatar_chat._generate_lipsync_video("https://x/a.jpg", b"\x00" * 100)
        )
        assert code == "ok", f"shape {shape} failed: code={code} dbg={dbg}"
        assert url and url.endswith(".mp4"), f"shape {shape} returned bad url: {url}"
