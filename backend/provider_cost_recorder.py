"""
provider_cost_recorder.py — Actual-provider cost ingestion.

Why a separate module
---------------------
The Cost Telemetry Dashboard's previous `estimated_cost = credits × cost_per_credit`
math worked for trends but couldn't catch a misconfigured-pricing leak (a feature
that consumes 50 credits but actually costs ₹40 of provider spend). This module
records the REAL provider cost at the call site, using:

  - Model name (e.g. `claude-sonnet-4-5-20250929`, `gpt-4o-mini`)
  - Token / unit counts (estimated for LLMs because the SDK strips
    `usage`, exact for TTS/STT where we have audio seconds)
  - Operator-configurable per-1k-tokens prices in admin_settings

Each call lands one row in `provider_cost_events`. Cost Telemetry then prefers
those rows over the apportionment math whenever they exist for the feature.

Why token estimation instead of exact
-------------------------------------
The `emergentintegrations.llm.chat.LlmChat.send_message()` API returns just the
content string — `response.usage` is discarded internally. Industry-standard
heuristic: 1 token ≈ 4 characters of English text (off by ~10% on average,
much less than the noise floor of pricing changes between billing cycles).
The dashboard labels this honestly as `cost_method=token_estimate`.

For audio surfaces we record exact seconds (durations are knowable client-side
and we already pass them around). Those rows carry `cost_method=metered`.
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from db import db
from admin import get_admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/cost-telemetry", tags=["billing-admin"])

PRICING_CONFIG_KEY = "provider_pricing_v1"

# Defaults reflect 2026 public pricing for the models we actually use, in USD
# per 1k tokens. The operator can override any of these from the admin
# console without code changes.
DEFAULT_PRICING_USD: dict[str, dict] = {
    # OpenAI
    "openai/gpt-4o": {"input_per_1k": 0.0025, "output_per_1k": 0.01},
    "openai/gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.0006},
    "openai/gpt-5.2": {"input_per_1k": 0.005, "output_per_1k": 0.015},
    # Anthropic
    "anthropic/claude-sonnet-4-5-20250929": {"input_per_1k": 0.003, "output_per_1k": 0.015},
    "anthropic/claude-haiku-4-5": {"input_per_1k": 0.0008, "output_per_1k": 0.004},
    "anthropic/claude-opus-4-5": {"input_per_1k": 0.015, "output_per_1k": 0.075},
    # Gemini
    "google/gemini-3-pro": {"input_per_1k": 0.00125, "output_per_1k": 0.005},
    "google/gemini-3-flash": {"input_per_1k": 0.00010, "output_per_1k": 0.0004},
    # Audio (per-minute)
    "openai/tts-1": {"per_minute": 0.015},
    "openai/whisper-1": {"per_minute": 0.006},
    "elevenlabs/multilingual_v2": {"per_minute": 0.18},
    # Video (per-second of output)
    "fal-ai/lipsync": {"per_second": 0.05},
    "heygen/v2": {"per_second": 0.10},
    # Image
    "openai/gpt-image-1": {"per_image": 0.04},
    "google/nano-banana": {"per_image": 0.039},
}

# USD → INR conversion. The operator can override too if their rails are different.
DEFAULT_USD_TO_INR = 86.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_tokens(text: str) -> int:
    """1 token ≈ 4 chars of English text. This matches OpenAI's published
    heuristic to within ~10% on natural-language strings. Multi-byte / CJK
    content tokenizes denser — we under-count there, which is the safer side
    of the cost ledger."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


async def _get_pricing() -> dict:
    """Returns {'usd_per_1k': {...}, 'usd_to_inr': float}. Defaults applied
    for any model key not explicitly overridden."""
    doc = await db.admin_settings.find_one({"key": PRICING_CONFIG_KEY}, {"_id": 0})
    overrides = (doc or {}).get("pricing") or {}
    usd_to_inr = float((doc or {}).get("usd_to_inr") or DEFAULT_USD_TO_INR)
    merged = {**DEFAULT_PRICING_USD}
    for k, v in overrides.items():
        if k in merged:
            merged[k] = {**merged[k], **(v or {})}
        else:
            merged[k] = v or {}
    return {"pricing": merged, "usd_to_inr": usd_to_inr}


def _resolve_pricing_key(provider: str, model: str) -> str:
    return f"{(provider or 'unknown').lower()}/{(model or 'unknown')}"


