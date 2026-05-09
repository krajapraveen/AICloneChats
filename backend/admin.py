"""
Admin Login Intelligence — observability for who logged in, from where, and how.
Privacy posture:
- Raw IP is NEVER returned by the API. We hash IP + a server secret and store
  only `ip_address_hash`. Country/region/city come from trusted edge headers.
- Admin-only. Gating is by user.role == "admin".
- Auto-promotion: any user whose email is listed in env ADMIN_EMAILS (CSV)
  is promoted to admin on next login / /me call. Idempotent.
"""
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from db import db
from auth import get_current_user
from models import now_iso

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger(__name__)

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret")


# ------------- ADMIN ROLE PROMOTION -------------
def _admin_emails() -> set:
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


async def ensure_admin_role(user: dict) -> dict:
    """Idempotent: if user.email is in ADMIN_EMAILS, mark role=admin."""
    if not user:
        return user
    email = (user.get("email") or "").lower()
    desired = "admin" if email in _admin_emails() else user.get("role", "user")
    if desired and user.get("role") != desired:
        await db.users.update_one(
            {"user_id": user["user_id"]},
            {"$set": {"role": desired, "updated_at": now_iso()}},
        )
        user["role"] = desired
    elif not user.get("role"):
        user["role"] = "user"
    return user


async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    user = await ensure_admin_role(user)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


# ------------- IP / GEO / UA HELPERS -------------
def _hash_ip(ip: str) -> str:
    if not ip:
        return ""
    return hashlib.sha256((ip + JWT_SECRET).encode("utf-8")).hexdigest()[:24]


def _extract_client_ip(request: Request) -> str:
    """Honor trusted proxy headers in priority order."""
    headers = request.headers
    # Cloudflare
    if headers.get("cf-connecting-ip"):
        return headers["cf-connecting-ip"].strip()
    # XFF — take leftmost (closest to client)
    xff = headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    # Fallback
    if headers.get("x-real-ip"):
        return headers["x-real-ip"].strip()
    return request.client.host if request.client else ""


def _extract_geo(request: Request) -> dict:
    """Pull country/region/city from edge proxy headers. Best-effort, never blocks."""
    headers = request.headers
    return {
        "country": (headers.get("cf-ipcountry") or headers.get("x-vercel-ip-country") or headers.get("x-country") or "").upper() or None,
        "region": headers.get("cf-region") or headers.get("x-vercel-ip-country-region") or None,
        "city": headers.get("cf-ipcity") or headers.get("x-vercel-ip-city") or None,
    }


_BROWSER_PATTERNS = [
    ("Edge", r"Edg(e|A|iOS)?/"),
    ("Chrome", r"Chrome/"),
    ("Safari", r"Safari/"),
    ("Firefox", r"Firefox/"),
    ("Samsung Internet", r"SamsungBrowser/"),
    ("Opera", r"OPR/|Opera/"),
]
_OS_PATTERNS = [
    ("iOS", r"iPhone|iPad|iPod"),
    ("Android", r"Android"),
    ("Windows", r"Windows NT"),
    ("macOS", r"Mac OS X|Macintosh"),
    ("Linux", r"Linux"),
    ("ChromeOS", r"CrOS"),
]


def _parse_user_agent(ua: str) -> dict:
    """Tiny, dependency-free UA parser. Good enough for admin observability."""
    if not ua:
        return {"browser": "Unknown", "os": "Unknown", "device_type": "unknown"}
    browser = "Unknown"
    for name, pat in _BROWSER_PATTERNS:
        if re.search(pat, ua):
            # Chrome appears in Edge/Opera UA strings — prefer earlier matches
            browser = name
            break
    os_name = "Unknown"
    for name, pat in _OS_PATTERNS:
        if re.search(pat, ua):
            os_name = name
            break
    device_type = "desktop"
    if re.search(r"Mobi|iPhone|iPod|Android.*Mobile", ua):
        device_type = "mobile"
    elif re.search(r"iPad|Tablet|Android(?!.*Mobile)", ua):
        device_type = "tablet"
    return {"browser": browser, "os": os_name, "device_type": device_type}


