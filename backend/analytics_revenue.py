"""
Admin Revenue Dashboard — READ-ONLY instrumentation.

Six sections (mirrors the founder spec):
  1. Funnel
  2. Revenue
  3. Credit Economy
  4. Emotional Gravity
  5. Cohorts
  6. Operational Health

Hard rules baked into this module:
  - No interpretation, no recommendations, no "AI insights"
  - No write mutations beyond a single `funnel_events` log endpoint used by
    the frontend to record a pricing_view event (one write per page visit).
    Paywall hits are written by credit_guard.py (one write per 402).
  - Every endpoint is admin-only.
  - Every section supports ?format=csv for export.
  - Aggregations run live against existing collections; daily materialization
    is intentionally deferred until query times grow.

Source-of-truth collections used:
  users, payment_orders, payment_refunds, credit_events, paywall_events,
  funnel_events, webhook_logs, admin_alerts, login_events, anonymous_messages,
  debate_arguments, translation_messages, voice_usage_events,
  avatar_chat_messages, clone_messages, smart_reply_sessions, delayed_messages
"""
from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from db import db
from auth import get_current_user, get_optional_user
from credits import is_admin_unlimited_user, CREDIT_COST
from models import now_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/revenue", tags=["admin-revenue"])
public_router = APIRouter(tags=["funnel-events"])

# Surface-to-collection map for "longest-session" computations.
# Each entry: (collection, user-id field, conversation/session-id field, timestamp field)
SURFACE_THREAD_MAP = {
    "clone_chat": ("clone_messages", None, "conversation_id", "created_at"),
    "mood_chat": ("clone_messages", None, "conversation_id", "created_at"),
    "anonymous_chat": ("anonymous_messages", "session_id", "room_slug", "created_at"),
    "debate_chat": ("debate_arguments", "user_id", "debate_id", "created_at"),
    "translation_chat": ("translation_messages", "sender_id", "room_id", "created_at"),
    "voice_message": ("generated_messages", "user_id", "voice_session_id", "created_at"),
    "video_avatar": ("avatar_chat_messages", "user_id", "conversation_id", "created_at"),
    "smart_reply": ("smart_reply_sessions", "user_id", "session_id", "created_at"),
    "delayed_create": ("delayed_messages", "sender_user_id", "delayed_message_id", "created_at"),
}

MONETIZED_SURFACES = list(CREDIT_COST.keys())


def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if not is_admin_unlimited_user(user) and (user.get("role") != "admin"):
        raise HTTPException(403, "Admin only")
    return user


def _window_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    if not rows:
        rows = [{"empty": "no_data"}]
    # Compute union of all keys so heterogeneous row shapes don't blow up DictWriter.
    fieldnames: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


def _maybe_csv(data: dict, rows_key: str, format_q: Optional[str], filename: str):
    if (format_q or "").lower() == "csv":
        return _csv_response(data.get(rows_key) or [], filename)
    return data


# ============================================================================
# Public — funnel event ingestion (the only write path)
# ============================================================================
@public_router.post("/api/funnel/event")
async def log_funnel_event(payload: dict, request: Request, user: Optional[dict] = Depends(get_optional_user)):
    """ONE write per call. Currently used for `pricing_view`. Admin caller events
    are still logged but flagged for filtering later if needed.
    """
    event_name = (payload or {}).get("event_name") or ""
    if event_name not in {"pricing_view"}:
        # Whitelist-only — never accept arbitrary client-supplied event names.
        raise HTTPException(400, "Unsupported event_name")
    await db.funnel_events.insert_one({
        "event_id": uuid.uuid4().hex,
        "event_name": event_name,
        "user_id": (user or {}).get("user_id"),
        "email": (user or {}).get("email"),
        "is_admin": bool(user and is_admin_unlimited_user(user)),
        "country_code": (user or {}).get("country_code"),
        "plan_id": (user or {}).get("plan_id"),
        "plan_status": (user or {}).get("plan_status"),
        "referrer": (payload or {}).get("referrer"),
        "created_at": now_iso(),
    })
    return {"ok": True}


