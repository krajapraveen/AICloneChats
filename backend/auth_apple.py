"""
Sign in with Apple — server-side OAuth flow.

Apple's web Sign-in uses OAuth 2.0 / OpenID Connect with one major quirk:
the `client_secret` is a short-lived ES256-signed JWT (not a static string)
derived from a .p8 private key you generate in the Apple Developer console.

Flow:
  1. Frontend calls `GET /api/auth/apple/login` → we 302 to Apple's authorize
     endpoint with state + nonce stashed in an HttpOnly cookie.
  2. User signs in on Apple → Apple POSTs (form-encoded) back to our
     `POST /api/auth/apple/callback` with {code, state, id_token, user?}.
  3. We verify state, exchange code+ES256 JWT for tokens, verify the returned
     id_token against Apple's JWKS, match/create the user, mint our session,
     and 302 to the SPA.

Matching strategy: primary by Apple `sub`, fallback to email (because Apple
shares email only on first sign-in; many users pick "Hide My Email" and
get a stable `@privaterelay.appleid.com` address).

REMINDER: Apple does NOT accept preview/wildcard domains as redirect URIs.
The frontend hides the button on non-production hosts; the backend
additionally refuses if no APPLE_* env vars are configured.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
import uuid
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jwt import PyJWKClient

from db import db
from auth import create_session, set_session_cookie
from models import now_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth/apple", tags=["auth-apple"])

# ----- env -----
APPLE_TEAM_ID = os.environ.get("APPLE_TEAM_ID", "")
APPLE_KEY_ID = os.environ.get("APPLE_KEY_ID", "")
APPLE_CLIENT_ID = os.environ.get("APPLE_CLIENT_ID", "")      # Services ID, e.g. com.aiclonechats.signin
APPLE_PRIVATE_KEY = os.environ.get("APPLE_PRIVATE_KEY", "")  # full .p8 contents (PEM)
APPLE_REDIRECT_URI = os.environ.get("APPLE_REDIRECT_URI", "")  # must EXACTLY match what's registered
APPLE_POST_AUTH_REDIRECT = os.environ.get("APPLE_POST_AUTH_REDIRECT", "https://aiclonechats.com/")

APPLE_AUTH_URL = "https://appleid.apple.com/auth/authorize"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"

# Single shared JWKS client — fetches+caches Apple's public keys.
_jwks_client: Optional[PyJWKClient] = None


def _jwks() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(APPLE_JWKS_URL)
    return _jwks_client


def is_configured() -> bool:
    """True when ALL five Apple env vars are present."""
    return bool(
        APPLE_TEAM_ID
        and APPLE_KEY_ID
        and APPLE_CLIENT_ID
        and APPLE_PRIVATE_KEY
        and APPLE_REDIRECT_URI
    )


def _generate_client_secret() -> str:
    """Build the ES256-signed JWT that Apple wants in the `client_secret` field
    of the /auth/token request.

    Spec: iss=Team ID, sub=Services ID, aud=https://appleid.apple.com,
          iat=now, exp<=180d, header kid=Key ID, alg=ES256.
    We use 6 hours — small enough that a leaked secret has limited blast
    radius, large enough to be cached if we ever wanted to.
    """
    now = int(time.time())
    payload = {
        "iss": APPLE_TEAM_ID,
        "iat": now,
        "exp": now + 6 * 60 * 60,
        "aud": APPLE_ISSUER,
        "sub": APPLE_CLIENT_ID,
    }
    headers = {"alg": "ES256", "kid": APPLE_KEY_ID}
    # The private key env var may have literal "\n" escapes (common when
    # operators paste a .p8 into a single-line env). Normalize.
    private_key = APPLE_PRIVATE_KEY.replace("\\n", "\n")
    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)


# ---------------------------------------------------------------------------
# Public config endpoint — the frontend reads this to decide whether to
# render the Apple button at all.
# ---------------------------------------------------------------------------
@router.get("/config")
async def apple_config():
    return {
        "configured": is_configured(),
        # No client_id exposed: Apple's Services ID is sensitive in the sense
        # that it ties to your developer account, and the frontend doesn't
        # need it (the whole flow is initiated server-side).
    }


# ---------------------------------------------------------------------------
# Step 1 — Kick off the flow: redirect the user's browser to Apple.
# ---------------------------------------------------------------------------
@router.get("/login")
async def apple_login(request: Request, next: Optional[str] = None):
    if not is_configured():
        raise HTTPException(status_code=503, detail="Sign in with Apple is not configured")

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)

    # Persist the state + nonce + next-redirect so we can verify on callback.
    # MongoDB row is short-lived (5 min) and indexed by state.
    await db.apple_oauth_states.insert_one({
        "state": state,
        "nonce": nonce,
        "next": (next or "/dashboard")[:200],
        "created_at": now_iso(),
        # 10 min — Apple's authorize page should be completed well within this.
        "expires_at_epoch": int(time.time()) + 600,
    })

    params = {
        "client_id": APPLE_CLIENT_ID,
        "redirect_uri": APPLE_REDIRECT_URI,
        "response_type": "code id_token",
        "response_mode": "form_post",  # Apple POSTs to our callback
        "scope": "name email",
        "state": state,
        "nonce": nonce,
    }
    return RedirectResponse(url=f"{APPLE_AUTH_URL}?{urlencode(params)}", status_code=302)


# ---------------------------------------------------------------------------
# Step 2 — Apple form-posts back here.
# IMPORTANT: This MUST be POST (Apple uses response_mode=form_post).
# Apple will also POST without our session cookie on first sign-in because the
# browser is coming back from appleid.apple.com — that's why we use a
# MongoDB-backed state instead of a cookie. Cookies with SameSite=Lax do
# follow a top-level POST in modern browsers, but the MongoDB row is more
# robust against Safari/iOS edge cases.
# ---------------------------------------------------------------------------
@router.post("/callback")
async def apple_callback(request: Request, response: Response):
    if not is_configured():
        raise HTTPException(status_code=503, detail="Sign in with Apple is not configured")

    from admin import record_login_event, ensure_admin_role

    form = await request.form()
    code = form.get("code")
    state = form.get("state")
    id_token_str_from_form = form.get("id_token")
    user_raw = form.get("user")  # JSON string, ONLY present on first sign-in

    if not code or not state:
        await record_login_event(
            request, event_type="login_failed",
            login_method="apple_oauth", failure_reason="missing_code_or_state",
        )
        return _redirect_to_login_error("apple_missing_params")

    # 1. Resolve & consume the state row (single-use).
    state_row = await db.apple_oauth_states.find_one_and_delete({"state": state})
    if not state_row:
        await record_login_event(
            request, event_type="login_failed",
            login_method="apple_oauth", failure_reason="state_not_found",
        )
        return _redirect_to_login_error("apple_state_invalid")
    if int(state_row.get("expires_at_epoch") or 0) < int(time.time()):
        await record_login_event(
            request, event_type="login_failed",
            login_method="apple_oauth", failure_reason="state_expired",
        )
        return _redirect_to_login_error("apple_state_expired")
    expected_nonce = state_row.get("nonce") or ""
    next_path = state_row.get("next") or "/dashboard"

    # 2. Exchange the authorization code for tokens (server-to-server).
    try:
        client_secret = _generate_client_secret()
    except Exception as e:  # bad private key / clock skew
        logger.exception("apple client_secret signing failed")
        await record_login_event(
            request, event_type="login_failed",
            login_method="apple_oauth", failure_reason=f"client_secret_sign_failed:{str(e)[:60]}",
        )
        return _redirect_to_login_error("apple_internal")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                APPLE_TOKEN_URL,
                data={
                    "client_id": APPLE_CLIENT_ID,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": APPLE_REDIRECT_URI,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as e:
        logger.warning("apple token endpoint unreachable: %s", e)
        await record_login_event(
            request, event_type="login_failed",
            login_method="apple_oauth", failure_reason="token_endpoint_unreachable",
        )
        return _redirect_to_login_error("apple_unreachable")

    if token_resp.status_code != 200:
        err_body = _safe_json(token_resp)
        err_code = err_body.get("error", f"http_{token_resp.status_code}")
        logger.warning("apple token exchange failed: %s | body=%s", err_code, token_resp.text[:300])
        await record_login_event(
            request, event_type="login_failed",
            login_method="apple_oauth", failure_reason=f"token_exchange_failed:{err_code}",
        )
        return _redirect_to_login_error(f"apple_token_{err_code}")

    tokens = token_resp.json()
    # Prefer the id_token from the token endpoint (server-side leg) over the
    # one Apple posted via the browser — it's the source of truth.
    id_token_str = tokens.get("id_token") or id_token_str_from_form
    if not id_token_str:
        await record_login_event(
            request, event_type="login_failed",
            login_method="apple_oauth", failure_reason="no_id_token",
        )
        return _redirect_to_login_error("apple_no_id_token")

    # 3. Verify the id_token signature + claims against Apple's JWKS.
    try:
        signing_key = _jwks().get_signing_key_from_jwt(id_token_str).key
        payload = jwt.decode(
            id_token_str,
            key=signing_key,
            algorithms=["RS256"],
            audience=APPLE_CLIENT_ID,
            issuer=APPLE_ISSUER,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        logger.warning("apple id_token verify failed: %s", e)
        await record_login_event(
            request, event_type="login_failed",
            login_method="apple_oauth", failure_reason=f"id_token_verify_failed:{str(e)[:80]}",
        )
        return _redirect_to_login_error("apple_invalid_token")

    # Nonce check — protects against replay of a captured id_token.
    if expected_nonce and payload.get("nonce") != expected_nonce:
        await record_login_event(
            request, event_type="login_failed",
            login_method="apple_oauth", failure_reason="nonce_mismatch",
        )
        return _redirect_to_login_error("apple_nonce_mismatch")

    apple_sub = payload.get("sub")
    email = (payload.get("email") or "").lower().strip()
    email_verified = bool(payload.get("email_verified") in (True, "true"))
    is_private_email = bool(payload.get("is_private_email") in (True, "true"))

    # Apple may have given us a richer `user` object (first sign-in only):
    # {"name": {"firstName": "...", "lastName": "..."}, "email": "..."}
    name_from_user = ""
    if user_raw:
        try:
            user_json = json.loads(user_raw)
            n = user_json.get("name") or {}
            first = (n.get("firstName") or "").strip()
            last = (n.get("lastName") or "").strip()
            name_from_user = (first + " " + last).strip()
            if not email:
                email = (user_json.get("email") or "").lower().strip()
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

    if not apple_sub:
        await record_login_event(
            request, event_type="login_failed",
            login_method="apple_oauth", failure_reason="missing_sub",
        )
        return _redirect_to_login_error("apple_missing_sub")

    # 4. Match/create user.
    #    Primary: apple_sub. Secondary: email (skip if Apple hid email and we
    #    never got a real address). Tertiary: create new user.
    user_doc = await db.users.find_one({"apple_sub": apple_sub}, {"_id": 0})
    if not user_doc and email:
        user_doc = await db.users.find_one({"email": email}, {"_id": 0})
        if user_doc:
            await db.users.update_one(
                {"user_id": user_doc["user_id"]},
                {"$set": {
                    "apple_sub": apple_sub,
                    "auth_provider": user_doc.get("auth_provider") or "apple",
                    "updated_at": now_iso(),
                }},
            )

    if not user_doc:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        await db.users.insert_one({
            "user_id": user_id,
            "email": email or None,
            "name": name_from_user,
            "picture": "",
            "auth_provider": "apple",
            "apple_sub": apple_sub,
            "apple_is_private_email": is_private_email,
            "role": "user",
            # Apple guarantees the email is verified per the id_token claim.
            # When `email` is missing entirely (rare; user revoked email scope)
            # we leave this False to keep semantics correct.
            "email_verified": bool(email and email_verified),
            "credits_balance": 0,
            "plan_id": "free",
            "plan_status": "pending_subscription",
            "created_at": now_iso(),
        })
        user_doc = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    else:
        # Existing user — opportunistically backfill name on first sign-in.
        if name_from_user and not user_doc.get("name"):
            await db.users.update_one(
                {"user_id": user_doc["user_id"]},
                {"$set": {"name": name_from_user, "updated_at": now_iso()}},
            )

    user_id = user_doc["user_id"]
    # 5. Mint session + redirect to SPA.
    token = await create_session(user_id, source="apple")
    set_session_cookie(response, token)
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0, "password_hash": 0})
    user = await ensure_admin_role(user)
    await record_login_event(request, event_type="login_success", login_method="apple_oauth", user=user)

    # Build the redirect to the SPA. We keep `next` opaque and only allow
    # in-app paths starting with `/` to prevent open-redirect.
    safe_next = next_path if next_path.startswith("/") else "/dashboard"
    spa_root = APPLE_POST_AUTH_REDIRECT.rstrip("/")
    # The cookie carries the session; the SPA hydrates from /api/auth/me.
    redirect_to = f"{spa_root}{safe_next}"
    final = RedirectResponse(url=redirect_to, status_code=302)
    # FastAPI strips set_cookie on RedirectResponse unless we re-apply.
    set_session_cookie(final, token)
    return final


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _safe_json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {}


def _redirect_to_login_error(code: str) -> RedirectResponse:
    """All hard failures bounce back to /login?error=<code> on the SPA."""
    spa_root = APPLE_POST_AUTH_REDIRECT.rstrip("/")
    return RedirectResponse(url=f"{spa_root}/login?error={code}", status_code=302)
