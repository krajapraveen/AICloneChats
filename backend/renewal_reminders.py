"""
renewal_reminders.py — One-shot renewal-nudge emails fired 3 days before
plan_expires_at. Idempotent per cycle, skips Admin·Unlimited users.

Why a helper, not a cron
------------------------
The platform has no scheduler. Instead, the on_startup hook can call
`run_due_reminders()` — it scans for users whose plan expires in the next 3
days, hasn't been reminded for the CURRENT order_id yet, and fires Resend.

The dedup key is the `order_id` of the most recent paid order on the user's
current plan. We mark `renewal_reminder_sent_for: order_id` on the user
document so re-runs (or pod restarts) never double-send.

Trigger options
---------------
- Startup hook (current): runs once per pod boot. Cheap, idempotent.
- External scheduler (future): hit `POST /api/admin/renewal-reminders/run`
  hourly. Admin endpoint already protected by get_admin_user.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from db import db
from admin import get_admin_user
from email_sender import send_email as multi_send_email
from credits import is_admin_unlimited_user

router = APIRouter(prefix="/api/admin/renewal-reminders", tags=["admin"])
logger = logging.getLogger(__name__)

REMINDER_WINDOW_DAYS = 3
PLAN_LENGTH_DAYS = 30


async def _send_one(user_doc: dict, order: dict, expires: datetime) -> bool:
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
    ok, _ = await multi_send_email(
        to_email=email, subject=subject, html=html, text=text,
        purpose="renewal_reminder",
    )
    return bool(ok)


async def run_due_reminders(*, dry_run: bool = False) -> dict:
    """Scan all paid orders whose +30-day expiry is within the next 3 days
    and the user hasn't yet been reminded for that order_id."""
    now = datetime.now(timezone.utc)
    window_start = now + timedelta(days=0)
    window_end = now + timedelta(days=REMINDER_WINDOW_DAYS)

    # Find candidate paid orders by paid_at range (paid_at + 30d falls in window)
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
    async for o in cursor:
        examined += 1
        user = await db.users.find_one(
            {"user_id": o["user_id"]},
            {"_id": 0, "user_id": 1, "email": 1, "renewal_reminder_sent_for": 1, "plan_id": 1},
        )
        if not user or not user.get("email"):
            continue
        if is_admin_unlimited_user(user):
            skipped_admin += 1
            continue
        # Only remind for the user's CURRENT plan
        if user.get("plan_id") and user["plan_id"] != o["plan_id"]:
            continue
        if user.get("renewal_reminder_sent_for") == o["order_id"]:
            skipped_already += 1
            continue

        try:
            expires = datetime.fromisoformat(o["paid_at"].replace("Z", "+00:00")) + timedelta(days=PLAN_LENGTH_DAYS)
        except Exception:
            continue

        if not dry_run:
            ok = await _send_one(user, o, expires)
            if ok:
                sent += 1
                await db.users.update_one(
                    {"user_id": user["user_id"]},
                    {"$set": {"renewal_reminder_sent_for": o["order_id"],
                              "renewal_reminder_sent_at": now.isoformat()}},
                )
            else:
                failures += 1

    summary = {
        "ran_at": now.isoformat(),
        "examined": examined,
        "sent": sent,
        "skipped_admin": skipped_admin,
        "skipped_already": skipped_already,
        "failures": failures,
        "dry_run": dry_run,
    }
    logger.info("renewal_reminders: %s", summary)
    return summary


@router.post("/run")
async def trigger_run(admin: dict = Depends(get_admin_user), dry_run: bool = False):
    return await run_due_reminders(dry_run=dry_run)
