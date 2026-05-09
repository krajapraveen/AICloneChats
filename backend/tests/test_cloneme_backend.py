"""CloneMe AI backend e2e tests covering auth, clones, memories, chat, storage."""
import os
import io
import time
import uuid
import struct
import zlib
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

TS = int(time.time())
EMAIL = f"e2e_{TS}_{uuid.uuid4().hex[:6]}@example.com"
EMAIL2 = f"e2e_{TS}_{uuid.uuid4().hex[:6]}@example.com"
PWD = "TestPass123!"
SLUG = f"e2e-clone-{TS}-{uuid.uuid4().hex[:4]}"


def _png_bytes() -> bytes:
    """Generate a minimal valid 1x1 PNG."""
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = b"\x00" + b"\xff\x00\x00"
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


@pytest.fixture(scope="module")
def state():
    return {}


# ---------------- Health ----------------
class TestHealth:
    def test_health(self):
        r = requests.get(f"{API}/health", timeout=15)
        assert r.status_code == 200
        assert r.json().get("ok") is True


# ---------------- Auth ----------------
class TestAuth:
    def test_register(self, state):
        r = requests.post(f"{API}/auth/register", json={"email": EMAIL, "password": PWD, "name": "E2E User"}, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "session_token" in data and len(data["session_token"]) > 10
        assert data["user"]["email"] == EMAIL.lower()
        assert "password_hash" not in data["user"]
        state["token"] = data["session_token"]
        state["user_id"] = data["user"]["user_id"]

    def test_register_duplicate(self):
        r = requests.post(f"{API}/auth/register", json={"email": EMAIL, "password": PWD}, timeout=20)
        assert r.status_code == 400

    def test_login_wrong_pw(self):
        r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": "WrongPass!"}, timeout=20)
        assert r.status_code == 401

    def test_login_correct(self, state):
        r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PWD}, timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert d["user"]["email"] == EMAIL.lower()
        assert d["session_token"]
        state["token"] = d["session_token"]

    def test_me_with_bearer(self, state):
        r = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {state['token']}"}, timeout=15)
        assert r.status_code == 200
        assert r.json()["email"] == EMAIL.lower()

    def test_me_no_auth(self):
        r = requests.get(f"{API}/auth/me", timeout=15)
        assert r.status_code == 401

    def test_register_user2(self, state):
        r = requests.post(f"{API}/auth/register", json={"email": EMAIL2, "password": PWD, "name": "Other"}, timeout=20)
        assert r.status_code == 200
        state["token2"] = r.json()["session_token"]
        state["user_id2"] = r.json()["user"]["user_id"]


