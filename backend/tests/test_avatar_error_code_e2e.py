"""
Live e2e test for the canonical lipsync error_code refactor.

Covers:
- POST /api/avatar-chat/send as admin produces a message that completes with
  error_code='PROVIDER_AUTH_FAILED' + lipsync_debug='no_fal_key' (preview has no FAL_KEY).
- GET /api/avatar-chat/messages?limit=2 returns each item with error_code,
  lipsync_debug, video_job_id fields.
"""
from __future__ import annotations
import os
import time
import pytest
import httpx

BASE = os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001"
API = f"{BASE}/api"
ADMIN_EMAIL = "krajapraveen@gmail.com"
ADMIN_PASSWORD = "TestPass123!"


def _login():
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
        return r.json()["session_token"]


@pytest.fixture(scope="module")
def admin_headers():
    return {"Authorization": f"Bearer {_login()}"}


def test_send_avatar_chat_returns_provider_auth_failed(admin_headers):
    """The full send→poll flow must end with PROVIDER_AUTH_FAILED + no_fal_key."""
    with httpx.Client(timeout=60) as c:
        r = c.post(
            f"{API}/avatar-chat/send",
            headers=admin_headers,
            json={"clone_id_or_slug": "companion", "message": "Hello from e2e test"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # The send endpoint returns the message id (either message_id or job_id field).
        message_id = (
            data.get("message_id")
            or data.get("id")
            or (data.get("message") or {}).get("message_id")
        )
        assert message_id, f"missing message_id in send response: {data}"

        # Poll the job endpoint until completed (or up to ~30s).
        final_msg = None
        final_job = None
        for _ in range(20):
            jr = c.get(f"{API}/avatar-chat/job/{message_id}", headers=admin_headers)
            if jr.status_code == 200:
                j = jr.json()
                msg = j.get("message") or {}
                job = j.get("job") or {}
                vs = (msg.get("video_status") or job.get("status") or "").lower()
                if vs in ("completed", "failed", "done"):
                    final_msg = msg
                    final_job = job
                    break
            time.sleep(1.5)
        assert final_msg is not None, "job did not reach terminal state in 30s"

        # Canonical assertions (preview has no FAL_KEY)
        assert final_msg.get("video_status") == "completed", final_msg
        assert final_msg.get("error_code") == "PROVIDER_AUTH_FAILED", final_msg
        assert final_msg.get("lipsync_debug") == "no_fal_key", final_msg
        assert final_msg.get("audio_url"), f"audio_url missing/empty: {final_msg}"
        assert not final_msg.get("video_url"), f"video_url should be empty: {final_msg}"
        vjid = final_msg.get("video_job_id") or ""
        assert vjid.startswith("avj_"), f"video_job_id must start with avj_: {vjid}"


def test_messages_listing_exposes_error_code(admin_headers):
    """GET /messages?limit=2 must include error_code, lipsync_debug, video_job_id on each item."""
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{API}/avatar-chat/messages?limit=2", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "messages" in d and isinstance(d["messages"], list)
        assert len(d["messages"]) >= 1, "expected at least one message after send test"
        canonical_codes = {
            "PROVIDER_AUTH_FAILED", "PROVIDER_422", "INVALID_AVATAR_ID",
            "JOB_TIMEOUT", "POLL_FAILED", "NO_VIDEO_URL",
            "RENDER_EXCEPTION", "VIDEO_NOT_STARTED",
        }
        # Each item must have the new fields present.
        for m in d["messages"]:
            assert "error_code" in m
            assert "lipsync_debug" in m
            assert "video_job_id" in m
        # At least one message (the freshly-sent one) must use a canonical code.
        recent_codes = [m.get("error_code") for m in d["messages"] if m.get("error_code")]
        assert any(c in canonical_codes for c in recent_codes), (
            f"no canonical error_code found in recent messages: {recent_codes}"
        )
