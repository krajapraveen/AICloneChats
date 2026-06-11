"""
anti_abuse.py — Production-grade anti-abuse helper for aiclonechats.com

Design philosophy
-----------------
- ONE source of truth for "is this user exempt?"
  Union of (a) env ADMIN_EMAILS, (b) DB admin_users collection, (c) env
  ADMIN_UNLIMITED_EMAIL CSV. All normalised lowercase, trimmed. Admin-exempt
  users still get audit-logged so we know when something was bypassed.

- Counter-based sliding-window rate limiter on MongoDB.
  No Redis required. Each "hit" is an insert into db.anti_abuse_events. To
  check, we count documents whose timestamp falls inside the window. Indexes
  keep the count cheap.

- Per-(scope, key) windows. Scope is a short label like "auth.login" or
  "chat.message". Key is the IP-hash or user-id, whichever the endpoint
  cares about. Endpoints typically call this twice — once with IP-hash,
  once with user-id (or email) — to defeat both single-IP and credential
  pivot abuse.

- Status escalation:
  abuse_status on the user document goes normal → limited → blocked.
  Blocked users cannot perform expensive actions. Admins are NEVER blocked
  or limited — admin status takes absolute precedence.

- Safe-fail policy:
  • Expensive generation endpoints fail CLOSED on infra errors (deny by default).
  • Auth/forgot-password endpoints fail OPEN with neutral 200 — no error leakage.

- Audit:
  Every limit/exempt-bypass/block is written to db.login_events for the
  existing admin dashboards.

Public API
----------
- async is_anti_abuse_exempt_user(email_or_user) -> bool
- async enforce_rate_limit(scope, key, max_count, window_s, *, user_email=None,
                           ip_hash=None, fail_closed=False) -> RateCheck
- async check_user_abuse_status(user) -> str  ("normal" | "limited" | "blocked")
- async record_abuse_event(event, *, user_id=None, email=None, ip_hash=None,
                           endpoint=None, reason=None, metadata=None) -> None
- async set_user_abuse_status(user_id, status, reason, *, by_admin_email) -> dict

Note on IP handling
-------------------
We never store raw IPs in long-term audit. The hash function below mirrors
the one used by auth.py / password_reset.py for cross-correlation.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Iterable

from fastapi import HTTPException, Request

from db import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exemption
# ---------------------------------------------------------------------------


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _env_admin_emails() -> set[str]:
    """Union of ADMIN_EMAILS (CSV) and ADMIN_UNLIMITED_EMAIL (CSV)."""
    raw_admins = os.environ.get("ADMIN_EMAILS", "") or ""
    raw_unlimited = os.environ.get("ADMIN_UNLIMITED_EMAIL", "krajapraveen@gmail.com") or ""
    emails: set[str] = set()
    for raw in (raw_admins, raw_unlimited):
        for e in raw.split(","):
            n = _normalize_email(e)
            if n:
                emails.add(n)
    return emails


# In-process cache for the DB-side admin_users list (TTL 30 s).
_DB_ADMIN_CACHE: dict = {"emails": set(), "fetched_at": 0.0}
_DB_ADMIN_TTL_S = 30


async def _db_admin_emails() -> set[str]:
    import time
    now = time.time()
    if now - _DB_ADMIN_CACHE["fetched_at"] < _DB_ADMIN_TTL_S and _DB_ADMIN_CACHE["emails"]:
        return _DB_ADMIN_CACHE["emails"]
    try:
        cursor = db.admin_users.find({}, {"_id": 0, "email": 1})
        emails = {(doc.get("email") or "").lower() async for doc in cursor}
        emails.discard("")
        _DB_ADMIN_CACHE["emails"] = emails
        _DB_ADMIN_CACHE["fetched_at"] = now
        return emails
    except Exception as e:
        logger.warning("anti_abuse: DB admin lookup failed, falling back to env only: %s", e)
        return set()


async def all_exempt_emails() -> set[str]:
    """Union of env admins + DB-persisted admins. Use sparingly — usually
    is_anti_abuse_exempt_user() is enough."""
    return _env_admin_emails() | await _db_admin_emails()


async def is_anti_abuse_exempt_user(email_or_user: str | dict | None) -> bool:
    """True when the email is in any admin allowlist (env or DB).

    Accepts either a raw email string or a user dict containing 'email'.
    """
    if email_or_user is None:
        return False
    email = email_or_user if isinstance(email_or_user, str) else email_or_user.get("email")
    email_n = _normalize_email(email)
    if not email_n:
        return False
    if email_n in _env_admin_emails():
        return True
    return email_n in await _db_admin_emails()


# ---------------------------------------------------------------------------
# IP helpers
# ---------------------------------------------------------------------------


def request_ip(request: Optional[Request]) -> str:
    """Best-effort client IP. Honours X-Forwarded-For first hop."""
    if request is None:
        return ""
    xff = request.headers.get("x-forwarded-for") or ""
    if xff:
        return xff.split(",")[0].strip()
    client = getattr(request, "client", None)
    return getattr(client, "host", "") or ""


def hash_ip(ip: str) -> str:
    if not ip:
        return ""
    return hashlib.sha256(ip.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Rate limiting (sliding window over db.anti_abuse_events)
# ---------------------------------------------------------------------------


@dataclass
class RateCheck:
    allowed: bool
    exempt: bool
    count: int
    limit: int
    window_s: int
    reason: Optional[str] = None  # "exempt" | "ok" | "rate_limited" | "fail_closed" | "user_blocked"


async def _record_event(scope: str, key: str, *, exempt: bool, user_id: Optional[str] = None,
                        email: Optional[str] = None, ip_hash: Optional[str] = None,
                        endpoint: Optional[str] = None) -> None:
    try:
        await db.anti_abuse_events.insert_one({
            "scope": scope,
            "key": key,
            "exempt": exempt,
            "user_id": user_id,
            "email": _normalize_email(email) if email else None,
            "ip_hash": ip_hash,
            "endpoint": endpoint,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.warning("anti_abuse: record_event failed scope=%s key=%s err=%s", scope, key, e)


async def enforce_rate_limit(
    scope: str,
    key: str,
    *,
    max_count: int,
    window_s: int,
    user_email: Optional[str] = None,
    user_id: Optional[str] = None,
    ip_hash: Optional[str] = None,
    endpoint: Optional[str] = None,
    fail_closed: bool = False,
) -> RateCheck:
    """Increment + check a sliding-window counter.

    Returns a RateCheck. Caller decides whether to raise. (We do not raise
    inside this helper so callers can craft scope-specific neutral responses,
    e.g. forgot-password.)

    fail_closed=True means: on DB failure, deny (returns allowed=False with
    reason='fail_closed'). Use for expensive paid generation. Default fails
    OPEN, used for auth where we never leak server errors.
    """
    # Exempt path — admin emails skip the limit entirely but still audit.
    if user_email and await is_anti_abuse_exempt_user(user_email):
        await _record_event(scope, key, exempt=True, user_id=user_id, email=user_email,
                            ip_hash=ip_hash, endpoint=endpoint)
        await _audit_log("anti_abuse_exempt_bypassed", user_id=user_id, email=user_email,
                         endpoint=endpoint, scope=scope, ip_hash=ip_hash)
        return RateCheck(allowed=True, exempt=True, count=0, limit=max_count,
                         window_s=window_s, reason="exempt")

    # Window math
    since = datetime.now(timezone.utc) - timedelta(seconds=window_s)
    try:
        count = await db.anti_abuse_events.count_documents({
            "scope": scope,
            "key": key,
            "created_at": {"$gte": since},
        })
        await _record_event(scope, key, exempt=False, user_id=user_id, email=user_email,
                            ip_hash=ip_hash, endpoint=endpoint)
        # +1 because we just inserted
        count += 1
    except Exception as e:
        logger.warning("anti_abuse: rate-limit read failed scope=%s key=%s err=%s", scope, key, e)
        if fail_closed:
            return RateCheck(allowed=False, exempt=False, count=0, limit=max_count,
                             window_s=window_s, reason="fail_closed")
        return RateCheck(allowed=True, exempt=False, count=0, limit=max_count,
                         window_s=window_s, reason="infra_fallback_open")

    if count > max_count:
        await _audit_log("anti_abuse_rate_limited", user_id=user_id, email=user_email,
                         endpoint=endpoint, scope=scope, ip_hash=ip_hash,
                         metadata={"count": count, "limit": max_count, "window_s": window_s})
        return RateCheck(allowed=False, exempt=False, count=count, limit=max_count,
                         window_s=window_s, reason="rate_limited")

    return RateCheck(allowed=True, exempt=False, count=count, limit=max_count,
                     window_s=window_s, reason="ok")


# ---------------------------------------------------------------------------
# User-level abuse status
# ---------------------------------------------------------------------------

ABUSE_STATUSES = ("normal", "limited", "blocked")


async def check_user_abuse_status(user: dict | None) -> str:
    """Returns the abuse_status from the user document. Admins are forced
    to 'normal' regardless of any stale flag the DB may hold.
    """
    if not user:
        return "normal"
    if await is_anti_abuse_exempt_user(user):
        return "normal"
    status = (user.get("abuse_status") or "normal").lower()
    return status if status in ABUSE_STATUSES else "normal"


async def set_user_abuse_status(
    user_id: str,
    status: str,
    reason: str,
    *,
    by_admin_email: str,
) -> dict:
    """Admin operation. Returns the updated user doc subset."""
    if status not in ABUSE_STATUSES:
        raise HTTPException(status_code=400, detail={"code": "invalid_status", "message": "Invalid status."})
    target = await db.users.find_one({"user_id": user_id}, {"_id": 0, "user_id": 1, "email": 1, "abuse_status": 1})
    if not target:
        raise HTTPException(status_code=404, detail={"code": "user_not_found", "message": "User not found."})
    if await is_anti_abuse_exempt_user(target):
        raise HTTPException(status_code=400, detail={
            "code": "user_is_admin",
            "message": "Admin-exempt users cannot be limited or blocked.",
        })

    await db.users.update_one(
        {"user_id": user_id},
        {"$set": {"abuse_status": status, "abuse_status_reason": reason,
                  "abuse_status_set_at": datetime.now(timezone.utc).isoformat(),
                  "abuse_status_set_by": _normalize_email(by_admin_email)}},
    )
    event_name = {"normal": "anti_abuse_user_unblocked",
                  "limited": "anti_abuse_user_limited",
                  "blocked": "anti_abuse_user_blocked"}[status]
    await _audit_log(event_name, user_id=user_id, email=target.get("email"),
                     endpoint="admin.set_abuse_status", reason=reason,
                     metadata={"by_admin": _normalize_email(by_admin_email)})
    return {"user_id": user_id, "email": target.get("email"), "abuse_status": status, "reason": reason}


async def reset_abuse_counters(user_id: str, *, by_admin_email: str) -> dict:
    """Wipe all anti_abuse_events for this user_id (and their email keys).
    Lets admins un-stick someone after a false positive."""
    target = await db.users.find_one({"user_id": user_id}, {"_id": 0, "email": 1})
    email_n = _normalize_email(target.get("email") if target else "")
    deleted = await db.anti_abuse_events.delete_many({
        "$or": [
            {"user_id": user_id},
            {"key": user_id},
            {"key": email_n} if email_n else {"key": "__nope__"},
            {"email": email_n} if email_n else {"email": "__nope__"},
        ]
    })
    await _audit_log("anti_abuse_counters_reset", user_id=user_id, email=email_n,
                     endpoint="admin.reset_abuse_counters",
                     metadata={"deleted": deleted.deleted_count, "by_admin": _normalize_email(by_admin_email)})
    return {"user_id": user_id, "deleted": deleted.deleted_count}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


async def _audit_log(
    event: str,
    *,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    endpoint: Optional[str] = None,
    scope: Optional[str] = None,
    reason: Optional[str] = None,
    ip_hash: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    try:
        import uuid
        await db.login_events.insert_one({
            "event_id": uuid.uuid4().hex,
            "event": event,
            "user_id": user_id,
            "email": _normalize_email(email) if email else None,
            "endpoint": endpoint,
            "scope": scope,
            "reason": reason,
            "ip_hash": ip_hash,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.warning("anti_abuse: audit_log failed event=%s err=%s", event, e)


async def record_abuse_event(
    event: str,
    *,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    ip_hash: Optional[str] = None,
    endpoint: Optional[str] = None,
    reason: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Public alias of the internal audit logger."""
    await _audit_log(event, user_id=user_id, email=email, endpoint=endpoint,
                     reason=reason, ip_hash=ip_hash, metadata=metadata)