async def record_llm_call(
    *,
    user_id: Optional[str],
    request_id: Optional[str],
    feature: str,
    surface: str,
    provider: str,
    model: str,
    input_text: str,
    output_text: str,
) -> Optional[dict]:
    """Estimate tokens from text length, multiply by current pricing, persist.

    Best-effort: any failure logs and returns None — never raises into the
    caller's request path.
    """
    try:
        pricing = await _get_pricing()
        key = _resolve_pricing_key(provider, model)
        row_pricing = pricing["pricing"].get(key) or {}
        input_per_1k = float(row_pricing.get("input_per_1k") or 0)
        output_per_1k = float(row_pricing.get("output_per_1k") or 0)
        usd_to_inr = float(pricing["usd_to_inr"])

        input_tokens = estimate_tokens(input_text)
        output_tokens = estimate_tokens(output_text)
        cost_usd = (input_tokens / 1000.0) * input_per_1k + (output_tokens / 1000.0) * output_per_1k
        cost_inr = cost_usd * usd_to_inr

        doc = {
            "cost_id": "cost_" + uuid.uuid4().hex[:18],
            "user_id": user_id,
            "request_id": request_id,
            "feature": feature,
            "surface": surface,
            "provider": provider,
            "model": model,
            "pricing_key": key,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_chars": len(input_text or ""),
            "output_chars": len(output_text or ""),
            "input_per_1k_usd": input_per_1k,
            "output_per_1k_usd": output_per_1k,
            "cost_usd": round(cost_usd, 8),
            "cost_inr": round(cost_inr, 6),
            "usd_to_inr": usd_to_inr,
            "cost_method": "token_estimate",
            "is_priced": input_per_1k > 0 or output_per_1k > 0,
            "created_at": _now_iso(),
        }
        await db.provider_cost_events.insert_one(doc)
        return doc
    except Exception as e:
        logger.warning("record_llm_call failed (best-effort, swallowed): %s", e)
        return None


async def record_audio_call(
    *,
    user_id: Optional[str],
    request_id: Optional[str],
    feature: str,
    surface: str,
    provider: str,
    model: str,
    duration_seconds: float,
) -> Optional[dict]:
    """Per-minute pricing × duration. Used for TTS/STT/voice-clone surfaces."""
    try:
        pricing = await _get_pricing()
        key = _resolve_pricing_key(provider, model)
        row_pricing = pricing["pricing"].get(key) or {}
        per_minute = float(row_pricing.get("per_minute") or 0)
        per_second = float(row_pricing.get("per_second") or 0)
        usd_to_inr = float(pricing["usd_to_inr"])
        seconds = float(duration_seconds or 0)
        cost_usd = (seconds / 60.0) * per_minute + seconds * per_second
        cost_inr = cost_usd * usd_to_inr
        doc = {
            "cost_id": "cost_" + uuid.uuid4().hex[:18],
            "user_id": user_id, "request_id": request_id,
            "feature": feature, "surface": surface,
            "provider": provider, "model": model,
            "pricing_key": key,
            "duration_seconds": seconds,
            "per_minute_usd": per_minute, "per_second_usd": per_second,
            "cost_usd": round(cost_usd, 8), "cost_inr": round(cost_inr, 6),
            "usd_to_inr": usd_to_inr,
            "cost_method": "metered",
            "is_priced": per_minute > 0 or per_second > 0,
            "created_at": _now_iso(),
        }
        await db.provider_cost_events.insert_one(doc)
        return doc
    except Exception as e:
        logger.warning("record_audio_call failed (best-effort): %s", e)
        return None


# ─────────────── Admin endpoints ───────────────

@router.get("/provider-pricing")
async def get_provider_pricing(admin: dict = Depends(get_admin_user)):
    p = await _get_pricing()
    return {
        "pricing": p["pricing"],
        "usd_to_inr": p["usd_to_inr"],
        "defaults": DEFAULT_PRICING_USD,
        "default_usd_to_inr": DEFAULT_USD_TO_INR,
    }


@router.post("/provider-pricing")
async def set_provider_pricing(payload: dict, admin: dict = Depends(get_admin_user)):
    pricing = (payload or {}).get("pricing") or {}
    if not isinstance(pricing, dict):
        raise HTTPException(400, detail={"code": "invalid_payload", "message": "Expected 'pricing' dict."})
    usd_to_inr = float((payload or {}).get("usd_to_inr") or DEFAULT_USD_TO_INR)
    if usd_to_inr <= 0:
        raise HTTPException(400, detail={"code": "invalid_fx", "message": "usd_to_inr must be > 0"})
    # Validate per-model entries: only allow known keys, sanity-check values.
    cleaned: dict[str, dict] = {}
    for key, vals in pricing.items():
        if not isinstance(vals, dict):
            continue
        bucket = {}
        for vk in ("input_per_1k", "output_per_1k", "per_minute", "per_second", "per_image"):
            if vk in vals and vals[vk] not in (None, ""):
                try:
                    f = float(vals[vk])
                except (TypeError, ValueError):
                    raise HTTPException(400, detail={"code": "invalid_value", "message": f"{key}.{vk} must be a number"})
                if f < 0:
                    raise HTTPException(400, detail={"code": "negative_value", "message": f"{key}.{vk} cannot be negative"})
                bucket[vk] = round(f, 6)
        if bucket:
            cleaned[key] = bucket

    await db.admin_settings.update_one(
        {"key": PRICING_CONFIG_KEY},
        {"$set": {
            "key": PRICING_CONFIG_KEY,
            "pricing": cleaned,
            "usd_to_inr": usd_to_inr,
            "updated_at": _now_iso(),
            "updated_by": admin.get("email"),
        }},
        upsert=True,
    )
    return {"ok": True, "pricing": cleaned, "usd_to_inr": usd_to_inr}


async def ensure_indexes() -> None:
    try:
        await db.provider_cost_events.create_index("cost_id", unique=True)
        await db.provider_cost_events.create_index([("created_at", -1)])
        await db.provider_cost_events.create_index([("feature", 1), ("created_at", -1)])
        await db.provider_cost_events.create_index("request_id", sparse=True)
        logger.info("provider_cost_recorder: indexes ensured")
    except Exception as e:
        logger.warning("provider_cost_recorder: index creation failed: %s", e)
