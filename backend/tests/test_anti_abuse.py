"""Anti-abuse module unit tests — verify exemption logic + rate-limit semantics
without going through HTTP. The DB is a real Mongo collection (test_database).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Bootstrap env from .env before importing anti_abuse (which imports db)
def _bootstrap_env():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)

_bootstrap_env()
os.environ["ADMIN_EMAILS"] = "krajapraveen@gmail.com,admin@aiclonechats.com"
os.environ["ADMIN_UNLIMITED_EMAIL"] = "krajapraveen@gmail.com,admin@aiclonechats.com"


def main():
    # Import after env bootstrap so the module reads our values
    from anti_abuse import (
        is_anti_abuse_exempt_user,
        enforce_rate_limit,
        check_user_abuse_status,
        set_user_abuse_status,
        reset_abuse_counters,
        hash_ip,
    )
    from db import db

    async def run():
        # Clean slate
        await db.anti_abuse_events.delete_many({"scope": {"$regex": "^test\\."}})
        await db.users.delete_many({"user_id": {"$in": ["test_anti_user_99", "test_admin_user", "test_admin_poison"]}})

        # 1. Exemption
        assert await is_anti_abuse_exempt_user("krajapraveen@gmail.com") is True
        assert await is_anti_abuse_exempt_user("KRAJAPRAVEEN@GMAIL.COM") is True
        assert await is_anti_abuse_exempt_user("admin@aiclonechats.com") is True
        assert await is_anti_abuse_exempt_user(" admin@aiclonechats.com ") is True
        assert await is_anti_abuse_exempt_user("vishal7293kumar@gmail.com") is False
        assert await is_anti_abuse_exempt_user("") is False
        assert await is_anti_abuse_exempt_user(None) is False
        assert await is_anti_abuse_exempt_user({"email": "krajapraveen@gmail.com"}) is True
        assert await is_anti_abuse_exempt_user({"email": "joe@example.com"}) is False
        print("TEST 1 exemption — PASS")

        # 2. Rate limit kicks in for normal user
        for i in range(5):
            r = await enforce_rate_limit("test.normal", "user_a",
                                          max_count=5, window_s=60,
                                          user_email="normal@example.com")
            assert r.allowed is True, f"hit {i+1}/5 should be allowed, got {r}"
        r = await enforce_rate_limit("test.normal", "user_a",
                                      max_count=5, window_s=60,
                                      user_email="normal@example.com")
        assert r.allowed is False and r.reason == "rate_limited", r
        print("TEST 2 rate limit kicks in for normal user — PASS")

        # 3. Admin NEVER rate-limited
        for i in range(20):
            r = await enforce_rate_limit("test.admin", "user_b",
                                          max_count=5, window_s=60,
                                          user_email="admin@aiclonechats.com")
            assert r.allowed is True, f"admin hit {i+1}/20 should always be allowed"
            assert r.exempt is True and r.reason == "exempt"
        print("TEST 3 admin never rate-limited (20 over limit) — PASS")

        # 4. Krajapraveen also exempt
        for _ in range(20):
            r = await enforce_rate_limit("test.kraja", "user_c",
                                          max_count=3, window_s=60,
                                          user_email="krajapraveen@gmail.com")
            assert r.allowed is True and r.exempt is True
        print("TEST 4 krajapraveen exempt — PASS")

        # 5. Per-key isolation — different keys don't interfere
        for _ in range(4):
            r = await enforce_rate_limit("test.iso", "key_x", max_count=5, window_s=60,
                                          user_email="a@x.com")
            assert r.allowed is True
        r2 = await enforce_rate_limit("test.iso", "key_y", max_count=5, window_s=60,
                                       user_email="b@x.com")
        assert r2.allowed is True and r2.count == 1
        print("TEST 5 per-key isolation — PASS")

        # 6. abuse_status — set/get/reset cycle
        await db.users.update_one(
            {"user_id": "test_anti_user_99"},
            {"$set": {"user_id": "test_anti_user_99", "email": "blocktest@example.com"}},
            upsert=True,
        )
        # Initial = normal
        u = await db.users.find_one({"user_id": "test_anti_user_99"}, {"_id": 0})
        assert await check_user_abuse_status(u) == "normal"

        # Set blocked
        await set_user_abuse_status("test_anti_user_99", "blocked",
                                     "test block", by_admin_email="admin@aiclonechats.com")
        u = await db.users.find_one({"user_id": "test_anti_user_99"}, {"_id": 0})
        assert await check_user_abuse_status(u) == "blocked"

        # Admin can never be blocked — pass the dict directly to avoid DB conflicts
        try:
            admin_user = {"user_id": "test_admin_user", "email": "admin@aiclonechats.com"}
            # set_user_abuse_status needs DB lookup, so insert a non-duplicate doc
            await db.users.delete_one({"email": "admin@aiclonechats.com_TEST_ALIAS"})
            await db.users.insert_one({
                "user_id": "test_admin_user",
                "email": "admin@aiclonechats.com_TEST_ALIAS",  # unique email for DB
            })
            # Inject admin email via env override path: directly check via is_anti_abuse_exempt_user
            # logic — we test the BLOCK path: caller passes a target user_id whose email IS admin.
            await db.users.update_one(
                {"user_id": "test_admin_user"},
                {"$set": {"email": "test_admin_user_alias@example.com"}},
            )
            # Add this alias to the env exempt list for the assertion
            os.environ["ADMIN_EMAILS"] = "krajapraveen@gmail.com,admin@aiclonechats.com,test_admin_user_alias@example.com"
            # Force cache invalidate
            from anti_abuse import _DB_ADMIN_CACHE
            _DB_ADMIN_CACHE["fetched_at"] = 0.0
            from fastapi import HTTPException
            try:
                await set_user_abuse_status("test_admin_user", "blocked",
                                             "should fail", by_admin_email="admin@aiclonechats.com")
                assert False, "should have raised"
            except HTTPException as e:
                assert e.detail["code"] == "user_is_admin"
        finally:
            await db.users.delete_one({"user_id": "test_admin_user"})
            os.environ["ADMIN_EMAILS"] = "krajapraveen@gmail.com,admin@aiclonechats.com"
        print("TEST 6 abuse_status set/check/admin-protect — PASS")

        # 7. Even if DB has abuse_status=blocked, admin email returns normal
        # (defense-in-depth against a poisoned DB row). No DB insert needed —
        # check_user_abuse_status accepts a user dict.
        poisoned_admin = {
            "user_id": "test_admin_poison",
            "email": "krajapraveen@gmail.com",
            "abuse_status": "blocked",
        }
        assert await check_user_abuse_status(poisoned_admin) == "normal", "admin must override poisoned status"
        print("TEST 7 admin overrides poisoned DB status — PASS")

        # 8. reset_abuse_counters removes events
        for _ in range(3):
            await enforce_rate_limit("test.reset_target", "test_anti_user_99",
                                      max_count=100, window_s=60,
                                      user_id="test_anti_user_99",
                                      user_email="blocktest@example.com")
        out = await reset_abuse_counters("test_anti_user_99", by_admin_email="admin@aiclonechats.com")
        assert out["deleted"] >= 3, out
        print("TEST 8 reset_abuse_counters — PASS")

        # Cleanup
        await db.users.delete_one({"user_id": "test_anti_user_99"})
        await db.anti_abuse_events.delete_many({"scope": {"$regex": "^test\\."}})

        # 9. IP hashing
        assert hash_ip("127.0.0.1") == hash_ip("127.0.0.1")
        assert hash_ip("127.0.0.1") != hash_ip("127.0.0.2")
        assert hash_ip("") == ""
        assert len(hash_ip("a.b.c.d")) == 32
        print("TEST 9 IP hashing — PASS")

        print("\nALL ANTI-ABUSE UNIT TESTS PASSED")

    asyncio.run(run())


if __name__ == "__main__":
    main()
