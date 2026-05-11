"""Backend tests for /api/admin/revenue/* dashboard + funnel ingestion + paywall_events writes."""
import os
import asyncio
import uuid
import datetime
import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else "https://digital-twin-119.preview.emergentagent.com"
ADMIN_EMAIL = "krajapraveen@gmail.com"
SUBSCRIBER_EMAIL = "subscriber-tester@example.com"
SUBSCRIBER_PWD = "TestPass123!"
FREE_EMAIL = "sr-tester@example.com"
FREE_PWD = "TestPass123!"


def _inject_admin_session():
    """Inject a session_token for admin user (krajapraveen@gmail.com) into Mongo."""
    async def go():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        user = await db.users.find_one({"email": ADMIN_EMAIL})
        if not user:
            # create the admin user if missing
            user = {
                "user_id": uuid.uuid4().hex,
                "email": ADMIN_EMAIL,
                "name": "Admin",
                "email_verified": True,
                "plan_id": "free",
                "plan_status": "active",
                "credits_balance": 0,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            await db.users.insert_one(user)
        token = uuid.uuid4().hex
        await db.user_sessions.insert_one({
            "session_token": token,
            "user_id": user["user_id"],
            "email": user["email"],
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "expires_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)).isoformat(),
        })
        return token
    return asyncio.get_event_loop().run_until_complete(go())


@pytest.fixture(scope="module")
def admin_token():
    return _inject_admin_session()


@pytest.fixture(scope="module")
def subscriber_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": SUBSCRIBER_EMAIL, "password": SUBSCRIBER_PWD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"subscriber login failed: {r.status_code} {r.text[:200]}")
    return r.json().get("token") or r.json().get("session_token") or r.json().get("access_token")


@pytest.fixture(scope="module")
def free_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": FREE_EMAIL, "password": FREE_PWD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"free login failed: {r.status_code} {r.text[:200]}")
    return r.json().get("token") or r.json().get("session_token") or r.json().get("access_token")


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


# ============================================================================
# Admin endpoints: shape + 200 OK
# ============================================================================
class TestAdminRevenueEndpoints:
    def test_funnel(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/revenue/funnel?days=30", headers=_h(admin_token), timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "steps" in d and len(d["steps"]) == 6
        assert "conversion_pct" in d and len(d["conversion_pct"]) == 5
        assert "topup_repeat" in d and set(d["topup_repeat"].keys()) >= {"buyers", "repeat_buyers", "repeat_rate_pct"}

    def test_revenue(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/revenue/revenue?days=30", headers=_h(admin_token), timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["mrr_inr", "active_subscriptions_by_plan", "subscription_revenue_window_by_plan",
                  "topup_revenue_window_by_pack", "refunds_window", "chargebacks_window",
                  "arpu_inr_window", "revenue_by_country", "credit_consumption_by_surface"]:
            assert k in d, f"missing {k}"

    def test_credit_economy(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/revenue/credit-economy?days=30", headers=_h(admin_token), timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["credits_purchased", "credits_consumed", "credits_refunded", "net_outstanding_in_window",
                  "burn_by_surface", "highest_cost_surfaces", "highest_margin_surfaces_by_volume"]:
            assert k in d, f"missing {k}"
        for row in d["burn_by_surface"]:
            assert "refund_rate_pct" in row

    def test_emotional_gravity(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/revenue/emotional-gravity?days=90", headers=_h(admin_token), timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["first_paid_intent_surface", "first_successful_payment_surface", "repeat_return_surface",
                  "longest_session_surface", "highest_top_up_correlation_surface"]:
            assert k in d, f"missing {k}"

    def test_cohorts(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/revenue/cohorts?weeks=12", headers=_h(admin_token), timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["by_acquisition_week", "by_plan_tier", "by_first_paywall_surface"]:
            assert k in d

    def test_operational_health(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/revenue/operational-health?days=30", headers=_h(admin_token), timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["payment_failure_pct", "payment_success_pct", "webhook_rejection_pct",
                  "webhook_result_breakdown", "refunds", "chargebacks",
                  "ai_failure_refund_rate_by_surface", "avg_response_latency_ms_by_surface"]:
            assert k in d
        assert d["avg_response_latency_ms_by_surface"] is None


# ============================================================================
# CSV format
# ============================================================================
class TestCSVExports:
    @pytest.mark.parametrize("path,qk,val", [
        ("/api/admin/revenue/funnel", "days", 30),
        ("/api/admin/revenue/revenue", "days", 30),
        ("/api/admin/revenue/credit-economy", "days", 30),
        ("/api/admin/revenue/emotional-gravity", "days", 90),
        ("/api/admin/revenue/cohorts", "weeks", 12),
        ("/api/admin/revenue/operational-health", "days", 30),
    ])
    def test_csv_format(self, admin_token, path, qk, val):
        r = requests.get(f"{BASE_URL}{path}?{qk}={val}&format=csv", headers=_h(admin_token), timeout=60)
        assert r.status_code == 200, r.text
        assert "text/csv" in r.headers.get("content-type", ""), r.headers.get("content-type")
        assert len(r.text) > 0


# ============================================================================
# 403 for non-admin
# ============================================================================
class TestAdminGating:
    @pytest.mark.parametrize("path,qk,val", [
        ("/api/admin/revenue/funnel", "days", 30),
        ("/api/admin/revenue/revenue", "days", 30),
        ("/api/admin/revenue/credit-economy", "days", 30),
        ("/api/admin/revenue/emotional-gravity", "days", 90),
        ("/api/admin/revenue/cohorts", "weeks", 12),
        ("/api/admin/revenue/operational-health", "days", 30),
    ])
    def test_subscriber_blocked(self, subscriber_token, path, qk, val):
        r = requests.get(f"{BASE_URL}{path}?{qk}={val}", headers=_h(subscriber_token), timeout=30)
        assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text[:200]}"


# ============================================================================
# Funnel event ingestion
# ============================================================================
class TestFunnelEvent:
    def test_pricing_view_inserts(self, subscriber_token):
        r = requests.post(f"{BASE_URL}/api/funnel/event",
                          headers=_h(subscriber_token),
                          json={"event_name": "pricing_view", "referrer": "test"},
                          timeout=15)
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

        # verify persisted
        async def chk():
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            return await db.funnel_events.count_documents({"event_name": "pricing_view"})
        n = asyncio.get_event_loop().run_until_complete(chk())
        assert n >= 1

    def test_unknown_event_rejected(self, subscriber_token):
        r = requests.post(f"{BASE_URL}/api/funnel/event",
                          headers=_h(subscriber_token),
                          json={"event_name": "evil_event"}, timeout=15)
        assert r.status_code == 400, r.text


# ============================================================================
# Paywall_events written on 402
# ============================================================================
class TestPaywallEventWrite:
    def test_free_user_402_writes_paywall_event(self, free_token):
        # baseline count
        async def count_before():
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            user = await db.users.find_one({"email": FREE_EMAIL})
            assert user, "free user must exist"
            return user["user_id"], await db.paywall_events.count_documents({"user_id": user["user_id"]})
        uid, before = asyncio.get_event_loop().run_until_complete(count_before())

        # Trigger 402 via companion clone chat
        r = requests.post(
            f"{BASE_URL}/api/clones/companion/chat",
            headers=_h(free_token),
            json={"message": "hi", "conversation_id": None},
            timeout=30,
        )
        # may be 402 with subscription_required
        assert r.status_code in (402, 403), f"expected paywall got {r.status_code}: {r.text[:300]}"

        # poll for paywall_event
        async def count_after():
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            return await db.paywall_events.count_documents({"user_id": uid, "code": {"$in": ["subscription_required", "plan_upgrade_required", "email_not_verified", "insufficient_balance"]}})
        # small grace
        import time as _t
        _t.sleep(1)
        after = asyncio.get_event_loop().run_until_complete(count_after())
        assert after > before, f"paywall_events did not grow ({before} -> {after})"
