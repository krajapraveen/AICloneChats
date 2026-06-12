"""Per-feature cost tagging — credit_events.feature field correctness.

Cost Telemetry foundation. Every credit-deduction surface MUST write the
correct `feature` value so the upcoming dashboard can do single-pass
aggregations.

What we verify:
  - `feature_for_surface()` mapping is correct for all known surfaces.
  - `deduct_credits()` writes `feature` matching the SURFACE_FEATURE_MAP.
  - Payment grants → feature="subscription".
  - Topup grants → feature="subscription".
  - Admin adjust → feature="admin_adjustment".
  - Backward compatibility: old rows without `feature` are tolerated by
    using `$ifNull` in aggregation queries (we just verify they don't
    crash the deduction path).
  - ALLOWED_FEATURES list is the full taxonomy.
"""
from __future__ import annotations

import os
import sys
import uuid
import asyncio
from pathlib import Path

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

from conftest import get_shared_loop  # noqa: E402
from credits import (  # noqa: E402
    feature_for_surface,
    SURFACE_FEATURE_MAP,
    ALLOWED_FEATURES,
    deduct_credits,
    refund_credits,
    credit_payment,
)


def _run(coro):
    return get_shared_loop().run_until_complete(coro)


def test_allowed_features_complete():
    """Spec: exactly these 9 buckets, in this order, must be available."""
    expected = {"ai_clone", "voice", "video", "chat", "image",
                "avatar", "subscription", "admin_adjustment", "unknown"}
    assert set(ALLOWED_FEATURES) == expected


def test_feature_for_surface_mapping():
    """Every known surface maps to a value inside ALLOWED_FEATURES."""
    for surface, feature in SURFACE_FEATURE_MAP.items():
        assert feature in ALLOWED_FEATURES, (surface, feature)
        assert feature_for_surface(surface) == feature

    # Specific business semantics
    assert feature_for_surface("clone_chat") == "ai_clone"
    assert feature_for_surface("conversation_memory") == "ai_clone"
    assert feature_for_surface("mood_chat") == "chat"
    assert feature_for_surface("translation_chat") == "chat"
    assert feature_for_surface("smart_reply") == "chat"
    assert feature_for_surface("debate_chat") == "chat"
    assert feature_for_surface("delayed_create") == "chat"
    assert feature_for_surface("anonymous_chat") == "chat"
    assert feature_for_surface("voice_message") == "voice"
    assert feature_for_surface("video_avatar") == "video"

    # Subscription + top-up + admin all funnel correctly
    assert feature_for_surface("payment:pro") == "subscription"
    assert feature_for_surface("topup:topup_small") == "subscription"
    assert feature_for_surface("subscription") == "subscription"
    assert feature_for_surface("admin:manual_adjustment") == "admin_adjustment"
    assert feature_for_surface("admin_adjust:rollback") == "admin_adjustment"

    # Unknown / missing / fresh-future surfaces default to "unknown"
    assert feature_for_surface(None) == "unknown"
    assert feature_for_surface("") == "unknown"
    assert feature_for_surface("totally_new_surface") == "unknown"


def _seed_user(user_id: str, credits: int = 100) -> None:
    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "email": f"feat_{user_id}@example.com",
                "credits_balance": credits,
                "plan_id": "pro",
                "plan_status": "active",
                "daily_credits_used": 0,
                "daily_credits_reset_at": "2000-01-01T00:00:00+00:00",
            }},
            upsert=True,
        )
    _run(_go())


def _latest_event(user_id: str) -> dict:
    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        return await db.credit_events.find_one(
            {"user_id": user_id}, {"_id": 0},
            sort=[("created_at", -1)],
        )
    return _run(_go())


@pytest.mark.parametrize("surface,expected_feature", [
    ("clone_chat", "ai_clone"),
    ("conversation_memory", "ai_clone"),
    ("mood_chat", "chat"),
    ("translation_chat", "chat"),
    ("smart_reply", "chat"),
    ("debate_chat", "chat"),
    ("anonymous_chat", "chat"),
    ("delayed_create", "chat"),
    ("voice_message", "voice"),
    ("video_avatar", "video"),
])
def test_deduct_credits_writes_correct_feature(surface, expected_feature):
    user_id = f"feat_test_{uuid.uuid4().hex[:10]}"
    _seed_user(user_id)
    user_doc = {"user_id": user_id, "email": f"{user_id}@example.com"}
    res = _run(deduct_credits(user_doc, surface, request_id=f"req_{uuid.uuid4().hex[:8]}"))
    assert res["ok"] is True, (surface, res)
    evt = _latest_event(user_id)
    assert evt is not None
    assert evt["kind"] == "deduct"
    assert evt["surface"] == surface
    assert evt["feature"] == expected_feature, (surface, evt["feature"])


