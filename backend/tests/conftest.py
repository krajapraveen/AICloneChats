"""Shared pytest configuration — one event loop across all payment tests so
Motor's binding doesn't break when multiple test modules touch the DB."""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_database")

# Module-level shared loop. Each test file's `_LOOP` should reference THIS one.
_SHARED_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_SHARED_LOOP)


def get_shared_loop():
    return _SHARED_LOOP


# Known test accounts whose login_failed events should not cascade into
# brute-force lockout across the full suite. Cleared once at session start.
_TEST_EMAILS = (
    "sr-tester@example.com",
    "subscriber-tester@example.com",
)


@pytest.fixture(scope="session", autouse=True)
def _clear_test_login_lockouts():
    """Purge brute-force lockout counters for known test emails BEFORE the
    suite runs. Without this, a single early test that fumbles credentials
    can lock out every later test in the suite (cascades into 100+ errors
    that look like real product bugs but are actually rate-limit pollution).

    Also demotes test users that previous test sessions may have left as
    admin (`role`/`is_admin` leftover from admin-flow tests). This prevents
    "this non-admin endpoint returned 200 instead of 403" cascades.
    """
    async def _clear():
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            client = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = client[os.environ["DB_NAME"]]
            await db.login_events.delete_many({
                "event_type": "login_failed",
                "email": {"$in": list(_TEST_EMAILS)},
            })
            # Clear forgot/reset password rate-limit counters for test emails
            await db.auth_rate_limits.delete_many({
                "key": {"$regex": "^(forgot|reset):"},
            })
            # Clear anti-abuse counters that throttle change-password / support
            # threads / etc. so each suite run starts with a fresh budget.
            await db.anti_abuse_events.delete_many({})
            # sr-tester is the canonical FREE user (per test_credentials.md).
            # Various test suites rely on this — reset it to free/0-credits on
            # every session start so a previous suite that promoted it can't
            # bleed state into the paywall tests.
            await db.users.update_one(
                {"email": "sr-tester@example.com"},
                {"$set": {
                    "plan_id": "free", "plan_status": "",
                    "credits_balance": 0, "email_verified": True,
                }},
            )
            # Strip stale admin role from non-admin test accounts
            await db.users.update_many(
                {"email": {"$in": list(_TEST_EMAILS)}},
                {"$unset": {"role": "", "is_admin": ""}},
            )
            # Remove test emails from admin_users collection (left behind by
            # earlier admin-flow tests that temporarily promoted them).
            await db.admin_users.delete_many(
                {"email": {"$in": list(_TEST_EMAILS)}},
            )
        except Exception:
            # Best-effort: never fail the suite for cleanup
            pass
    _SHARED_LOOP.run_until_complete(_clear())
    yield
