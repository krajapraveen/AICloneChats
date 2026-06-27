"""
iter33 integration tests: verifies the 8 new diagnostic fields are present
in /api/avatar-chat/send and /api/avatar-chat/job/{message_id} responses.
On preview (no FAL_KEY), values will be null/None but keys must exist.
"""
import os
import time
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")

ADMIN_EMAIL = "krajapraveen@gmail.com"
ADMIN_PASSWORD = "TestPass123!"

NEW_DIAG_FIELDS = [
    "provider_request_id",
    "provider_status",
    "poll_attempts",
    "last_poll_at",
    "fal_endpoint",
    "final_result_keys",
    "failure_reason",
    "completed_at",
    "video_url_present",
]


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text[:200]}"
    token = r.json().get("session_token") or r.json().get("token")
    assert token, f"no token in login response: {r.json()}"
    return token


@pytest.fixture(scope="module")
def admin_session(admin_token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"})
    return s


def _pick_clone_id(session):
    """Find a clone owned by admin, or seeded demo clone."""
    # mine
    r = session.get(f"{BASE_URL}/api/clones/mine", timeout=15)
    if r.status_code == 200:
        items = r.json()
        if isinstance(items, list) and items:
            return items[0].get("clone_id") or items[0].get("id")
    # well-known seeded demo
    for slug in ("raja-demo", "companion"):
        r = session.get(f"{BASE_URL}/api/clones/by-slug/{slug}", timeout=15)
        if r.status_code == 200:
            cid = r.json().get("clone_id")
            if cid:
                return cid
    return None


def test_send_response_includes_new_diagnostic_fields(admin_session):
    clone_id = _pick_clone_id(admin_session)
    if not clone_id:
        pytest.skip("no clone available for admin")
    payload = {"clone_id_or_slug": clone_id, "message": "Hello, iter33 diagnostic check."}
    r = admin_session.post(f"{BASE_URL}/api/avatar-chat/send", json=payload, timeout=60)
    if r.status_code in (402, 403):
        pytest.skip(f"send gated: {r.status_code} {r.text[:120]}")
    assert r.status_code == 200, f"send failed: {r.status_code} {r.text[:300]}"
    body = r.json()
    msg = body.get("message") or {}
    for f in NEW_DIAG_FIELDS:
        assert f in msg, f"missing field '{f}' in send response.message; got keys={list(msg.keys())}"
    # video_url_present must be a bool
    assert isinstance(msg["video_url_present"], bool), f"video_url_present must be bool, got {type(msg['video_url_present'])}"


def test_job_response_includes_new_diagnostic_fields(admin_session):
    clone_id = _pick_clone_id(admin_session)
    if not clone_id:
        pytest.skip("no clone available for admin")
    payload = {"clone_id_or_slug": clone_id, "message": "Hello, iter33 job check."}
    r = admin_session.post(f"{BASE_URL}/api/avatar-chat/send", json=payload, timeout=60)
    if r.status_code in (402, 403):
        pytest.skip(f"send gated: {r.status_code} {r.text[:120]}")
    assert r.status_code == 200, f"send failed: {r.status_code} {r.text[:300]}"
    msg_id = (r.json().get("message") or {}).get("message_id")
    assert msg_id, f"no message id: {r.json()}"

    # allow pipeline to settle
    time.sleep(5)

    j = admin_session.get(f"{BASE_URL}/api/avatar-chat/job/{msg_id}", timeout=15)
    assert j.status_code == 200, f"job fetch failed: {j.status_code} {j.text[:300]}"
    body = j.json()
    msg = body.get("message") or {}
    for f in NEW_DIAG_FIELDS:
        assert f in msg, f"missing field '{f}' in /job response.message; got keys={list(msg.keys())}"
    assert isinstance(msg["video_url_present"], bool)


def test_failure_reason_set_when_no_fal_key(admin_session):
    """On preview (no FAL_KEY), audio-only fallback path → failure_reason populated."""
    clone_id = _pick_clone_id(admin_session)
    if not clone_id:
        pytest.skip("no clone available for admin")
    r = admin_session.post(f"{BASE_URL}/api/avatar-chat/send",
                          json={"clone_id_or_slug": clone_id, "message": "iter33 failure_reason test"},
                          timeout=60)
    if r.status_code in (402, 403):
        pytest.skip(f"send gated: {r.status_code} {r.text[:120]}")
    assert r.status_code == 200
    msg_id = (r.json().get("message") or {}).get("message_id")
    assert msg_id
    # poll until pipeline finishes (max 30s)
    msg = None
    for _ in range(15):
        time.sleep(2)
        j = admin_session.get(f"{BASE_URL}/api/avatar-chat/job/{msg_id}", timeout=15)
        if j.status_code != 200:
            continue
        msg = (j.json() or {}).get("message") or {}
        if msg.get("video_status") in ("completed", "failed"):
            break
    assert msg is not None
    # Preview has no FAL_KEY → failure_reason should be non-null (e.g. PROVIDER_AUTH_FAILED)
    # OR error_code populated. Since failure_reason falls back to error_code in _public_message,
    # presence of either is acceptable here.
    has_failure = bool(msg.get("failure_reason") or msg.get("error_code"))
    assert has_failure, f"expected failure_reason or error_code populated on preview; msg={msg}"
    # video_url_present must be False since no video rendered
    assert msg.get("video_url_present") is False, f"video_url_present should be False on preview, got {msg.get('video_url_present')}"
