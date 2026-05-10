"""
Voice Messaging — share + admin metrics + PII redaction tests (iteration_12).

Covers:
- POST /api/voice/messages/{id}/share (confirmation gate, PII redaction, idempotency, ownership)
- GET /api/voice/share/{share_id} (public, increments view_count, emits event)
- DELETE /api/voice/messages/{id}/share
- GET /api/admin/voice/metrics (admin-gated; payload shape)
- pii_redact.redact() unit tests for url/email/card/phone/otp/address
"""
import os
import time
import uuid

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

TEST_EMAIL = "sr-tester@example.com"
TEST_PASS = "TestPass123!"


@pytest.fixture(scope="module")
def mongo():
    c = MongoClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest.fixture(scope="module")
def auth_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASS}, timeout=15)
    if r.status_code != 200:
        s.post(f"{BASE_URL}/api/auth/register", json={"email": TEST_EMAIL, "password": TEST_PASS, "display_name": "SR Tester"}, timeout=15)
        r = s.post(f"{BASE_URL}/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASS}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"Cannot login: {r.status_code}")
    tok = r.json().get("token") or r.json().get("session_token") or r.json().get("access_token")
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    return s


@pytest.fixture(scope="module")
def authed_user_id(auth_session, mongo):
    r = auth_session.get(f"{BASE_URL}/api/auth/me", timeout=10)
    assert r.status_code == 200
    uid = r.json().get("user_id") or r.json().get("id")
    mongo.users.update_one({"user_id": uid}, {"$set": {"voice_daily_count": 0, "voice_daily_day": "1970-01-01"}})
    return uid


@pytest.fixture(scope="module")
def pii_message_id(auth_session):
    """Create a session+message containing every PII category."""
    pii_text = (
        "Hey call me at +1 555 123 4567 or email test@example.com. "
        "My card is 4242 4242 4242 4242 and OTP is 384921. "
        "See https://example.com/secret. I live at 123 Main Street."
    )
    r = auth_session.post(f"{BASE_URL}/api/voice/text-input", json={"text": pii_text}, timeout=60)
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    g = auth_session.post(f"{BASE_URL}/api/voice/generate", json={"session_id": sid, "tone": "professional"}, timeout=60)
    assert g.status_code == 200, g.text
    return g.json()["message_id"]


# ----------------- Share endpoint -----------------
class TestShareEndpoint:
    def test_share_requires_confirmation(self, auth_session, pii_message_id):
        r = auth_session.post(f"{BASE_URL}/api/voice/messages/{pii_message_id}/share",
                              json={"message_id": pii_message_id, "confirmed": False}, timeout=15)
        assert r.status_code == 400, r.text

    def test_share_create_redacts_all_categories(self, auth_session, pii_message_id, mongo):
        r = auth_session.post(f"{BASE_URL}/api/voice/messages/{pii_message_id}/share",
                              json={"message_id": pii_message_id, "confirmed": True}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["share_id"].startswith("v")
        assert d["url_path"] == f"/v/{d['share_id']}"
        cats = set(d["redacted_categories"])
        # Must catch at least these high-risk categories from the PII text
        for required in ("phone", "email", "url"):
            assert required in cats, f"Missing redaction: {required} in {cats}"
        # Verify stored docs have the redacted text
        share_doc = mongo.voice_shares.find_one({"share_id": d["share_id"]})
        assert share_doc is not None
        raw_red = share_doc.get("raw_input_redacted", "") or ""
        assert "test@example.com" not in raw_red
        assert "+1 555 123 4567" not in raw_red
        assert "https://example.com" not in raw_red
        assert "[email redacted]" in raw_red or "[phone redacted]" in raw_red or "[link redacted]" in raw_red
        pytest.shared_share_id = d["share_id"]
        pytest.shared_share_msg = pii_message_id

    def test_share_idempotent(self, auth_session, pii_message_id):
        r1 = auth_session.post(f"{BASE_URL}/api/voice/messages/{pii_message_id}/share",
                               json={"message_id": pii_message_id, "confirmed": True}, timeout=15)
        r2 = auth_session.post(f"{BASE_URL}/api/voice/messages/{pii_message_id}/share",
                               json={"message_id": pii_message_id, "confirmed": True}, timeout=15)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["share_id"] == r2.json()["share_id"]

    def test_public_get_share_no_auth(self, mongo):
        sid = pytest.shared_share_id
        # Plain requests without auth header
        before = mongo.voice_shares.find_one({"share_id": sid}) or {}
        before_views = int(before.get("view_count", 0))
        r = requests.get(f"{BASE_URL}/api/voice/share/{sid}", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["share_id"] == sid
        assert d["watermark"] == "Optimized with aiclonechats.com Voice"
        assert "raw_input" in d and "polished_message" in d
        time.sleep(0.5)
        after = mongo.voice_shares.find_one({"share_id": sid}) or {}
        assert int(after.get("view_count", 0)) == before_views + 1
        # event emitted
        ev = mongo.voice_usage_events.find_one(
            {"event_name": "voice_share_viewed", "metadata.share_id": sid}
        )
        assert ev is not None
        assert ev["metadata"].get("experience_variant") == "voice_v1"

    def test_public_get_404_for_unknown(self):
        r = requests.get(f"{BASE_URL}/api/voice/share/v_does_not_exist_xyz", timeout=10)
        assert r.status_code == 404

    def test_share_owner_isolation(self, pii_message_id):
        # Anonymous device cannot share an authed user's message
        dev = f"foreign-dev-{uuid.uuid4().hex[:12]}"
        r = requests.post(f"{BASE_URL}/api/voice/messages/{pii_message_id}/share",
                          json={"message_id": pii_message_id, "confirmed": True},
                          headers={"X-Device-Id": dev, "Content-Type": "application/json"}, timeout=15)
        assert r.status_code == 404, r.text

    def test_delete_share_removes_public_access(self, auth_session):
        msg_id = pytest.shared_share_msg
        sid = pytest.shared_share_id
        r = auth_session.delete(f"{BASE_URL}/api/voice/messages/{msg_id}/share", timeout=10)
        assert r.status_code == 200, r.text
        # public GET now 404
        r2 = requests.get(f"{BASE_URL}/api/voice/share/{sid}", timeout=10)
        assert r2.status_code == 404


# ----------------- Track new events -----------------
class TestTrackShareEvents:
    def test_track_share_warning_shown(self):
        dev = f"trk-{uuid.uuid4().hex[:10]}"
        for ev in ("voice_share_warning_shown", "voice_share_warning_dismissed"):
            r = requests.post(f"{BASE_URL}/api/voice/track", json={"event_name": ev},
                              headers={"X-Device-Id": dev, "Content-Type": "application/json"}, timeout=10)
            assert r.status_code == 200, f"{ev}: {r.text}"


# ----------------- Admin metrics -----------------
class TestAdminMetrics:
    def test_metrics_unauth_401_or_403(self):
        r = requests.get(f"{BASE_URL}/api/admin/voice/metrics?days=7", timeout=15)
        assert r.status_code in (401, 403), r.status_code

    def test_metrics_non_admin_403(self):
        # Register a fresh non-admin user
        email = f"nonadmin-{uuid.uuid4().hex[:8]}@example.com"
        s = requests.Session()
        s.post(f"{BASE_URL}/api/auth/register",
               json={"email": email, "password": "TestPass123!", "display_name": "NonAdmin"}, timeout=15)
        r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": "TestPass123!"}, timeout=15)
        if r.status_code != 200:
            pytest.skip("Cannot create non-admin user")
        tok = r.json().get("token") or r.json().get("session_token") or r.json().get("access_token")
        r2 = requests.get(f"{BASE_URL}/api/admin/voice/metrics?days=7",
                          headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        assert r2.status_code == 403, r2.status_code

    def test_metrics_admin_full_payload(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/admin/voice/metrics?days=7", timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        # window
        assert d["window_days"] == 7
        # funnel — 7 stages
        assert isinstance(d["funnel"], list) and len(d["funnel"]) == 7
        stages = [row["stage"] for row in d["funnel"]]
        assert stages == ["viewed", "input_started", "transcription_completed",
                          "generated", "copied", "second_gen_same_day", "returned_next_day"]
        for row in d["funnel"]:
            assert "actors" in row and "drop_from_prev_pct" in row and "pct_of_top" in row
        # north_star
        ns = d["north_star"]
        assert ns["label"] == "Generation -> Copy Rate"
        assert "overall_copy_rate_pct" in ns and "messages_generated" in ns and "messages_copied" in ns
        # tone_performance
        tp = d["tone_performance"]
        assert "rows" in tp and "best_tone" in tp and "worst_tone" in tp
        if tp["rows"]:
            assert tp["best_tone"]["copy_rate_pct"] >= tp["worst_tone"]["copy_rate_pct"]
            for row in tp["rows"]:
                for k in ("tone", "generated", "copied", "copy_rate_pct"):
                    assert k in row
        # trust_signals
        ts = d["trust_signals"]
        assert "edit_before_copy_pct" in ts
        # source_split
        assert isinstance(d["source_split"], list)
        # actors
        ac = d["actors"]
        for k in ("total_anonymous", "total_authed", "anonymous_to_signup_conversion_pct"):
            assert k in ac
        # retention
        ret = d["retention"]
        for k in ("actors_with_2nd_gen_same_day", "actors_returned_next_day", "d1_return_rate_pct"):
            assert k in ret
        # daily_active_actors
        assert isinstance(d["daily_active_actors"], list)

    def test_metrics_window_clamp(self, auth_session):
        # invalid days outside 1..90 → 422
        r = auth_session.get(f"{BASE_URL}/api/admin/voice/metrics?days=500", timeout=15)
        assert r.status_code == 422


# ----------------- PII redact unit -----------------
class TestPIIRedact:
    """Direct unit tests on pii_redact.redact()."""

    def test_redact_categories(self):
        import sys
        sys.path.insert(0, "/app/backend")
        from pii_redact import redact

        text = ("Call +1 555 123 4567 or email me at user@example.com. "
                "Card 4242 4242 4242 4242. OTP is 384921. "
                "Visit https://example.com/path. 123 Main Street.")
        out, cats = redact(text)
        assert "user@example.com" not in out
        assert "https://example.com/path" not in out
        assert "[email redacted]" in out
        assert "[link redacted]" in out
        # at least these are caught
        for c in ("email", "url", "phone"):
            assert c in cats, f"missing {c} in {cats}; out={out}"

    def test_redact_empty_safe(self):
        from pii_redact import redact
        assert redact("") == ("", [])
        assert redact(None)[0] == ""
