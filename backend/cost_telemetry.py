"""
cost_telemetry.py — Profit-per-feature + revenue-attribution dashboard.

Why this file
-------------
Revenue without cost visibility is half the story. This module gives the
operator a single-screen, reproducible answer to:
  1. "Which features make money? Which lose money?"
  2. "Which entry point on the site actually converts to paid subscribers?"

Both reads are pure aggregations over the immutable source rows:
  - `credit_events` (with `feature` field from earlier cost-tagging work)
  - `funnel_events` (with `pricing_visit_source`)
  - `payment_orders` (with `pricing_visit_source` + `amount_inr`)

Cost configuration is OPERATOR-CONFIGURABLE (`admin_settings.cost_per_credit_inr`)
so the dashboard never silently guesses. If no cost is set for a feature
the row shows "—" and a "Configure costs" prompt. We never make up numbers.

Endpoints
---------
  GET  /api/admin/cost-telemetry/cost-config
  POST /api/admin/cost-telemetry/cost-config
  GET  /api/admin/cost-telemetry/profit-per-feature?days=30
  GET  /api/admin/cost-telemetry/contribution-by-source?days=30
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException

from db import db
from admin import get_admin_user
from credits import ALLOWED_FEATURES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/cost-telemetry", tags=["billing-admin"])

# Feature buckets shown on the dashboard. `subscription` and `admin_adjustment`
# are deliberately excluded — they don't reflect product usage, they reflect
# inventory movement (grants vs spend) and would skew the profit math.
DASHBOARD_FEATURES = ("ai_clone", "voice", "video", "chat", "image", "avatar", "unknown")

COST_CONFIG_KEY = "cost_telemetry_cost_per_credit_inr"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _window_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def _get_cost_table() -> dict[str, float]:
    """Return {feature: estimated_inr_per_credit}. Missing features → not in dict.

    Defaults: empty. The operator must explicitly configure costs. This is
    intentional — silent zero would falsely show "100% margin" on a feature
    with real fal.ai / OpenAI / TTS spend.
    """
    doc = await db.admin_settings.find_one({"key": COST_CONFIG_KEY}, {"_id": 0, "values": 1})
    return ((doc or {}).get("values") or {})


@router.get("/cost-config")
async def get_cost_config(admin: dict = Depends(get_admin_user)):
    table = await _get_cost_table()
    return {
        "key": COST_CONFIG_KEY,
        "values": table,  # {feature: float}
        "features": list(DASHBOARD_FEATURES),
        "note": "Estimated INR cost per credit consumed for each feature. Set to 0 only if you really pay nothing for that feature's infra.",
    }


@router.post("/cost-config")
async def set_cost_config(payload: dict, admin: dict = Depends(get_admin_user)):
    """Replace the cost table. Accepts a dict of {feature: float_or_null}.

    Validation:
      - Only ALLOWED_FEATURES (the master 9-bucket taxonomy from credits.py)
        are accepted; rogue keys are rejected with 400.
      - Negative costs rejected.
      - Null / missing → key cleared (treated as "not configured").
    """
    raw = (payload or {}).get("values") or {}
    if not isinstance(raw, dict):
        raise HTTPException(400, detail={"code": "invalid_payload", "message": "Expected a 'values' dict."})

    cleaned: dict[str, float] = {}
    for k, v in raw.items():
        if k not in ALLOWED_FEATURES:
            raise HTTPException(400, detail={"code": "unknown_feature", "message": f"Unknown feature key: {k}"})
        if v is None or v == "":
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise HTTPException(400, detail={"code": "invalid_cost", "message": f"Cost for {k} must be a number."})
        if f < 0:
            raise HTTPException(400, detail={"code": "negative_cost", "message": f"Cost for {k} cannot be negative."})
        cleaned[k] = round(f, 6)

    await db.admin_settings.update_one(
        {"key": COST_CONFIG_KEY},
        {"$set": {
            "key": COST_CONFIG_KEY, "values": cleaned,
            "updated_at": _now_iso(), "updated_by": admin.get("email"),
        }},
        upsert=True,
    )
    return {"ok": True, "values": cleaned}


@router.get("/profit-per-feature")
async def profit_per_feature(
    admin: dict = Depends(get_admin_user),
    days: int = Query(default=30, ge=1, le=365),
):
    """Per-feature credits consumed + estimated cost + apportioned revenue
    + gross profit + margin %.

    Revenue apportionment
    ---------------------
    Total subscription/topup revenue in window is apportioned to features
    proportionally to credits consumed. This is the most defensible
    apportionment given how plan pricing works ("you pay for credits, you
    spend credits on features"). The dashboard explicitly labels this as
    an apportionment rather than a direct measurement.
    """
    since = _window_iso(days)
    cost_table = await _get_cost_table()

    # ── Credits consumed per feature (negative deltas = spend; exclude
    # admin_adjust + refund). Refunds are inflows; netting them in would
    # under-count consumption for features with high failure rates.
    consumed_rows = await db.credit_events.aggregate([
        {"$match": {
            "created_at": {"$gte": since},
            "delta": {"$lt": 0},
            "kind": {"$nin": ["admin_adjust", "refund"]},
        }},
        {"$group": {
            "_id": {"$ifNull": ["$feature", "unknown"]},
            "credits_consumed": {"$sum": {"$abs": "$delta"}},
            "usage_count": {"$sum": 1},
        }},
    ]).to_list(50)
    by_feature = {r["_id"]: r for r in consumed_rows}

    # ── Provider-metered actual cost per feature (when recorded)
    metered_rows = await db.provider_cost_events.aggregate([
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {
            "_id": {"$ifNull": ["$feature", "unknown"]},
            "metered_cost_inr": {"$sum": "$cost_inr"},
            "metered_calls": {"$sum": 1},
        }},
    ]).to_list(50)
    metered_by_feature = {r["_id"]: r for r in metered_rows}

    total_credits_consumed = sum(r["credits_consumed"] for r in consumed_rows) or 0

    # ── Revenue in window (paid orders only)
    paid_orders = await db.payment_orders.aggregate([
        {"$match": {"status": "paid", "paid_at": {"$gte": since}}},
        {"$group": {"_id": None, "total_inr": {"$sum": "$amount_inr"}, "count": {"$sum": 1}}},
    ]).to_list(2)
    total_revenue_inr = float((paid_orders[0]["total_inr"] if paid_orders else 0) or 0)

    rows: list[dict] = []
    for feature in DASHBOARD_FEATURES:
        bucket = by_feature.get(feature, {})
        credits = int(bucket.get("credits_consumed") or 0)
        usage = int(bucket.get("usage_count") or 0)

        cost_per_credit = cost_table.get(feature)
        metered_bucket = metered_by_feature.get(feature, {})
        metered_cost_inr = metered_bucket.get("metered_cost_inr")
        metered_calls = int(metered_bucket.get("metered_calls") or 0)

        if metered_cost_inr is not None and metered_cost_inr > 0:
            # Real provider-metered cost trumps configured estimate
            estimated_cost_inr = round(float(metered_cost_inr), 2)
            cost_source = "provider_metered"
        elif cost_per_credit is not None:
            estimated_cost_inr = round(credits * cost_per_credit, 2)
            cost_source = "configured"
        else:
            estimated_cost_inr = None
            cost_source = "not_configured"

        # Apportioned revenue: feature_share_of_credits × total_revenue
        if total_credits_consumed > 0:
            share = credits / total_credits_consumed
            revenue_attributed_inr = round(total_revenue_inr * share, 2)
        else:
            share = 0.0
            revenue_attributed_inr = 0.0

        if estimated_cost_inr is None:
            gross_profit_inr = None
            margin_pct = None
        else:
            gross_profit_inr = round(revenue_attributed_inr - estimated_cost_inr, 2)
            margin_pct = round((gross_profit_inr / revenue_attributed_inr * 100), 2) if revenue_attributed_inr > 0 else None

        rows.append({
            "feature": feature,
            "credits_consumed": credits,
            "usage_count": usage,
            "share_of_credits_pct": round(share * 100, 2),
            "estimated_cost_inr": estimated_cost_inr,
            "cost_per_credit_inr": cost_per_credit,
            "cost_source": cost_source,
            "metered_calls": metered_calls,
            "metered_cost_inr": round(float(metered_cost_inr), 2) if metered_cost_inr else 0.0,
            "revenue_attributed_inr": revenue_attributed_inr,
            "gross_profit_inr": gross_profit_inr,
            "margin_pct": margin_pct,
        })

    # Totals row (only computable if every feature has a cost; otherwise
    # we report the partial total + a flag)
    has_full_cost = all(r["estimated_cost_inr"] is not None for r in rows)
    totals = {
        "credits_consumed": sum(r["credits_consumed"] for r in rows),
        "usage_count": sum(r["usage_count"] for r in rows),
        "revenue_attributed_inr": round(sum(r["revenue_attributed_inr"] for r in rows), 2),
        "estimated_cost_inr": round(sum(r["estimated_cost_inr"] or 0 for r in rows), 2),
        "gross_profit_inr": None if not has_full_cost else round(sum(r["gross_profit_inr"] or 0 for r in rows), 2),
        "margin_pct": None,
        "all_features_costed": has_full_cost,
    }
    if has_full_cost and totals["revenue_attributed_inr"] > 0:
        totals["margin_pct"] = round(totals["gross_profit_inr"] / totals["revenue_attributed_inr"] * 100, 2)

    return {
        "window_days": days,
        "computed_at": _now_iso(),
        "total_revenue_inr": round(total_revenue_inr, 2),
        "total_credits_consumed": total_credits_consumed,
        "rows": rows,
        "totals": totals,
        "config_status": {
            "features_configured": sum(1 for f in DASHBOARD_FEATURES if f in cost_table),
            "features_total": len(DASHBOARD_FEATURES),
            "cost_table": cost_table,
        },
    }


@router.get("/contribution-by-source")
async def contribution_by_source(
    admin: dict = Depends(get_admin_user),
    days: int = Query(default=30, ge=1, le=365),
):
    """Conversion funnel by `pricing_visit_source`. Joins:
      - `funnel_events` (pricing_view counts → "visits")
      - `payment_orders.status=created` (checkout starts)
      - `payment_orders.status=paid` (successful payments + revenue)

    The visits→checkout→paid funnel is computed PER source, so the operator
    can spot which entry points convert most efficiently.
    """
    since = _window_iso(days)

    # Pricing visits per source
    visits_rows = await db.funnel_events.aggregate([
        {"$match": {"event_name": "pricing_view", "created_at": {"$gte": since}}},
        {"$group": {"_id": {"$ifNull": ["$pricing_visit_source", "unknown"]}, "visits": {"$sum": 1}}},
    ]).to_list(50)
    visits_by_source = {r["_id"]: r["visits"] for r in visits_rows}

    # All orders per source (any status)
    starts_rows = await db.payment_orders.aggregate([
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": {"$ifNull": ["$pricing_visit_source", "unknown"]}, "starts": {"$sum": 1}}},
    ]).to_list(50)
    starts_by_source = {r["_id"]: r["starts"] for r in starts_rows}

    # Paid orders per source
    paid_rows = await db.payment_orders.aggregate([
        {"$match": {"status": "paid", "paid_at": {"$gte": since}}},
        {"$group": {
            "_id": {"$ifNull": ["$pricing_visit_source", "unknown"]},
            "paid_count": {"$sum": 1},
            "revenue_inr": {"$sum": "$amount_inr"},
        }},
    ]).to_list(50)
    paid_by_source = {r["_id"]: r for r in paid_rows}

    all_sources = set(visits_by_source) | set(starts_by_source) | set(paid_by_source)

    rows: list[dict] = []
    for source in sorted(all_sources):
        visits = int(visits_by_source.get(source, 0))
        starts = int(starts_by_source.get(source, 0))
        paid_bucket = paid_by_source.get(source, {})
        paid = int(paid_bucket.get("paid_count", 0))
        revenue = float(paid_bucket.get("revenue_inr", 0) or 0)
        conversion = round((paid / visits) * 100, 2) if visits > 0 else None
        arppu = round(revenue / paid, 2) if paid > 0 else None
        rows.append({
            "pricing_visit_source": source,
            "visits": visits,
            "checkout_starts": starts,
            "paid_orders": paid,
            "conversion_pct": conversion,
            "revenue_inr": round(revenue, 2),
            "arppu_inr": arppu,
        })

    # Sort by revenue desc — the operator's first question is "what's making me money"
    rows.sort(key=lambda r: (r["revenue_inr"], r["paid_orders"]), reverse=True)

    totals = {
        "visits": sum(r["visits"] for r in rows),
        "checkout_starts": sum(r["checkout_starts"] for r in rows),
        "paid_orders": sum(r["paid_orders"] for r in rows),
        "revenue_inr": round(sum(r["revenue_inr"] for r in rows), 2),
    }
    totals["conversion_pct"] = round(totals["paid_orders"] / totals["visits"] * 100, 2) if totals["visits"] > 0 else None
    totals["arppu_inr"] = round(totals["revenue_inr"] / totals["paid_orders"], 2) if totals["paid_orders"] > 0 else None

    return {
        "window_days": days,
        "computed_at": _now_iso(),
        "rows": rows,
        "totals": totals,
    }
