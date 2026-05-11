"""
Admin observability — email send pipeline health.

Read-only diagnostics:
- Configured provider chain (from env)
- Per-provider configured/missing status
- Last 24h aggregate: total sends, successes, failures, success rate
- Per-provider rollup (24h)
- Recent attempts (last 50)
- Public lightweight health probe at /api/email/health (no secrets, anonymous)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from db import db
from admin import get_admin_user
from email_sender import configured_providers

router = APIRouter(tags=["admin-email"])

PUBLIC_HEALTH_LIMIT = 100  # documents to scan for the public probe


@router.get("/api/admin/email/health")
async def admin_email_health(_admin=Depends(get_admin_user)):
    """Admin-only diagnostics: configuration + last-24h send telemetry."""
    cfg = configured_providers()

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    pipeline_total = [
        {"$match": {"timestamp": {"$gte": since}}},
        {"$group": {"_id": None, "total": {"$sum": 1}, "ok": {"$sum": {"$cond": ["$ok", 1, 0]}}}},
    ]
    totals_doc = await db.email_send_events.aggregate(pipeline_total).to_list(1)
    totals = {"total": 0, "ok": 0, "failures": 0, "success_rate": None}
    if totals_doc:
        t = totals_doc[0]
        totals["total"] = t.get("total", 0)
        totals["ok"] = t.get("ok", 0)
        totals["failures"] = totals["total"] - totals["ok"]
        totals["success_rate"] = (totals["ok"] / totals["total"]) if totals["total"] else None

    pipeline_per_provider = [
        {"$match": {"timestamp": {"$gte": since}}},
        {"$group": {
            "_id": "$provider",
            "total": {"$sum": 1},
            "ok": {"$sum": {"$cond": ["$ok", 1, 0]}},
            "avg_latency_ms": {"$avg": "$latency_ms"},
        }},
    ]
    per_provider_rows = await db.email_send_events.aggregate(pipeline_per_provider).to_list(20)
    per_provider = []
    for r in per_provider_rows:
        total = r.get("total", 0)
        ok = r.get("ok", 0)
        per_provider.append({
            "provider": r.get("_id"),
            "total": total,
            "ok": ok,
            "failures": total - ok,
            "success_rate": (ok / total) if total else None,
            "avg_latency_ms": int(r.get("avg_latency_ms") or 0),
        })

    recent_cursor = db.email_send_events.find(
        {},
        {"_id": 0, "event_id": 1, "event_group": 1, "timestamp": 1, "provider": 1,
         "purpose": 1, "recipient_domain": 1, "ok": 1, "error_code": 1, "latency_ms": 1},
        sort=[("timestamp", -1)],
    ).limit(50)
    recent = await recent_cursor.to_list(50)

    return {
        "configured": cfg,
        "totals_24h": totals,
        "per_provider_24h": per_provider,
        "recent": recent,
    }


@router.get("/api/email/health")
async def public_email_health():
    """Anonymous lightweight probe — never leaks provider names or recipients.
    Returns only whether at least one provider has had a recent success.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    recent_ok = await db.email_send_events.find_one({"timestamp": {"$gte": since}, "ok": True}, {"_id": 0, "timestamp": 1})
    total = await db.email_send_events.count_documents({"timestamp": {"$gte": since}})
    return {
        "healthy": bool(recent_ok) or total == 0,  # no traffic = no failure signal
        "last_24h_attempts": total,
    }
