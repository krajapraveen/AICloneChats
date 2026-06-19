"""End-to-end coverage for Sign in with Apple at `/api/auth/apple/*`.

We mock every Apple-controlled endpoint (authorize, token, JWKS) because:
  1. Apple's authorize endpoint requires a real registered Services ID +
     verified domain — impossible to hit from a CI test.
  2. The token endpoint requires a valid ES256-signed client_secret derived
     from a real .p8 — also impossible.
We DO however generate a real RS256-signed id_token with a key we own and
serve it via a fake JWKS, so the signature-verification path is exercised
end-to-end with real cryptography.

Specs we pin here:
  - GET /api/auth/apple/config returns {configured: bool}
  - GET /api/auth/apple/login → 503 when not configured, 302 to apple when
    configured (with state stashed in apple_oauth_states)
  - POST /api/auth/apple/callback → invalid state bounces to /login?error=…
  - The full happy path creates a new user with apple_sub set and email
    set when the token includes one.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ec
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


# ---------------------------------------------------------------------------
# Fixtures: real RSA key for the id_token, real EC key for client_secret.
# ---------------------------------------------------------------------------
def _gen_rsa_pem() -> tuple[bytes, dict]:
    """Return (PEM-private, JWKS-public) for an RS256 keypair."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_numbers = key.public_key().public_numbers()

    def _int_to_b64url(n: int) -> str:
        import base64
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA",
        "kid": "test-rsa-kid",
        "use": "sig",
        "alg": "RS256",
        "n": _int_to_b64url(pub_numbers.n),
        "e": _int_to_b64url(pub_numbers.e),
    }
    return priv_pem, {"keys": [jwk]}


def _gen_ec_pem() -> bytes:
    """ES256 private key for client_secret signing."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


# ---------------------------------------------------------------------------
# Unconfigured behaviour — runs against the live preview server.
# ---------------------------------------------------------------------------
def test_config_endpoint_reports_unconfigured():
    """When APPLE_* env vars are blank, config returns configured=False."""
    r = requests.get(f"{BASE_URL}/api/auth/apple/config", timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert "configured" in body
    # Preview has no APPLE_* values — expect False.
    if all(not os.environ.get(v) for v in [
        "APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_CLIENT_ID",
        "APPLE_PRIVATE_KEY", "APPLE_REDIRECT_URI",
    ]):
        assert body["configured"] is False


def test_login_redirect_503_when_not_configured():
    """/login refuses to issue an Apple redirect when env is incomplete."""
    if any(os.environ.get(v) for v in [
        "APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_CLIENT_ID",
        "APPLE_PRIVATE_KEY", "APPLE_REDIRECT_URI",
    ]):
        pytest.skip("Apple env is configured on this host; skipping unconfigured-case test")
    r = requests.get(f"{BASE_URL}/api/auth/apple/login", timeout=15, allow_redirects=False)
    assert r.status_code == 503


def test_callback_503_when_not_configured():
    """Apple's POST callback also returns 503 when env is incomplete."""
    if any(os.environ.get(v) for v in [
        "APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_CLIENT_ID",
        "APPLE_PRIVATE_KEY", "APPLE_REDIRECT_URI",
    ]):
        pytest.skip("Apple env is configured on this host; skipping unconfigured-case test")
    r = requests.post(
        f"{BASE_URL}/api/auth/apple/callback",
        data={"code": "x", "state": "y"},
        timeout=15,
        allow_redirects=False,
    )
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# In-process tests — import the module directly and patch env + Apple.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def _module_app_client():
    """One TestClient for the whole module — running its lifespan once.

    Each test still gets fresh monkeypatch (function-scoped) for env vars
    and Apple-network mocks; only the FastAPI app's startup/shutdown is
    shared, which is what we want (Motor connection + index creation).
    """
    from fastapi.testclient import TestClient
    from server import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def app_client(_module_app_client, apple_env):
    """Per-test app client view — patches apple_env first, then yields the
    shared TestClient instance.
    """
    return _module_app_client


