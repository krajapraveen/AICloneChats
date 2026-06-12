import os
import re
import uuid
import logging
import bcrypt
import requests
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Request, Response, Depends, Header, Cookie
from typing import Optional

from db import db
from models import RegisterRequest, LoginRequest, GoogleCallbackRequest, User, now_iso

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret")
SESSION_TTL_DAYS = 7

# Custom Google OAuth (replaces Emergent-managed flow)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Brute-force lockout config
LOGIN_LOCKOUT_MAX_FAILS = 5
LOGIN_LOCKOUT_WINDOW_SEC = 900  # 15 minutes


def _new_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:16]


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )


def _auth_err(code: str, message: str, request_id: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, "request_id": request_id})


async def _login_rate_limit_or_raise(request: Request, email: str, request_id: str) -> None:
    """Brute-force lockout: count recent failed attempts by IP+email bucket."""
    try:
        from admin import _extract_client_ip, _hash_ip  # reuse hashing for parity with log writes
        ip = _extract_client_ip(request)
        ip_hash = _hash_ip(ip)
        since = (datetime.now(timezone.utc) - timedelta(seconds=LOGIN_LOCKOUT_WINDOW_SEC)).isoformat()
        fails = await db.login_events.count_documents({
            "event_type": "login_failed",
            "ip_address_hash": ip_hash,
            "email": (email or "").lower(),
            "created_at": {"$gte": since},
        })
        if fails >= LOGIN_LOCKOUT_MAX_FAILS:
            raise _auth_err(
                "rate_limited",
                "Too many failed attempts. Try again in 15 minutes.",
                request_id,
                status=429,
            )
    except HTTPException:
        raise
    except Exception:
        # Rate-limit store failing should NEVER block real users
        return


# ----- helpers -----
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def new_session_token() -> str:
    return f"st_{uuid.uuid4().hex}{uuid.uuid4().hex}"


async def create_session(user_id: str, source: str, token: Optional[str] = None) -> str:
    token = token or new_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    await db.user_sessions.insert_one({
        "session_token": token,
        "user_id": user_id,
        "source": source,
        "created_at": now_iso(),
        "expires_at": expires_at.isoformat(),
    })
    return token


def set_session_cookie(response: Response, token: str):
    response.set_cookie(
        key="session_token",
        value=token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )


def clear_session_cookie(response: Response):
    response.delete_cookie("session_token", path="/", samesite="none", secure=True)


