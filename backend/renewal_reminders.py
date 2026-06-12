"""
renewal_reminders.py — One-shot renewal-nudge emails fired 3 days before
plan_expires_at. Idempotent per cycle, skips Admin·Unlimited users.

Reliability model
-----------------
This module is designed to be called by:
  1. backend startup hook (best-effort, cheap insurance against missed cron)
  2. an EXTERNAL daily scheduler (Cloudflare Cron / GitHub Actions / etc.)
     hitting `POST /api/admin/billing/run-renewal-reminders` with an admin
     bearer token. See `/app/docs/RENEWAL_SCHEDULER.md` for the recipe.

Idempotency contract
--------------------
- Dedup key = `reminder_cycle_identifier` = the `order_id` of the most-recent
  paid order on the user's current plan.
- Once an email is sent (or failed permanently) for a given (user_id, cycle_id)
  pair, no further email is sent for that cycle. Stored on the user doc as
  `renewal_reminder_sent_for: <order_id>` + `renewal_reminder_sent_at: iso`.
- Every run — whether anything was sent or not — is persisted to
  `renewal_reminder_run_logs` so the admin dashboard can show a real cron-style
  history without us having to run a stdout-tailing log scraper.

Schedule guidance
-----------------
Daily at 09:00 UTC is sufficient. The 3-day reminder window means even a
3-day scheduler outage is recovered by the next successful run (the user
gets the same one email, just slightly later).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query

from db import db
from admin import get_admin_user
from email_sender import send_email as multi_send_email
from credits import is_admin_unlimited_user

router = APIRouter(prefix="/api/admin/renewal-reminders", tags=["admin"])
billing_alias_router = APIRouter(prefix="/api/admin/billing", tags=["billing-admin"])
logger = logging.getLogger(__name__)

REMINDER_WINDOW_DAYS = 3
PLAN_LENGTH_DAYS = 30


async def _send_one(user_doc: dict, order: dict, expires: datetime) -> tuple[bool, Optional[str]]:
    days_left = (expires - datetime.now(timezone.utc)).days
    email = user_doc.get("email", "")
    subject = f"Your {order.get('plan_id') or 'plan'} on aiclonechats.com renews in {max(days_left, 0)} day(s)"
    text = (
        f"Hi,\n\n"
        f"Your aiclonechats.com plan ({order.get('plan_id') or 'subscription'}) "
        f"is set to expire on {expires.date().isoformat()}.\n\n"
        f"Renew at https://aiclonechats.com/pricing — keeps your credits flowing without interruption.\n\n"
        f"Questions? Reply to this email or open a concern at https://aiclonechats.com/account/concerns.\n"
    )
    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 480px; margin: 0 auto; padding: 20px; color: #0d0d10;">
      <h2 style="margin:0 0 8px; font-size: 22px;">Your plan renews in {max(days_left, 0)} day(s)</h2>
      <p style="font-size: 14px; line-height: 1.6; color:#444;">
        Your <strong>{order.get('plan_id') or 'subscription'}</strong> on aiclonechats.com is set to expire on
        <strong>{expires.date().isoformat()}</strong>. Renew now to keep your clones, memories, and credits flowing.
      </p>
      <p style="margin: 18px 0;">
        <a href="https://aiclonechats.com/pricing"
           style="background:#f59e0b; color:#0d0d10; padding: 12px 20px; border-radius: 10px; text-decoration: none; font-weight: 700;">
          Renew plan
        </a>
      </p>
      <p style="font-size: 12px; color:#777; margin-top: 24px;">
        You can manage your subscription anytime at
        <a href="https://aiclonechats.com/account/settings/subscriptions" style="color:#f59e0b;">/account/settings/subscriptions</a>.
      </p>
    </div>
    """
    ok, provider = await multi_send_email(
        to_email=email, subject=subject, html=html, text=text,
        purpose="renewal_reminder",
    )
    return bool(ok), provider


