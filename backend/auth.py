import os
import uuid
import bcrypt
import requests
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Request, Response, Depends, Header, Cookie
from typing import Optional

from db import db
from models import RegisterRequest, LoginRequest, GoogleSessionRequest, User, now_iso

router = APIRouter(prefix="/api/auth", tags=["auth"])

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret")
SESSION_TTL_DAYS = 7
EMERGENT_AUTH_DATA_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"


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

    existing = await db.users.find_one({"email": payload.email.lower()}, {"_id": 0})
    if existing:
        await record_login_event(
            request,
            event_type="login_failed",
            login_method="email_password",
            email=payload.email,
            failure_reason="email_already_registered",
        )
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    user_doc = {
        "user_id": user_id,
        "email": payload.email.lower(),
        "name": payload.name or payload.email.split("@")[0],
        "picture": "",
        "password_hash": hash_password(payload.password),
        "auth_provider": "email",
        "role": "user",
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
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    from admin import record_login_event, ensure_admin_role

    user = await db.users.find_one({"email": payload.email.lower()}, {"_id": 0})
    if not user or not user.get("password_hash"):
        await record_login_event(
            request,
            event_type="login_failed",
            login_method="email_password",
            email=payload.email,
            failure_reason="invalid_credentials",
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(payload.password, user["password_hash"]):
        await record_login_event(
            request,
            event_type="login_failed",
            login_method="email_password",
            email=payload.email,
            user={"user_id": user["user_id"], "email": user["email"], "name": user.get("name")},
            failure_reason="invalid_password",
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

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
    }


@router.post("/google/session")
async def google_session(payload: GoogleSessionRequest, request: Request, response: Response):
    """Exchange Emergent session_id for our session_token."""
    from admin import record_login_event, ensure_admin_role

    try:
        r = requests.get(
            EMERGENT_AUTH_DATA_URL,
            headers={"X-Session-ID": payload.session_id},
            timeout=15,
        )
        if r.status_code != 200:
            await record_login_event(request, event_type="login_failed", login_method="google_oauth", failure_reason="invalid_emergent_session")
            raise HTTPException(status_code=401, detail="Invalid Emergent session")
        data = r.json()
    except requests.RequestException:
        await record_login_event(request, event_type="login_failed", login_method="google_oauth", failure_reason="auth_provider_unreachable")
        raise HTTPException(status_code=502, detail="Auth service unreachable")

    email = (data.get("email") or "").lower()
    name = data.get("name", "")
    picture = data.get("picture", "")
    emergent_token = data.get("session_token") or new_session_token()

    if not email:
        await record_login_event(request, event_type="login_failed", login_method="google_oauth", failure_reason="no_email_from_provider")
        raise HTTPException(status_code=400, detail="No email from auth provider")

    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        user_id = existing["user_id"]
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {"name": name or existing.get("name", ""), "picture": picture or existing.get("picture", "")}},
        )
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        new_user = {
            "user_id": user_id,
            "email": email,
            "name": name,
            "picture": picture,
            "auth_provider": "google",
            "role": "user",
            "created_at": now_iso(),
        }
        await db.users.insert_one(dict(new_user))

    token = await create_session(user_id, source="google", token=emergent_token)
    set_session_cookie(response, token)
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0, "password_hash": 0})
    user = await ensure_admin_role(user)
    await record_login_event(request, event_type="login_success", login_method="google_oauth", user=user)
    return {"user": user, "session_token": token}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    from admin import ensure_admin_role
    return await ensure_admin_role(user)


@router.post("/logout")
async def logout(request: Request, response: Response, session_token: Optional[str] = Cookie(default=None)):
    from admin import record_login_event
    user = None
    if session_token:
        sess = await db.user_sessions.find_one({"session_token": session_token}, {"_id": 0})
        if sess:
            user = await db.users.find_one({"user_id": sess["user_id"]}, {"_id": 0, "password_hash": 0})
        await db.user_sessions.delete_one({"session_token": session_token})
    if user:
        await record_login_event(request, event_type="logout", login_method=user.get("auth_provider", "email") + ("_oauth" if user.get("auth_provider") == "google" else "_password"), user=user)
    clear_session_cookie(response)
    return {"ok": True}