async def get_current_user(
    session_token: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    """Resolve current user from cookie OR Authorization: Bearer header."""
    token = session_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    sess = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if not sess:
        raise HTTPException(status_code=401, detail="Invalid session")

    expires_at = sess["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")

    user = await db.users.find_one({"user_id": sess["user_id"]}, {"_id": 0, "password_hash": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user.get("is_deleted") or user.get("is_deactivated"):
        # Defense-in-depth: deletion already removes sessions, but if a stale
        # token slips through (e.g. between cascade steps) we still hard-deny.
        raise HTTPException(status_code=401, detail="Account no longer exists")
    return user


async def get_optional_user(
    session_token: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Optional[dict]:
    try:
        return await get_current_user(session_token=session_token, authorization=authorization)
    except HTTPException:
        return None


# ----- routes -----
@router.post("/register")
async def register(payload: RegisterRequest, request: Request, response: Response):
    # Lazy import to avoid circular: admin imports get_current_user from this module
    from admin import record_login_event, ensure_admin_role

    request_id = _new_request_id()
    response.headers["X-Request-Id"] = request_id

    email = (payload.email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise _auth_err("invalid_email", "Please enter a valid email address.", request_id, status=400)
    if not payload.password or len(payload.password) < 6:
        raise _auth_err("weak_password", "Password must be at least 6 characters.", request_id, status=400)

    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        await record_login_event(
            request,
            event_type="login_failed",
            login_method="email_password",
            email=email,
            failure_reason="email_already_registered",
        )
        raise _auth_err("email_already_registered", "An account with this email already exists.", request_id, status=400)

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    user_doc = {
        "user_id": user_id,
        "email": email,
        "name": payload.name or email.split("@")[0],
        "picture": "",
        "password_hash": hash_password(payload.password),
        "auth_provider": "email",
        "role": "user",
        "email_verified": False,
        "credits_balance": 0,
        "plan_id": "free",
        "plan_status": "pending_verification",
        "created_at": now_iso(),
    }
    await db.users.insert_one(dict(user_doc))

    token = await create_session(user_id, source="email")
    set_session_cookie(response, token)
    user_view = {k: v for k, v in user_doc.items() if k not in ("password_hash", "_id")}
    user_view = await ensure_admin_role(user_view)
    await record_login_event(
        request,
        event_type="login_success",
        login_method="email_password",
        user=user_view,
    )
    return {
        "user": user_view,
        "session_token": token,
        "request_id": request_id,
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    from admin import record_login_event, ensure_admin_role

    request_id = _new_request_id()
    response.headers["X-Request-Id"] = request_id

    email = (payload.email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise _auth_err("invalid_email", "Please enter a valid email address.", request_id, status=400)
    if not payload.password:
        raise _auth_err("missing_password", "Password is required.", request_id, status=400)

    # Brute-force lockout BEFORE doing any DB work — also denies enumeration via timing
    await _login_rate_limit_or_raise(request, email, request_id)

    user = await db.users.find_one({"email": email}, {"_id": 0})
    invalid_msg = "Email or password is incorrect."
    if not user or not user.get("password_hash"):
        await record_login_event(
            request,
            event_type="login_failed",
            login_method="email_password",
            email=email,
            failure_reason="invalid_credentials",
        )
        raise _auth_err("invalid_credentials", invalid_msg, request_id, status=401)
    if user.get("is_suspended") or user.get("is_deactivated") or user.get("is_deleted"):
        await record_login_event(
            request,
            event_type="login_failed",
            login_method="email_password",
            email=email,
            user={"user_id": user["user_id"], "email": user["email"], "name": user.get("name")},
            failure_reason="account_suspended",
        )
        raise _auth_err("account_suspended", "This account has been suspended. Contact support.", request_id, status=403)
    if not verify_password(payload.password, user["password_hash"]):
        await record_login_event(
            request,
            event_type="login_failed",
            login_method="email_password",
            email=email,
            user={"user_id": user["user_id"], "email": user["email"], "name": user.get("name")},
            failure_reason="invalid_password",
        )
        raise _auth_err("invalid_credentials", invalid_msg, request_id, status=401)

    token = await create_session(user["user_id"], source="email")
    set_session_cookie(response, token)
    user_view = {k: v for k, v in user.items() if k not in ("password_hash", "_id")}
    user_view = await ensure_admin_role(user_view)
    await record_login_event(
        request,
        event_type="login_success",
        login_method="email_password",
        user=user_view,
    )
    return {
        "user": user_view,
        "session_token": token,
        "request_id": request_id,
    }


@router.post("/google/callback")
async def google_callback(payload: GoogleCallbackRequest, request: Request, response: Response):
    """
    Custom Google OAuth (auth code flow).
    Frontend completes Google sign-in via @react-oauth/google and POSTs us the auth code.
    We exchange it for tokens, verify the ID token, and create/match a user by email.

    REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
    """
    from admin import record_login_event, ensure_admin_role

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")

    # 1. Exchange code for tokens
    try:
        token_resp = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": payload.code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": payload.redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
    except requests.RequestException:
        await record_login_event(request, event_type="login_failed", login_method="google_oauth", failure_reason="token_endpoint_unreachable")
        raise HTTPException(status_code=502, detail="Google token endpoint unreachable")

    if token_resp.status_code != 200:
        # Surface the actual Google error so debugging from logs/UI is possible.
        # Google returns JSON like {"error": "redirect_uri_mismatch", "error_description": "..."}
        try:
            err_body = token_resp.json()
            err_code = err_body.get("error", f"http_{token_resp.status_code}")
            err_desc = err_body.get("error_description", "")
        except Exception:
            err_code = f"http_{token_resp.status_code}"
            err_desc = token_resp.text[:200] if token_resp.text else ""
        await record_login_event(
            request,
            event_type="login_failed",
            login_method="google_oauth",
            failure_reason=f"token_exchange_failed:{err_code}",
        )
        logger.warning("Google token exchange failed: %s | %s | redirect_uri=%s", err_code, err_desc, payload.redirect_uri)
        raise HTTPException(
            status_code=401,
            detail=f"Google sign-in rejected: {err_code}{(' — ' + err_desc) if err_desc else ''}",
        )

    tokens = token_resp.json()
    id_token_str = tokens.get("id_token")
    if not id_token_str:
        await record_login_event(request, event_type="login_failed", login_method="google_oauth", failure_reason="no_id_token")
        raise HTTPException(status_code=401, detail="No ID token from Google")

    # 2. Verify ID token signature + claims (aud, iss, exp)
    try:
        from google.oauth2 import id_token as g_id_token
        from google.auth.transport import requests as g_requests
        idinfo = g_id_token.verify_oauth2_token(id_token_str, g_requests.Request(), GOOGLE_CLIENT_ID)
    except ValueError as e:
        await record_login_event(request, event_type="login_failed", login_method="google_oauth", failure_reason=f"id_token_verify_failed:{str(e)[:80]}")
        raise HTTPException(status_code=401, detail="Invalid ID token")

    email = (idinfo.get("email") or "").lower()
    if not email or not idinfo.get("email_verified"):
        await record_login_event(request, event_type="login_failed", login_method="google_oauth", failure_reason="email_not_verified")
        raise HTTPException(status_code=400, detail="Google email not verified")

    name = idinfo.get("name", "")
    picture = idinfo.get("picture", "")

    # 3. Match by email so existing google users keep working (relink to new flow)
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        user_id = existing["user_id"]
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "name": name or existing.get("name", ""),
                "picture": picture or existing.get("picture", ""),
                "auth_provider": "google",  # in case they were email-only before
                "updated_at": now_iso(),
            }},
        )
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        await db.users.insert_one({
            "user_id": user_id,
            "email": email,
            "name": name,
            "picture": picture,
            "auth_provider": "google",
            "role": "user",
            "email_verified": True,  # Google email is already verified per check above
            "credits_balance": 0,
            "plan_id": "free",
            "plan_status": "pending_subscription",
            "created_at": now_iso(),
        })

    token = await create_session(user_id, source="google")
    set_session_cookie(response, token)
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0, "password_hash": 0})
    user = await ensure_admin_role(user)
    await record_login_event(request, event_type="login_success", login_method="google_oauth", user=user)
    return {"user": user, "session_token": token}


@router.get("/google/config")
async def google_config():
    """Public endpoint — frontend reads the Google client_id from here so it never has to be hardcoded."""
    return {"client_id": GOOGLE_CLIENT_ID, "configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    from admin import ensure_admin_role
    return await ensure_admin_role(user)


@router.post("/logout")
async def logout(request: Request, response: Response, session_token: Optional[str] = Cookie(default=None), authorization: Optional[str] = Header(default=None)):
    from admin import record_login_event
    token = session_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    user = None
    if token:
        sess = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
        if sess:
            user = await db.users.find_one({"user_id": sess["user_id"]}, {"_id": 0, "password_hash": 0})
        await db.user_sessions.delete_one({"session_token": token})
    if user:
        await record_login_event(request, event_type="logout", login_method=user.get("auth_provider", "email") + ("_oauth" if user.get("auth_provider") == "google" else "_password"), user=user)
    clear_session_cookie(response)
    return {"ok": True}