def test_refund_credits_carries_same_feature():
    """Refunds must also be tagged with the same feature as the original
    deduction so net consumption per feature is computable."""
    user_id = f"refund_{uuid.uuid4().hex[:10]}"
    _seed_user(user_id)
    user_doc = {"user_id": user_id, "email": f"{user_id}@example.com"}
    _run(deduct_credits(user_doc, "voice_message", request_id="req_voice_1"))
    _run(refund_credits(user_doc, "voice_message", request_id="req_voice_1"))
    evt = _latest_event(user_id)
    assert evt["kind"] == "refund"
    assert evt["feature"] == "voice"


def test_payment_grants_tagged_as_subscription():
    user_id = f"pay_{uuid.uuid4().hex[:10]}"
    _seed_user(user_id, credits=0)
    _run(credit_payment(user_id, 2500, order_id=f"order_test_{uuid.uuid4().hex[:8]}",
                        plan_id="pro", kind="subscription"))
    evt = _latest_event(user_id)
    assert evt["kind"] == "grant"
    assert evt["feature"] == "subscription"


def test_topup_grants_tagged_as_subscription():
    user_id = f"topup_{uuid.uuid4().hex[:10]}"
    _seed_user(user_id, credits=0)
    _run(credit_payment(user_id, 300, order_id=f"order_test_{uuid.uuid4().hex[:8]}",
                        plan_id=None, kind="topup", pack_id="topup_small"))
    evt = _latest_event(user_id)
    assert evt["kind"] == "grant"
    assert evt["feature"] == "subscription"


def test_unknown_surface_falls_back_to_unknown_feature():
    """Future / un-tagged surface labels must NOT raise and must record
    feature='unknown' so they're visible in the dashboard for follow-up."""
    user_id = f"unk_{uuid.uuid4().hex[:10]}"
    _seed_user(user_id, credits=1000)
    # Call _emit_credit_event directly with a surface that isn't in the map
    from credits import _emit_credit_event
    _run(_emit_credit_event(user_id, "deduct", -1, 1000, 999,
                            surface="experimental_thing", request_id="req_x"))
    evt = _latest_event(user_id)
    assert evt["feature"] == "unknown"


def test_old_rows_without_feature_dont_break_aggregations():
    """Backward-compat: insert a row WITHOUT a feature field (simulating old
    data) and confirm an $ifNull aggregation buckets it as 'unknown'.

    This test pins the aggregation pattern future Cost Telemetry queries
    will use, so we don't have to backfill historical rows.
    """
    user_id = f"legacy_{uuid.uuid4().hex[:10]}"
    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.credit_events.insert_one({
            "event_id": uuid.uuid4().hex,
            "user_id": user_id,
            "kind": "deduct",
            "delta": -1,
            "balance_before": 100,
            "balance_after": 99,
            "surface": "ancient_surface",
            # NO feature field — simulates a row from before this migration
            "request_id": "legacy_req",
            "created_at": "2024-01-01T00:00:00+00:00",
        })
        # Pin the aggregation pattern for the Cost Telemetry dashboard
        rows = await db.credit_events.aggregate([
            {"$match": {"user_id": user_id}},
            {"$group": {
                "_id": {"$ifNull": ["$feature", "unknown"]},
                "count": {"$sum": 1},
                "delta_sum": {"$sum": "$delta"},
            }},
        ]).to_list(10)
        return rows
    rows = _run(_go())
    assert len(rows) == 1
    assert rows[0]["_id"] == "unknown"
    assert rows[0]["count"] == 1


def test_no_existing_callers_broken():
    """Sanity: deduct_credits + refund_credits + credit_payment all still
    succeed with no extra parameters from callers — backward compatibility
    of the function signatures."""
    user_id = f"compat_{uuid.uuid4().hex[:10]}"
    _seed_user(user_id, credits=50)
    user_doc = {"user_id": user_id, "email": f"{user_id}@example.com"}
    # The exact call patterns from the chat/voice routes
    r1 = _run(deduct_credits(user_doc, "clone_chat", request_id="r1"))
    assert r1["ok"] is True
    r2 = _run(deduct_credits(user_doc, "mood_chat", request_id="r2"))
    assert r2["ok"] is True
    _run(refund_credits(user_doc, "mood_chat", request_id="r2"))
    bal = _run(credit_payment(user_id, 100, order_id="o_compat", plan_id="pro", kind="subscription"))
    assert bal > 0