@pytest.fixture
def apple_env(monkeypatch):
    """Spin up valid-looking Apple env vars for in-proc tests.

    NOTE: `auth_apple` reads its env vars at module IMPORT time. Reloading
    the module would create a NEW router instance that the already-mounted
    FastAPI app doesn't know about. So instead we patch the existing
    module-level globals on the LIVE module. The route handlers reference
    these via `apple_env.MODULE.X`, which sees our patched values.
    """
    ec_pem = _gen_ec_pem()
    import auth_apple
    monkeypatch.setattr(auth_apple, "APPLE_TEAM_ID", "TEAMTEST00")
    monkeypatch.setattr(auth_apple, "APPLE_KEY_ID", "KEYTEST123")
    monkeypatch.setattr(auth_apple, "APPLE_CLIENT_ID", "com.test.aiclonechats.signin")
    monkeypatch.setattr(auth_apple, "APPLE_PRIVATE_KEY", ec_pem.decode())
    monkeypatch.setattr(auth_apple, "APPLE_REDIRECT_URI", "https://aiclonechats.com/api/auth/apple/callback")
    monkeypatch.setattr(auth_apple, "APPLE_POST_AUTH_REDIRECT", "https://aiclonechats.com/")
    # Reset the cached JWKS client so per-test patches stick.
    monkeypatch.setattr(auth_apple, "_jwks_client", None)
    return auth_apple


def test_is_configured_true_when_env_set(apple_env):
    assert apple_env.is_configured() is True


def test_client_secret_jwt_structure(apple_env):
    """The signed client_secret must carry Apple's expected claims."""
    secret = apple_env._generate_client_secret()
    # Decode WITHOUT verification — we just want to inspect the payload.
    payload = jwt.decode(secret, options={"verify_signature": False})
    headers = jwt.get_unverified_header(secret)
    assert headers["alg"] == "ES256"
    assert headers["kid"] == "KEYTEST123"
    assert payload["iss"] == "TEAMTEST00"
    assert payload["sub"] == "com.test.aiclonechats.signin"
    assert payload["aud"] == "https://appleid.apple.com"
    assert payload["exp"] > payload["iat"]
    assert payload["exp"] - payload["iat"] <= 6 * 60 * 60 + 5  # 6h max


