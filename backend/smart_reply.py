"""
Smart Reply — third product on the same site, separate funnels.

Critical analytics rule: every event must be stamped with
metadata.experience_variant = "smart_reply_v1" so this never merges with
CloneMe/Mood-Chat funnels.
"""
import os
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from emergentintegrations.llm.chat import LlmChat, UserMessage

from db import db
from auth import get_current_user
from models import now_iso

router = APIRouter(prefix="/api/smart-reply", tags=["smart-reply"])
logger = logging.getLogger(__name__)

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
SMART_REPLY_MODEL = ("anthropic", "claude-sonnet-4-5-20250929")
EXPERIENCE_VARIANT = "smart_reply_v1"

FREE_DAILY_LIMIT = 5

MODES = {
    "dating": "dating reply (real chemistry, never coercive)",
    "professional": "professional message (clear, polite, decisive)",
    "apology": "sincere apology (own it, no excuses)",
    "negotiation": "negotiation reply (firm, calm, value-anchored)",
}
TONES = ["warm", "calm", "flirty", "professional", "confident", "direct"]


# -------------- Schemas --------------
class GenerateRequest(BaseModel):
    incoming_message: str = Field(min_length=1, max_length=2000)
    mode: Literal["dating", "professional", "apology", "negotiation"]
    desired_tone: Literal["warm", "calm", "flirty", "professional", "confident", "direct"] = "warm"
    relationship_context: str = Field(default="", max_length=500)
    user_goal: str = Field(default="", max_length=300)
    what_i_want_to_say: str = Field(default="", max_length=500)


class GeneratedReply(BaseModel):
    label: Literal["safe", "warm", "confident"]
    length: Literal["short", "medium", "long"]
    reply: str
    why_it_works: str
    risk_level: Literal["low", "medium", "high"]


class GenerateResponse(BaseModel):
    session_id: str
    mode: str
    desired_tone: str
    tone_explanation: str
    risk_warning: Optional[str] = None
    replies: List[GeneratedReply]
    daily_remaining: int
    is_pro: bool


# -------------- Usage Gate --------------
async def _check_usage(user_id: str) -> dict:
    """Verify user is allowed to generate. Raise 402 if exhausted. Does NOT increment."""
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    sub_status = user.get("subscription_status", "free")
    is_pro = sub_status == "pro"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last_day = user.get("daily_reply_day")
    count = int(user.get("daily_reply_count", 0)) if last_day == today else 0

    if not is_pro and count >= FREE_DAILY_LIMIT:
        await _emit(user_id, "smart_reply_usage_limit_hit", {"daily_count": count})
        raise HTTPException(
            status_code=402,
            detail={"code": "usage_limit_reached", "limit": FREE_DAILY_LIMIT, "remaining": 0},
        )

    user["_today"] = today
    user["_current_count"] = count
    user["subscription_status"] = sub_status
    return user


async def _consume_usage(user_id: str, today: str, current_count: int) -> int:
    """Increment daily count. Called only after a successful generation."""
    new_count = current_count + 1
    await db.users.update_one(
        {"user_id": user_id},
        {"$set": {
            "daily_reply_count": new_count,
            "daily_reply_day": today,
            "updated_at": now_iso(),
        }},
    )
    return new_count


async def _emit(user_id: Optional[str], event_name: str, props: Optional[dict] = None):
    await db.clone_analytics.insert_one({
        "event_id": uuid.uuid4().hex,
        "event_name": event_name,
        "user_id": user_id,
        "clone_id": None,
        "metadata": {**(props or {}), "experience_variant": EXPERIENCE_VARIANT},
        "created_at": now_iso(),
    })


# -------------- Prompt builder + safety --------------
def _build_system_prompt(mode: str, tone: str) -> str:
    mode_desc = MODES[mode]
    return f"""You are SmartReply, an emotionally intelligent assistant that helps the user craft a single reply they can send right now.

CONTEXT
- Mode: {mode} — {mode_desc}
- Desired tone: {tone}

OUTPUT — RETURN STRICT JSON ONLY (no prose, no markdown fences):
{{
  "tone_explanation": "1 short sentence: how the tone was applied",
  "risk_warning": "OPTIONAL — only set if a reply could escalate conflict, hurt feelings, or be misread. <=1 sentence. Otherwise empty string.",
  "safety_flags": {{
    "harassment": false,
    "sexual_content": false,
    "manipulation": false,
    "self_harm": false,
    "legal_or_financial": false
  }},
  "replies": [
    {{ "label": "safe",      "length": "short",  "reply": "...", "why_it_works": "...", "risk_level": "low" }},
    {{ "label": "warm",      "length": "medium", "reply": "...", "why_it_works": "...", "risk_level": "low|medium" }},
    {{ "label": "confident", "length": "long",   "reply": "...", "why_it_works": "...", "risk_level": "low|medium" }}
  ]
}}

WRITING RULES
- Replies must sound human, copy-ready, and like a real person typing.
- "short" = 1-2 sentences. "medium" = 3-4 sentences. "long" = 5-7 sentences max.
- Match the requested tone but never robotic.
- Preserve realism; no clichés like "I hope this email finds you well".
- Never invent facts the user did not provide.

HARD SAFETY RULES — refuse and return safe alternatives instead:
- No harassment, manipulation, coercion, sexual pressure, or revenge content.
- No fake legal threats, fake credentials, or impersonation.
- No replies that pressure consent, isolate the recipient, or shame them.
- If the incoming message indicates self-harm, the safe reply must encourage reaching a trusted person or local emergency services; never minimize.

If the user's intent is unsafe, set the relevant safety_flag=true and rewrite all 3 replies as the *safest* equivalent that achieves a healthy version of the goal."""