# ---------------------------------------------------------------------------
# Convenience: guard helpers that endpoints can call as one-liners
# ---------------------------------------------------------------------------


async def guard_expensive_action(
    *,
    user: dict,
    scope: str,
    request: Optional[Request],
    max_per_user_per_min: int = 30,
    max_per_user_per_hour: int = 300,
    endpoint: Optional[str] = None,
) -> None:
    """Use for chat/upload/generation. Raises HTTP 429 / 403 as needed.

    - Hard-blocks if user is `blocked`.
    - Limits per-minute + per-hour windows per user.
    - Admins bypass everything.
    """
    email = (user.get("email") or "") if user else ""
    user_id = (user.get("user_id") or "") if user else ""
    ip = request_ip(request)
    ip_h = hash_ip(ip)

    # 1. Hard block check
    status = await check_user_abuse_status(user)
    if status == "blocked":
        await _audit_log("anti_abuse_blocked_user_attempt", user_id=user_id, email=email,
                         endpoint=endpoint, scope=scope, ip_hash=ip_h)
        raise HTTPException(status_code=403, detail={
            "code": "account_blocked",
            "message": "Your account has been temporarily restricted. Contact admin@aiclonechats.com.",
        })

    # 2. Per-user, per-minute
    r1 = await enforce_rate_limit(
        scope=f"{scope}.user_per_min", key=user_id or email,
        max_count=max_per_user_per_min, window_s=60,
        user_email=email, user_id=user_id, ip_hash=ip_h, endpoint=endpoint,
        fail_closed=True,
    )
    if not r1.allowed:
        raise HTTPException(status_code=429, detail={
            "code": "rate_limited",
            "message": "Too many requests. Please slow down and try again shortly.",
            "retry_after_s": 60,
        })

    # 3. Per-user, per-hour (bigger budget)
    r2 = await enforce_rate_limit(
        scope=f"{scope}.user_per_hour", key=user_id or email,
        max_count=max_per_user_per_hour, window_s=3600,
        user_email=email, user_id=user_id, ip_hash=ip_h, endpoint=endpoint,
        fail_closed=True,
    )
    if not r2.allowed:
        raise HTTPException(status_code=429, detail={
            "code": "rate_limited",
            "message": "Hourly limit reached. Try again later.",
            "retry_after_s": 3600,
        })

    # 4. Per-IP, per-hour (defeat credential pivots)
    if ip_h:
        r3 = await enforce_rate_limit(
            scope=f"{scope}.ip_per_hour", key=ip_h,
            max_count=max(max_per_user_per_hour * 3, 600),  # generous, IP is shared (NAT, offices)
            window_s=3600,
            user_email=email, user_id=user_id, ip_hash=ip_h, endpoint=endpoint,
            fail_closed=False,  # don't punish whole NAT pool on infra blips
        )
        if not r3.allowed:
            raise HTTPException(status_code=429, detail={
                "code": "rate_limited",
                "message": "Network-level limit reached. Try again later.",
                "retry_after_s": 3600,
            })


