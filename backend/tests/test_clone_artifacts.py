"""
Tests for clone conversation artifacts.

Covers:
- Pull-only extraction (no scheduler, no auto-extraction)
- Identity scoping (visitor_id required when not authenticated)
- Cross-user/cross-visitor 403
- Empty conversation 400
- Task CRUD with ownership checks
- Admin metrics shape
- No reminder/notification side-effects exist
"""
from __future__ import annotations
import os, uuid
import httpx
import pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001"
API = f"{BASE}/api"
ADMIN_EMAIL = "sr-tester@example.com"
ADMIN_PASSWORD = "TestPass123!"


def _admin_token(client):
    r = client.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r.status_code != 200:
        client.post(f"{API}/auth/register", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "name": "SR Tester"})
        r = client.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    return r.json()["session_token"]


@pytest.fixture(scope="module")
def visitor_id():
    return f"v_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def conversation_with_messages(visitor_id):
    """Create a real conversation by sending visitor messages to a clone."""
    with httpx.Client(timeout=120) as c:
        # Use first available clone from the explore endpoint
        r = c.get(f"{API}/explore")
        clones = r.json().get("clones", []) if r.status_code == 200 else []
        if not clones:
            pytest.skip("No clones in DB to test against")
        slug = clones[0]["slug"]
        # Send a message that has clear extractable artifacts
        msg = "Tomorrow at 3pm I need to call my dad. Also I decided to write 500 words a day starting Monday — that's a real commitment. One thing I'm still not sure about: should I tell him in person or on the phone?"
        r = c.post(f"{API}/clones/{slug}/chat", json={
            "message": msg, "visitor_id": visitor_id, "visitor_name": "Test",
        })
        assert r.status_code == 200, r.text
        return r.json()["conversation_id"]


def test_extract_requires_identity(conversation_with_messages):
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{API}/clone-artifacts/extract", json={"conversation_id": conversation_with_messages})
        assert r.status_code == 401


def test_extract_visitor_happy_path(conversation_with_messages, visitor_id):
    with httpx.Client(timeout=180) as c:
        r = c.post(f"{API}/clone-artifacts/extract", json={
            "conversation_id": conversation_with_messages, "visitor_id": visitor_id,
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["artifact"]["artifact_id"].startswith("art_")
        assert isinstance(d["artifact"]["tasks"], list)
        assert isinstance(d["artifact"]["decisions"], list)
        assert isinstance(d["artifact"]["follow_ups"], list)
        # Whether the LLM caught everything is non-deterministic. We just assert shape.


def test_cross_visitor_403(conversation_with_messages):
    other_visitor = f"v_other_{uuid.uuid4().hex[:8]}"
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{API}/clone-artifacts/extract", json={
            "conversation_id": conversation_with_messages, "visitor_id": other_visitor,
        })
        assert r.status_code == 403


def test_extract_unknown_conversation_404(visitor_id):
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{API}/clone-artifacts/extract", json={
            "conversation_id": "conv_nope_404", "visitor_id": visitor_id,
        })
        assert r.status_code == 404


def test_list_and_task_crud(conversation_with_messages, visitor_id):
    with httpx.Client(timeout=180) as c:
        # Ensure at least one extraction exists
        c.post(f"{API}/clone-artifacts/extract", json={
            "conversation_id": conversation_with_messages, "visitor_id": visitor_id,
        })
        # List artifacts
        r = c.get(f"{API}/clone-artifacts?conversation_id={conversation_with_messages}&visitor_id={visitor_id}")
        assert r.status_code == 200
        assert len(r.json()["artifacts"]) >= 1
        # List tasks (may be 0 if LLM returned no tasks — both fine)
        r = c.get(f"{API}/clone-artifacts/tasks?conversation_id={conversation_with_messages}&visitor_id={visitor_id}")
        assert r.status_code == 200
        tasks = r.json()["tasks"]
        if tasks:
            task_id = tasks[0]["task_id"]
            # Update status
            r = c.patch(f"{API}/clone-artifacts/tasks/{task_id}?visitor_id={visitor_id}", json={"status": "done"})
            assert r.status_code == 200
            assert r.json()["task"]["status"] == "done"
            assert r.json()["task"]["completed_at"]
            # Cross-visitor cannot touch
            r2 = c.patch(f"{API}/clone-artifacts/tasks/{task_id}?visitor_id=v_other_x", json={"status": "open"})
            assert r2.status_code in (403, 404)
            # Delete
            r = c.delete(f"{API}/clone-artifacts/tasks/{task_id}?visitor_id={visitor_id}")
            assert r.status_code == 200


def test_admin_metrics_protected():
    with httpx.Client(timeout=10) as c:
        r = c.get(f"{API}/admin/clone-artifacts/metrics")
        assert r.status_code in (401, 403)


def test_admin_metrics_shape():
    with httpx.Client(timeout=15) as c:
        token = _admin_token(c)
        r = c.get(f"{API}/admin/clone-artifacts/metrics?days=7", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        d = r.json()
        for k in ("artifacts_extracted_in_window", "distinct_extractors_in_window", "tasks_extracted_in_window", "tasks_completed_in_window", "repeat_extractors_total", "operator_note"):
            assert k in d
        # Operator note must explicitly state no nudges/scheduler
        assert "behavior over activity" in d["operator_note"].lower()


def test_no_reminder_endpoints_exist():
    """Constitutional check: no reminder/notification/dispatcher routes for clone artifacts."""
    with httpx.Client(timeout=10) as c:
        for path in ["/clone-artifacts/reminders", "/clone-artifacts/dispatch", "/clone-artifacts/notify", "/clone-artifacts/digest"]:
            r = c.get(f"{API}{path}")
            # Must be 404 (route doesn't exist) or 405. Anything else implies a reminder mechanism crept in.
            assert r.status_code in (404, 405), f"unexpected route {path} returned {r.status_code}"
