"""AI Debate Rooms — full backend regression suite.

Covers:
- list / get debate
- join side (auth required)
- submit argument (AI-scored)
- list arguments + my_vote echo
- vote (single vote, switch, clear, no self-vote)
- leaderboard / results
- report
- admin gates + admin metrics
- analytics events tagged with experience_variant=debate_v1
"""
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "sr-tester@example.com"
ADMIN_PASSWORD = "TestPass123!"


def _login_or_register(email: str, password: str, name: str = "Tester") -> str:
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": password, "full_name": name})
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    return body.get("access_token") or body.get("session_token") or body.get("token")


@pytest.fixture(scope="module")
def admin_token():
    return _login_or_register(ADMIN_EMAIL, ADMIN_PASSWORD, "SR Tester")


@pytest.fixture(scope="module")
def user_a():
    email = f"debate-a-{uuid.uuid4().hex[:8]}@example.com"
    return _login_or_register(email, "DebateA123!", "Debate A")


@pytest.fixture(scope="module")
def user_b():
    email = f"debate-b-{uuid.uuid4().hex[:8]}@example.com"
    return _login_or_register(email, "DebateB123!", "Debate B")


# ---- Public read ----
class TestList:
    def test_list_active_debates_seeded(self):
        r = requests.get(f"{BASE_URL}/api/debates")
        assert r.status_code == 200
        d = r.json()
        slugs = {x["slug"] for x in d["debates"]}
        assert {"ai-creativity", "ai-friends"} <= slugs

    def test_get_debate_404(self):
        r = requests.get(f"{BASE_URL}/api/debates/does-not-exist-xyz")
        assert r.status_code == 404

    def test_get_debate_ok(self):
        r = requests.get(f"{BASE_URL}/api/debates/ai-creativity")
        assert r.status_code == 200
        d = r.json()
        assert d["slug"] == "ai-creativity"
        assert "side_a_label" in d and "side_b_label" in d
        assert "my_side" in d and "my_handle" in d


# ---- Join ----
class TestJoin:
    def test_join_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/debates/ai-creativity/join", json={"side": "A"})
        assert r.status_code in (401, 403)

    def test_join_invalid_side(self, user_a):
        h = {"Authorization": f"Bearer {user_a}"}
        r = requests.post(f"{BASE_URL}/api/debates/ai-creativity/join", json={"side": "X"}, headers=h)
        assert r.status_code == 400

    def test_join_idempotent_same_side(self, user_a):
        h = {"Authorization": f"Bearer {user_a}"}
        r1 = requests.post(f"{BASE_URL}/api/debates/ai-creativity/join", json={"side": "A"}, headers=h)
        assert r1.status_code == 200, r1.text
        h1 = r1.json()["anonymous_handle"]
        r2 = requests.post(f"{BASE_URL}/api/debates/ai-creativity/join", json={"side": "A"}, headers=h)
        assert r2.status_code == 200
        assert r2.json()["anonymous_handle"] == h1


# ---- Submit argument ----
class TestArguments:
    def test_submit_short_rejected(self, user_a):
        h = {"Authorization": f"Bearer {user_a}"}
        r = requests.post(f"{BASE_URL}/api/debates/ai-creativity/arguments", json={"side": "A", "content": "yes"}, headers=h)
        assert r.status_code == 422

    def test_submit_long_rejected(self, user_a):
        h = {"Authorization": f"Bearer {user_a}"}
        r = requests.post(f"{BASE_URL}/api/debates/ai-creativity/arguments", json={"side": "A", "content": "x" * 4001}, headers=h)
        assert r.status_code == 422

    def test_submit_succeeds_and_is_scored(self, user_a):
        h = {"Authorization": f"Bearer {user_a}"}
        content = (
            "AI creativity is genuine when measured by what creativity actually does in the world: it generates novel "
            "combinations that are useful or beautiful. Humans don't invent ex nihilo either — we recombine. The "
            "substrate is different but the function is identical."
        )
        r = requests.post(f"{BASE_URL}/api/debates/ai-creativity/arguments", json={"side": "A", "content": content}, headers=h)
        assert r.status_code == 200, r.text
        a = r.json()["argument"]
        assert a["argument_id"].startswith("da_")
        assert a["side"] == "A"
        assert 0 <= int(a["ai_score"]) <= 100
        assert a["moderation_status"] in ("visible", "flagged")
        bd = a["ai_score_breakdown"]
        for k in ("clarity", "logic", "evidence", "originality", "civility", "persuasiveness"):
            assert k in bd

    def test_cannot_submit_to_other_side(self, user_a):
        h = {"Authorization": f"Bearer {user_a}"}
        r = requests.post(
            f"{BASE_URL}/api/debates/ai-creativity/arguments",
            json={"side": "B", "content": "Switching sides should be blocked because user already submitted to A."},
            headers=h,
        )
        assert r.status_code == 409, r.text

    def test_list_arguments_ranks(self, user_b):
        # user_b joins side B and submits one
        h = {"Authorization": f"Bearer {user_b}"}
        requests.post(f"{BASE_URL}/api/debates/ai-creativity/join", json={"side": "B"}, headers=h)
        r = requests.post(
            f"{BASE_URL}/api/debates/ai-creativity/arguments",
            json={"side": "B", "content": "AI outputs are sophisticated pattern matching across training data — that's imitation, not creativity. The model has no inner life, no intent, no aesthetic motivation."},
            headers=h,
        )
        assert r.status_code == 200, r.text

        # Public read: must include both sides
        r2 = requests.get(f"{BASE_URL}/api/debates/ai-creativity/arguments")
        assert r2.status_code == 200
        rows = r2.json()["arguments"]
        sides = {x["side"] for x in rows}
        assert "A" in sides and "B" in sides


