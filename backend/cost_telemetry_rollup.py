"""
cost_telemetry_rollup.py — Daily snapshot of per-feature cost / revenue / margin.

Why this exists
---------------
Every read of `/api/admin/cost-telemetry/profit-per-feature?days=N` aggregates
the entire `credit_events`, `provider_cost_events`, and `payment_orders`
collections from scratch. That's fine for 30-day windows today; it does NOT
scale to 90-day trend charts or 1-year executive reports without sub-second
budget hits.

This module computes one document per (date, feature) and stores it in
`cost_telemetry_daily`. Trend charts then read pre-aggregated rows — a flat,
trivial query.

Invariants
----------
- Idempotent: re-running for the same date overwrites the row, never duplicates.
- Same math as `cost_telemetry.profit_per_feature` for a single 1-day window.
  If the live endpoint formula changes, this module MUST change in lockstep
  (one of the test cases pins this contract).
- Best-effort: the rollup never raises into request paths. Failure is logged
  and surfaced to admins via the run-status endpoint.

API surface
-----------
  POST /api/admin/cost-telemetry/rollup        — rollup a date (or last N days)
  GET  /api/admin/cost-telemetry/daily?days=30 — read pre-aggregated trend
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta, date

from fastapi import APIRouter, Depends, HTTPException, Query

from db import db
from admin import get_admin_user
from cost_telemetry import DASHBOARD_FEATURES, _get_cost_table

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/cost-telemetry", tags=["billing-admin"])

ROLLUP_VERSION = "v1"


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _day_bounds_iso(d: date) -> tuple[str, str]:
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


async def rollup_day(d: date) -> dict:
    """Compute and upsert one row per feature for the given UTC date.

    Returns a summary {date, features_written, total_credits, total_revenue}.
    """
    start_iso, end_iso = _day_bounds_iso(d)
    cost_table = await _get_cost_table()

    # Credits consumed per feature (deduct events only)
    consumed = await db.credit_events.aggregate([
        {"$match": {
            "created_at": {"$gte": start_iso, "$lt": end_iso},
            "delta": {"$lt": 0},
            "kind": {"$nin": ["admin_adjust", "refund"]},
        }},
        {"$group": {
            "_id": {"$ifNull": ["$feature", "unknown"]},
            "credits_consumed": {"$sum": {"$abs": "$delta"}},
            "usage_count": {"$sum": 1},
        }},
    ]).to_list(50)
    by_feature_credits = {r["_id"]: r for r in consumed}

    # Provider-metered actual cost
    metered = await db.provider_cost_events.aggregate([
        {"$match": {"created_at": {"$gte": start_iso, "$lt": end_iso}}},
        {"$group": {
            "_id": {"$ifNull": ["$feature", "unknown"]},
            "metered_cost_inr": {"$sum": "$cost_inr"},
            "metered_calls": {"$sum": 1},
        }},
    ]).to_list(50)
    by_feature_metered = {r["_id"]: r for r in metered}

    # Day's total revenue (paid orders only)
    paid = await db.payment_orders.aggregate([
        {"$match": {"status": "paid", "paid_at": {"$gte": start_iso, "$lt": end_iso}}},
        {"$group": {"_id": None, "total_inr": {"$sum": "$amount_inr"}, "count": {"$sum": 1}}},
    ]).to_list(2)
    total_revenue_inr = float((paid[0]["total_inr"] if paid else 0) or 0)
    total_paid_count = int((paid[0]["count"] if paid else 0) or 0)

    total_credits = sum(r["credits_consumed"] for r in consumed) or 0

    written: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    date_str = d.isoformat()

    for feature in DASHBOARD_FEATURES:
        bucket = by_feature_credits.get(feature, {})
        credits = int(bucket.get("credits_consumed") or 0)
        usage = int(bucket.get("usage_count") or 0)

        m = by_feature_metered.get(feature, {})
        metered_cost_inr = float(m.get("metered_cost_inr") or 0)
        metered_calls = int(m.get("metered_calls") or 0)

        cost_per_credit = cost_table.get(feature)
        if metered_cost_inr > 0:
            estimated_cost_inr = round(metered_cost_inr, 6)
            cost_source = "provider_metered"
        elif cost_per_credit is not None:
            estimated_cost_inr = round(credits * float(cost_per_credit), 6)
            cost_source = "configured"
        else:
            estimated_cost_inr = None
            cost_source = "not_configured"

        if total_credits > 0:
            share = credits / total_credits
            revenue_apportioned_inr = round(total_revenue_inr * share, 6)
        else:
            share = 0.0
            revenue_apportioned_inr = 0.0

        if estimated_cost_inr is None:
            gross_profit_inr = None
            margin_pct = None
        else:
            gross_profit_inr = round(revenue_apportioned_inr - estimated_cost_inr, 6)
            margin_pct = (
                round((gross_profit_inr / revenue_apportioned_inr) * 100, 4)
                if revenue_apportioned_inr > 0 else None
            )

        doc = {
            "date": date_str,
            "feature": feature,
            "credits_consumed": credits,
            "usage_count": usage,
            "share_of_credits_pct": round(share * 100, 4),
            "metered_cost_inr": round(metered_cost_inr, 6),
            "metered_calls": metered_calls,
            "cost_per_credit_inr": cost_per_credit,
            "estimated_cost_inr": estimated_cost_inr,
            "cost_source": cost_source,
            "revenue_apportioned_inr": revenue_apportioned_inr,
            "gross_profit_inr": gross_profit_inr,
            "margin_pct": margin_pct,
            "total_credits_day": total_credits,
            "total_revenue_inr_day": round(total_revenue_inr, 6),
            "total_paid_orders_day": total_paid_count,
            "version": ROLLUP_VERSION,
            "computed_at": now,
        }
        await db.cost_telemetry_daily.update_one(
            {"date": date_str, "feature": feature},
            {"$set": doc},
            upsert=True,
        )
        written.append({"feature": feature, "credits": credits, "metered_cost_inr": round(metered_cost_inr, 4)})

    return {
        "date": date_str,
        "features_written": len(written),
        "total_credits": total_credits,
        "total_revenue_inr": round(total_revenue_inr, 2),
        "total_paid_orders": total_paid_count,
        "version": ROLLUP_VERSION,
        "computed_at": now,
        "features": written,
    }


async def rollup_recent(days: int = 2) -> list[dict]:
    """Compute the last `days` UTC days inclusive of today (so today's
    partial-day row is updated every run, and yesterday is finalized).
    """
    days = max(1, min(int(days), 14))  # safety clamp
    today = _utc_today()
    out: list[dict] = []
    for offset in range(days):
        d = today - timedelta(days=offset)
        try:
            summary = await rollup_day(d)
            out.append(summary)
        except Exception as e:
            logger.warning("cost_telemetry rollup_day(%s) failed: %s", d, e)
            out.append({"date": d.isoformat(), "ok": False, "error": str(e)})
    return out


# ───────────────────── Admin endpoints ─────────────────────

@router.post("/rollup")
async def run_rollup(
    days: int = Query(default=2, ge=1, le=14),
    admin: dict = Depends(get_admin_user),
):
    """Recompute the last N daily rollups (default: today + yesterday).

    Safe to call from cron every hour — overwrite-by-key keeps the doc
    set converging on truth. Returns the list of day summaries.
    """
    summaries = await rollup_recent(days)
    return {
        "ok": True,
        "days_processed": len(summaries),
        "summaries": summaries,
        "triggered_by": admin.get("email"),
    }


@router.get("/daily")
async def get_daily_series(
    days: int = Query(default=30, ge=1, le=180),
    feature: str | None = Query(default=None),
    admin: dict = Depends(get_admin_user),
):
    """Read pre-aggregated daily rows for the trend chart.

    Filters
    -------
    - `days`: window from today (inclusive) backwards.
    - `feature`: optional, narrow to a single feature bucket.

    Response is sorted ascending by date so the frontend can plot it
    straight into recharts without re-sorting.
    """
    today = _utc_today()
    cutoff_iso = (today - timedelta(days=days - 1)).isoformat()
    q: dict = {"date": {"$gte": cutoff_iso}}
    if feature:
        if feature not in DASHBOARD_FEATURES:
            raise HTTPException(400, detail={"code": "unknown_feature", "message": f"Unknown feature: {feature}"})
        q["feature"] = feature

    rows = await db.cost_telemetry_daily.find(q, {"_id": 0}).sort([("date", 1), ("feature", 1)]).to_list(2000)

    # Build a daily-totals roll-up across all features so the UI can show
    # one trend line per metric AND per-feature lines.
    by_date: dict[str, dict] = {}
    for r in rows:
        d = r["date"]
        bucket = by_date.setdefault(d, {
            "date": d,
            "credits": 0, "usage_count": 0,
            "metered_cost_inr": 0.0, "metered_calls": 0,
            "estimated_cost_inr": 0.0,
            "revenue_inr": float(r.get("total_revenue_inr_day") or 0),
            "gross_profit_inr": 0.0,
            "any_unconfigured": False,
        })
        bucket["credits"] += int(r.get("credits_consumed") or 0)
        bucket["usage_count"] += int(r.get("usage_count") or 0)
        bucket["metered_cost_inr"] += float(r.get("metered_cost_inr") or 0)
        bucket["metered_calls"] += int(r.get("metered_calls") or 0)
        if r.get("estimated_cost_inr") is None:
            bucket["any_unconfigured"] = True
        else:
            bucket["estimated_cost_inr"] += float(r["estimated_cost_inr"])
            bucket["gross_profit_inr"] += float(r.get("gross_profit_inr") or 0)

    totals_series = []
    for d in sorted(by_date.keys()):
        b = by_date[d]
        margin_pct = None
        if not b["any_unconfigured"] and b["revenue_inr"] > 0:
            margin_pct = round((b["gross_profit_inr"] / b["revenue_inr"]) * 100, 4)
        totals_series.append({
            "date": d,
            "credits": b["credits"],
            "usage_count": b["usage_count"],
            "metered_cost_inr": round(b["metered_cost_inr"], 4),
            "metered_calls": b["metered_calls"],
            "estimated_cost_inr": round(b["estimated_cost_inr"], 4),
            "revenue_inr": round(b["revenue_inr"], 4),
            "gross_profit_inr": None if b["any_unconfigured"] else round(b["gross_profit_inr"], 4),
            "margin_pct": margin_pct,
        })

    return {
        "window_days": days,
        "feature_filter": feature,
        "series": rows,               # raw per-(date,feature) rows
        "totals_series": totals_series,  # per-date totals across all features
        "version": ROLLUP_VERSION,
    }


async def ensure_indexes() -> None:
    try:
        await db.cost_telemetry_daily.create_index([("date", -1), ("feature", 1)], unique=True)
        await db.cost_telemetry_daily.create_index("computed_at")
        logger.info("cost_telemetry_rollup: indexes ensured")
    except Exception as e:
        logger.warning("cost_telemetry_rollup: index creation failed: %s", e)