# ---------------- Clones ----------------
class TestClones:
    def test_check_slug_available(self):
        r = requests.get(f"{API}/clones/check-slug/{SLUG}", timeout=15)
        assert r.status_code == 200
        assert r.json()["available"] is True

    def test_check_slug_reserved(self):
        for slug in ["api", "login", "dashboard", "new"]:
            r = requests.get(f"{API}/clones/check-slug/{slug}", timeout=15)
            assert r.status_code == 200
            assert r.json()["available"] is False

    def test_create_clone(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        body = {
            "slug": SLUG,
            "display_name": "Test Twin",
            "bio": "An e2e test AI clone",
            "visibility": "public",
            "personality": {"tone": "friendly", "humor_level": 7, "reply_length": "short"},
        }
        r = requests.post(f"{API}/clones", json=body, headers=h, timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["slug"] == SLUG
        assert d["display_name"] == "Test Twin"
        assert d["personality"]["humor_level"] == 7
        state["clone_id"] = d["clone_id"]

    def test_create_clone_reserved_slug(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.post(f"{API}/clones", json={"slug": "api", "display_name": "X"}, headers=h, timeout=20)
        assert r.status_code == 400

    def test_create_clone_duplicate_slug(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.post(f"{API}/clones", json={"slug": SLUG, "display_name": "Dup"}, headers=h, timeout=20)
        assert r.status_code == 400

    def test_create_clone_no_auth(self):
        r = requests.post(f"{API}/clones", json={"slug": "noauth-clone", "display_name": "X"}, timeout=15)
        assert r.status_code == 401

    def test_list_my_clones(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.get(f"{API}/clones/mine", headers=h, timeout=15)
        assert r.status_code == 200
        clones = r.json()
        assert any(c["clone_id"] == state["clone_id"] for c in clones)

    def test_list_other_user_clones_isolation(self, state):
        h = {"Authorization": f"Bearer {state['token2']}"}
        r = requests.get(f"{API}/clones/mine", headers=h, timeout=15)
        assert r.status_code == 200
        assert all(c["clone_id"] != state["clone_id"] for c in r.json())

    def test_get_by_slug_public_no_auth(self):
        r = requests.get(f"{API}/clones/by-slug/{SLUG}", timeout=15)
        assert r.status_code == 200
        assert r.json()["slug"] == SLUG

    def test_check_slug_after_taken(self):
        r = requests.get(f"{API}/clones/check-slug/{SLUG}", timeout=15)
        assert r.json()["available"] is False

    def test_patch_clone(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        r = requests.patch(
            f"{API}/clones/{state['clone_id']}",
            headers=h,
            json={"bio": "Updated bio", "personality": {"warmth": 9}},
            timeout=20,
        )
        assert r.status_code == 200
        d = r.json()
        assert d["bio"] == "Updated bio"
        assert d["personality"]["warmth"] == 9
        assert d["personality"]["humor_level"] == 7  # preserved

    def test_patch_clone_other_user_forbidden(self, state):
        h = {"Authorization": f"Bearer {state['token2']}"}
        r = requests.patch(f"{API}/clones/{state['clone_id']}", headers=h, json={"bio": "hax"}, timeout=15)
        assert r.status_code == 404  # not found for non-owner

    def test_private_clone_blocks_non_owner(self, state):
        # Create a private clone
        h = {"Authorization": f"Bearer {state['token']}"}
        priv_slug = f"priv-{TS}-{uuid.uuid4().hex[:4]}"
        r = requests.post(
            f"{API}/clones",
            headers=h,
            json={"slug": priv_slug, "display_name": "Private", "visibility": "private"},
            timeout=15,
        )
        assert r.status_code == 200
        state["priv_clone_id"] = r.json()["clone_id"]
        state["priv_slug"] = priv_slug
        # No auth -> 403
        r2 = requests.get(f"{API}/clones/by-slug/{priv_slug}", timeout=15)
        assert r2.status_code == 403
        # Other user -> 403
        h2 = {"Authorization": f"Bearer {state['token2']}"}
        r3 = requests.get(f"{API}/clones/by-slug/{priv_slug}", headers=h2, timeout=15)
        assert r3.status_code == 403
        # Owner -> 200
        r4 = requests.get(f"{API}/clones/by-slug/{priv_slug}", headers=h, timeout=15)
        assert r4.status_code == 200


# ---------------- Memories ----------------
class TestMemories:
    def test_create_list_update_delete(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        cid = state["clone_id"]
        # CREATE
        r = requests.post(
            f"{API}/clones/{cid}/memories",
            headers=h,
            json={"content": "I love rock climbing on weekends", "memory_type": "preference", "importance": 0.9},
            timeout=15,
        )
        assert r.status_code == 200
        mem = r.json()
        assert mem["content"] == "I love rock climbing on weekends"
        state["memory_id"] = mem["memory_id"]
        # LIST
        r = requests.get(f"{API}/clones/{cid}/memories", headers=h, timeout=15)
        assert r.status_code == 200
        assert any(m["memory_id"] == state["memory_id"] for m in r.json())
        # UPDATE
        r = requests.patch(
            f"{API}/clones/{cid}/memories/{state['memory_id']}",
            headers=h,
            json={"content": "Updated memory", "can_use_for_reply": False},
            timeout=15,
        )
        assert r.status_code == 200
        assert r.json()["content"] == "Updated memory"
        # DELETE
        r = requests.delete(f"{API}/clones/{cid}/memories/{state['memory_id']}", headers=h, timeout=15)
        assert r.status_code == 200
        # Re-add for chat tests
        r = requests.post(
            f"{API}/clones/{cid}/memories",
            headers=h,
            json={"content": "I love rock climbing on weekends", "memory_type": "preference", "importance": 0.9},
            timeout=15,
        )
        assert r.status_code == 200

    def test_memory_owner_only(self, state):
        h = {"Authorization": f"Bearer {state['token2']}"}
        r = requests.get(f"{API}/clones/{state['clone_id']}/memories", headers=h, timeout=15)
        assert r.status_code == 404


# ---------------- Chat ----------------
class TestChat:
    def test_chat_public_first_message(self, state):
        body = {"message": "Hi! What do you like to do on weekends?", "visitor_id": "v_e2e_1", "visitor_name": "Tester"}
        r = requests.post(f"{API}/clones/{SLUG}/chat", json=body, timeout=90)
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d["reply"], str) and len(d["reply"]) > 0
        assert d["conversation_id"]
        # Should not be the LLM-not-configured / error fallback
        assert "LLM is not configured" not in d["reply"]
        assert not d["reply"].startswith("(I hit a snag")
        state["conv_id"] = d["conversation_id"]

    def test_chat_followup_uses_conversation(self, state):
        body = {
            "message": "Who are you exactly? Are you the real person?",
            "visitor_id": "v_e2e_1",
            "conversation_id": state["conv_id"],
        }
        r = requests.post(f"{API}/clones/{SLUG}/chat", json=body, timeout=90)
        assert r.status_code == 200
        d = r.json()
        assert d["conversation_id"] == state["conv_id"]
        # Reply must indicate AI clone (not real person)
        low = d["reply"].lower()
        assert any(k in low for k in ["ai", "clone", "not the real", "not a real"]), f"Unexpected reply: {d['reply']}"

    def test_messages_history(self, state):
        r = requests.get(f"{API}/clones/{SLUG}/conversations/{state['conv_id']}/messages", timeout=20)
        assert r.status_code == 200
        msgs = r.json()
        # at least 4 (2 visitor + 2 clone)
        assert len(msgs) >= 4
        # ascending order
        ts = [m["created_at"] for m in msgs]
        assert ts == sorted(ts)
        # alternating senders
        assert msgs[0]["sender"] == "visitor"

    def test_chat_private_clone_blocked(self, state):
        body = {"message": "hi", "visitor_id": "v_x"}
        r = requests.post(f"{API}/clones/{state['priv_slug']}/chat", json=body, timeout=20)
        assert r.status_code == 403

    def test_chat_unknown_slug(self):
        body = {"message": "hi", "visitor_id": "v_x"}
        r = requests.post(f"{API}/clones/nope-{TS}/chat", json=body, timeout=20)
        assert r.status_code == 404


# ---------------- Storage ----------------
class TestStorage:
    def test_upload_and_serve(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        png = _png_bytes()
        files = {"file": ("avatar.png", io.BytesIO(png), "image/png")}
        r = requests.post(f"{API}/storage/upload-avatar", headers=h, files=files, timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["avatar_url"].startswith("/api/storage/files/")
        # Fetch
        url = f"{BASE_URL}{d['avatar_url']}"
        r2 = requests.get(url, timeout=30)
        assert r2.status_code == 200
        assert r2.headers.get("Content-Type", "").startswith("image/")
        assert len(r2.content) > 0

    def test_upload_no_auth(self):
        png = _png_bytes()
        files = {"file": ("a.png", io.BytesIO(png), "image/png")}
        r = requests.post(f"{API}/storage/upload-avatar", files=files, timeout=30)
        assert r.status_code == 401

    def test_upload_bad_type(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        files = {"file": ("bad.txt", io.BytesIO(b"hello"), "text/plain")}
        r = requests.post(f"{API}/storage/upload-avatar", headers=h, files=files, timeout=30)
        assert r.status_code == 400


# ---------------- Cleanup / Cascade ----------------
class TestZCleanup:
    def test_delete_clone_cascade(self, state):
        h = {"Authorization": f"Bearer {state['token']}"}
        cid = state["clone_id"]
        r = requests.delete(f"{API}/clones/{cid}", headers=h, timeout=20)
        assert r.status_code == 200
        # Verify gone
        r2 = requests.get(f"{API}/clones/by-slug/{SLUG}", timeout=15)
        assert r2.status_code == 404
        # Memories endpoint -> 404 (clone gone)
        r3 = requests.get(f"{API}/clones/{cid}/memories", headers=h, timeout=15)
        assert r3.status_code == 404
        # Delete private clone too
        if state.get("priv_clone_id"):
            requests.delete(f"{API}/clones/{state['priv_clone_id']}", headers=h, timeout=15)

    def test_logout(self, state):
        # Logout user2
        h = {"Authorization": f"Bearer {state['token2']}"}
        r = requests.post(f"{API}/auth/logout", headers=h, timeout=15)
        assert r.status_code == 200