# ============================================================================
# 1. FUNNEL
# ============================================================================
@router.get("/funnel")
async def funnel(_admin: dict = Depends(_require_admin), days: int = Query(30, ge=1, le=365), format: Optional[str] = None):
    since = _window_iso(days)

    signups = await db.users.count_documents({"created_at": {"$gte": since}})

    # signup → pricing_view: distinct users who created an account in window AND
    # later hit /pricing. We measure the JOIN via user_id presence on funnel_events.
    pricing_viewers = await db.funnel_events.distinct("user_id", {
        "event_name": "pricing_view",
        "created_at": {"$gte": since},
        "is_admin": {"$ne": True},
    })
    pricing_views_total = await db.funnel_events.count_documents({
        "event_name": "pricing_view",
        "created_at": {"$gte": since},
        "is_admin": {"$ne": True},
    })

    # first_402 (paywall hit) per user
    first_paywall_users = await db.paywall_events.distinct("user_id", {
        "created_at": {"$gte": since},
    })
    paywall_hits_total = await db.paywall_events.count_documents({"created_at": {"$gte": since}})

    # checkout_start = payment_orders.created_at (any kind)
    checkouts_started_total = await db.payment_orders.count_documents({"created_at": {"$gte": since}})
    checkout_users = await db.payment_orders.distinct("user_id", {"created_at": {"$gte": since}})

    # payment_success
    paid_total = await db.payment_orders.count_documents({"status": "paid", "credited_at": {"$gte": since}})
    paid_users = await db.payment_orders.distinct("user_id", {"status": "paid", "credited_at": {"$gte": since}})

    # first_return_after_payment: paid users who had a login_event AFTER their
    # most-recent payment in window.
    first_return_count = 0
    for uid in paid_users:
        last_pay = await db.payment_orders.find_one(
            {"user_id": uid, "status": "paid"}, {"_id": 0, "credited_at": 1}, sort=[("credited_at", -1)]
        )
        if not last_pay or not last_pay.get("credited_at"):
            continue
        ret = await db.login_events.count_documents({
            "user_id": uid,
            "event_type": {"$in": ["login_success", "session_resume"]},
            "created_at": {"$gt": last_pay["credited_at"]},
        })
        if ret > 0:
            first_return_count += 1

    # Top-up repeat rate: of users with >=1 paid topup, % with >=2
    topup_buyers = await db.payment_orders.aggregate([
        {"$match": {"kind": "topup", "status": "paid", "credited_at": {"$gte": since}}},
        {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
    ]).to_list(10000)
    topup_total_buyers = len(topup_buyers)
    topup_repeat_buyers = sum(1 for b in topup_buyers if b["n"] >= 2)
    topup_repeat_rate_pct = round(100 * topup_repeat_buyers / topup_total_buyers, 1) if topup_total_buyers else 0.0

    data = {
        "window_days": days,
        "steps": [
            {"step": "signup", "value": signups, "unit": "users"},
            {"step": "pricing_view", "value": len(pricing_viewers), "unit": "unique_users", "secondary": {"total_views": pricing_views_total}},
            {"step": "first_402_paywall", "value": len(first_paywall_users), "unit": "unique_users", "secondary": {"total_hits": paywall_hits_total}},
            {"step": "checkout_start", "value": len(checkout_users), "unit": "unique_users", "secondary": {"total_orders": checkouts_started_total}},
            {"step": "payment_success", "value": len(paid_users), "unit": "unique_users", "secondary": {"total_paid_orders": paid_total}},
            {"step": "first_return_after_payment", "value": first_return_count, "unit": "users"},
        ],
        "conversion_pct": {
            "signup_to_pricing_view": _pct(len(pricing_viewers), signups),
            "pricing_view_to_first_paywall": _pct(len(first_paywall_users), len(pricing_viewers)),
            "first_paywall_to_checkout_start": _pct(len(checkout_users), len(first_paywall_users)),
            "checkout_start_to_payment_success": _pct(len(paid_users), len(checkout_users)),
            "payment_success_to_return": _pct(first_return_count, len(paid_users)),
        },
        "topup_repeat": {
            "buyers": topup_total_buyers,
            "repeat_buyers": topup_repeat_buyers,
            "repeat_rate_pct": topup_repeat_rate_pct,
        },
    }
    if (format or "").lower() == "csv":
        flat = [{"metric": s["step"], "value": s["value"], "unit": s["unit"]} for s in data["steps"]]
        for k, v in data["conversion_pct"].items():
            flat.append({"metric": f"pct_{k}", "value": v, "unit": "percent"})
        flat.append({"metric": "topup_repeat_rate_pct", "value": topup_repeat_rate_pct, "unit": "percent"})
        return _csv_response(flat, f"funnel_{days}d")
    return data


def _pct(num, denom) -> float:
    if not denom:
        return 0.0
    return round(100 * num / denom, 1)


# ============================================================================
# 2. REVENUE
# ============================================================================
@router.get("/revenue")
async def revenue(_admin: dict = Depends(_require_admin), days: int = Query(30, ge=1, le=365), format: Optional[str] = None):
    since = _window_iso(days)

    # MRR — active non-free subscribers count × monthly INR price.
    # Plans live in DB (subscription_plans). Active = plan_status=active.
    plans = await db.subscription_plans.find({}, {"_id": 0, "plan_id": 1, "price_inr": 1, "name": 1, "monthly_credits": 1}).to_list(20)
    plan_by_id = {p["plan_id"]: p for p in plans}

    active_by_plan_rows = await db.users.aggregate([
        {"$match": {"plan_status": "active", "plan_id": {"$nin": [None, "free"]}}},
        {"$group": {"_id": "$plan_id", "n": {"$sum": 1}}},
    ]).to_list(20)
    active_by_plan = []
    mrr_inr = 0
    for r in active_by_plan_rows:
        p = plan_by_id.get(r["_id"]) or {}
        price = p.get("price_inr") or 0
        active_by_plan.append({"plan_id": r["_id"], "plan_name": p.get("name") or r["_id"], "active_users": r["n"], "monthly_price_inr": price, "revenue_inr": r["n"] * price})
        mrr_inr += r["n"] * price

    # Subscription paid orders in window (revenue actually banked)
    sub_revenue_rows = await db.payment_orders.aggregate([
        {"$match": {"status": "paid", "credited_at": {"$gte": since}, "$or": [{"kind": {"$exists": False}}, {"kind": "subscription"}]}},
        {"$group": {
            "_id": "$plan_id",
            "orders": {"$sum": 1},
            "revenue_inr": {"$sum": {"$ifNull": ["$amount_inr", 0]}},
        }},
    ]).to_list(20)
    subscription_revenue_window = [{"plan_id": r["_id"], "orders": r["orders"], "revenue_inr": r["revenue_inr"]} for r in sub_revenue_rows]
    subscription_revenue_window_total = sum(r["revenue_inr"] for r in subscription_revenue_window)

    # Top-up revenue
    topup_revenue_rows = await db.payment_orders.aggregate([
        {"$match": {"status": "paid", "kind": "topup", "credited_at": {"$gte": since}}},
        {"$group": {
            "_id": "$pack_id",
            "orders": {"$sum": 1},
            "credits_delivered": {"$sum": "$credits"},
            "revenue_inr": {"$sum": {"$ifNull": ["$amount_inr", 0]}},
        }},
    ]).to_list(20)
    topup_revenue = [{"pack_id": r["_id"], "orders": r["orders"], "credits_delivered": r["credits_delivered"], "revenue_inr": r["revenue_inr"]} for r in topup_revenue_rows]
    topup_revenue_window_total = sum(r["revenue_inr"] for r in topup_revenue)

    # Refunds & chargebacks
    refunds_rows = await db.payment_refunds.aggregate([
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}, "amount": {"$sum": {"$ifNull": ["$amount", 0]}}}},
    ]).to_list(20)
    refunds_total_amount = sum(r["amount"] for r in refunds_rows if r["_id"] in ("SUCCESS", "PAID"))
    chargebacks = await db.admin_alerts.count_documents({"kind": "chargeback", "created_at": {"$gte": since}})

    # ARPU — total paid revenue (window) / total paying users (window)
    paying_users = await db.payment_orders.distinct("user_id", {"status": "paid", "credited_at": {"$gte": since}})
    arpu_inr = round((subscription_revenue_window_total + topup_revenue_window_total) / max(1, len(paying_users)), 2) if paying_users else 0.0

    # Revenue by country/currency
    by_country_rows = await db.payment_orders.aggregate([
        {"$match": {"status": "paid", "credited_at": {"$gte": since}}},
        {"$group": {
            "_id": {"country": "$country_code", "display_currency": "$display_currency"},
            "orders": {"$sum": 1},
            "revenue_display": {"$sum": {"$ifNull": ["$display_amount", 0]}},
            "revenue_charge_inr": {"$sum": {"$ifNull": ["$amount_inr", 0]}},
        }},
        {"$sort": {"revenue_charge_inr": -1}},
    ]).to_list(200)
    by_country = [{
        "country_code": r["_id"].get("country") or "unknown",
        "display_currency": r["_id"].get("display_currency") or "unknown",
        "orders": r["orders"],
        "revenue_in_display_currency": r["revenue_display"],
        "revenue_in_inr": r["revenue_charge_inr"],
    } for r in by_country_rows]

    # Revenue by feature surface — top-ups don't tie to a surface, subscriptions
    # don't either; the closest read is "which surface drove the deduction"
    # within the user's first paid month. We expose it as credit_value_consumed
    # per surface (deduct events × cost) within the window. This is INR-proxy,
    # not gateway revenue.
    consumed_rows = await db.credit_events.aggregate([
        {"$match": {"kind": "deduct", "created_at": {"$gte": since}}},
        {"$group": {"_id": "$surface", "credits_consumed": {"$sum": {"$abs": "$delta"}}, "events": {"$sum": 1}}},
        {"$sort": {"credits_consumed": -1}},
    ]).to_list(50)
    by_surface = [{
        "surface": r["_id"],
        "events": r["events"],
        "credits_consumed": r["credits_consumed"],
    } for r in consumed_rows]

    data = {
        "window_days": days,
        "mrr_inr": mrr_inr,
        "active_subscriptions_by_plan": active_by_plan,
        "subscription_revenue_window_inr": subscription_revenue_window_total,
        "subscription_revenue_window_by_plan": subscription_revenue_window,
        "topup_revenue_window_inr": topup_revenue_window_total,
        "topup_revenue_window_by_pack": topup_revenue,
        "refunds_window": {
            "by_status": [{"status": r["_id"], "n": r["n"], "amount": r["amount"]} for r in refunds_rows],
            "succeeded_amount": refunds_total_amount,
        },
        "chargebacks_window": chargebacks,
        "arpu_inr_window": arpu_inr,
        "paying_users_window": len(paying_users),
        "revenue_by_country": by_country,
        "credit_consumption_by_surface": by_surface,
    }
    if (format or "").lower() == "csv":
        flat = []
        flat.append({"metric": "mrr_inr", "value": mrr_inr})
        flat.append({"metric": "subscription_revenue_window_inr", "value": subscription_revenue_window_total})
        flat.append({"metric": "topup_revenue_window_inr", "value": topup_revenue_window_total})
        flat.append({"metric": "arpu_inr_window", "value": arpu_inr})
        flat.append({"metric": "paying_users_window", "value": len(paying_users)})
        flat.append({"metric": "chargebacks_window", "value": chargebacks})
        flat.append({"metric": "refunds_succeeded_amount", "value": refunds_total_amount})
        for r in active_by_plan:
            flat.append({"metric": f"active_{r['plan_id']}", "value": r["active_users"]})
        for r in by_country:
            flat.append({"metric": f"revenue_inr_{r['country_code']}", "value": r["revenue_in_inr"]})
        return _csv_response(flat, f"revenue_{days}d")
    return data