def _build_user_message(req: GenerateRequest) -> str:
    parts = [
        f"INCOMING MESSAGE:\n{req.incoming_message.strip()}",
    ]
    if req.relationship_context.strip():
        parts.append(f"RELATIONSHIP CONTEXT:\n{req.relationship_context.strip()}")
    if req.user_goal.strip():
        parts.append(f"GOAL:\n{req.user_goal.strip()}")
    if req.what_i_want_to_say.strip():
        parts.append(f"WHAT I WANT TO SAY (rough):\n{req.what_i_want_to_say.strip()}")
    parts.append("Now produce the strict JSON described in the system prompt.")
    return "\n\n".join(parts)


def _parse_llm_json(raw: str) -> dict:
    """Best-effort JSON extraction (handles ```json fences etc)."""
    if not raw:
        raise ValueError("empty LLM response")
    s = raw.strip()
    if s.startswith("```"):
        # strip code fences
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    # find first { and last }
    first = s.find("{")
    last = s.rfind("}")
    if first == -1 or last == -1:
        raise ValueError("no JSON object in response")
    return json.loads(s[first:last + 1])


def _normalize_payload(parsed: dict) -> dict:
    replies = parsed.get("replies", []) or []
    if len(replies) < 3:
        raise ValueError("expected 3 replies")
    # enforce label/length pairing matches our contract
    expected = [("safe", "short"), ("warm", "medium"), ("confident", "long")]
    out = []
    for i, (label, length) in enumerate(expected):
        r = replies[i] or {}
        out.append({
            "label": label,
            "length": length,
            "reply": str(r.get("reply", "")).strip() or "(no reply generated)",
            "why_it_works": str(r.get("why_it_works", "")).strip(),
            "risk_level": r.get("risk_level") if r.get("risk_level") in ("low", "medium", "high") else "low",
        })
    return {
        "tone_explanation": str(parsed.get("tone_explanation", "")).strip(),
        "risk_warning": str(parsed.get("risk_warning", "")).strip() or None,
        "safety_flags": parsed.get("safety_flags", {}) or {},
        "replies": out,
    }


# -------------- Endpoints --------------
@router.get("/subscription/status")
async def subscription_status(user: dict = Depends(get_current_user)):
    sub_status = user.get("subscription_status", "free")
    is_pro = sub_status == "pro"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last_day = user.get("daily_reply_day")
    used = int(user.get("daily_reply_count", 0)) if last_day == today else 0
    return {
        "subscription_status": sub_status,
        "is_pro": is_pro,
        "daily_limit": None if is_pro else FREE_DAILY_LIMIT,
        "daily_used": used,
        "daily_remaining": None if is_pro else max(0, FREE_DAILY_LIMIT - used),
    }


