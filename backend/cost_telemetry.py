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


# ─────────────── Loss-making request alerts + Top-N expensive ───────────────

def _severity_for_margin_pct(margin_pct: Optional[float]) -> str:
    """info  : margin < 10% (warning zone but still profitable)
       warning: margin < 0%  (actively losing money)
       critical: margin < -20% (bleeding fast)
       ok    : margin >= 10%
    """
    if margin_pct is None:
        return "unknown"
    if margin_pct < -20:
        return "critical"
    if margin_pct < 0:
        return "warning"
    if margin_pct < 10:
        return "info"
    return "ok"


# Margins below this threshold are almost certainly a config/attribution
# bug — real businesses don't lose 1000% on a single request. We flag and
# the UI surfaces a validation banner.
SUSPECT_MARGIN_PCT = -1000


def _recovery_action(
    row: dict,
    feature_bucket: dict,
    user_spend_share: float,
    top_model_share: float,
    top_model: Optional[str],
) -> dict:
    """Data-driven recommendation based on actual telemetry signals.

    Precedence:
      1. Suspect data: margin < -1000% → validate config first.
      2. Healthy: margin ≥ 10% → no action.
      3. Abuse pattern: one user > 40% of feature spend → review.
      4. Model problem: one model > 70% of feature cost AND a cheaper
         tier exists → switch.
      5. Pricing problem: feature net margin < 0 AND ≥ 5 requests in
         window → increase credit cost.
      6. Fallback: investigate.
    """
    margin_pct = row.get("margin_pct")
    margin_inr = row.get("margin_inr") or 0
    cost_inr = row.get("metered_cost_inr") or 0
    credits = row.get("credits_deducted") or 0
    feature = row.get("feature") or "unknown"

    if margin_pct is not None and margin_pct < SUSPECT_MARGIN_PCT:
        return {
            "kind": "data_validation",
            "label": "Validate telemetry config",
            "reason": (
                f"Margin {margin_pct}% is suspiciously low. Likely cause: "
                f"missing credit deduction, wrong USD→INR rate, or seeded test data. "
                f"Verify before changing pricing."
            ),
            "estimated_margin_gain_inr": None,
        }

    if margin_pct is not None and margin_pct >= 10:
        return {
            "kind": "healthy",
            "label": "Healthy margin",
            "reason": f"Margin {margin_pct}% is comfortable. No action required.",
            "estimated_margin_gain_inr": 0,
        }

    if user_spend_share > 0.40 and feature_bucket.get("requests", 0) >= 3:
        return {
            "kind": "abuse_review",
            "label": "Review user activity",
            "reason": (
                f"This user accounts for {round(user_spend_share * 100)}% of "
                f"{feature} spend in window. Investigate for abnormal usage."
            ),
            "estimated_margin_gain_inr": round(cost_inr * user_spend_share, 2),
        }

    if top_model and top_model_share > 0.70:
        # Suggest the cheaper tier for known model families
        downgrade_map = {
            "claude-opus-4-5": "claude-sonnet-4-5-20250929",
            "claude-sonnet-4-5-20250929": "claude-haiku-4-5",
            "gpt-5.2": "gpt-4o",
            "gpt-4o": "gpt-4o-mini",
            "gemini-3-pro": "gemini-3-flash",
        }
        cheaper = downgrade_map.get(top_model)
        if cheaper:
            return {
                "kind": "model_downgrade",
                "label": f"Consider {cheaper} instead of {top_model}",
                "reason": (
                    f"{round(top_model_share * 100)}% of {feature} cost uses "
                    f"{top_model}. The cheaper {cheaper} may keep quality "
                    f"acceptable at a fraction of the price."
                ),
                # Heuristic: cheaper tier ≈ 70-90% cheaper for Sonnet→Haiku.
                "estimated_margin_gain_inr": round(cost_inr * 0.75, 2),
            }

    if (feature_bucket.get("margin_inr") or 0) < 0 and feature_bucket.get("requests", 0) >= 5:
        # Recommend a credit-cost bump. Target: 20% margin.
        if credits > 0 and cost_inr > 0:
            cost_per_credit = cost_inr / credits
            # Need revenue_per_credit ≥ cost_per_credit × 1.25 for 20% margin
            target_credits = max(credits + 1, int(round(cost_per_credit * 1.25 / (row.get("estimated_revenue_inr") or 1) * credits)))
            if target_credits > credits:
                return {
                    "kind": "raise_credit_cost",
                    "label": f"Raise credit charge {credits} → {target_credits}",
                    "reason": (
                        f"Feature {feature} has net negative margin across "
                        f"{feature_bucket.get('requests')} requests this window. "
                        f"Raising the per-call credit cost restores profitability."
                    ),
                    "estimated_margin_gain_inr": round((target_credits - credits) * (row.get("estimated_revenue_inr") or 0) / max(credits, 1), 2),
                }

    return {
        "kind": "investigate",
        "label": "Investigate",
        "reason": "Below profitable margin but no single clear cause. Inspect surface, model, and user.",
        "estimated_margin_gain_inr": None,
    }


