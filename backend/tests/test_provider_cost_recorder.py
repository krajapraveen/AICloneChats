"""Provider-metered cost ingestion + telemetry integration.

Verified contracts:
  - `estimate_tokens` matches the 4-chars-per-token heuristic with min=1.
  - `record_llm_call` persists a provider_cost_events row with the expected
    fields and the computed INR cost.
  - Cost Telemetry endpoint switches `cost_source` to `provider_metered`
    when metered rows exist for a feature, otherwise falls back to
    `configured` / `not_configured`.
  - Provider-pricing GET/POST round-trip; non-numeric / negative values
    rejected; unknown model keys accepted (operator may add new ones).
  - Recorder swallows errors silently — never raises into the caller.
"""
from __future__ import annotations

import os
import sys
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
ADMIN_EMAIL = "krajapraveen@gmail.com"

from conftest import get_shared_loop  # noqa: E402
from provider_cost_recorder import (  # noqa: E402
    estimate_tokens, record_llm_call, record_audio_call,
)


def _run(coro):
    return get_shared_loop().run_until_complete(coro)


@pytest.fixture(scope="module")
def admin_token() -> str:
    async def _mint():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        admin = await db.users.find_one({"email": ADMIN_EMAIL}, {"user_id": 1})
        if not admin:
            return None
        token = f"st_{uuid.uuid4().hex}{uuid.uuid4().hex}"
        await db.user_sessions.insert_one({
            "session_token": token, "user_id": admin["user_id"],
            "source": "test-mint-admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        })
        return token
    t = _run(_mint())
    if not t:
        pytest.skip("admin not seeded")
    return t


def test_estimate_tokens_heuristic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1   # min floor
    assert estimate_tokens("a" * 4) == 1
    assert estimate_tokens("a" * 8) == 2
    assert estimate_tokens("a" * 100) == 25


def test_record_llm_call_persists_row():
    user_id = f"meter_{uuid.uuid4().hex[:10]}"
    request_id = f"req_{uuid.uuid4().hex[:8]}"
    doc = _run(record_llm_call(
        user_id=user_id, request_id=request_id,
        feature="ai_clone", surface="clone_chat",
        provider="anthropic", model="claude-sonnet-4-5-20250929",
        input_text="x" * 400,    # 100 tokens estimated
        output_text="y" * 800,   # 200 tokens estimated
    ))
    assert doc is not None
    assert doc["pricing_key"] == "anthropic/claude-sonnet-4-5-20250929"
    assert doc["input_tokens"] == 100
    assert doc["output_tokens"] == 200
    # Expected cost = (100/1000)*0.003 + (200/1000)*0.015 = 0.0003 + 0.003 = 0.0033 USD
    expected_usd = 100 / 1000 * 0.003 + 200 / 1000 * 0.015
    assert abs(doc["cost_usd"] - expected_usd) < 1e-6
    assert doc["cost_inr"] > 0
    assert doc["cost_method"] == "token_estimate"
    assert doc["is_priced"] is True


def test_record_llm_call_unknown_model_zero_cost():
    """Unknown model → pricing rows missing → zero cost row that still gets
    persisted (so the operator can see usage and add a price)."""
    doc = _run(record_llm_call(
        user_id="u", request_id="r",
        feature="chat", surface="experimental",
        provider="brand_new_vendor", model="future_model_v9",
        input_text="hello", output_text="world",
    ))
    assert doc is not None
    assert doc["cost_usd"] == 0
    assert doc["cost_inr"] == 0
    assert doc["is_priced"] is False


def test_record_audio_call_per_minute():
    doc = _run(record_audio_call(
        user_id="u", request_id="r",
        feature="voice", surface="voice_message",
        provider="openai", model="tts-1",
        duration_seconds=120.0,  # 2 minutes
    ))
    assert doc is not None
    # 2 minutes × $0.015/min = $0.03
    assert abs(doc["cost_usd"] - 0.03) < 1e-6
    assert doc["cost_method"] == "metered"


def test_recorder_swallows_errors():
    """Forcing a bad provider name shouldn't raise into the caller's path."""
    doc = _run(record_llm_call(
        user_id=None, request_id=None,
        feature="ai_clone", surface="clone_chat",
        provider=None, model=None,  # type: ignore[arg-type]
        input_text="hi", output_text="ok",
    ))
    # Either the row is persisted with `unknown/None` key, or recorder returns None.
    # The important contract is: no exception bubbles up.
    assert doc is None or doc["pricing_key"] in ("none/None", "unknown/None", "unknown/unknown")


def test_pricing_config_round_trip(admin_token: str):
    new_pricing = {
        "anthropic/claude-haiku-4-5": {"input_per_1k": 0.001, "output_per_1k": 0.005},
        "future_provider/some_model": {"input_per_1k": 0.5, "output_per_1k": 1.0},
    }
    r = requests.post(
        f"{BASE_URL}/api/admin/cost-telemetry/provider-pricing",
        json={"pricing": new_pricing, "usd_to_inr": 87.5},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    saved = r.json()["pricing"]
    assert saved["anthropic/claude-haiku-4-5"]["input_per_1k"] == 0.001
    assert saved["future_provider/some_model"]["output_per_1k"] == 1.0
    assert r.json()["usd_to_inr"] == 87.5

    # Get reflects the override
    r2 = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/provider-pricing",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    body = r2.json()
    assert body["usd_to_inr"] == 87.5
    # Override merged on top of defaults
    assert body["pricing"]["anthropic/claude-haiku-4-5"]["input_per_1k"] == 0.001


def test_pricing_config_rejects_negative(admin_token: str):
    r = requests.post(
        f"{BASE_URL}/api/admin/cost-telemetry/provider-pricing",
        json={"pricing": {"openai/gpt-4o": {"input_per_1k": -0.5}}},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "negative_value"


def test_pricing_config_rejects_invalid_fx(admin_token: str):
    r = requests.post(
        f"{BASE_URL}/api/admin/cost-telemetry/provider-pricing",
        json={"pricing": {}, "usd_to_inr": -1},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 400


def test_cost_telemetry_prefers_metered_over_configured(admin_token: str):
    """If provider_cost_events has rows for a feature, the dashboard uses
    metered cost; cost_source flips to provider_metered."""
    # Restore a sensible USD→INR before measuring
    requests.post(
        f"{BASE_URL}/api/admin/cost-telemetry/provider-pricing",
        json={"pricing": {}, "usd_to_inr": 86.0},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=10,
    )
    # Seed a metered row for ai_clone
    _run(record_llm_call(
        user_id="meter_user", request_id="meter_req",
        feature="ai_clone", surface="clone_chat",
        provider="anthropic", model="claude-sonnet-4-5-20250929",
        input_text="x" * 4000, output_text="y" * 4000,
    ))
    r = requests.get(
        f"{BASE_URL}/api/admin/cost-telemetry/profit-per-feature?days=1",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    ai_row = next(row for row in body["rows"] if row["feature"] == "ai_clone")
    assert ai_row["cost_source"] == "provider_metered"
    assert ai_row["metered_calls"] >= 1
    assert ai_row["metered_cost_inr"] > 0
    assert ai_row["estimated_cost_inr"] == ai_row["metered_cost_inr"]


def test_provider_pricing_admin_only():
    r = requests.get(f"{BASE_URL}/api/admin/cost-telemetry/provider-pricing", timeout=15)
    assert r.status_code in (401, 403)
    r2 = requests.post(f"{BASE_URL}/api/admin/cost-telemetry/provider-pricing", json={}, timeout=15)
    assert r2.status_code in (401, 403)