@router.post("/generate", response_model=GenerateResponse)
async def generate(payload: GenerateRequest, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]

    await _emit(user_id, "smart_reply_generate_clicked", {"mode": payload.mode, "tone": payload.desired_tone})

    user = await _check_usage(user_id)
    is_pro = user.get("subscription_status") == "pro"
    today = user["_today"]
    prev_count = user["_current_count"]

    if not EMERGENT_LLM_KEY:
        raise HTTPException(status_code=500, detail="LLM not configured")

    system_prompt = _build_system_prompt(payload.mode, payload.desired_tone)
    user_msg_text = _build_user_message(payload)
    session_id = f"sr_{uuid.uuid4().hex[:14]}"

    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=session_id,
            system_message=system_prompt,
        ).with_model(SMART_REPLY_MODEL[0], SMART_REPLY_MODEL[1])
        raw = await chat.send_message(UserMessage(text=user_msg_text))
    except Exception as e:
        logger.exception("Smart Reply LLM failed")
        raise HTTPException(status_code=502, detail=f"Generation failed: {type(e).__name__}")

    try:
        parsed = _parse_llm_json(raw)
        norm = _normalize_payload(parsed)
    except Exception as e:
        logger.error("Smart Reply parse failed: %s | raw=%s", e, (raw or "")[:500])
        raise HTTPException(status_code=502, detail="Could not parse model output")

    # Persist session
    doc = {
        "session_id": session_id,
        "user_id": user_id,
        "mode": payload.mode,
        "desired_tone": payload.desired_tone,
        "incoming_message": payload.incoming_message,
        "relationship_context": payload.relationship_context,
        "user_goal": payload.user_goal,
        "what_i_want_to_say": payload.what_i_want_to_say,
        "tone_explanation": norm["tone_explanation"],
        "risk_warning": norm["risk_warning"],
        "safety_flags": norm["safety_flags"],
        "generated_replies": norm["replies"],
        "experience_variant": EXPERIENCE_VARIANT,
        "created_at": now_iso(),
    }
    await db.smart_reply_sessions.insert_one(dict(doc))

    # Only consume quota on successful generation + parse
    new_count = await _consume_usage(user_id, today, prev_count) if not is_pro else prev_count
    remaining = -1 if is_pro else max(0, FREE_DAILY_LIMIT - new_count)

    await _emit(user_id, "smart_reply_generated", {
        "mode": payload.mode,
        "tone": payload.desired_tone,
        "session_id": session_id,
        "risk_warning": bool(norm["risk_warning"]),
    })

    return GenerateResponse(
        session_id=session_id,
        mode=payload.mode,
        desired_tone=payload.desired_tone,
        tone_explanation=norm["tone_explanation"],
        risk_warning=norm["risk_warning"],
        replies=[GeneratedReply(**r) for r in norm["replies"]],
        daily_remaining=remaining,
        is_pro=is_pro,
    )


class TrackRequest(BaseModel):
    event_name: str = Field(min_length=1, max_length=64)
    metadata: Optional[dict] = None


@router.post("/track")
async def track(payload: TrackRequest, user: dict = Depends(get_current_user)):
    """Lightweight client-side analytics for smart-reply UI events."""
    allowed = {
        "smart_reply_paste_started",
        "smart_reply_copy_clicked",
        "smart_reply_regenerate_clicked",
        "smart_reply_favorited",
        "smart_reply_unfavorited",
        "smart_reply_paywall_opened",
        "smart_reply_upgrade_clicked",
        "smart_reply_landing_view",
        "smart_reply_page_opened",
    }
    if payload.event_name not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported event")
    await _emit(user["user_id"], payload.event_name, payload.metadata or {})
    return {"ok": True}


@router.get("/history")
async def history(user: dict = Depends(get_current_user), limit: int = 50):
    cursor = (
        db.smart_reply_sessions.find({"user_id": user["user_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .limit(min(max(limit, 1), 100))
    )
    return {"sessions": await cursor.to_list(100)}


class FavoriteRequest(BaseModel):
    reply_index: int = Field(ge=0, le=2)
    reply_text: str = Field(min_length=1, max_length=4000)


@router.post("/{session_id}/favorite")
async def favorite_reply(session_id: str, payload: FavoriteRequest, user: dict = Depends(get_current_user)):
    sess = await db.smart_reply_sessions.find_one(
        {"session_id": session_id, "user_id": user["user_id"]},
        {"_id": 0},
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    fav_id = f"fav_{uuid.uuid4().hex[:14]}"
    doc = {
        "favorite_id": fav_id,
        "user_id": user["user_id"],
        "session_id": session_id,
        "mode": sess.get("mode"),
        "reply_index": payload.reply_index,
        "reply_text": payload.reply_text,
        "label": (sess.get("generated_replies") or [{}, {}, {}])[payload.reply_index].get("label", "safe"),
        "created_at": now_iso(),
    }
    await db.smart_reply_favorites.insert_one(dict(doc))
    await _emit(user["user_id"], "smart_reply_favorited", {"session_id": session_id, "reply_index": payload.reply_index})
    return {"favorite_id": fav_id, "ok": True}


@router.get("/favorites")
async def list_favorites(user: dict = Depends(get_current_user)):
    cursor = (
        db.smart_reply_favorites.find({"user_id": user["user_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .limit(200)
    )
    return {"favorites": await cursor.to_list(200)}


@router.delete("/favorites/{favorite_id}")
async def delete_favorite(favorite_id: str, user: dict = Depends(get_current_user)):
    res = await db.smart_reply_favorites.delete_one({
        "favorite_id": favorite_id,
        "user_id": user["user_id"],
    })
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Favorite not found")
    await _emit(user["user_id"], "smart_reply_unfavorited", {"favorite_id": favorite_id})
    return {"ok": True}
