"""
One-time credit reset migration.

Run with:
    python -m migrations.reset_credits_2026_05_11 --dry-run
    python -m migrations.reset_credits_2026_05_11 --execute

Constitutional discipline:
  - krajapraveen@gmail.com (or whatever ADMIN_UNLIMITED_EMAIL points to) is
    NEVER touched. Admin keeps the unlimited-bypass model — no numeric balance.
  - Dry-run prints affected count without writing.
  - Execute writes a single audit record before mutating anything, then
    flips credits to 0 for every non-admin user.
  - Verifies no non-admin user has positive credits post-migration.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# Load backend .env
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


async def run(dry_run: bool) -> int:
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    # ADMIN_UNLIMITED_EMAIL supports CSV (multiple admin emails). Default keeps
    # backward compatibility with the original single-email behavior.
    raw_admin = os.environ.get("ADMIN_UNLIMITED_EMAIL") or "krajapraveen@gmail.com"
    admin_emails = sorted({e.lower().strip() for e in raw_admin.split(",") if e and e.strip()})
    admin_email = admin_emails[0] if admin_emails else ""  # display only

    cli = AsyncIOMotorClient(mongo_url)
    db = cli[db_name]

    # Idempotency guard: don't run twice
    if not dry_run:
        prior = await db.audit_log.find_one({"migration": "credit_reset_2026_05_11"}, {"_id": 0})
        if prior:
            print(f"REFUSED: audit_log already has credit_reset_2026_05_11 entry from {prior.get('created_at')}.")
            print("If you genuinely need to re-run, delete that audit entry first.")
            return 1

    # Count what we'll touch
    total_users = await db.users.count_documents({})
    admin_users = await db.users.count_documents({"email": {"$in": admin_emails}})
    non_admin_with_credits = await db.users.count_documents({
        "email": {"$nin": admin_emails},
        "$or": [
            {"credits_balance": {"$gt": 0}},
            {"credits": {"$gt": 0}},
            {"credit_balance": {"$gt": 0}},
            {"promo_credits": {"$gt": 0}},
            {"welcome_credits": {"$gt": 0}},
        ],
    })
    non_admin_total = await db.users.count_documents({"email": {"$nin": admin_emails}})

    print("==============================================")
    print("CREDIT RESET MIGRATION  ·  credit_reset_2026_05_11")
    print("==============================================")
    print(f"  mode                      : {'DRY-RUN (no writes)' if dry_run else 'EXECUTE'}")
    print(f"  admin protected emails    : {', '.join(admin_emails)}")
    print(f"  admin users found         : {admin_users}")
    print(f"  total users in DB         : {total_users}")
    print(f"  non-admin users           : {non_admin_total}")
    print(f"  non-admin w/ positive cr  : {non_admin_with_credits}")
    print("----------------------------------------------")

    # Pre-audit (only on execute)
    if not dry_run:
        await db.audit_log.insert_one({
            "migration": "credit_reset_2026_05_11",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "admin_email": admin_email,
            "admin_emails": admin_emails,
            "admin_users_protected": admin_users,
            "non_admin_users": non_admin_total,
            "non_admin_with_positive_credits": non_admin_with_credits,
            "reason": "Founder-directed reset before monetization enforcement",
            "phase": "started",
        })

    # The actual mutation
    if not dry_run:
        res = await db.users.update_many(
            {"email": {"$nin": admin_emails}},
            {"$set": {
                "credits_balance": 0,
                "credits": 0,
                "credit_balance": 0,
                "promo_credits": 0,
                "welcome_credits": 0,
                "referral_credits": 0,
                "daily_credits_used": 0,
                "credits_reset_at_2026_05_11": datetime.now(timezone.utc).isoformat(),
            }},
        )
        # Mark all prior credit_grants as nullified so anti-abuse history is intact
        # but those grants no longer reflect a live balance.
        # We do NOT delete credit_grants — the unique-index on email/user_id is
        # what stops a re-grant from happening again. That's the right behavior.

        # Verify
        leak = await db.users.find_one(
            {"email": {"$nin": admin_emails}, "credits_balance": {"$gt": 0}},
            {"_id": 0, "email": 1, "credits_balance": 1},
        )
        if leak:
            print(f"  ❌ POST-MIGRATION LEAK: {leak}")
            await db.audit_log.update_one(
                {"migration": "credit_reset_2026_05_11", "phase": "started"},
                {"$set": {"phase": "failed_leak_detected", "leak": leak, "ended_at": datetime.now(timezone.utc).isoformat()}},
            )
            return 2

        # Verify admins untouched
        admin_docs = await db.users.find({"email": {"$in": admin_emails}}, {"_id": 0, "credits_balance": 1, "email": 1}).to_list(length=100)
        print(f"  matched={res.matched_count} modified={res.modified_count}")
        print(f"  admin docs post-migration: {admin_docs}")

        await db.audit_log.update_one(
            {"migration": "credit_reset_2026_05_11", "phase": "started"},
            {"$set": {
                "phase": "completed",
                "matched": res.matched_count,
                "modified": res.modified_count,
                "admin_docs_after": admin_docs,
                "ended_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        print("  ✅ DONE.")
    else:
        print("  (no writes performed — re-run with --execute to apply)")

    return 0


def main():
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    rc = asyncio.run(run(dry_run=args.dry_run))
    sys.exit(rc)


if __name__ == "__main__":
    main()