def test_callback_invalid_state_redirects_to_login_error(app_client):
    """A POST with a state we never issued must redirect to /login?error=…"""
    r = app_client.post(
        "/api/auth/apple/callback",
        data={"code": "ignored", "state": "never-issued-state"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "/login?error=apple_state_invalid" in r.headers["location"]


def test_login_creates_state_and_redirects_to_apple(app_client):
    """The /login endpoint must stash a state row and 302 to Apple."""
    r = app_client.get("/api/auth/apple/login?next=/voice", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("https://appleid.apple.com/auth/authorize?")
    assert "client_id=com.test.aiclonechats.signin" in loc
    assert "response_type=code+id_token" in loc or "response_type=code%20id_token" in loc
    assert "response_mode=form_post" in loc
    assert "scope=name+email" in loc or "scope=name%20email" in loc

    # And a state row was inserted with the right `next` payload.
    state_from_loc = None
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(loc).query)
    state_from_loc = qs.get("state", [None])[0]
    assert state_from_loc

    async def _check():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        return await d.apple_oauth_states.find_one({"state": state_from_loc})
    row = asyncio.new_event_loop().run_until_complete(_check())
    assert row is not None
    assert row["state"] == state_from_loc
    assert row["nonce"]
    assert row["next"] == "/voice"


def test_callback_happy_path_creates_user(apple_env, app_client, monkeypatch):
    """The whole id_token verification + user-create path, with Apple mocked."""
    # Generate a real RS256 keypair + a fake JWKS we can serve.
    priv_pem, jwks_doc = _gen_rsa_pem()
    apple_sub = f"apple_{uuid.uuid4().hex[:10]}"
    email = f"happy_{uuid.uuid4().hex[:6]}@privaterelay.appleid.com"
    nonce_holder = {}

    # Patch PyJWKClient.get_signing_key_from_jwt to return OUR public key.
    class _FakeSigningKey:
        def __init__(self, k): self.key = k
    pub_key = serialization.load_pem_private_key(priv_pem, password=None).public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_key_obj = serialization.load_pem_public_key(pub_key)

    class _FakeJWKS:
        def get_signing_key_from_jwt(self, _token):
            return _FakeSigningKey(pub_key_obj)
    monkeypatch.setattr(apple_env, "_jwks", lambda: _FakeJWKS())

    # Patch httpx.AsyncClient.post to mock Apple's token endpoint.
    class _FakeResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)
        def json(self):
            return self._payload

    def _build_id_token():
        now = int(time.time())
        body = {
            "iss": "https://appleid.apple.com",
            "aud": "com.test.aiclonechats.signin",
            "sub": apple_sub,
            "iat": now,
            "exp": now + 600,
            "nonce": nonce_holder.get("nonce", ""),
            "email": email,
            "email_verified": "true",
            "is_private_email": "true",
        }
        return jwt.encode(body, priv_pem, algorithm="RS256",
                          headers={"kid": "test-rsa-kid"})

    async def _fake_post(self, url, data=None, headers=None):
        return _FakeResp(200, {
            "access_token": "fake_at",
            "id_token": _build_id_token(),
            "token_type": "bearer",
        })
    monkeypatch.setattr("httpx.AsyncClient.post", _fake_post)

    client = app_client
    # Step 1: /login to get a valid state+nonce.
    r1 = client.get("/api/auth/apple/login?next=/dashboard", follow_redirects=False)
    assert r1.status_code == 302

    # Pull the most recent state row.
    async def _latest_state():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        return await d.apple_oauth_states.find_one(
            {"next": "/dashboard"}, sort=[("created_at", -1)]
        )
    row = asyncio.new_event_loop().run_until_complete(_latest_state())
    assert row, "state row missing"
    state = row["state"]
    nonce_holder["nonce"] = row["nonce"]

    # Step 2: simulate Apple's form_post callback.
    r2 = client.post(
        "/api/auth/apple/callback",
        data={
            "code": "fake_authz_code",
            "state": state,
            "user": json.dumps({"name": {"firstName": "Ada", "lastName": "Lovelace"}, "email": email}),
        },
        follow_redirects=False,
    )
    assert r2.status_code == 302, r2.text
    assert "aiclonechats.com" in r2.headers["location"]
    assert "/dashboard" in r2.headers["location"]

    # Verify the user was created with apple_sub set.
    async def _find_user():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        return await d.users.find_one({"apple_sub": apple_sub}, {"_id": 0})
    u = asyncio.new_event_loop().run_until_complete(_find_user())
    assert u is not None
    assert u["apple_sub"] == apple_sub
    assert u["email"] == email
    assert u["auth_provider"] == "apple"
    assert u["plan_id"] == "free"
    assert u["credits_balance"] == 0  # strict 0-credit policy
    assert u.get("email_verified") is True
    assert u.get("apple_is_private_email") is True
    # Confirm a session was minted.
    assert "session_token" in r2.cookies or r2.cookies  # cookie carrier


def test_callback_nonce_mismatch_rejected(apple_env, app_client, monkeypatch):
    """Replay defense: a valid signature but wrong nonce must be rejected."""
    from server import app  # noqa: F401
    priv_pem, _ = _gen_rsa_pem()
    apple_sub = f"apple_{uuid.uuid4().hex[:10]}"

    pub = serialization.load_pem_private_key(priv_pem, password=None).public_key()

    class _FakeKey:
        key = pub

    class _FakeJWKS:
        def get_signing_key_from_jwt(self, _token):
            return _FakeKey()
    monkeypatch.setattr(apple_env, "_jwks", lambda: _FakeJWKS())

    def _bad_nonce_token():
        now = int(time.time())
        body = {
            "iss": "https://appleid.apple.com",
            "aud": "com.test.aiclonechats.signin",
            "sub": apple_sub,
            "iat": now,
            "exp": now + 600,
            "nonce": "wrong-nonce-value",
            "email": "x@x.com",
            "email_verified": "true",
        }
        return jwt.encode(body, priv_pem, algorithm="RS256", headers={"kid": "rsa-x"})

    class _R:
        status_code = 200
        text = "{}"
        def json(self): return {"id_token": _bad_nonce_token()}

    async def _post(self, url, data=None, headers=None):
        return _R()
    monkeypatch.setattr("httpx.AsyncClient.post", _post)

    client = app_client
    r1 = client.get("/api/auth/apple/login", follow_redirects=False)
    assert r1.status_code == 302

    async def _latest_state():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        return await d.apple_oauth_states.find_one(sort=[("created_at", -1)])
    row = asyncio.new_event_loop().run_until_complete(_latest_state())
    state = row["state"]
    r2 = client.post(
        "/api/auth/apple/callback",
        data={"code": "x", "state": state},
        follow_redirects=False,
    )
    assert r2.status_code == 302
    assert "/login?error=apple_nonce_mismatch" in r2.headers["location"]


def test_callback_links_existing_user_by_email(apple_env, app_client, monkeypatch):
    """A user who first registered with email-password gets linked when their
    Apple-shared email matches an existing account."""
    # Pre-create a vanilla email/password user.
    email = f"existing_{uuid.uuid4().hex[:6]}@example.com"
    pre_uid = f"user_{uuid.uuid4().hex[:12]}"

    async def _seed():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        await d.users.insert_one({
            "user_id": pre_uid,
            "email": email,
            "auth_provider": "email",
            "role": "user",
            "credits_balance": 0,
            "plan_id": "free",
            "plan_status": "pending_subscription",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    asyncio.new_event_loop().run_until_complete(_seed())

    priv_pem, _ = _gen_rsa_pem()
    apple_sub = f"apple_{uuid.uuid4().hex[:10]}"
    pub = serialization.load_pem_private_key(priv_pem, password=None).public_key()

    class _FakeKey:
        key = pub

    class _FakeJWKS:
        def get_signing_key_from_jwt(self, _token):
            return _FakeKey()
    monkeypatch.setattr(apple_env, "_jwks", lambda: _FakeJWKS())
    nonce_holder = {}

    def _tok():
        now = int(time.time())
        return jwt.encode({
            "iss": "https://appleid.apple.com",
            "aud": "com.test.aiclonechats.signin",
            "sub": apple_sub,
            "iat": now, "exp": now + 600,
            "nonce": nonce_holder.get("nonce", ""),
            "email": email,
            "email_verified": "true",
        }, priv_pem, algorithm="RS256", headers={"kid": "rsa-link"})

    class _R:
        status_code = 200
        text = "{}"
        def json(self): return {"id_token": _tok()}

    async def _post(self, url, data=None, headers=None):
        return _R()
    monkeypatch.setattr("httpx.AsyncClient.post", _post)

    client = app_client
    client.get("/api/auth/apple/login", follow_redirects=False)

    async def _latest_state():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        return await d.apple_oauth_states.find_one(sort=[("created_at", -1)])
    row = asyncio.new_event_loop().run_until_complete(_latest_state())
    state = row["state"]
    nonce_holder["nonce"] = row["nonce"]

    r2 = client.post(
        "/api/auth/apple/callback",
        data={"code": "x", "state": state},
        follow_redirects=False,
    )
    assert r2.status_code == 302, r2.text

    # The PRE-EXISTING user should now have apple_sub linked.
    async def _check():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        return await d.users.find_one({"user_id": pre_uid}, {"_id": 0})
    u = asyncio.new_event_loop().run_until_complete(_check())
    assert u is not None
    assert u["apple_sub"] == apple_sub
    assert u["email"] == email
    # No duplicate user was created.
    async def _count():
        c = AsyncIOMotorClient(MONGO_URL)
        d = c[DB_NAME]
        return await d.users.count_documents({"email": email})
    n = asyncio.new_event_loop().run_until_complete(_count())
    assert n == 1
