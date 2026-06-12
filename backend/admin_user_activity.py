"""
admin_user_activity.py — One-stop user-activity dashboard.

The Admin console already has three siloed surfaces that each tell a partial
story about a user:

  - /admin/users                → subscription/billing history per user
  - /admin/login-intelligence   → aggregate login events (no per-user drill)
  - /admin/cost-telemetry       → feature-level cost aggregates

What was missing — and what the operator asked for — is a single
per-user view that shows:

  * Who they are (email, plan_id, plan_status, credits_balance)
  * When they last logged in (timestamp + city/country)
  * How active they are (login count + feature uses in the window)
  * Which features they touched (top 5 by deduct events)
  * Full chronological timeline on drill-down (logins + feature events)

We keep it READ-ONLY. No moderation actions. No emails. Pure observation,
matching the "the system remembers; it does not chase" doctrine.

API
---
  GET /api/admin/user-activity?days=30&q=&plan=&sort=&page=&limit=
      List view. Supports email/user_id search, plan filter, multiple
      sort orders, pagination. Each row carries the aggregated window
      stats so the table renders without N+1 fetches.

  GET /api/admin/user-activity/{user_id}?days=30
      Detail view. Returns the full user document (sanitized) +
      window-bounded login events + window-bounded credit (feature) events +
      a single sorted timeline mix.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from db import db
from admin import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/user-activity", tags=["admin-user-activity"])


# Public fields — never expose secrets in a list view. Inclusion-only
# projection (don't mix with explicit 0 exclusions per Mongo rules).
_SAFE_USER_FIELDS = {
    "user_id": 1, "email": 1, "display_name": 1, "name": 1,
    "plan_id": 1, "plan_status": 1, "credits_balance": 1,
    "email_verified": 1, "role": 1, "auth_provider": 1,
    "created_at": 1, "updated_at": 1,
    "cancel_at_period_end": 1, "cancel_requested_at": 1, "cancel_reason": 1,
    "current_period_end": 1,
    "_id": 0,  # _id is the one allowed exception in an inclusion projection
}

VALID_SORTS = {
    "last_active":   ("last_active_at", -1),
    "last_login":    ("last_login_at", -1),
    "logins":        ("logins_in_window", -1),
    "features":      ("feature_uses_in_window", -1),
    "email":         ("email", 1),
    "created":       ("created_at", -1),
    "plan":          ("plan_id", 1),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _window_iso(days: int) -> str:
    return (_now() - timedelta(days=days)).isoformat()


async def _aggregate_window(user_ids: list[str], since_iso: str) -> dict[str, dict]:
    """For a batch of user_ids, compute window-bounded:
      - last_login_at + last_login_city/country/method/browser/os/device/ip_hash
      - logins_in_window + failed_logins_in_window
      - distinct_ip_count, distinct_country_count, distinct_city_count
      - feature_uses_in_window
      - top_features (top 5 by count)
    """
    if not user_ids:
        return {}

    # ─── login_events aggregate — successes ───
    login_agg = await db.login_events.aggregate([
        {"$match": {
            "user_id": {"$in": user_ids},
            "event_type": "login_success",
            "created_at": {"$gte": since_iso},
        }},
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": "$user_id",
            "logins_in_window": {"$sum": 1},
            "last_login_at": {"$first": "$created_at"},
            "last_login_country": {"$first": "$ip_country"},
            "last_login_city": {"$first": "$ip_city"},
            "last_login_region": {"$first": "$ip_region"},
            "last_login_method": {"$first": "$login_method"},
            "last_login_device": {"$first": "$device_type"},
            "last_login_browser": {"$first": "$browser"},
            "last_login_os": {"$first": "$os"},
            "last_login_ip_hash": {"$first": "$ip_address_hash"},
            # Distinct counts: security signal (multiple IPs/cities = travel
            # OR account sharing OR compromised account)
            "distinct_ip_hashes": {"$addToSet": "$ip_address_hash"},
            "distinct_countries": {"$addToSet": "$ip_country"},
            "distinct_cities": {"$addToSet": {"$concat": [
                {"$ifNull": ["$ip_city", "unknown"]}, ", ",
                {"$ifNull": ["$ip_country", "unknown"]},
            ]}},
        }},
    ]).to_list(len(user_ids) * 2)

    # ─── login_events aggregate — failures (security signal) ───
    failed_agg = await db.login_events.aggregate([
        {"$match": {
            "user_id": {"$in": user_ids},
            "event_type": "login_failed",
            "created_at": {"$gte": since_iso},
        }},
        {"$group": {"_id": "$user_id", "failed_logins_in_window": {"$sum": 1}}},
    ]).to_list(len(user_ids) * 2)
    fail_by_uid = {r["_id"]: int(r["failed_logins_in_window"] or 0) for r in failed_agg}

    # ─── credit_events aggregate (feature usage) ───
    feature_agg = await db.credit_events.aggregate([
        {"$match": {
            "user_id": {"$in": user_ids},
            "delta": {"$lt": 0},
            "kind": {"$nin": ["admin_adjust", "refund"]},
            "created_at": {"$gte": since_iso},
        }},
        {"$group": {
            "_id": {"user_id": "$user_id", "feature": {"$ifNull": ["$feature", "unknown"]}},
            "count": {"$sum": 1},
            "last_used_at": {"$max": "$created_at"},
        }},
        {"$sort": {"_id.user_id": 1, "count": -1}},
        {"$group": {
            "_id": "$_id.user_id",
            "feature_uses_in_window": {"$sum": "$count"},
            "last_feature_at": {"$max": "$last_used_at"},
            "top_features": {"$push": {"feature": "$_id.feature", "count": "$count"}},
        }},
    ]).to_list(len(user_ids) * 2)

    out: dict[str, dict] = {}
    for r in login_agg:
        ips = [x for x in (r.get("distinct_ip_hashes") or []) if x]
        countries = [x for x in (r.get("distinct_countries") or []) if x]
        cities = [x for x in (r.get("distinct_cities") or []) if x and "unknown" not in x.lower()]
        out[r["_id"]] = {
            "logins_in_window": int(r.get("logins_in_window") or 0),
            "failed_logins_in_window": fail_by_uid.get(r["_id"], 0),
            "last_login_at": r.get("last_login_at"),
            "last_login_country": r.get("last_login_country"),
            "last_login_city": r.get("last_login_city"),
            "last_login_region": r.get("last_login_region"),
            "last_login_method": r.get("last_login_method"),
            "last_login_device": r.get("last_login_device"),
            "last_login_browser": r.get("last_login_browser"),
            "last_login_os": r.get("last_login_os"),
            "last_login_ip_hash": r.get("last_login_ip_hash"),
            "distinct_ip_count": len(ips),
            "distinct_country_count": len(countries),
            "distinct_city_count": len(cities),
            "distinct_cities_sample": sorted(cities)[:5],
        }
    # Also surface failed-login counts even when there are 0 successful logins
    for uid, n in fail_by_uid.items():
        if uid not in out:
            out[uid] = {"failed_logins_in_window": n, "logins_in_window": 0}
    for r in feature_agg:
        bucket = out.setdefault(r["_id"], {})
        bucket["feature_uses_in_window"] = int(r.get("feature_uses_in_window") or 0)
        bucket["last_feature_at"] = r.get("last_feature_at")
        bucket["top_features"] = (r.get("top_features") or [])[:5]
    return out


@router.get("")
async def user_activity_list(
    days: int = Query(default=30, ge=1, le=365),
    q: Optional[str] = Query(default=None, description="email substring or full user_id"),
    plan: Optional[str] = Query(default=None, description="filter by plan_id e.g. 'free' / 'pro'"),
    sort: str = Query(default="last_active", description="last_active|last_login|logins|features|email|created|plan"),
    page: int = Query(default=1, ge=1, le=200),
    limit: int = Query(default=25, ge=1, le=100),
    admin: dict = Depends(get_admin_user),
):
    """Paginated activity list. Each row = one user with their window
    aggregates already attached (last login, logins, feature uses, top
    features). Sorting happens *after* aggregation so window-derived
    columns (logins, features, last_active) can drive order.
    """
    since_iso = _window_iso(days)

    # Build the base user query
    user_q: dict = {}
    if q:
        q = q.strip()
        if q.startswith("u_") or q.startswith("user_"):
            user_q["user_id"] = q
        else:
            user_q["email"] = {"$regex": q.lower(), "$options": "i"}
    if plan:
        user_q["plan_id"] = plan

    # Stage 1 — pull a generous candidate set so we can sort by aggregate
    # columns. We cap at 2000 to bound work; the UI is paginated so this
    # is plenty for operator browsing.
    candidate_cap = 2000
    cursor = db.users.find(user_q, _SAFE_USER_FIELDS).sort("updated_at", -1).limit(candidate_cap)
    candidates = await cursor.to_list(candidate_cap)
    total_candidates = len(candidates)

    user_ids = [u["user_id"] for u in candidates if u.get("user_id")]
    aggs = await _aggregate_window(user_ids, since_iso)

    # Stitch aggregates onto rows + derive last_active_at = max(last_login, last_feature)
    rows: list[dict] = []
    for u in candidates:
        a = aggs.get(u.get("user_id"), {})
        last_login = a.get("last_login_at")
        last_feature = a.get("last_feature_at")
        last_active = max(filter(None, [last_login, last_feature]), default=None)
        rows.append({
            **u,
            "logins_in_window": a.get("logins_in_window", 0),
            "failed_logins_in_window": a.get("failed_logins_in_window", 0),
            "feature_uses_in_window": a.get("feature_uses_in_window", 0),
            "last_login_at": last_login,
            "last_login_city": a.get("last_login_city"),
            "last_login_region": a.get("last_login_region"),
            "last_login_country": a.get("last_login_country"),
            "last_login_method": a.get("last_login_method"),
            "last_login_device": a.get("last_login_device"),
            "last_login_browser": a.get("last_login_browser"),
            "last_login_os": a.get("last_login_os"),
            "last_login_ip_hash": a.get("last_login_ip_hash"),
            "distinct_ip_count": a.get("distinct_ip_count", 0),
            "distinct_country_count": a.get("distinct_country_count", 0),
            "distinct_city_count": a.get("distinct_city_count", 0),
            "distinct_cities_sample": a.get("distinct_cities_sample", []),
            "last_feature_at": last_feature,
            "last_active_at": last_active,
            "top_features": a.get("top_features", []),
        })

    # Sort. We want nulls to sink to the BOTTOM regardless of direction
    # (a user with no activity shouldn't outrank a user with 192 logins/30d
    # just because they're alphabetically first). Two-pass stable sort:
    #   1. Inner: sort by value, direction-aware
    #   2. Outer: sort by has-value flag (stable, preserves inner ordering)
    sort_key, sort_dir = VALID_SORTS.get(sort, VALID_SORTS["last_active"])
    _NUMERIC_KEYS = {"logins_in_window", "feature_uses_in_window"}
    _default = 0 if sort_key in _NUMERIC_KEYS else ""

    def _value(r):
        v = r.get(sort_key)
        return v if v is not None else _default

    rows.sort(key=_value, reverse=(sort_dir == -1))
    rows.sort(key=lambda r: r.get(sort_key) in (None, "", 0))  # False (have value) before True (don't)

    # Paginate
    start = (page - 1) * limit
    end = start + limit
    page_rows = rows[start:end]

    return {
        "window_days": days,
        "total_candidates": total_candidates,
        "page": page,
        "limit": limit,
        "items": page_rows,
        "sort": sort,
        "plan_filter": plan,
        "q": q,
    }


@router.get("/{user_id}")
async def user_activity_detail(
    user_id: str,
    days: int = Query(default=30, ge=1, le=365),
    admin: dict = Depends(get_admin_user),
):
    """Full per-user activity view: user doc + login history + feature
    history + a single merged chronological timeline."""
    user = await db.users.find_one({"user_id": user_id}, _SAFE_USER_FIELDS)
    if not user:
        raise HTTPException(404, detail={"code": "user_not_found", "message": "No such user"})

    since_iso = _window_iso(days)

    # Login history (logins + failures both shown to operator)
    logins = await db.login_events.find(
        {"user_id": user_id, "created_at": {"$gte": since_iso}},
        {"_id": 0, "ip_address": 0},
    ).sort("created_at", -1).limit(200).to_list(200)

    # Feature usage (deduct events, the actual product touches)
    features = await db.credit_events.find(
        {"user_id": user_id, "delta": {"$lt": 0}, "created_at": {"$gte": since_iso}},
        {"_id": 0},
    ).sort("created_at", -1).limit(300).to_list(300)

    # Paywall hits — every 402 the user has hit recently.
    paywall_hits = await db.paywall_events.find(
        {"user_id": user_id, "created_at": {"$gte": since_iso}},
        {"_id": 0},
    ).sort("created_at", -1).limit(50).to_list(50)

    # Subscription transitions
    transitions = await db.subscription_transitions.find(
        {"user_id": user_id, "at": {"$gte": since_iso}},
        {"_id": 0},
    ).sort("at", -1).limit(50).to_list(50)

    # Build single chronological timeline
    timeline: list[dict] = []
    for e in logins:
        timeline.append({
            "kind": "login",
            "at": e.get("created_at"),
            "event_type": e.get("event_type"),
            "success": e.get("success"),
            "city": e.get("ip_city"),
            "country": e.get("ip_country"),
            "region": e.get("ip_region"),
            "method": e.get("login_method"),
            "device": e.get("device_type"),
            "browser": e.get("browser"),
            "os": e.get("os"),
            "ip_hash": e.get("ip_address_hash"),
            "failure_reason": e.get("failure_reason"),
        })
    for e in features:
        timeline.append({
            "kind": "feature_use",
            "at": e.get("created_at"),
            "feature": e.get("feature") or "unknown",
            "surface": e.get("surface"),
            "credits": abs(e.get("delta") or 0),
            "balance_after": e.get("balance_after"),
        })
    for e in paywall_hits:
        timeline.append({
            "kind": "paywall_hit",
            "at": e.get("created_at"),
            "surface": e.get("surface"),
            "feature": e.get("feature"),
            "reason": e.get("reason"),
        })
    for e in transitions:
        timeline.append({
            "kind": "subscription_transition",
            "at": e.get("at"),
            "transition": e.get("kind") or e.get("transition_kind"),
            "from_plan": e.get("from_plan"),
            "to_plan": e.get("to_plan"),
            "reason": e.get("reason"),
        })
    timeline.sort(key=lambda r: r.get("at") or "", reverse=True)

    # Window aggregates (single user — but reuse the helper)
    aggs = await _aggregate_window([user_id], since_iso)
    a = aggs.get(user_id, {})

    # Lifetime spend — sum of paid orders (any time)
    paid_orders = await db.payment_orders.find(
        {"user_id": user_id, "status": "paid"},
        {"_id": 0, "amount_inr": 1, "plan_id": 1, "paid_at": 1, "order_id": 1},
    ).sort("paid_at", -1).to_list(50)
    lifetime_spend_inr = sum(float(o.get("amount_inr") or 0) for o in paid_orders)

    return {
        "user": user,
        "window_days": days,
        "summary": {
            "logins_in_window": a.get("logins_in_window", 0),
            "failed_logins_in_window": a.get("failed_logins_in_window", 0),
            "feature_uses_in_window": a.get("feature_uses_in_window", 0),
            "paywall_hits_in_window": len(paywall_hits),
            "last_login_at": a.get("last_login_at"),
            "last_login_city": a.get("last_login_city"),
            "last_login_region": a.get("last_login_region"),
            "last_login_country": a.get("last_login_country"),
            "last_login_method": a.get("last_login_method"),
            "last_login_browser": a.get("last_login_browser"),
            "last_login_os": a.get("last_login_os"),
            "last_login_device": a.get("last_login_device"),
            "last_login_ip_hash": a.get("last_login_ip_hash"),
            "distinct_ip_count": a.get("distinct_ip_count", 0),
            "distinct_country_count": a.get("distinct_country_count", 0),
            "distinct_city_count": a.get("distinct_city_count", 0),
            "distinct_cities_sample": a.get("distinct_cities_sample", []),
            "top_features": a.get("top_features", []),
            "lifetime_spend_inr": round(lifetime_spend_inr, 2),
            "paid_orders_count": len(paid_orders),
        },
        "logins": logins,
        "features": features,
        "paywall_hits": paywall_hits,
        "subscription_transitions": transitions,
        "paid_orders": paid_orders,
        "timeline": timeline[:300],
        "computed_at": _now().isoformat(),
    }
