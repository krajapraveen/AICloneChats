"""Iteration 3 — verify /api/avatar-chat/status surfaces the new
`lipsync_endpoint` and `lipsync_model` fields, and that /send + /job/{id}
end-to-end on the preview env returns the graceful PROVIDER_AUTH_FAILED
(no FAL_KEY) state with a populated audio_url and completed video_status."""

import os
import time
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")

ADMIN_EMAIL = "krajapraveen@gmail.com"
ADMIN_PASS = "TestPass123!"


def _login(session):
    r = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        timeout=20,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("session_token")
    if tok:
        session.headers.update({"Authorization": f"Bearer {tok}"})
    return r.json()


def test_status_includes_lipsync_endpoint_and_model():
    s = requests.Session()
    _login(s)
    r = s.get(f"{BASE_URL}/api/avatar-chat/status", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    # New fields
    assert "lipsync_endpoint" in body, body
    assert "lipsync_model" in body, body
    # Defaults: endpoint is the canonical sync-lipsync, model is None
    assert body["lipsync_endpoint"] == "fal-ai/sync-lipsync", body
    assert body["lipsync_model"] in (None, ""), body
    # Existing fields still surface
    for k in ("enabled_for_public", "tts_configured", "lipsync_configured", "lipsync_provider"):
        assert k in body, (k, body)


def test_avatar_send_graceful_no_fal_key():
    """In preview FAL_KEY is unset → pipeline should still return 200 with
    audio_url populated, video_status='completed' (graceful), and the
    canonical error_code PROVIDER_AUTH_FAILED with lipsync_debug='no_fal_key'."""
    import pytest
    s = requests.Session()
    _login(s)

    payload = {
        "clone_id_or_slug": "companion",
        "message": "Hello, this is a quick lipsync regression check.",
    }
    rs = s.post(f"{BASE_URL}/api/avatar-chat/send", json=payload, timeout=60)
    if rs.status_code in (403, 503):
        pytest.skip(f"avatar-chat gated in preview: {rs.status_code} {rs.text[:200]}")
    if rs.status_code == 404:
        pytest.skip(f"clone 'companion' not present: {rs.text[:200]}")
    assert rs.status_code == 200, rs.text[:400]
    sent = rs.json()
    msg = sent.get("message") or sent
    message_id = msg.get("message_id") or sent.get("message_id")
    assert message_id, sent

    # Poll briefly
    job_msg = None
    for _ in range(30):
        rj = s.get(f"{BASE_URL}/api/avatar-chat/job/{message_id}", timeout=15)
        assert rj.status_code == 200, rj.text[:300]
        body = rj.json()
        job_msg = body.get("message") or body
        if job_msg.get("video_status") in ("completed", "failed"):
            break
        time.sleep(2)
    assert job_msg is not None
    # Without FAL_KEY we expect a graceful "completed" (text+audio only).
    assert job_msg.get("video_status") == "completed", job_msg
    assert job_msg.get("audio_url"), job_msg
    # Canonical error_code from iteration_26 + iter3:
    assert job_msg.get("error_code") in ("PROVIDER_AUTH_FAILED", "lipsync_unavailable"), job_msg
    assert (job_msg.get("lipsync_debug") or "") == "no_fal_key", job_msg