async def guard_public_endpoint(
    *,
    scope: str,
    request: Optional[Request],
    max_per_ip_per_min: int = 10,
    max_per_ip_per_hour: int = 60,
    endpoint: Optional[str] = None,
) -> None:
    """For un-authenticated endpoints (contact form, public APIs).
    Anonymous → no email exemption. Pure IP-based."""
    ip_h = hash_ip(request_ip(request))
    if not ip_h:
        return  # behind weird proxy; don't crash production
    r1 = await enforce_rate_limit(
        scope=f"{scope}.ip_per_min", key=ip_h,
        max_count=max_per_ip_per_min, window_s=60,
        ip_hash=ip_h, endpoint=endpoint,
    )
    if not r1.allowed:
        raise HTTPException(status_code=429, detail={
            "code": "rate_limited",
            "message": "Too many requests. Please slow down.",
            "retry_after_s": 60,
        })
    r2 = await enforce_rate_limit(
        scope=f"{scope}.ip_per_hour", key=ip_h,
        max_count=max_per_ip_per_hour, window_s=3600,
        ip_hash=ip_h, endpoint=endpoint,
    )
    if not r2.allowed:
        raise HTTPException(status_code=429, detail={
            "code": "rate_limited",
            "message": "Hourly limit reached.",
            "retry_after_s": 3600,
        })


async def ensure_indexes() -> None:
    """Called once at startup from server.py."""
    try:
        await db.anti_abuse_events.create_index([("scope", 1), ("key", 1), ("created_at", -1)])
        await db.anti_abuse_events.create_index("created_at", expireAfterSeconds=60 * 60 * 24 * 14)  # 14-day TTL
        await db.users.create_index("abuse_status")
        logger.info("anti_abuse: indexes ensured")
    except Exception as e:
        logger.warning("anti_abuse: index creation skipped: %s", e)