# ------------- LOGIN EVENT RECORDER -------------
async def record_login_event(
    request: Request,
    *,
    event_type: str,  # login_success | login_failed | logout
    login_method: str,  # email_password | google_oauth
    email: Optional[str] = None,
    user: Optional[dict] = None,
    failure_reason: Optional[str] = None,
):
    """Best-effort. Never raises — auth flow must not fail because logging failed."""
    try:
        ua = request.headers.get("user-agent", "")
        ip = _extract_client_ip(request)
        geo = _extract_geo(request)
        ua_info = _parse_user_agent(ua)

        doc = {
            "event_id": uuid.uuid4().hex,
            "user_id": (user or {}).get("user_id"),
            "email": (email or (user or {}).get("email") or "").lower() or None,
            "name": (user or {}).get("name"),
            "login_method": login_method,
            "event_type": event_type,
            "success": event_type == "login_success",
            "failure_reason": failure_reason,
            "ip_address_hash": _hash_ip(ip),
            "ip_country": geo["country"],
            "ip_region": geo["region"],
            "ip_city": geo["city"],
            "user_agent": ua[:500],
            "browser": ua_info["browser"],
            "os": ua_info["os"],
            "device_type": ua_info["device_type"],
            "created_at": now_iso(),
        }
        await db.login_events.insert_one(doc)
    except Exception as e:
        logger.warning("record_login_event failed: %s", e)


# ------------- ADMIN ENDPOINTS -------------
def _strip_event(doc: dict) -> dict:
    """Drop _id and ANY raw IP keys before returning to client."""
    if not doc:
        return doc
    out = {k: v for k, v in doc.items() if k not in ("_id", "ip_address")}
    return out


@router.get("/login-events")
async def list_login_events(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    email: Optional[str] = None,
    user_id: Optional[str] = None,
    login_method: Optional[str] = None,
    event_type: Optional[str] = None,
    country: Optional[str] = None,
    success: Optional[bool] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    _admin: dict = Depends(get_admin_user),
):
    q: dict = {}
    if email:
        q["email"] = {"$regex": re.escape(email.lower()), "$options": "i"}
    if user_id:
        q["user_id"] = user_id
    if login_method:
        q["login_method"] = login_method
    if event_type:
        q["event_type"] = event_type
    if country:
        q["ip_country"] = country.upper()
    if success is not None:
        q["success"] = success
    if date_from or date_to:
        rng = {}
        if date_from:
            rng["$gte"] = date_from
        if date_to:
            rng["$lte"] = date_to
        q["created_at"] = rng

    total = await db.login_events.count_documents(q)
    skip = (page - 1) * limit
    cursor = (
        db.login_events.find(q, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )
    events = [_strip_event(e) for e in await cursor.to_list(limit)]
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "pages": max(1, (total + limit - 1) // limit),
        "events": events,
    }


@router.get("/login-events/summary")
async def login_events_summary(_admin: dict = Depends(get_admin_user)):
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    today_match = {"created_at": {"$gte": today_start}}

    total_logins_today = await db.login_events.count_documents(
        {**today_match, "event_type": "login_success"}
    )
    failed_logins_today = await db.login_events.count_documents(
        {**today_match, "event_type": "login_failed"}
    )

    unique_users_today = len(
        await db.login_events.distinct("user_id", {**today_match, "event_type": "login_success", "user_id": {"$ne": None}})
    )

    async def _agg(pipeline):
        out = []
        async for row in db.login_events.aggregate(pipeline):
            out.append(row)
        return out

    top_countries = await _agg([
        {"$match": {"created_at": {"$gte": seven_days_ago}, "event_type": "login_success", "ip_country": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$ip_country", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 8},
    ])
    top_methods = await _agg([
        {"$match": {"created_at": {"$gte": seven_days_ago}, "event_type": "login_success"}},
        {"$group": {"_id": "$login_method", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ])
    top_devices = await _agg([
        {"$match": {"created_at": {"$gte": seven_days_ago}, "event_type": "login_success"}},
        {"$group": {"_id": "$device_type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ])

    recent_failed = []
    cursor = (
        db.login_events.find(
            {"event_type": "login_failed"},
            {"_id": 0, "ip_address_hash": 0},  # hash hidden in summary list — too noisy
        )
        .sort("created_at", -1)
        .limit(10)
    )
    recent_failed = [_strip_event(e) for e in await cursor.to_list(10)]

    return {
        "total_logins_today": total_logins_today,
        "unique_users_today": unique_users_today,
        "failed_logins_today": failed_logins_today,
        "top_countries": [{"country": r["_id"], "count": r["count"]} for r in top_countries],
        "top_login_methods": [{"method": r["_id"], "count": r["count"]} for r in top_methods],
        "top_devices": [{"device": r["_id"], "count": r["count"]} for r in top_devices],
        "recent_failed_logins": recent_failed,
    }


@router.get("/me")
async def admin_me(user: dict = Depends(get_admin_user)):
    """Tiny endpoint for the frontend to verify admin access without leaking data."""
    return {"role": user.get("role"), "email": user.get("email"), "user_id": user.get("user_id")}