# ============================================================================
# 3. CREDIT ECONOMY
# ============================================================================
@router.get("/credit-economy")
async def credit_economy(_admin: dict = Depends(_require_admin), days: int = Query(30, ge=1, le=365), format: Optional[str] = None):
    since = _window_iso(days)

    purchased = await db.credit_events.aggregate([
        {"$match": {"kind": "grant", "created_at": {"$gte": since}}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "credits": {"$sum": "$delta"}}},
    ]).to_list(1)
    consumed = await db.credit_events.aggregate([
        {"$match": {"kind": "deduct", "created_at": {"$gte": since}}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "credits": {"$sum": {"$abs": "$delta"}}}},
    ]).to_list(1)
    refunded = await db.credit_events.aggregate([
        {"$match": {"kind": "refund", "created_at": {"$gte": since}}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "credits": {"$sum": "$delta"}}},
    ]).to_list(1)

    p_credits = (purchased[0]["credits"] if purchased else 0) or 0
    c_credits = (consumed[0]["credits"] if consumed else 0) or 0
    r_credits = (refunded[0]["credits"] if refunded else 0) or 0

    # Burn rate by surface (credits consumed in window per surface)
    by_surface_rows = await db.credit_events.aggregate([
        {"$match": {"kind": "deduct", "created_at": {"$gte": since}}},
        {"$group": {"_id": "$surface", "events": {"$sum": 1}, "credits": {"$sum": {"$abs": "$delta"}}}},
    ]).to_list(50)
    burn_by_surface = sorted(
        [{
            "surface": r["_id"],
            "events": r["events"],
            "credits_consumed": r["credits"],
            "credit_cost_per_event": CREDIT_COST.get(r["_id"]),
        } for r in by_surface_rows],
        key=lambda x: x["credits_consumed"],
        reverse=True,
    )

    # Refund-rate by surface (how often the AI fails / generation fails)
    refunds_by_surface_rows = await db.credit_events.aggregate([
        {"$match": {"kind": "refund", "created_at": {"$gte": since}}},
        {"$group": {"_id": "$surface", "refund_events": {"$sum": 1}, "refund_credits": {"$sum": "$delta"}}},
    ]).to_list(50)
    refund_map = {r["_id"]: r for r in refunds_by_surface_rows}
    for row in burn_by_surface:
        rr = refund_map.get(row["surface"])
        row["refund_events"] = rr["refund_events"] if rr else 0
        row["refund_rate_pct"] = _pct(row["refund_events"], row["events"])

    data = {
        "window_days": days,
        "credits_purchased": p_credits,
        "credits_consumed": c_credits,
        "credits_refunded": r_credits,
        "net_outstanding_in_window": p_credits - c_credits + r_credits,
        "burn_by_surface": burn_by_surface,
        "highest_cost_surfaces": sorted(burn_by_surface, key=lambda x: x["credits_consumed"], reverse=True)[:5],
        "highest_margin_surfaces_by_volume": sorted(
            burn_by_surface,
            key=lambda x: (x["credits_consumed"] * (x.get("credit_cost_per_event") or 0)),
            reverse=True,
        )[:5],
    }
    if (format or "").lower() == "csv":
        flat = [
            {"metric": "credits_purchased", "value": p_credits},
            {"metric": "credits_consumed", "value": c_credits},
            {"metric": "credits_refunded", "value": r_credits},
            {"metric": "net_outstanding", "value": p_credits - c_credits + r_credits},
        ]
        for r in burn_by_surface:
            flat.append({"metric": f"burn_{r['surface']}", "value": r["credits_consumed"]})
            flat.append({"metric": f"refund_rate_pct_{r['surface']}", "value": r["refund_rate_pct"]})
        return _csv_response(flat, f"credit_economy_{days}d")
    return data