async def run_due_reminders(*, dry_run: bool = False, triggered_by: str = "internal") -> dict:
    """Scan paid orders whose +30-day expiry falls within the next 3 days
    and the user hasn't yet been reminded for that order_id."""
    now = datetime.now(timezone.utc)
    run_id = "run_" + uuid.uuid4().hex[:18]
    window_start = now + timedelta(days=0)
    window_end = now + timedelta(days=REMINDER_WINDOW_DAYS)

    paid_at_lo = (window_start - timedelta(days=PLAN_LENGTH_DAYS)).isoformat()
    paid_at_hi = (window_end - timedelta(days=PLAN_LENGTH_DAYS)).isoformat()

    cursor = db.payment_orders.find(
        {"status": "paid", "plan_id": {"$exists": True, "$ne": None},
         "paid_at": {"$gte": paid_at_lo, "$lt": paid_at_hi}},
        {"_id": 0, "order_id": 1, "user_id": 1, "plan_id": 1, "paid_at": 1},
    )

    sent = 0
    skipped_admin = 0
    skipped_already = 0
    failures = 0
    examined = 0
    failure_samples: list[dict] = []  # capture the first 5 failures for debugging

    async for o in cursor:
        examined += 1
        user = await db.users.find_one(
            {"user_id": o["user_id"]},
            {"_id": 0, "user_id": 1, "email": 1, "renewal_reminder_sent_for": 1,
             "plan_id": 1, "is_deleted": 1, "is_deactivated": 1, "role": 1},
        )
        if not user or not user.get("email"):
            continue
        if user.get("is_deleted") or user.get("is_deactivated"):
            continue
        if is_admin_unlimited_user(user):
            skipped_admin += 1
            continue
        if user.get("plan_id") and user["plan_id"] != o["plan_id"]:
            continue
        cycle_id = o["order_id"]
        if user.get("renewal_reminder_sent_for") == cycle_id:
            skipped_already += 1
            continue

        try:
            expires = datetime.fromisoformat(o["paid_at"].replace("Z", "+00:00")) + timedelta(days=PLAN_LENGTH_DAYS)
        except Exception:
            continue

        if not dry_run:
            ok, provider = await _send_one(user, o, expires)
            if ok:
                sent += 1
                await db.users.update_one(
                    {"user_id": user["user_id"]},
                    {"$set": {
                        "renewal_reminder_sent_for": cycle_id,
                        "renewal_reminder_sent_at": now.isoformat(),
                        "renewal_reminder_cycle_identifier": cycle_id,
                        "renewal_reminder_last_provider": provider,
                    }},
                )
            else:
                failures += 1
                if len(failure_samples) < 5:
                    failure_samples.append({
                        "user_id": user["user_id"],
                        "order_id": cycle_id,
                        "email_domain": (user["email"].split("@", 1)[1] if "@" in user["email"] else ""),
                    })

    summary = {
        "run_id": run_id,
        "ran_at": now.isoformat(),
        "triggered_by": triggered_by,
        "examined": examined,
        "sent": sent,
        "skipped_admin": skipped_admin,
        "skipped_already": skipped_already,
        "failures": failures,
        "dry_run": dry_run,
        "failure_samples": failure_samples,
    }

    if not dry_run:
        try:
            await db.renewal_reminder_run_logs.insert_one({**summary})
        except Exception as e:
            logger.warning("renewal_reminders: run-log persist failed: %s", e)

    logger.info("renewal_reminders: %s", summary)
    return summary


