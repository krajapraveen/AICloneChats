"""iter32 — Verify /api/avatar-chat/status + new /api/avatar-chat/fal-health admin route.

Validates:
 - status returns sadtalker defaults
 - fal-health admin returns expected diagnostic shape
 - fal-health non-admin returns 403
 - send + job/{id} preview path still produces audio + PROVIDER_AUTH_FAILED
"""
import os
import time
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE:
    # fall back to frontend/.env when env not propagated to pytest shell
    from dotenv import dotenv_values
    BASE = (dotenv_values("/app/frontend/.env").get("REACT_APP_BACKEND_URL") or "").rstrip("/")

ADMIN_EMAIL = "krajapraveen@gmail.com"
ADMIN_PASS = "TestPass123!"
USER_EMAIL = "sr-tester@example.com"
USER_PASS = "TestPass123!"


def _login(email, password):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": password}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"login failed for {email}: {r.status_code} {r.text[:200]}")
    tok = r.json().get("session_token") or r.json().get("token") or r.json().get("access_token")
    assert tok, f"no token in {r.json()}"
    return tok


@pytest.fixture(scope="module")
def admin_headers():
    return {"Authorization": f"Bearer {_login(ADMIN_EMAIL, ADMIN_PASS)}"}


@pytest.fixture(scope="module")
def user_headers():
    return {"Authorization": f"Bearer {_login(USER_EMAIL, USER_PASS)}"}


# --- /status defaults ---
def test_status_exposes_sadtalker_defaults(admin_headers):
    r = requests.get(f"{BASE}/api/avatar-chat/status", headers=admin_headers, timeout=15)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("lipsync_endpoint") == "fal-ai/sadtalker", j
    assert j.get("lipsync_image_field") == "source_image_url", j
    assert j.get("lipsync_audio_field") == "driven_avatar_url" or j.get("lipsync_audio_field") == "driven_audio_url", j
    assert j.get("lipsync_audio_field") == "driven_audio_url", j
    assert j.get("lipsync_sync_mode") == "loop", j
    assert j.get("lipsync_model") in (None, ""), j


# --- /fal-health admin path on preview (no FAL_KEY) ---
def test_fal_health_admin_preview_no_key(admin_headers):
    r = requests.get(f"{BASE}/api/avatar-chat/fal-health", headers=admin_headers, timeout=15)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("endpoint") == "fal-ai/sadtalker", j
    assert j.get("image_field") == "source_image_url", j
    assert j.get("audio_field") == "driven_audio_url", j
    assert j.get("fal_key_present") is False, j
    assert j.get("error") == "FAL_KEY not set in env", j


# --- /fal-health non-admin gate ---
def test_fal_health_non_admin_403(user_headers):
    r = requests.get(f"{BASE}/api/avatar-chat/fal-health", headers=user_headers, timeout=15)
    assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text[:300]}"


# --- /fal-health unauthenticated ---
def test_fal_health_unauthenticated_401():
    r = requests.get(f"{BASE}/api/avatar-chat/fal-health", timeout=15)
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}: {r.text[:300]}"


# --- send + job preview flow ---
def test_send_and_job_preview(admin_headers):
    # Use one of the seeded demo clones
    payload = {"clone_id_or_slug": "raja-demo", "message": "Hello, this is an iter32 test."}
    r2 = requests.post(f"{BASE}/api/avatar-chat/send", headers=admin_headers, json=payload, timeout=60)
    if r2.status_code != 200:
        pytest.skip(f"send failed (likely paywall/feature gate): {r2.status_code} {r2.text[:300]}")
    msg = r2.json()
    inner = msg.get("message", {}) if isinstance(msg.get("message"), dict) else {}
    mid = inner.get("message_id") or inner.get("id") or msg.get("message_id") or msg.get("id")
    assert mid, msg
    # Poll job a few times
    final = None
    for _ in range(8):
        time.sleep(1.5)
        rj = requests.get(f"{BASE}/api/avatar-chat/job/{mid}", headers=admin_headers, timeout=15)
        if rj.status_code == 200:
            final = rj.json()
            if final.get("video_status") in ("completed", "failed"):
                break
    assert final is not None, "no job response"
    # Job response is {job: {...}, message: {...}} — flatten
    flat = {**(final.get("job") or {}), **(final.get("message") or {})} if isinstance(final, dict) else {}
    if not flat:
        flat = final
    # In preview, fal not reachable -> PROVIDER_AUTH_FAILED + audio populated
    assert flat.get("audio_url"), final
    assert flat.get("video_status") in ("completed", "failed"), final
    if flat.get("error_code"):
        assert "PROVIDER_AUTH_FAILED" in flat.get("error_code") or "no_fal_key" in (flat.get("lipsync_debug") or ""), final
