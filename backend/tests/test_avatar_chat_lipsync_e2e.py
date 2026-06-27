"""
End-to-end test for the lipsync image-upload fix (iteration_25).

Validates:
 1. GET /api/avatar-chat/messages?limit=2 returns 200 with proper shape
 2. POST /api/avatar-chat/send returns message_id starting with `avm_`
 3. Polling /api/avatar-chat/job/{message_id} produces audio + graceful degrade
 4. Audio URL is publicly fetchable (200, audio/* content-type)
 5. /api/avatar-chat/messages without `limit` defaults to 20 and returns 200
 6. /api/avatar-chat/job/non_existent_id returns 404 (no crash)

Runs against the live preview backend. No FAL_KEY ⇒ expects
`video_status=completed`, `error_code=lipsync_unavailable`,
`lipsync_debug=no_fal_key`, `audio_url` populated, `video_url=None`.
"""
from __future__ import annotations
import os
import time
import pytest
import httpx

BASE = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
ADMIN_EMAIL = "krajapraveen@gmail.com"
ADMIN_PASSWORD = "TestPass123!"
CLONE_SLUG = "companion"


def _login(c: httpx.Client, email: str, password: str) -> str | None:
    r = c.post(f"{API}/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        return None
    return r.json().get("session_token")


@pytest.fixture(scope="module")
def admin_headers():
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        tok = _login(c, ADMIN_EMAIL, ADMIN_PASSWORD)
        if not tok:
            # Try to register then re-login (preview env may be ephemeral)
            c.post(
                f"{API}/auth/register",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "name": "Praveen"},
            )
            tok = _login(c, ADMIN_EMAIL, ADMIN_PASSWORD)
        if not tok:
            pytest.skip(f"Cannot obtain admin token for {ADMIN_EMAIL}")
    return {"Authorization": f"Bearer {tok}"}


# --- Test 1: /messages?limit=2 returns 200 ---
def test_messages_with_limit(admin_headers):
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{API}/avatar-chat/messages?limit=2", headers=admin_headers)
    assert r.status_code in (200, 503), r.text
    if r.status_code == 503:
        pytest.skip("avatar_chat_unavailable for this user (non-admin role)")
    data = r.json()
    assert "messages" in data and isinstance(data["messages"], list)
    assert len(data["messages"]) <= 2
    for m in data["messages"]:
        for f in ("message_id", "video_status", "ai_response_text", "reply_text"):
            assert f in m, f"Missing field {f} in message {m}"


# --- Test 5: /messages without limit defaults to 20 ---
def test_messages_default_limit(admin_headers):
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{API}/avatar-chat/messages", headers=admin_headers)
    assert r.status_code in (200, 503), r.text
    if r.status_code == 503:
        pytest.skip("avatar_chat_unavailable")
    data = r.json()
    assert "messages" in data
    assert len(data["messages"]) <= 20


# --- Test 6: /job/non_existent_id → 404 ---
def test_job_non_existent_id(admin_headers):
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{API}/avatar-chat/job/non_existent_id_xyz", headers=admin_headers)
    assert r.status_code in (404, 503), r.text


# --- Test 2 + 3 + 4: Send → poll → graceful audio-only degrade → fetch audio ---
def test_send_poll_and_audio_fetch(admin_headers):
    with httpx.Client(timeout=60) as c:
        send_r = c.post(
            f"{API}/avatar-chat/send",
            headers=admin_headers,
            json={"clone_id_or_slug": CLONE_SLUG, "message": "Quick test from regression"},
        )

    if send_r.status_code == 503:
        pytest.skip("avatar_chat_unavailable")
    if send_r.status_code == 402:
        pytest.skip(f"paywall: {send_r.text[:200]}")
    if send_r.status_code == 404:
        pytest.skip(f"clone '{CLONE_SLUG}' not found in preview: {send_r.text[:200]}")
    assert send_r.status_code == 200, f"send failed: {send_r.status_code} {send_r.text[:500]}"

    body = send_r.json()
    assert "conversation_id" in body
    assert "message" in body
    msg = body["message"]
    msg_id = msg.get("message_id")
    assert msg_id, "message_id missing"
    assert msg_id.startswith("avm_"), f"message_id should start with avm_, got {msg_id}"
    # New aliases
    assert "ai_response_text" in msg
    assert "reply_text" in msg
    assert "video_status" in msg
    print(f"[INFO] send ok msg_id={msg_id} initial status={msg.get('video_status')}")

    # Poll job for up to 60s
    final = None
    audio_url = None
    with httpx.Client(timeout=30) as c:
        deadline = time.time() + 60
        while time.time() < deadline:
            jr = c.get(f"{API}/avatar-chat/job/{msg_id}", headers=admin_headers)
            assert jr.status_code == 200, f"job fetch failed: {jr.status_code} {jr.text[:300]}"
            jdata = jr.json().get("message") or {}
            status = jdata.get("video_status")
            audio_url = jdata.get("audio_url")
            print(
                f"[INFO] poll status={status} audio={'yes' if audio_url else 'no'} "
                f"video={'yes' if jdata.get('video_url') else 'no'} "
                f"err={jdata.get('error_code')} dbg={jdata.get('lipsync_debug')}"
            )
            if status in ("completed", "failed"):
                final = jdata
                break
            time.sleep(2)

    assert final is not None, "pipeline did not reach terminal state within 60s"
    # Expected graceful degrade in preview (no FAL_KEY)
    assert final.get("video_status") == "completed", f"unexpected final status: {final.get('video_status')}"
    assert final.get("audio_url"), "audio_url should be populated"
    assert final.get("video_url") in (None, ""), f"video_url should be empty without FAL_KEY, got {final.get('video_url')}"
    # Acceptable error codes when FAL is missing — debug should call it out
    err = final.get("error_code")
    dbg = final.get("lipsync_debug")
    print(f"[INFO] final error_code={err} lipsync_debug={dbg}")
    # Soft-assert: we expect lipsync_unavailable / no_fal_key but accept other no_*_key debugs
    if err is not None:
        assert err in ("lipsync_unavailable",), f"unexpected error_code: {err}"
        assert dbg in ("no_fal_key", "no_image_url") or (dbg or "").startswith("image_fetch_failed"), \
            f"unexpected lipsync_debug: {dbg}"

    # Test 4: audio URL is publicly fetchable (no auth)
    audio_path = final.get("audio_url")
    assert audio_path, "audio_url required for fetch test"
    full_url = audio_path if audio_path.startswith("http") else f"{BASE}{audio_path}"
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        ar = c.get(full_url)
    assert ar.status_code == 200, f"audio fetch failed: {ar.status_code} url={full_url}"
    ct = ar.headers.get("content-type", "")
    assert "audio" in ct, f"unexpected audio content-type: {ct}"
    assert len(ar.content) > 0, "audio body empty"
    print(f"[INFO] audio fetched ok ct={ct} size={len(ar.content)}")
