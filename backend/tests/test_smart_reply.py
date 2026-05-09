"""Smart Reply backend e2e tests — auth, generate, contracts, history, favorites, usage gate, analytics."""
import os
import time
import uuid
import pytest
import requests
from dotenv import load_dotenv

# Load backend .env so MONGO_URL/DB_NAME are available to tests run from any cwd
load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

TS = int(time.time())
EMAIL = f"sr_{TS}_{uuid.uuid4().hex[:6]}@example.com"
EMAIL2 = f"sr_{TS}_{uuid.uuid4().hex[:6]}@example.com"
PWD = "TestPass123!"


@pytest.fixture(scope="module")
def state():
    return {}


# -------- Setup: register two fresh users --------
class TestSetup:
    def test_register_user1(self, state):
        r = requests.post(f"{API}/auth/register", json={"email": EMAIL, "password": PWD, "name": "SR User"}, timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        state["token"] = d["session_token"]
        state["user_id"] = d["user"]["user_id"]

    def test_register_user2(self, state):
        r = requests.post(f"{API}/auth/register", json={"email": EMAIL2, "password": PWD, "name": "Other"}, timeout=20)
        assert r.status_code == 200, r.text
        state["token2"] = r.json()["session_token"]


# -------- Auth gating --------
class TestAuthGate:
    def test_subscription_status_no_auth(self):
        r = requests.get(f"{API}/smart-reply/subscription/status", timeout=15)
        assert r.status_code == 401

    def test_generate_no_auth(self):
        r = requests.post(f"{API}/smart-reply/generate", json={
            "incoming_message": "hi", "mode": "dating", "desired_tone": "warm",
        }, timeout=15)
        assert r.status_code == 401

    def test_history_no_auth(self):
        r = requests.get(f"{API}/smart-reply/history", timeout=15)
        assert r.status_code == 401

    def test_favorites_list_no_auth(self):
        r = requests.get(f"{API}/smart-reply/favorites", timeout=15)
        assert r.status_code == 401


# -------- Subscription status --------
class TestSubscriptionStatus:
    def test_status_fresh_free_user(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.get(f"{API}/smart-reply/subscription/status", headers=h, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["subscription_status"] == "free"
        assert d["is_pro"] is False
        assert d["daily_limit"] == 5
        assert d["daily_used"] == 0
        assert d["daily_remaining"] == 5


# -------- Generate contract --------
class TestGenerate:
    def test_generate_returns_full_contract(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        body = {
            "incoming_message": "Hey, sorry I went quiet — work has been crazy. Can we hang this weekend?",
            "mode": "dating",
            "desired_tone": "warm",
            "relationship_context": "talking 2 weeks",
            "user_goal": "confirm without sounding desperate",
        }
        r = requests.post(f"{API}/smart-reply/generate", headers=h, json=body, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        # Top-level shape
        for k in ("session_id", "mode", "desired_tone", "tone_explanation", "replies", "daily_remaining", "is_pro"):
            assert k in d, f"missing top-level key {k}"
        assert d["mode"] == "dating"
        assert d["desired_tone"] == "warm"
        assert d["is_pro"] is False
        assert isinstance(d["replies"], list) and len(d["replies"]) == 3
        # Strict label/length contract enforced server-side
        expected = [("safe", "short"), ("warm", "medium"), ("confident", "long")]
        for i, (lbl, lng) in enumerate(expected):
            r_i = d["replies"][i]
            for k in ("label", "length", "reply", "why_it_works", "risk_level"):
                assert k in r_i, f"reply[{i}] missing {k}"
            assert r_i["label"] == lbl, f"reply[{i}].label expected {lbl} got {r_i['label']}"
            assert r_i["length"] == lng, f"reply[{i}].length expected {lng} got {r_i['length']}"
            assert isinstance(r_i["reply"], str) and len(r_i["reply"]) > 0
            assert r_i["risk_level"] in ("low", "medium", "high")
        # Counter advanced
        assert d["daily_remaining"] == 4
        state["session_id"] = d["session_id"]
        state["first_reply_text"] = d["replies"][0]["reply"]

    def test_status_after_one_generate(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.get(f"{API}/smart-reply/subscription/status", headers=h, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["daily_used"] == 1
        assert d["daily_remaining"] == 4

    def test_generate_invalid_mode(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.post(f"{API}/smart-reply/generate", headers=h, json={
            "incoming_message": "hi", "mode": "invalid_mode", "desired_tone": "warm",
        }, timeout=15)
        assert r.status_code == 422

    def test_generate_invalid_tone(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.post(f"{API}/smart-reply/generate", headers=h, json={
            "incoming_message": "hi", "mode": "dating", "desired_tone": "rage",
        }, timeout=15)
        assert r.status_code == 422


# -------- History (own sessions only) --------
class TestHistory:
    def test_history_lists_own_sessions(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.get(f"{API}/smart-reply/history", headers=h, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "sessions" in d
        sids = [s["session_id"] for s in d["sessions"]]
        assert state["session_id"] in sids
        # All sessions belong to user1
        for s in d["sessions"]:
            assert s["user_id"] == state["user_id"]

    def test_history_isolation_user2_empty(self, state):
        h = {"Authorization": f"Bearer {state['token2']}"}
        r = requests.get(f"{API}/smart-reply/history", headers=h, timeout=15)
        assert r.status_code == 200
        sids = [s["session_id"] for s in r.json()["sessions"]]
        assert state["session_id"] not in sids


# -------- Favorites --------
class TestFavorites:
    def test_favorite_create(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.post(
            f"{API}/smart-reply/{state['session_id']}/favorite",
            headers=h,
            json={"reply_index": 0, "reply_text": state["first_reply_text"]},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True
        assert "favorite_id" in d and d["favorite_id"].startswith("fav_")
        state["favorite_id"] = d["favorite_id"]

    def test_favorite_session_isolation(self, state):
        # User2 cannot favorite into user1's session
        h2 = {"Authorization": f"Bearer {state['token2']}"}
        r = requests.post(
            f"{API}/smart-reply/{state['session_id']}/favorite",
            headers=h2,
            json={"reply_index": 0, "reply_text": "hax"},
            timeout=15,
        )
        assert r.status_code == 404

    def test_favorites_list(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.get(f"{API}/smart-reply/favorites", headers=h, timeout=15)
        assert r.status_code == 200
        favs = r.json()["favorites"]
        ids = [f["favorite_id"] for f in favs]
        assert state["favorite_id"] in ids
        fav = next(f for f in favs if f["favorite_id"] == state["favorite_id"])
        assert fav["session_id"] == state["session_id"]
        assert fav["reply_index"] == 0
        assert fav["reply_text"] == state["first_reply_text"]

    def test_favorites_user2_empty(self, state):
        h2 = {"Authorization": f"Bearer {state['token2']}"}
        r = requests.get(f"{API}/smart-reply/favorites", headers=h2, timeout=15)
        assert r.status_code == 200
        ids = [f["favorite_id"] for f in r.json()["favorites"]]
        assert state["favorite_id"] not in ids

    def test_favorite_delete(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.delete(f"{API}/smart-reply/favorites/{state['favorite_id']}", headers=h, timeout=15)
        assert r.status_code == 200
        # confirm gone
        r2 = requests.get(f"{API}/smart-reply/favorites", headers=h, timeout=15)
        assert state["favorite_id"] not in [f["favorite_id"] for f in r2.json()["favorites"]]

    def test_favorite_delete_404(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.delete(f"{API}/smart-reply/favorites/fav_nonexistent_xyz", headers=h, timeout=15)
        assert r.status_code == 404


# -------- Usage gate (5/day) --------
# We avoid making 5 fresh LLM calls (~25s+); instead, set counter=5 in DB then verify 6th-call returns 402.
class TestUsageGate:
    def test_seed_counter_to_limit_via_mongo(self, state):
        # Use a NEW user so we don't pollute counters used by other tests.
        email = f"sr_gate_{TS}_{uuid.uuid4().hex[:6]}@example.com"
        rg = requests.post(f"{API}/auth/register", json={"email": email, "password": PWD}, timeout=20)
        assert rg.status_code == 200
        token = rg.json()["session_token"]
        user_id = rg.json()["user"]["user_id"]
        state["gate_token"] = token
        state["gate_user_id"] = user_id

        # Set daily_reply_count=5, daily_reply_day=today directly via mongo
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        from pymongo import MongoClient
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME")
        assert mongo_url and db_name, "MONGO_URL/DB_NAME not set in env"
        client = MongoClient(mongo_url)
        client[db_name].users.update_one(
            {"user_id": user_id},
            {"$set": {"daily_reply_count": 5, "daily_reply_day": today}},
        )
        client.close()

    def test_status_shows_zero_remaining(self, state):
        h = {"Authorization": f"Bearer {state['gate_token']}"}
        r = requests.get(f"{API}/smart-reply/subscription/status", headers=h, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["daily_used"] == 5
        assert d["daily_remaining"] == 0

    def test_sixth_generate_returns_402(self, state):
        h = {"Authorization": f"Bearer {state['gate_token']}"}
        r = requests.post(f"{API}/smart-reply/generate", headers=h, json={
            "incoming_message": "hi there", "mode": "professional", "desired_tone": "calm",
        }, timeout=30)
        assert r.status_code == 402, r.text
        detail = r.json().get("detail")
        assert isinstance(detail, dict)
        assert detail.get("code") == "usage_limit_reached"
        assert detail.get("limit") == 5
        assert detail.get("remaining") == 0


# -------- Analytics tagging --------
class TestAnalytics:
    def test_track_emits_tagged_event(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.post(f"{API}/smart-reply/track", headers=h, json={
            "event_name": "smart_reply_copy_clicked",
            "metadata": {"mode": "dating", "reply_index": 0},
        }, timeout=15)
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_track_rejects_unknown_event(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.post(f"{API}/smart-reply/track", headers=h, json={
            "event_name": "evil_event_name",
        }, timeout=15)
        assert r.status_code == 400

    def test_analytics_collection_tagged_with_variant(self, state):
        # Verify directly in mongo that events for this user are stamped with experience_variant=smart_reply_v1
        from pymongo import MongoClient
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME")
        client = MongoClient(mongo_url)
        events = list(client[db_name].clone_analytics.find({"user_id": state["user_id"]}))
        client.close()
        assert events, "expected analytics events for SR user"
        sr_events = [e for e in events if (e.get("metadata") or {}).get("experience_variant") == "smart_reply_v1"]
        assert sr_events, "no SR events stamped with experience_variant=smart_reply_v1"
        # Generate event must be present
        names = {e["event_name"] for e in sr_events}
        assert "smart_reply_generated" in names
        assert "smart_reply_generate_clicked" in names