# ============================================================================
# 4. EMOTIONAL GRAVITY
# ============================================================================
@router.get("/emotional-gravity")
async def emotional_gravity(_admin: dict = Depends(_require_admin), days: int = Query(90, ge=1, le=365), format: Optional[str] = None):
    since = _window_iso(days)

    # first_paid_intent_surface: per user, the surface of their FIRST paywall hit
    first_paywall_per_user = await db.paywall_events.aggregate([
        {"$match": {"created_at": {"$gte": since}, "user_id": {"$ne": None}}},
        {"$sort": {"created_at": 1}},
        {"$group": {"_id": "$user_id", "surface": {"$first": "$surface"}, "code": {"$first": "$code"}, "created_at": {"$first": "$created_at"}}},
    ]).to_list(50000)
    first_intent_counts: dict = {}
    for row in first_paywall_per_user:
        first_intent_counts[row["surface"]] = first_intent_counts.get(row["surface"], 0) + 1
    first_paid_intent_surface = sorted(
        [{"surface": k, "users": v} for k, v in first_intent_counts.items()],
        key=lambda x: x["users"],
        reverse=True,
    )

    # first_successful_payment_surface: per user, the surface of the LAST
    # paywall_hit BEFORE their first successful payment.
    paid_users_rows = await db.payment_orders.aggregate([
        {"$match": {"status": "paid", "credited_at": {"$gte": since}}},
        {"$sort": {"credited_at": 1}},
        {"$group": {"_id": "$user_id", "first_paid_at": {"$first": "$credited_at"}}},
    ]).to_list(10000)
    fsp_counts: dict = {}
    for pu in paid_users_rows:
        pw = await db.paywall_events.find_one(
            {"user_id": pu["_id"], "created_at": {"$lt": pu["first_paid_at"]}},
            {"_id": 0, "surface": 1},
            sort=[("created_at", -1)],
        )
        if pw and pw.get("surface"):
            fsp_counts[pw["surface"]] = fsp_counts.get(pw["surface"], 0) + 1
    first_successful_payment_surface = sorted(
        [{"surface": k, "users": v} for k, v in fsp_counts.items()],
        key=lambda x: x["users"],
        reverse=True,
    )

    # repeat_return_surface: for users with >=2 distinct days of credit_events,
    # the surface they used most on their second-or-later day.
    pipeline = [
        {"$match": {"kind": "deduct", "created_at": {"$gte": since}}},
        {"$project": {
            "user_id": 1,
            "surface": 1,
            "day": {"$substr": ["$created_at", 0, 10]},
        }},
        {"$group": {"_id": {"user_id": "$user_id", "day": "$day", "surface": "$surface"}, "n": {"$sum": 1}}},
    ]
    rows = await db.credit_events.aggregate(pipeline).to_list(50000)
    by_user: dict = {}
    for r in rows:
        uid = r["_id"]["user_id"]; day = r["_id"]["day"]; surf = r["_id"]["surface"]
        by_user.setdefault(uid, []).append((day, surf, r["n"]))
    rr_counts: dict = {}
    for uid, items in by_user.items():
        days_set = sorted({d for d, _, _ in items})
        if len(days_set) < 2:
            continue
        repeat_days = set(days_set[1:])
        for d, surf, n in items:
            if d in repeat_days:
                rr_counts[surf] = rr_counts.get(surf, 0) + n
    repeat_return_surface = sorted(
        [{"surface": k, "events_on_return_days": v} for k, v in rr_counts.items()],
        key=lambda x: x["events_on_return_days"],
        reverse=True,
    )

    # longest_session_surface: per surface, median messages per conversation/session
    longest_session = []
    for surface, (coll, _user_field, thread_field, _ts_field) in SURFACE_THREAD_MAP.items():
        try:
            agg = await db[coll].aggregate([
                {"$match": {"created_at": {"$gte": since}}},
                {"$group": {"_id": f"${thread_field}", "msgs": {"$sum": 1}}},
            ]).to_list(20000)
            counts = sorted([r["msgs"] for r in agg if r["msgs"] > 0])
            if not counts:
                continue
            median = counts[len(counts)//2]
            longest_session.append({
                "surface": surface,
                "threads": len(counts),
                "median_messages_per_thread": median,
                "max_messages_in_thread": counts[-1],
                "p90_messages_per_thread": counts[max(0, int(len(counts)*0.9) - 1)],
            })
        except Exception as e:
            logger.warning("longest_session compute failed for surface=%s: %s", surface, e)
    longest_session.sort(key=lambda x: x["p90_messages_per_thread"], reverse=True)

    # highest_top_up_correlation_surface: users with >=1 paid topup → their
    # most-deducted surface in the 14 days BEFORE the topup.
    topup_orders = await db.payment_orders.find(
        {"kind": "topup", "status": "paid", "credited_at": {"$gte": since}},
        {"_id": 0, "user_id": 1, "credited_at": 1},
    ).to_list(10000)
    topup_surf_counts: dict = {}
    for o in topup_orders:
        uid = o.get("user_id"); ts = o.get("credited_at")
        if not (uid and ts):
            continue
        try:
            window_start = (datetime.fromisoformat(ts.replace("Z","+00:00")) - timedelta(days=14)).isoformat()
        except Exception:
            continue
        rows = await db.credit_events.aggregate([
            {"$match": {"user_id": uid, "kind": "deduct", "created_at": {"$gte": window_start, "$lt": ts}}},
            {"$group": {"_id": "$surface", "credits": {"$sum": {"$abs": "$delta"}}}},
            {"$sort": {"credits": -1}},
            {"$limit": 1},
        ]).to_list(1)
        if rows:
            s = rows[0]["_id"]
            topup_surf_counts[s] = topup_surf_counts.get(s, 0) + 1
    topup_correlation = sorted(
        [{"surface": k, "topup_purchases_preceded_by_this_surface": v} for k, v in topup_surf_counts.items()],
        key=lambda x: x["topup_purchases_preceded_by_this_surface"],
        reverse=True,
    )

    data = {
        "window_days": days,
        "first_paid_intent_surface": first_paid_intent_surface,
        "first_successful_payment_surface": first_successful_payment_surface,
        "repeat_return_surface": repeat_return_surface,
        "longest_session_surface": longest_session,
        "highest_top_up_correlation_surface": topup_correlation,
    }
    if (format or "").lower() == "csv":
        flat = []
        for r in first_paid_intent_surface:
            flat.append({"metric": "first_paid_intent_users", "surface": r["surface"], "value": r["users"]})
        for r in first_successful_payment_surface:
            flat.append({"metric": "first_successful_payment_users", "surface": r["surface"], "value": r["users"]})
        for r in repeat_return_surface:
            flat.append({"metric": "repeat_return_events", "surface": r["surface"], "value": r["events_on_return_days"]})
        for r in longest_session:
            flat.append({"metric": "p90_messages_per_thread", "surface": r["surface"], "value": r["p90_messages_per_thread"]})
        for r in topup_correlation:
            flat.append({"metric": "topup_preceded_by_surface", "surface": r["surface"], "value": r["topup_purchases_preceded_by_this_surface"]})
        return _csv_response(flat, f"emotional_gravity_{days}d")
    return data


# ============================================================================
# 5. COHORTS
# ============================================================================
@router.get("/cohorts")
async def cohorts(_admin: dict = Depends(_require_admin), weeks: int = Query(12, ge=1, le=52), format: Optional[str] = None):
    since = _window_iso(weeks * 7)
    users = await db.users.find(
        {"created_at": {"$gte": since}, "email": {"$ne": None}},
        {"_id": 0, "user_id": 1, "email": 1, "created_at": 1, "plan_id": 1},
    ).to_list(50000)

    # Helper: did user have any login_event on day X after signup?
    async def returned_on_day(user_id: str, created_at: str, day: int) -> bool:
        try:
            base = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except Exception:
            return False
        start = base + timedelta(days=day)
        end = start + timedelta(days=1)
        n = await db.login_events.count_documents({
            "user_id": user_id,
            "event_type": {"$in": ["login_success", "session_resume"]},
            "created_at": {"$gte": start.isoformat(), "$lt": end.isoformat()},
        })
        return n > 0

    # Cohort buckets keyed by ISO year-week of signup
    cohort_map: dict = {}
    for u in users:
        ts = u.get("created_at") or ""
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        wk = d.strftime("%G-W%V")
        cohort_map.setdefault(wk, []).append(u)

    cohort_rows = []
    for wk, items in sorted(cohort_map.items()):
        d1 = d7 = d30 = 0
        for u in items:
            uid = u["user_id"]; created = u.get("created_at") or ""
            if await returned_on_day(uid, created, 1):
                d1 += 1
            if await returned_on_day(uid, created, 7):
                d7 += 1
            if await returned_on_day(uid, created, 30):
                d30 += 1
        cohort_rows.append({
            "cohort_week": wk,
            "signups": len(items),
            "d1_return": d1,
            "d7_return": d7,
            "d30_return": d30,
            "d1_pct": _pct(d1, len(items)),
            "d7_pct": _pct(d7, len(items)),
            "d30_pct": _pct(d30, len(items)),
        })

    # By plan tier
    plan_rows = await db.users.aggregate([
        {"$group": {"_id": "$plan_id", "n": {"$sum": 1}}},
    ]).to_list(20)
    by_plan = [{"plan_id": r["_id"] or "unknown", "users": r["n"]} for r in plan_rows]

    # By first paid surface (joins emotional_gravity output)
    first_paid_paywall = await db.paywall_events.aggregate([
        {"$match": {"user_id": {"$ne": None}}},
        {"$sort": {"created_at": 1}},
        {"$group": {"_id": "$user_id", "surface": {"$first": "$surface"}}},
    ]).to_list(50000)
    fps_counts: dict = {}
    for r in first_paid_paywall:
        fps_counts[r["surface"]] = fps_counts.get(r["surface"], 0) + 1
    by_first_paid_surface = sorted(
        [{"surface": k, "users": v} for k, v in fps_counts.items()],
        key=lambda x: x["users"],
        reverse=True,
    )

    data = {
        "weeks": weeks,
        "by_acquisition_week": cohort_rows,
        "by_plan_tier": by_plan,
        "by_first_paywall_surface": by_first_paid_surface,
    }
    if (format or "").lower() == "csv":
        flat = []
        for r in cohort_rows:
            flat.append({"metric": "signups", "cohort_week": r["cohort_week"], "value": r["signups"]})
            flat.append({"metric": "d1_return", "cohort_week": r["cohort_week"], "value": r["d1_return"]})
            flat.append({"metric": "d7_return", "cohort_week": r["cohort_week"], "value": r["d7_return"]})
            flat.append({"metric": "d30_return", "cohort_week": r["cohort_week"], "value": r["d30_return"]})
        for r in by_plan:
            flat.append({"metric": "users_by_plan", "plan_id": r["plan_id"], "value": r["users"]})
        return _csv_response(flat, f"cohorts_{weeks}w")
    return data


# ============================================================================
# 6. OPERATIONAL HEALTH
# ============================================================================
@router.get("/operational-health")
async def operational_health(_admin: dict = Depends(_require_admin), days: int = Query(30, ge=1, le=365), format: Optional[str] = None):
    since = _window_iso(days)

    # Payment failure %
    created = await db.payment_orders.count_documents({"created_at": {"$gte": since}})
    failed = await db.payment_orders.count_documents({"status": "failed", "updated_at": {"$gte": since}})
    paid = await db.payment_orders.count_documents({"status": "paid", "credited_at": {"$gte": since}})

    # Webhook rejection %
    wh_total = await db.webhook_logs.count_documents({"received_at": {"$gte": since}})
    wh_rejected = await db.webhook_logs.count_documents({
        "received_at": {"$gte": since},
        "result": {"$in": ["rejected_signature", "rejected_replay", "amount_mismatch", "currency_mismatch", "manual_review_required"]},
    })
    wh_breakdown_rows = await db.webhook_logs.aggregate([
        {"$match": {"received_at": {"$gte": since}}},
        {"$group": {"_id": "$result", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]).to_list(50)

    refunds_total = await db.payment_refunds.count_documents({"created_at": {"$gte": since}})
    chargebacks = await db.admin_alerts.count_documents({"kind": "chargeback", "created_at": {"$gte": since}})

    # AI failure refund rate by surface (refund kind / deduct kind)
    deducts = await db.credit_events.aggregate([
        {"$match": {"kind": "deduct", "created_at": {"$gte": since}}},
        {"$group": {"_id": "$surface", "n": {"$sum": 1}}},
    ]).to_list(50)
    refunds_per_surface = await db.credit_events.aggregate([
        {"$match": {"kind": "refund", "created_at": {"$gte": since}}},
        {"$group": {"_id": "$surface", "n": {"$sum": 1}}},
    ]).to_list(50)
    d_map = {r["_id"]: r["n"] for r in deducts}
    ai_refund = []
    for s, n in d_map.items():
        r_n = next((x["n"] for x in refunds_per_surface if x["_id"] == s), 0)
        ai_refund.append({"surface": s, "deductions": n, "refunds": r_n, "ai_failure_refund_rate_pct": _pct(r_n, n)})
    ai_refund.sort(key=lambda x: x["ai_failure_refund_rate_pct"], reverse=True)

    data = {
        "window_days": days,
        "payment_orders_created": created,
        "payment_orders_paid": paid,
        "payment_orders_failed": failed,
        "payment_failure_pct": _pct(failed, created),
        "payment_success_pct": _pct(paid, created),
        "webhook_total": wh_total,
        "webhook_rejected": wh_rejected,
        "webhook_rejection_pct": _pct(wh_rejected, wh_total),
        "webhook_result_breakdown": [{"result": r["_id"] or "unknown", "n": r["n"]} for r in wh_breakdown_rows],
        "refunds": refunds_total,
        "refund_pct_of_paid": _pct(refunds_total, paid),
        "chargebacks": chargebacks,
        "chargeback_pct_of_paid": _pct(chargebacks, paid),
        "ai_failure_refund_rate_by_surface": ai_refund,
        # Avg response latency by surface — not currently instrumented at the
        # request layer. Surfaced here as null so consumers don't infer a 0.
        "avg_response_latency_ms_by_surface": None,
    }
    if (format or "").lower() == "csv":
        flat = [
            {"metric": "payment_orders_created", "value": created},
            {"metric": "payment_orders_paid", "value": paid},
            {"metric": "payment_orders_failed", "value": failed},
            {"metric": "payment_failure_pct", "value": data["payment_failure_pct"]},
            {"metric": "webhook_total", "value": wh_total},
            {"metric": "webhook_rejected", "value": wh_rejected},
            {"metric": "webhook_rejection_pct", "value": data["webhook_rejection_pct"]},
            {"metric": "refunds", "value": refunds_total},
            {"metric": "chargebacks", "value": chargebacks},
        ]
        for r in ai_refund:
            flat.append({"metric": f"ai_refund_rate_pct_{r['surface']}", "value": r["ai_failure_refund_rate_pct"]})
        return _csv_response(flat, f"operational_health_{days}d")
    return data
