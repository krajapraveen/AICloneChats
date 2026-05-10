"""
AI moderation for Anonymous Reality rooms.
Toxicity-only Phase 1: BLOCK on uncertainty, ESCALATE self-harm with supportive response.

Returns: {decision, category, reason, severity, supportive_response?}
- decision: allow | block | escalate
- category: toxicity | hate | harassment | threats | sexual_abuse | doxxing | spam | self_harm | clean
- supportive_response: ONLY for self_harm — never block, show this instead

Operator principle: optimize for emotional safety, not engagement.
"""
import json
import logging
import os
import re
from typing import Tuple

from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")

SYSTEM_PROMPT = """You are the safety moderator for an anonymous emotional-support chat room.
Users are posting honestly about loneliness, family pressure, money stress, mental load, etc.

Classify the message into ONE of:
- "clean": safe to publish.
- "toxicity": insults, slurs, name-calling, dehumanizing language.
- "hate": targeted hate speech against a group (race, religion, gender, sexuality, etc.).
- "harassment": personal attacks, bullying, doxxing-adjacent intimidation.
- "threats": violence, harm, intimidation directed at a person or group.
- "sexual_abuse": sexual content targeting minors or non-consenting parties; predatory grooming.
- "doxxing": real names, phone numbers, addresses, emails, account handles of others.
- "spam": ads, scams, repetitive nonsense, cryptocurrency promotion.
- "self_harm": user expressing suicidal ideation or self-harm intent (about themselves).

Decision rules:
- If category is "clean": decision=allow.
- If category is "self_harm": decision=escalate. Include a brief, kind, NON-shaming supportive_response (1-2 sentences) acknowledging their feelings and gently noting that talking to someone who can help (a trusted friend, a hotline) is a strong move. Never moralize. Never block self-harm posts — escalate silently.
- For everything else: decision=block.
- WHEN UNCERTAIN: choose block. False positives are recoverable. False negatives destroy room trust.

Severity: 1-5. Higher = more dangerous.

Output ONLY a JSON object. No prose, no markdown fences. Schema:
{"decision":"allow|block|escalate","category":"...","severity":1-5,"reason":"<10 words","supportive_response":"..."}
"""


def _heuristic_check(text: str) -> Tuple[str, str] | None:
    """Cheap pre-filter for obvious cases. Returns (category, reason) if matched, else None."""
    t = (text or "").strip()
    if not t:
        return ("spam", "empty")
    if len(t) > 1500:
        return ("spam", "too_long")
    # crude doxxing — phone/email patterns
    if re.search(r"\b\d{10,}\b", t) or re.search(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", t):
        return ("doxxing", "contact_info")
    return None


async def moderate_message(text: str) -> dict:
    """Run moderation. Returns dict with decision/category/severity/reason/supportive_response."""
    text = (text or "").strip()
    h = _heuristic_check(text)
    if h:
        cat, reason = h
        return {"decision": "block", "category": cat, "severity": 2, "reason": reason, "supportive_response": None}

    if not EMERGENT_LLM_KEY:
        # Fail-closed: when LLM not configured, default to allow (avoid silencing all rooms)
        # but log loudly so this never happens in production
        logger.error("EMERGENT_LLM_KEY missing — moderation degraded to allow-all")
        return {"decision": "allow", "category": "clean", "severity": 1, "reason": "moderation_unavailable", "supportive_response": None}

    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"mod_{abs(hash(text)) % 10**8}",
            system_message=SYSTEM_PROMPT,
        ).with_model("anthropic", "claude-haiku-4-5-20251001")
        raw = await chat.send_message(UserMessage(text=text))
    except Exception as e:
        # Operator rule: when uncertain, BLOCK + ESCALATE.
        logger.exception("Moderation LLM failed: %s", e)
        return {"decision": "escalate", "category": "harassment", "severity": 3, "reason": "moderation_error", "supportive_response": None}

    raw = (raw or "").strip().strip("`")
    # Strip optional ```json fences
    if raw.startswith("json"):
        raw = raw[4:].strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        # Couldn't parse — fail closed
        logger.warning("Moderation parse failed: %r", raw)
        return {"decision": "block", "category": "harassment", "severity": 3, "reason": "moderation_parse_error", "supportive_response": None}

    decision = parsed.get("decision") or "block"
    if decision not in ("allow", "block", "escalate"):
        decision = "block"
    category = parsed.get("category") or "clean"
    severity = int(parsed.get("severity") or 1)
    severity = max(1, min(5, severity))
    reason = (parsed.get("reason") or "")[:120]
    supportive = parsed.get("supportive_response") or None
    if category == "self_harm" and decision != "escalate":
        decision = "escalate"
    return {
        "decision": decision,
        "category": category,
        "severity": severity,
        "reason": reason,
        "supportive_response": supportive,
    }