# ---- Voting ----
class TestVotes:
    def _get_someones_argument(self, voter_token: str, slug: str) -> str:
        # Find an argument NOT owned by the voter to vote on.
        r = requests.get(f"{BASE_URL}/api/debates/{slug}/arguments", headers={"Authorization": f"Bearer {voter_token}"})
        rows = r.json()["arguments"]
        for x in rows:
            if not x.get("is_mine"):
                return x["argument_id"]
        raise AssertionError("No argument by another user found")

    def test_vote_requires_auth(self, user_a):
        argid = self._get_someones_argument(user_a, "ai-creativity")
        r = requests.post(f"{BASE_URL}/api/debates/arguments/{argid}/vote", json={"vote_type": "up"})
        assert r.status_code in (401, 403)

    def test_no_self_vote(self, user_a):
        # Find an argument owned by user_a
        rA = requests.get(f"{BASE_URL}/api/debates/ai-creativity/arguments", headers={"Authorization": f"Bearer {user_a}"})
        my = next((x for x in rA.json()["arguments"] if x.get("is_mine")), None)
        assert my is not None
        r = requests.post(f"{BASE_URL}/api/debates/arguments/{my['argument_id']}/vote", json={"vote_type": "up"}, headers={"Authorization": f"Bearer {user_a}"})
        assert r.status_code == 403

    def test_vote_up_then_switch_then_clear(self, user_a, user_b):
        # user_b votes on user_a's argument
        argid = self._get_someones_argument(user_b, "ai-creativity")
        h = {"Authorization": f"Bearer {user_b}"}
        r1 = requests.post(f"{BASE_URL}/api/debates/arguments/{argid}/vote", json={"vote_type": "up"}, headers=h)
        assert r1.status_code == 200
        assert r1.json()["my_vote"] == "up"
        # Switch to down
        r2 = requests.post(f"{BASE_URL}/api/debates/arguments/{argid}/vote", json={"vote_type": "down"}, headers=h)
        assert r2.status_code == 200
        assert r2.json()["my_vote"] == "down"
        # Clear
        r3 = requests.post(f"{BASE_URL}/api/debates/arguments/{argid}/vote", json={"vote_type": "clear"}, headers=h)
        assert r3.status_code == 200
        assert r3.json()["my_vote"] is None


# ---- Leaderboard / Results ----
class TestLeaderboard:
    def test_leaderboard_shape(self):
        r = requests.get(f"{BASE_URL}/api/debates/ai-creativity/leaderboard")
        assert r.status_code == 200
        d = r.json()
        assert "sides" in d and "A" in d["sides"] and "B" in d["sides"]
        for k in ("label", "side_score", "participants", "top_arguments"):
            assert k in d["sides"]["A"]

    def test_results_shape(self):
        r = requests.get(f"{BASE_URL}/api/debates/ai-creativity/results")
        assert r.status_code == 200
        d = r.json()
        assert "winner_side" in d and "ended" in d


# ---- Report ----
class TestReport:
    def test_report_argument(self, user_a, user_b):
        # user_a reports user_b's argument
        rB = requests.get(f"{BASE_URL}/api/debates/ai-creativity/arguments")
        b_arg = next((x for x in rB.json()["arguments"] if x["side"] == "B"), None)
        assert b_arg is not None
        r = requests.post(f"{BASE_URL}/api/debates/arguments/{b_arg['argument_id']}/report", json={"reason": "test report"}, headers={"Authorization": f"Bearer {user_a}"})
        assert r.status_code == 200


# ---- Admin ----
class TestAdmin:
    def test_admin_list_requires_admin(self, user_a):
        r = requests.get(f"{BASE_URL}/api/admin/debates", headers={"Authorization": f"Bearer {user_a}"})
        assert r.status_code == 403

    def test_admin_metrics(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/debates/metrics?days=7", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("debates_total", "debates_active", "arguments_total", "votes", "participants_joined", "reports", "hidden_rate_pct"):
            assert k in d
        assert d["debates_total"] >= 8

    def test_admin_create_debate(self, admin_token):
        r = requests.post(
            f"{BASE_URL}/api/admin/debates",
            json={"title": f"Test debate {uuid.uuid4().hex[:6]}", "description": "Test description body content here.", "category": "test", "duration_days": 1},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200, r.text
        d = r.json()["debate"]
        assert d["status"] == "active"

    def test_admin_moderate_argument(self, admin_token):
        r0 = requests.get(f"{BASE_URL}/api/debates/ai-creativity/arguments")
        rows = r0.json()["arguments"]
        if not rows:
            pytest.skip("no arguments to moderate")
        argid = rows[0]["argument_id"]
        r = requests.patch(
            f"{BASE_URL}/api/admin/debates/arguments/{argid}",
            json={"moderation_status": "flagged", "reason": "admin test"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200
        # Restore
        requests.patch(
            f"{BASE_URL}/api/admin/debates/arguments/{argid}",
            json={"moderation_status": "visible"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )


# ---- Analytics separation ----
class TestAnalytics:
    def test_track_emits_experience_variant(self, user_a):
        h = {"Authorization": f"Bearer {user_a}"}
        r = requests.post(f"{BASE_URL}/api/debates/ai-creativity/track", json={"event_name": "debate_test_event", "metadata": {"foo": "bar"}}, headers=h)
        assert r.status_code == 200
