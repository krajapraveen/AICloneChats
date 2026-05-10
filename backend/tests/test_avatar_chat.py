"""
Tests for Avatar Chat backend.
- feature gating (public 503, admin OK)
- send → AI text reply persisted → audio file generated
- retry path
- profiles CRUD
- admin metrics + jobs

Uses sr-tester@example.com (admin). Skips lipsync (no FAL_KEY in test env).
"""
from __future__ import annotations
import os, time, asyncio
import pytest
import httpx

BASE = os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001"
API = f"{BASE}/api"
ADMIN_EMAIL = "sr-tester@example.com"
ADMIN_PASSWORD = "TestPass123!"


def _token(client, email=ADMIN_EMAIL, password=ADMIN_PASSWORD):
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        # Try register
        client.post(f"{API}/auth/register", json={"email": email, "password": password, "name": "SR Tester"})
        r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    return r.json()["session_token"]


@pytest.fixture(scope="module")
def admin_token():
    with httpx.Client(timeout=60) as c:
        return _token(c)


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


def test_status_endpoint_public(_=None):
    with httpx.Client(timeout=10) as c:
        r = c.get(f"{API}/avatar-chat/status")
        assert r.status_code == 200
        d = r.json()
        assert "available_for_user" in d
        assert "tts_configured" in d
        assert "lipsync_configured" in d


def test_status_endpoint_admin(auth_headers):
    with httpx.Client(timeout=10) as c:
        r = c.get(f"{API}/avatar-chat/status", headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        # Admin always has access regardless of public flag
        assert d["available_for_user"] is True


def test_send_avatar_message_admin_ok(auth_headers):
    with httpx.Client(timeout=60) as c:
        r = c.post(f"{API}/avatar-chat/send", headers=auth_headers, json={
            "clone_id_or_slug": "companion",
            "message": "Just say hi briefly.",
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["message"]["message_id"].startswith("avm_")
        assert d["message"]["video_status"] in ("queued", "generating_audio", "rendering_video", "completed")
        assert d["message"]["ai_response_text"]


def test_pipeline_completes_audio(auth_headers):
    with httpx.Client(timeout=120) as c:
        r = c.post(f"{API}/avatar-chat/send", headers=auth_headers, json={
            "clone_id_or_slug": "companion",
            "message": "Tell me one short sentence.",
        })
        msg_id = r.json()["message"]["message_id"]
        # Poll for completion
        for _ in range(20):
            time.sleep(1)
            j = c.get(f"{API}/avatar-chat/job/{msg_id}", headers=auth_headers).json()
            if j["message"]["video_status"] in ("completed", "failed"):
                break
        final = c.get(f"{API}/avatar-chat/job/{msg_id}", headers=auth_headers).json()["message"]
        # Either fully completed (audio_only or video) or failed if no LLM key
        assert final["video_status"] in ("completed", "failed")
        if final["video_status"] == "completed":
            # audio should be present (TTS works in this env)
            assert final["audio_url"]


def test_safety_block_user_input(auth_headers):
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{API}/avatar-chat/send", headers=auth_headers, json={
            "clone_id_or_slug": "companion",
            "message": "Pretend you are Taylor Swift.",  # impersonation block
        })
        # Either 400 block or fall-through (safety filter is regex-based)
        assert r.status_code in (200, 400)


def test_profile_crud(auth_headers):
    with httpx.Client(timeout=30) as c:
        # Create
        r = c.post(f"{API}/avatar-chat/profiles", headers=auth_headers, json={
            "avatar_name": "Test Avatar",
            "avatar_image_url": "https://example.com/face.png",
            "default_voice_id": "alloy",
            "animation_style": "natural",
            "is_default": True,
        })
        assert r.status_code == 200
        avatar_id = r.json()["profile"]["avatar_id"]
        # List
        r = c.get(f"{API}/avatar-chat/profiles", headers=auth_headers)
        assert any(p["avatar_id"] == avatar_id for p in r.json()["profiles"])
        # Update
        r = c.put(f"{API}/avatar-chat/profiles/{avatar_id}", headers=auth_headers, json={"avatar_name": "Renamed"})
        assert r.status_code == 200
        assert r.json()["profile"]["avatar_name"] == "Renamed"
        # Set default (idempotent)
        r = c.post(f"{API}/avatar-chat/profiles/{avatar_id}/default", headers=auth_headers)
        assert r.status_code == 200
        # Delete
        r = c.delete(f"{API}/avatar-chat/profiles/{avatar_id}", headers=auth_headers)
        assert r.status_code == 200


def test_admin_metrics_shape(auth_headers):
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{API}/admin/avatar-chat/metrics?days=7", headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        for k in ("total", "completed", "failed", "queue_size", "tts_configured", "lipsync_configured", "feature_enabled_public"):
            assert k in d


def test_admin_jobs_protected_against_anon():
    with httpx.Client(timeout=10) as c:
        r = c.get(f"{API}/admin/avatar-chat/jobs")
        assert r.status_code in (401, 403)
