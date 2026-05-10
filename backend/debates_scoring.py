"""
AI Debate Rooms — scoring service.

Uses Claude Sonnet 4.5 via Emergent LLM key to score arguments on:
- clarity, logic, evidence, originality, civility, persuasiveness (each 0-100)
- final_score (0-100), short feedback string

Returns a strict dict; on any failure, returns a degraded but usable score
(never blocks publishing — debates need flow). However it DOES classify
abusive content for moderation_status="hidden".
"""
import json
import logging
import os
import re
from typing import Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")

SYSTEM_PROMPT = """You are an impartial AI debate judge for a public debate room.
You will receive a single argument. Score it on these dimensions (each integer 0-100):

- clarity: how clearly the argument is articulated
- logic: structural soundness, internal consistency, freedom from fallacies
- evidence: presence of facts, data, examples, citations, lived experience
- originality: novelty of angle vs. obvious takes
- civility: respect for opponents and absence of personal attacks
- persuasiveness: overall power to change a reasonable mind

Then compute a final_score 0-100 = a weighted blend you choose, but if civility < 40
the final_score MUST be capped at 40 regardless of other dimensions.

Also classify the argument:
- "ok": publishable
- "hidden": personal attack, slur, hate, harassment, threats, sexual content, doxxing,
  or pure spam. Hidden arguments are NEVER published.

Return STRICT JSON ONLY (no prose, no markdown fences). Schema:
{
  "clarity": int,
  "logic": int,
  "evidence": int,
  "originality": int,
  "civility": int,
  "persuasiveness": int,
  "final_score": int,
  "feedback": "1-2 sentences, specific, no flattery",
  "moderation_status": "ok" | "hidden",
  "moderation_reason": "string or empty"
}
"""


def _heuristic_block(text: str) -> Optional[str]:
    t = (text or "").strip()
    if len(t) < 10:
        return "too_short"
    if len(t) > 4000:
        return "too_long"
    # crude doxxing — phone/email patterns
    if re.search(r"\b\d{10,}\b", t) or re.search(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", t):
        return "contact_info"
    return None


def _empty_score(reason: str = "") -> dict:
    return {
        "clarity": 0,
        "logic": 0,
        "evidence": 0,
        "originality": 0,
        "civility": 0,
        "persuasiveness": 0,
        "final_score": 0,
        "feedback": "",
        "moderation_status": "hidden",
        "moderation_reason": reason or "blocked",
        "raw_model_response": "",
        "scoring_version": "debate_v1",
    }


def _degraded_score(reason: str = "") -> dict:
    """Fallback when LLM is unavailable. Still publishable; flagged as degraded."""
    return {
        "clarity": 50,
        "logic": 50,
        "evidence": 50,
        "originality": 50,
        "civility": 50,
        "persuasiveness": 50,
        "final_score": 50,
        "feedback": "AI scoring unavailable — argument published with neutral score.",
        "moderation_status": "ok",
        "moderation_reason": reason,
        "raw_model_response": "",
        "scoring_version": "debate_v1_degraded",
    }


async def score_argument(content: str, debate_title: str, side: str, side_label: str) -> dict:
    """Score a debate argument. Always returns a dict with a stable schema."""
    text = (content or "").strip()
    h = _heuristic_block(text)
    if h:
        out = _empty_score(h)
        return out

    if not EMERGENT_LLM_KEY:
        logger.error("EMERGENT_LLM_KEY missing — debate scoring degraded")
        return _degraded_score("llm_key_missing")

    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"debate_{abs(hash(text)) % 10**8}",
            system_message=SYSTEM_PROMPT,
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")
        user_msg = (
            f"DEBATE TOPIC: {debate_title}\n"
            f"USER'S SIDE: {side} ({side_label})\n"
            f"ARGUMENT:\n{text}\n\n"
            f"Score this argument as the user's contribution to their side."
        )
        raw = await chat.send_message(UserMessage(text=user_msg))
    except Exception as e:
        logger.exception("Debate scoring LLM failed: %s", e)
        return _degraded_score("llm_error")

    raw_str = (raw or "").strip().strip("`")
    if raw_str.startswith("json"):
        raw_str = raw_str[4:].strip()
    try:
        parsed = json.loads(raw_str)
    except Exception:
        logger.warning("Debate score parse failed: %r", raw_str[:300])
        return _degraded_score("parse_error")

    def _clamp(v):
        try:
            return max(0, min(100, int(v)))
        except Exception:
            return 50

    clarity = _clamp(parsed.get("clarity"))
    logic = _clamp(parsed.get("logic"))
    evidence = _clamp(parsed.get("evidence"))
    originality = _clamp(parsed.get("originality"))
    civility = _clamp(parsed.get("civility"))
    persuasiveness = _clamp(parsed.get("persuasiveness"))
    final_score = _clamp(parsed.get("final_score"))
    if civility < 40:
        final_score = min(final_score, 40)
    mod = (parsed.get("moderation_status") or "ok").lower()
    if mod not in ("ok", "hidden"):
        mod = "ok"
    feedback = (parsed.get("feedback") or "").strip()[:280]
    mod_reason = (parsed.get("moderation_reason") or "").strip()[:120]

    return {
        "clarity": clarity,
        "logic": logic,
        "evidence": evidence,
        "originality": originality,
        "civility": civility,
        "persuasiveness": persuasiveness,
        "final_score": final_score,
        "feedback": feedback,
        "moderation_status": mod,
        "moderation_reason": mod_reason,
        "raw_model_response": raw_str[:4000],
        "scoring_version": "debate_v1",
    }
