"""
Voice-First AI Messaging — backend regression tests.

Covers:
- /api/voice/usage for anon (X-Device-Id) and authed user
- /api/voice/text-input (cleans filler words, creates session)
- /api/voice/generate single tone
- /api/voice/generate-all (6 tones in parallel)
- /api/voice/refine (returns NEW message_id, rewritten text)
- PATCH /api/voice/sessions/{id} (updates cleaned_transcript)
- /api/voice/copy-event (increments copy_count)
- /api/voice/history (auth=sessions+messages, anon=empty, is_anonymous=true)
- Anonymous limit -> 4th call returns 402 anon_limit_reached, wall=signup
- /api/voice/transcribe with tiny WAV (Whisper integration)
- /api/voice/track allowed/disallowed events
- Analytics separation: events go to db.voice_usage_events with experience_variant=voice_v1
  and do NOT pollute clone_analytics or smart_reply_sessions
"""
import io
import os
import struct
import time
import uuid
import wave

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
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest.fixture(scope="module")
def device_id():
    return f"test-dev-{uuid.uuid4().hex[:16]}"


@pytest.fixture(scope="module")
def anon_session(device_id):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "X-Device-Id": device_id})
    return s


@pytest.fixture(scope="module")
def auth_token():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASS}, timeout=15)
    if r.status_code != 200:
        # Try registering
        s.post(f"{BASE_URL}/api/auth/register", json={"email": TEST_EMAIL, "password": TEST_PASS, "display_name": "SR Tester"}, timeout=15)
        r = s.post(f"{BASE_URL}/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASS}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"Cannot login test user: {r.status_code} {r.text}")
    data = r.json()
    return data.get("token") or data.get("session_token") or data.get("access_token")


@pytest.fixture(scope="module")
def auth_session(auth_token):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {auth_token}"})
    return s


@pytest.fixture(scope="module")
def authed_user_id(auth_session, mongo):
    r = auth_session.get(f"{BASE_URL}/api/auth/me", timeout=10)
    assert r.status_code == 200, r.text
    uid = r.json().get("user_id") or r.json().get("id")
    # Reset daily count so we don't hit usage limits from prior tests
    mongo.users.update_one({"user_id": uid}, {"$set": {"voice_daily_count": 0, "voice_daily_day": "1970-01-01"}})
    return uid


# -------- Helpers --------
def _tiny_wav_bytes(seconds=1.0, freq=440, rate=16000):
    """Generate a tiny mono WAV with a sine tone."""
    import math
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(rate * seconds)
        for i in range(n):
            val = int(32767 * 0.3 * math.sin(2 * math.pi * freq * (i / rate)))
            w.writeframes(struct.pack("<h", val))
    return buf.getvalue()


# -------- Tests: Usage --------
class TestUsage:
    def test_anon_usage_initial(self, anon_session):
        r = anon_session.get(f"{BASE_URL}/api/voice/usage", timeout=10)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["is_anonymous"] is True
        assert d["anon_limit"] == 3
        assert d["anon_remaining"] == 3
        assert d["daily_limit"] is None

    def test_authed_usage(self, auth_session, authed_user_id):
        r = auth_session.get(f"{BASE_URL}/api/voice/usage", timeout=10)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["is_anonymous"] is False
        assert d["daily_remaining"] == 20 or d.get("is_pro") is True

    def test_usage_requires_device_or_auth(self):
        r = requests.get(f"{BASE_URL}/api/voice/usage", timeout=10)
        assert r.status_code in (400, 401, 422)


# -------- Tests: text-input + generate flows (authed user) --------
class TestTextPipeline:
    def test_text_input_cleans_filler(self, auth_session):
        raw = "um like, you know, I basically need to tell my boss I will be late tomorrow morning"
        r = auth_session.post(f"{BASE_URL}/api/voice/text-input", json={"text": raw}, timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["session_id"].startswith("vs_")
        assert d["raw_transcript"] == raw.strip()
        assert d["source_type"] == "text"
        cleaned_lower = d["cleaned_transcript"].lower()
        # filler words should be removed
        for filler in (" um ", " like,", "you know", "basically"):
            assert filler not in cleaned_lower, f"Filler '{filler}' not removed: {d['cleaned_transcript']}"
        pytest.shared_session_id = d["session_id"]
        pytest.shared_cleaned = d["cleaned_transcript"]

    def test_generate_all_returns_six_tones(self, auth_session):
        sid = pytest.shared_session_id
        t0 = time.time()
        r = auth_session.post(f"{BASE_URL}/api/voice/generate-all", json={"session_id": sid}, timeout=90)
        elapsed = time.time() - t0
        assert r.status_code == 200, r.text
        msgs = r.json()["messages"]
        assert len(msgs) == 6, f"Expected 6 tones, got {len(msgs)}"
        tones = {m["tone"] for m in msgs}
        assert tones == {"concise", "professional", "friendly", "apology", "dating", "negotiation"}
        for m in msgs:
            assert m["message_id"].startswith("vm_")
            assert isinstance(m["message"], str) and len(m["message"]) > 5
        print(f"generate-all took {elapsed:.1f}s for 6 tones")
        pytest.shared_messages = {m["tone"]: m["message_id"] for m in msgs}

    def test_generate_single_tone(self, auth_session):
        sid = pytest.shared_session_id
        r = auth_session.post(f"{BASE_URL}/api/voice/generate", json={"session_id": sid, "tone": "professional"}, timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["tone"] == "professional"
        assert d["message_id"].startswith("vm_")
        assert len(d["generated_message"]) > 5

    def test_refine_creates_new_message_id(self, auth_session):
        original_id = pytest.shared_messages["friendly"]
        r = auth_session.post(f"{BASE_URL}/api/voice/refine", json={"message_id": original_id, "refine_type": "shorter"}, timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["message_id"] != original_id
        assert d["message_id"].startswith("vm_")
        assert isinstance(d["generated_message"], str) and len(d["generated_message"]) > 0

    def test_refine_all_types(self, auth_session):
        msg_id = pytest.shared_messages["concise"]
        for rt in ("confident", "polite", "flirty", "professional"):
            r = auth_session.post(f"{BASE_URL}/api/voice/refine", json={"message_id": msg_id, "refine_type": rt}, timeout=60)
            assert r.status_code == 200, f"refine={rt}: {r.text}"
            assert r.json()["message_id"] != msg_id

    def test_patch_session_updates_transcript(self, auth_session, mongo):
        sid = pytest.shared_session_id
        new_text = "I need to tell my boss I will arrive 30 minutes late"
        r = auth_session.patch(f"{BASE_URL}/api/voice/sessions/{sid}", json={"cleaned_transcript": new_text}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["cleaned_transcript"] == new_text
        # Verify persisted
        sess = mongo.voice_sessions.find_one({"session_id": sid})
        assert sess["cleaned_transcript"] == new_text
        assert sess.get("edited_by_user") is True

    def test_copy_event_increments(self, auth_session, mongo):
        msg_id = pytest.shared_messages["apology"]
        before = mongo.generated_messages.find_one({"message_id": msg_id}) or {}
        before_count = int(before.get("copy_count", 0))
        r = auth_session.post(f"{BASE_URL}/api/voice/copy-event", json={"message_id": msg_id}, timeout=10)
        assert r.status_code == 200, r.text
        after = mongo.generated_messages.find_one({"message_id": msg_id})
        assert after["copy_count"] == before_count + 1


# -------- Tests: history --------
class TestHistory:
    def test_authed_history_has_messages(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/voice/history", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["is_anonymous"] is False
        assert isinstance(d["sessions"], list)
        assert len(d["sessions"]) > 0
        s = next((x for x in d["sessions"] if x["session_id"] == pytest.shared_session_id), None)
        assert s is not None, "Test session not in history"
        assert "messages" in s and len(s["messages"]) > 0

    def test_anon_history_empty(self, anon_session):
        r = anon_session.get(f"{BASE_URL}/api/voice/history", timeout=10)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["is_anonymous"] is True
        assert d["sessions"] == []


# -------- Tests: anonymous limit gating --------
class TestAnonLimit:
    def test_burn_three_then_block(self, mongo):
        dev = f"test-burn-{uuid.uuid4().hex[:14]}"
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json", "X-Device-Id": dev})
        # 3 successful text-input + generate cycles consume 3 trials (text-input itself consumes via _check_usage)
        for i in range(3):
            r = s.post(f"{BASE_URL}/api/voice/text-input", json={"text": f"Just a quick note number {i}"}, timeout=60)
            assert r.status_code == 200, f"Trial {i+1} failed: {r.status_code} {r.text}"
        # 4th must be blocked
        r4 = s.post(f"{BASE_URL}/api/voice/text-input", json={"text": "Fourth attempt should fail"}, timeout=15)
        assert r4.status_code == 402, f"Expected 402, got {r4.status_code}: {r4.text}"
        body = r4.json()
        detail = body.get("detail", body)
        assert detail.get("code") == "anon_limit_reached"
        assert detail.get("wall") == "signup"

        # Cleanup
        mongo.voice_anon_trials.delete_one({"device_id": dev})


# -------- Tests: transcribe (Whisper) --------
class TestTranscribe:
    def test_transcribe_tiny_wav(self, auth_session):
        wav = _tiny_wav_bytes(seconds=1.5, freq=440)
        files = {"audio_file": ("test.wav", wav, "audio/wav")}
        data = {"source_type": "upload"}
        # Use a fresh requests call without Content-Type json header
        headers = {k: v for k, v in auth_session.headers.items() if k.lower() != "content-type"}
        r = requests.post(f"{BASE_URL}/api/voice/transcribe", files=files, data=data, headers=headers, timeout=60)
        # A pure sine wave has no speech. Whisper may return empty -> 422, OR random text.
        # Both behaviors are acceptable; we accept 200 OR 422.
        assert r.status_code in (200, 422), f"Unexpected status: {r.status_code} {r.text}"
        if r.status_code == 200:
            d = r.json()
            assert "session_id" in d and d["session_id"].startswith("vs_")
            assert "raw_transcript" in d
            assert "cleaned_transcript" in d
            assert d["source_type"] == "upload"

    def test_transcribe_rejects_empty(self, auth_session):
        files = {"audio_file": ("empty.wav", b"", "audio/wav")}
        headers = {k: v for k, v in auth_session.headers.items() if k.lower() != "content-type"}
        r = requests.post(f"{BASE_URL}/api/voice/transcribe", files=files, data={"source_type": "upload"}, headers=headers, timeout=15)
        assert r.status_code == 400


# -------- Tests: track --------
class TestTrack:
    def test_track_allowed_event(self, device_id):
        r = requests.post(f"{BASE_URL}/api/voice/track", data={"event_name": "voice_page_viewed"},
                          headers={"X-Device-Id": device_id}, timeout=10)
        assert r.status_code == 200, r.text

    def test_track_rejects_unknown(self, device_id):
        r = requests.post(f"{BASE_URL}/api/voice/track", data={"event_name": "totally_made_up_event"},
                          headers={"X-Device-Id": device_id}, timeout=10)
        assert r.status_code == 400

    def test_track_all_allowed_events(self, device_id):
        events = ["voice_record_started", "voice_example_clicked", "voice_signup_wall_shown"]
        for ev in events:
            r = requests.post(f"{BASE_URL}/api/voice/track", data={"event_name": ev},
                              headers={"X-Device-Id": device_id}, timeout=10)
            assert r.status_code == 200, f"event {ev}: {r.text}"


# -------- Tests: analytics separation --------
class TestAnalyticsSeparation:
    def test_voice_events_only_in_voice_collection(self, mongo, authed_user_id):
        # Sample an event we just emitted
        evs = list(mongo.voice_usage_events.find({"user_id": authed_user_id}).limit(20))
        assert len(evs) > 0, "No voice events recorded"
        for e in evs:
            assert e["metadata"].get("experience_variant") == "voice_v1"
        # Ensure clone_analytics has NO voice_* events
        bad = mongo.clone_analytics.count_documents({"event_name": {"$regex": "^voice_"}})
        assert bad == 0, f"clone_analytics polluted with {bad} voice_* events"
        # Ensure smart_reply_sessions has no voice_session_id field leak
        bad2 = mongo.smart_reply_sessions.count_documents({"experience_variant": "voice_v1"}) if "smart_reply_sessions" in mongo.list_collection_names() else 0
        assert bad2 == 0, f"smart_reply_sessions polluted: {bad2}"


# -------- Tests: regression --------
class TestRegression:
    def test_smart_reply_health_or_existing(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert r.status_code in (200, 404)  # endpoint may or may not exist

    def test_login_works(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASS}, timeout=15)
        assert r.status_code == 200
