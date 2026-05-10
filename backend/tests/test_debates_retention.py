"""Debates retention dashboard backend tests."""
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "sr-tester@example.com"
ADMIN_PASSWORD = "TestPass123!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r.status_code != 200:
        requests.post(f"{BASE_URL}/api/auth/register", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "full_name": "SR Tester"})
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    body = r.json()
    return body.get("access_token") or body.get("session_token") or body.get("token")


def test_retention_requires_auth():
    r = requests.get(f"{BASE_URL}/api/admin/debates/retention")
    assert r.status_code in (401, 403)


def test_retention_non_admin_403():
    email = f"non-{uuid.uuid4().hex[:8]}@example.com"
    requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": "x12345678!", "full_name": "Non"})
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": "x12345678!"})
    tok = (r.json().get("access_token") or r.json().get("session_token") or r.json().get("token"))
    r2 = requests.get(f"{BASE_URL}/api/admin/debates/retention", headers={"Authorization": f"Bearer {tok}"})
    assert r2.status_code == 403


def test_retention_payload_shape(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = requests.get(f"{BASE_URL}/api/admin/debates/retention?days=14", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ["generated_at", "window_days", "funnel", "return_to_defend", "engagement", "retention", "cohorts_first_category", "qualitative"]:
        assert k in body
    f = body["funnel"]
    for k in ["list_viewed_users", "opened_users", "joined_users", "submitted_users", "voted_users",
              "open_rate_pct", "join_rate_pct", "argument_rate_pct", "vote_rate_pct"]:
        assert k in f
    r2d = body["return_to_defend"]
    for k in ["submitter_debate_pairs", "returned", "pct"]:
        assert k in r2d
    e = body["engagement"]
    for k in ["submitters", "multi_submitters", "avg_args_per_submitter", "avg_argument_length_chars", "lurker_pct"]:
        assert k in e
    ret = body["retention"]
    for k in ["d1", "d7"]:
        assert k in ret and "pct" in ret[k]
    qual = body["qualitative"]
    assert "fastest_rising" in qual and "most_reported" in qual


def test_retention_window_bounds(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r1 = requests.get(f"{BASE_URL}/api/admin/debates/retention?days=0", headers=headers)
    assert r1.status_code == 422
    r2 = requests.get(f"{BASE_URL}/api/admin/debates/retention?days=999", headers=headers)
    assert r2.status_code == 422


def test_events_export_admin_only(admin_token):
    r = requests.get(f"{BASE_URL}/api/admin/debates/events/export?days=7&limit=5", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "events" in body and "count" in body
    assert isinstance(body["events"], list)
    if body["events"]:
        ev = body["events"][0]
        assert "event_name" in ev
        assert "metadata" in ev
        # Strict analytics separation: every event should be tagged debate_v1
        assert ev["metadata"].get("experience_variant") == "debate_v1"


def test_events_export_filter(admin_token):
    r = requests.get(f"{BASE_URL}/api/admin/debates/events/export?days=14&event_name=debate_argument_submitted", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    for e in body["events"]:
        assert e["event_name"] == "debate_argument_submitted"