@router.get("/loss-making")
async def loss_making_requests(
    admin: dict = Depends(get_admin_user),
    days: int = Query(default=30, ge=1, le=365),
    top_n: int = Query(default=10, ge=5, le=50),
):
    """Per-request margin analysis built on the metered cost ledger.

    For each `provider_cost_events` row in window, we compute:
      - credits_deducted: the matching `credit_events.delta` (joined by
        request_id; falls back to 0 if no match — typical for surfaces that
        record cost but don't deduct credits, or for legacy rows pre-tagging)
      - estimated_revenue_inr: `credits_deducted × revenue_per_credit`
        where `revenue_per_credit` = total_window_revenue / total_window_credits_consumed
        (the same apportionment used by the profit-per-feature table — keeps
        the two views internally consistent).
      - margin_inr: estimated_revenue_inr - cost_inr
      - severity: critical / warning / info / ok based on margin %.

    Returns:
      - summary  (total flagged, total negative margin, by_feature, by_severity)
      - top_expensive (Top-N cost desc)
      - top_losses (Top-N margin asc)
    """
    since = _window_iso(days)

    # Revenue per credit, derived once over the window for consistency
    paid_total_rows = await db.payment_orders.aggregate([
        {"$match": {"status": "paid", "paid_at": {"$gte": since}}},
        {"$group": {"_id": None, "total_inr": {"$sum": "$amount_inr"}}},
    ]).to_list(2)
    total_revenue_inr = float((paid_total_rows[0]["total_inr"] if paid_total_rows else 0) or 0)
    consumed_total_rows = await db.credit_events.aggregate([
        {"$match": {"created_at": {"$gte": since}, "delta": {"$lt": 0},
                    "kind": {"$nin": ["admin_adjust", "refund"]}}},
        {"$group": {"_id": None, "total": {"$sum": {"$abs": "$delta"}}}},
    ]).to_list(2)
    total_credits = int((consumed_total_rows[0]["total"] if consumed_total_rows else 0) or 0)
    revenue_per_credit = (total_revenue_inr / total_credits) if total_credits > 0 else 0.0

    # All cost rows in window — these are the universe of metered requests.
    cost_rows = await db.provider_cost_events.find(
        {"created_at": {"$gte": since}, "is_priced": True},
        {"_id": 0},
    ).to_list(length=10000)

    # Bulk-load matching credit_events by request_id (one read per unique
    # request_id; small list in practice). If a row has no request_id, we
    # treat credits_deducted = 0 (no revenue attributable).
    request_ids = [r["request_id"] for r in cost_rows if r.get("request_id")]
    credits_by_request: dict[str, int] = {}
    if request_ids:
        async for ce in db.credit_events.find(
            {"request_id": {"$in": request_ids}, "delta": {"$lt": 0}},
            {"_id": 0, "request_id": 1, "delta": 1},
        ):
            rid = ce.get("request_id")
            if rid:
                credits_by_request[rid] = credits_by_request.get(rid, 0) + abs(int(ce["delta"]))

    enriched: list[dict] = []
    flagged_count = 0
    total_negative_margin = 0.0
    by_feature: dict[str, dict] = {}
    by_severity: dict[str, int] = {"critical": 0, "warning": 0, "info": 0, "ok": 0, "unknown": 0}

    for row in cost_rows:
        rid = row.get("request_id")
        credits = credits_by_request.get(rid, 0) if rid else 0
        cost_inr = float(row.get("cost_inr") or 0)
        revenue_inr = round(credits * revenue_per_credit, 4)
        margin_inr = round(revenue_inr - cost_inr, 4)
        margin_pct = round((margin_inr / revenue_inr) * 100, 2) if revenue_inr > 0 else None
        sev = _severity_for_margin_pct(margin_pct)
        enriched_row = {
            "cost_id": row.get("cost_id"),
            "created_at": row.get("created_at"),
            "user_id": row.get("user_id"),
            "request_id": rid,
            "feature": row.get("feature") or "unknown",
            "surface": row.get("surface"),
            "provider": row.get("provider"),
            "model": row.get("model"),
            "cost_method": row.get("cost_method"),
            "credits_deducted": credits,
            "metered_cost_inr": round(cost_inr, 4),
            "estimated_revenue_inr": revenue_inr,
            "margin_inr": margin_inr,
            "margin_pct": margin_pct,
            "severity": sev,
        }
        enriched.append(enriched_row)

        by_severity[sev] = by_severity.get(sev, 0) + 1
        feat = enriched_row["feature"]
        b = by_feature.setdefault(feat, {
            "feature": feat, "requests": 0, "cost_inr": 0.0,
            "revenue_inr": 0.0, "margin_inr": 0.0, "flagged": 0,
        })
        b["requests"] += 1
        b["cost_inr"] += cost_inr
        b["revenue_inr"] += revenue_inr
        b["margin_inr"] += margin_inr
        if sev in ("warning", "critical"):
            flagged_count += 1
            b["flagged"] += 1
            if margin_inr < 0:
                total_negative_margin += margin_inr

    # Round bucket numbers
    for b in by_feature.values():
        b["cost_inr"] = round(b["cost_inr"], 2)
        b["revenue_inr"] = round(b["revenue_inr"], 2)
        b["margin_inr"] = round(b["margin_inr"], 2)

    # Build per-feature user-spend share map (for abuse-pattern detection) +
    # per-feature model-share map (for model-downgrade recommendations).
    user_spend_per_feature: dict[tuple[str, str], float] = {}
    model_spend_per_feature: dict[tuple[str, str], float] = {}
    for r in cost_rows:
        feat = r.get("feature") or "unknown"
        uid = r.get("user_id") or "anonymous"
        model = r.get("model") or "unknown"
        c = float(r.get("cost_inr") or 0)
        user_spend_per_feature[(feat, uid)] = user_spend_per_feature.get((feat, uid), 0.0) + c
        model_spend_per_feature[(feat, model)] = model_spend_per_feature.get((feat, model), 0.0) + c

    # Suspect-data warnings (margin < -1000% — almost certainly a config bug)
    suspect_rows = []

    for row in enriched:
        feat = row["feature"]
        bucket = by_feature.get(feat, {})
        total_feat_cost = bucket.get("cost_inr") or 0
        # User share
        user_cost = user_spend_per_feature.get((feat, row.get("user_id") or "anonymous"), 0)
        user_share = (user_cost / total_feat_cost) if total_feat_cost > 0 else 0.0
        # Top model share
        model_buckets = [(m, c) for (f, m), c in model_spend_per_feature.items() if f == feat]
        top_model = None
        top_model_share = 0.0
        if model_buckets:
            top_model, top_cost = max(model_buckets, key=lambda x: x[1])
            top_model_share = (top_cost / total_feat_cost) if total_feat_cost > 0 else 0.0
        row["recovery_action"] = _recovery_action(
            row=row, feature_bucket=bucket,
            user_spend_share=user_share,
            top_model_share=top_model_share,
            top_model=top_model,
        )
        if row.get("margin_pct") is not None and row["margin_pct"] < SUSPECT_MARGIN_PCT:
            suspect_rows.append({
                "cost_id": row["cost_id"],
                "feature": feat,
                "margin_pct": row["margin_pct"],
                "metered_cost_inr": row["metered_cost_inr"],
                "credits_deducted": row["credits_deducted"],
            })

    top_expensive = sorted(enriched, key=lambda r: r["metered_cost_inr"], reverse=True)[:top_n]
    top_losses = sorted(
        [r for r in enriched if r["severity"] in ("warning", "critical")],
        key=lambda r: r["margin_inr"],
    )[:top_n]

    return {
        "window_days": days,
        "computed_at": _now_iso(),
        "revenue_per_credit_inr": round(revenue_per_credit, 6),
        "summary": {
            "total_requests_analyzed": len(enriched),
            "total_flagged": flagged_count,
            "total_negative_margin_inr": round(total_negative_margin, 2),
            "by_severity": by_severity,
            "by_feature": sorted(by_feature.values(), key=lambda b: b["margin_inr"]),
        },
        "top_expensive": top_expensive,
        "top_losses": top_losses,
        "validation": {
            "suspect_margin_threshold_pct": SUSPECT_MARGIN_PCT,
            "suspect_rows_count": len(suspect_rows),
            "suspect_rows_sample": suspect_rows[:5],
            "has_suspect_data": len(suspect_rows) > 0,
            "warning": (
                f"{len(suspect_rows)} request(s) have margin worse than "
                f"{SUSPECT_MARGIN_PCT}%. This is almost always a configuration "
                f"problem (missing credit deduction, wrong USD→INR rate, or "
                f"seeded test data) rather than a real loss. Validate before "
                f"acting on the dashboard's recommendations."
            ) if suspect_rows else None,
        },
        "thresholds": {
            "critical_margin_pct": -20,
            "warning_margin_pct": 0,
            "info_margin_pct": 10,
        },
    }