async def get_dashboard_summary(now: Optional[datetime] = None) -> dict:
    """Counts + previews for the admin dashboard."""
    now = now or datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_iso = today_start.isoformat()

    # Orders whose expiry falls in the next 3 days (currently due)
    window_end = now + timedelta(days=REMINDER_WINDOW_DAYS)
    paid_at_lo = (now - timedelta(days=PLAN_LENGTH_DAYS)).isoformat()
    paid_at_hi = (window_end - timedelta(days=PLAN_LENGTH_DAYS)).isoformat()
    due_orders_cursor = db.payment_orders.find(
        {"status": "paid", "plan_id": {"$exists": True, "$ne": None},
         "paid_at": {"$gte": paid_at_lo, "$lt": paid_at_hi}},
        {"_id": 0, "order_id": 1, "user_id": 1, "plan_id": 1, "paid_at": 1},
    ).limit(200)
    due_orders = await due_orders_cursor.to_list(length=200)

    # Hydrate user fields for the "next expiring" table
    due_today = 0
    next_expiring_preview = []
    for o in due_orders:
        u = await db.users.find_one(
            {"user_id": o["user_id"]},
            {"_id": 0, "user_id": 1, "email": 1, "plan_id": 1, "is_deleted": 1,
             "is_deactivated": 1, "role": 1, "renewal_reminder_sent_for": 1},
        ) or {}
        if u.get("is_deleted") or u.get("is_deactivated"):
            continue
        if is_admin_unlimited_user(u):
            continue
        if u.get("plan_id") and u["plan_id"] != o["plan_id"]:
            continue
        try:
            expires = datetime.fromisoformat(o["paid_at"].replace("Z", "+00:00")) + timedelta(days=PLAN_LENGTH_DAYS)
        except Exception:
            continue
        already_sent = u.get("renewal_reminder_sent_for") == o["order_id"]
        due_today += 1
        next_expiring_preview.append({
            "user_id": o["user_id"],
            "email": u.get("email"),
            "plan_id": o.get("plan_id"),
            "expires_at": expires.isoformat(),
            "days_left": max(0, (expires - now).days),
            "order_id": o["order_id"],
            "already_sent": already_sent,
        })

    next_expiring_preview.sort(key=lambda x: x["expires_at"])

    # Today's sent / failed: pull from latest run logs (any run that
    # happened today)
    todays_runs = await db.renewal_reminder_run_logs.find(
        {"ran_at": {"$gte": today_start_iso}}, {"_id": 0},
    ).sort("ran_at", -1).to_list(length=50)
    sent_today = sum(r.get("sent", 0) for r in todays_runs)
    failed_today = sum(r.get("failures", 0) for r in todays_runs)
    skipped_already_today = sum(r.get("skipped_already", 0) for r in todays_runs)
    skipped_admin_today = sum(r.get("skipped_admin", 0) for r in todays_runs)

    # Last 10 runs across all time for the audit table
    recent_runs = await db.renewal_reminder_run_logs.find(
        {}, {"_id": 0},
    ).sort("ran_at", -1).to_list(length=10)

    return {
        "computed_at": now.isoformat(),
        "today": {
            "due": due_today,
            "sent": sent_today,
            "failed": failed_today,
            "skipped_already_reminded": skipped_already_today,
            "skipped_admin": skipped_admin_today,
            "runs": len(todays_runs),
        },
        "next_expiring": next_expiring_preview[:50],
        "recent_runs": recent_runs,
        "config": {
            "reminder_window_days": REMINDER_WINDOW_DAYS,
            "plan_length_days": PLAN_LENGTH_DAYS,
            "recommended_schedule": "Daily at 09:00 UTC",
            "scheduler_doc": "/app/docs/RENEWAL_SCHEDULER.md",
        },
    }


# ─────────────── Endpoints ───────────────

@router.post("/run")
async def trigger_run_legacy(admin: dict = Depends(get_admin_user), dry_run: bool = False):
    """Legacy path — kept for backwards compatibility."""
    return await run_due_reminders(dry_run=dry_run, triggered_by=f"admin:{admin.get('email', 'unknown')}")


@billing_alias_router.post("/run-renewal-reminders")
async def run_renewal_reminders(
    admin: dict = Depends(get_admin_user),
    dry_run: bool = Query(default=False, description="Evaluate candidates without sending or persisting state."),
):
    """Spec-mandated path for external schedulers.

    Authenticate as the admin (Bearer token) and POST with no body. Returns
    a JSON summary so the scheduler can log the response.
    """
    return await run_due_reminders(dry_run=dry_run, triggered_by=f"scheduler:{admin.get('email', 'unknown')}")


@billing_alias_router.get("/renewal-reminders/summary")
async def renewal_reminders_summary(admin: dict = Depends(get_admin_user)):
    return await get_dashboard_summary()


async def ensure_indexes() -> None:
    try:
        await db.renewal_reminder_run_logs.create_index("run_id", unique=True)
        await db.renewal_reminder_run_logs.create_index([("ran_at", -1)])
        logger.info("renewal_reminders: indexes ensured")
    except Exception as e:
        logger.warning("renewal_reminders: index creation failed: %s", e)
