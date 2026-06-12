"""
exit_insights.py — Why users leave.

Two distinct exit signals exist in the codebase already:

  1. `account_deletion_events.reason` — full account deletion (Apple/Google
     compliance flow). User explicitly tore the account down. Strong signal.

  2. `users.cancel_reason` (paired with `cancel_at_period_end=True` and
     `cancel_requested_at`) — pending subscription cancellation. Softer
     signal: account stays, just won't renew.

This module aggregates both into a single "why are people leaving" view
for the admin dashboard. The "no chasing" principle dictates that we
*observe* exit signal without ever using it to trigger retention emails
or pop-ups — operators see the trend and can make product decisions.

Endpoints
---------
  GET  /api/admin/exit-insights?days=90

Response shape
--------------
{
  "window_days": 90,
  "computed_at": "...",
  "summary": {
    "total_exits": int,                       # deletions + pending cancels
    "deletions": int,
    "subscription_cancellations": int,
    "exits_with_reason": int,
    "exits_without_reason": int,
    "reason_capture_rate_pct": float,
  },
  "by_reason_bucket": [
    {"bucket": "pricing", "count": 12, "examples": ["too expensive", "...", ...]},
    {"bucket": "ux",      "count":  8, "examples": [...]},
    ...
  ],
  "monthly_series": [
    {"month": "2026-04", "deletions": 5, "cancellations": 7, "total": 12},
    ...
  ],
  "recent_exits": [
    {"kind": "deletion",     "at": "...", "reason": "...", "plan_id": "...",  "bucket": "..."},
    {"kind": "cancellation", "at": "...", "reason": "...", "plan_id": "...",  "bucket": "..."},
    ...
  ]
}
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query

from db import db
from admin import get_admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["billing-admin"])


# Reason keyword → bucket. Lower-cased substring matching. First match wins.
REASON_BUCKETS: list[tuple[str, tuple[str, ...]]] = [
    ("pricing",       ("expensive", "cost", "price", "afford", "money", "cheap", "pricing", "subscription cost")),
    ("missing_feature", ("missing", "feature", "doesn't have", "doesnt have", "wish it had", "should have", "lacks")),
    ("quality",       ("quality", "bad reply", "bad response", "not smart", "wrong answer", "hallucinat", "inaccurat", "useless")),
    ("ux",            ("confusing", "ux", "ui", "interface", "hard to use", "difficult", "complicated", "buggy", "broken", "doesn't work")),
    ("privacy",       ("privacy", "data", "deletion", "gdpr", "personal info", "tracking")),
    ("not_using",     ("not using", "no longer", "stopped using", "forgot", "don't need", "dont need", "no use")),
    ("alternative",   ("alternative", "switched", "switching", "use chatgpt", "use claude", "competitor", "other app")),
    ("trust",         ("trust", "scary", "creepy", "uncomfortable", "weird", "concerned")),
]


def _bucket_for_reason(reason: str | None) -> str:
    text = (reason or "").lower().strip()
    if not text:
        return "no_reason"
    for bucket, kws in REASON_BUCKETS:
        for kw in kws:
            if kw in text:
                return bucket
    return "other"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _window_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _yyyymm(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return iso[:7]  # "2026-06-12T..." → "2026-06"
    except Exception:
        return None


@router.get("/exit-insights")
async def exit_insights(
    days: int = Query(default=90, ge=7, le=365),
    admin: dict = Depends(get_admin_user),
):
    since = _window_iso(days)

    # ─── Deletions ───
    deletions = await db.account_deletion_events.find(
        {"deleted_at": {"$gte": since}},
        {"_id": 0, "deleted_at": 1, "reason": 1, "auth_provider": 1, "user_id": 1},
    ).sort("deleted_at", -1).to_list(2000)

    # ─── Pending cancellations (cancel_at_period_end=True within window) ───
    cancellations_cursor = db.users.find(
        {
            "cancel_at_period_end": True,
            "cancel_requested_at": {"$gte": since},
        },
        {"_id": 0, "cancel_requested_at": 1, "cancel_reason": 1, "plan_id": 1, "user_id": 1},
    ).sort("cancel_requested_at", -1)
    cancellations = await cancellations_cursor.to_list(2000)

    # ─── Aggregate by reason bucket ───
    bucket_counts: dict[str, int] = {}
    bucket_examples: dict[str, list[str]] = {}
    exits_with_reason = 0
    exits_without_reason = 0

    def _record(reason: str | None):
        nonlocal exits_with_reason, exits_without_reason
        b = _bucket_for_reason(reason)
        bucket_counts[b] = bucket_counts.get(b, 0) + 1
        text = (reason or "").strip()
        if text:
            exits_with_reason += 1
            samples = bucket_examples.setdefault(b, [])
            if text not in samples and len(samples) < 5:
                # Truncate per-example to 140 chars (Twitter-length elevator pitch)
                samples.append(text[:140])
        else:
            exits_without_reason += 1

    for d in deletions:
        _record(d.get("reason"))
    for c in cancellations:
        _record(c.get("cancel_reason"))

    by_reason_bucket = sorted(
        [
            {"bucket": b, "count": n, "examples": bucket_examples.get(b, [])}
            for b, n in bucket_counts.items()
        ],
        key=lambda r: r["count"], reverse=True,
    )

    # ─── Monthly time series (deletions vs cancellations) ───
    monthly: dict[str, dict] = {}
    for d in deletions:
        m = _yyyymm(d.get("deleted_at"))
        if not m:
            continue
        b = monthly.setdefault(m, {"month": m, "deletions": 0, "cancellations": 0, "total": 0})
        b["deletions"] += 1
        b["total"] += 1
    for c in cancellations:
        m = _yyyymm(c.get("cancel_requested_at"))
        if not m:
            continue
        b = monthly.setdefault(m, {"month": m, "deletions": 0, "cancellations": 0, "total": 0})
        b["cancellations"] += 1
        b["total"] += 1
    monthly_series = sorted(monthly.values(), key=lambda r: r["month"])

    # ─── Recent exits feed (mixed) ───
    recent: list[dict] = []
    for d in deletions[:20]:
        recent.append({
            "kind": "deletion",
            "at": d.get("deleted_at"),
            "reason": (d.get("reason") or None),
            "bucket": _bucket_for_reason(d.get("reason")),
            "auth_provider": d.get("auth_provider"),
            "plan_id": None,
            "user_id": d.get("user_id"),
        })
    for c in cancellations[:20]:
        recent.append({
            "kind": "cancellation",
            "at": c.get("cancel_requested_at"),
            "reason": (c.get("cancel_reason") or None),
            "bucket": _bucket_for_reason(c.get("cancel_reason")),
            "auth_provider": None,
            "plan_id": c.get("plan_id"),
            "user_id": c.get("user_id"),
        })
    recent.sort(key=lambda r: r["at"] or "", reverse=True)
    recent = recent[:30]

    total_exits = len(deletions) + len(cancellations)
    capture_rate = (
        round((exits_with_reason / total_exits) * 100, 2)
        if total_exits > 0 else 0.0
    )

    return {
        "window_days": days,
        "computed_at": _now_iso(),
        "summary": {
            "total_exits": total_exits,
            "deletions": len(deletions),
            "subscription_cancellations": len(cancellations),
            "exits_with_reason": exits_with_reason,
            "exits_without_reason": exits_without_reason,
            "reason_capture_rate_pct": capture_rate,
        },
        "by_reason_bucket": by_reason_bucket,
        "monthly_series": monthly_series,
        "recent_exits": recent,
        "buckets_catalog": [b for b, _ in REASON_BUCKETS] + ["other", "no_reason"],
    }
