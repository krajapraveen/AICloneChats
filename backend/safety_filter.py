"""
Centralized safety filter for aiclonechats.com.

Purpose:
- Pre-filter user inputs across all surfaces (clone chat, smart reply, debates, anonymous, clone bios).
- Pre-filter AI outputs before they reach the frontend.
- Log moderation events to `safety_moderation_events` for the admin dashboard.
- Provide a unified safety clause appended to every system prompt.

Design notes:
- Regex/dictionary prefilter is a *fast* layer. The deeper LLM moderation
  (debates_scoring.py + anonymous_moderation.py) remains the authority for
  contextual judgment. This module is the floor, not the ceiling.
- Logs ONLY a content snippet hash + category, never raw unsafe text. We do
  store a 60-char preview because moderators need *some* signal to triage.
- Categories:
    profanity        — vulgar/strong language
    sexual           — explicit adult content
    violence         — gore, threats, weapons
    hate             — slurs, harassment, dehumanizing language
    self_harm        — suicide/self-harm encouragement
    illegal          — piracy, drug-dealing how-tos, illegal activity
    impersonation    — celebrity/brand/franchise impersonation
- Severity:
    low      — soft profanity, can rewrite
    medium   — strong profanity, blocked
    high     — sexual/violence/hate/self_harm/illegal/impersonation, blocked
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


SAFETY_CLAUSE = (
    "\n\nSAFETY RULES (non-negotiable, applied to every reply):\n"
    "- Always respond using safe, respectful, non-vulgar, non-sexual, non-violent, non-hateful language.\n"
    "- Do NOT include profanity, adult/sexual content, violent descriptions, threats, harassment, slurs,\n"
    "  piracy or illegal-activity instructions, trademark misuse, celebrity impersonation, or copyrighted\n"
    "  character imitation (Disney/Marvel/anime/etc.).\n"
    "- If the user asks for unsafe content, politely redirect to a clean and helpful answer.\n"
    "- Never produce content that sexualizes minors or depicts real public figures in private/sexual scenarios.\n"
    "- Do not roleplay as a real living celebrity by name without explicit consent context.\n"
    "- If unsure, choose the safer response."
)


SAFE_FALLBACK_REPLY = (
    "Let's keep this chat safe and respectful. Could you ask something clean, friendly, and appropriate?"
)


# --- Category regex patterns ---
# These are intentionally broad. We rewrite-on-low and block-on-high.
# Word boundaries and case-insensitive.
_HIGH_SEXUAL = [
    r"\bporn\b", r"\bxxx\b", r"\bnude\b", r"\bnaked\b", r"\bnaked\s+pic", r"\bsex\s+chat",
    r"\bblowjob\b", r"\bhandjob\b", r"\bcum\b", r"\bcumming\b", r"\borgasm",
    r"\b(suck|lick)\s+(my|her|his|your)\s*(dick|cock|pussy|tits|boobs)\b",
    r"\b(dick|cock|pussy|vagina|penis|tits|boobs)\b\s+(pic|photo|video|nude)",
    r"\berotic\b", r"\bnsfw\b", r"\bonlyfans\b", r"\bsexting\b",
    r"\bjerk\s*off\b", r"\bmasturbat",
    r"\bhorny\b\s+(roleplay|chat|story)",
    r"\bunderage\s+(sex|porn|nude)", r"\bcp\b\s*(image|video|pic)",
]
_HIGH_VIOLENCE = [
    r"\bkill\s+(yourself|myself|him|her|them)\b",
    r"\bhow\s+to\s+(kill|murder|stab|shoot|poison|behead|strangle)\b",
    r"\b(rape|raping)\b", r"\bbeheading\b", r"\bgore\b",
    r"\bbomb\s+(making|recipe|tutorial)\b", r"\bbuild\s+a\s+bomb\b",
    r"\bschool\s+shoot", r"\bmass\s+shoot",
    r"\b(behead|decapitate|dismember)\b",
]
_HIGH_HATE = [
    # Slurs we will NEVER tolerate. Intentionally non-exhaustive; LLM moderation handles the rest.
    r"\bn[i1]gg[ae3]r[s]?\b", r"\bn[i1]gg[ae3]\b",
    r"\bf[a4]gg[o0]t[s]?\b", r"\bf[a4]g\b(?!ot[a-z])",  # "fag" outside "fagot"-like words
    r"\bch[i1]nk[s]?\b", r"\bsp[i1]c[s]?\b\s+(people|kid|man|woman)",
    r"\bk[i1]ke[s]?\b", r"\btr[a4]nn[i1]e[s]?\b",
    r"\b(retard|retards|retarded)\b",
    r"\bgo\s+back\s+to\s+your\s+country\b",
]
_HIGH_SELF_HARM = [
    r"\bhow\s+to\s+(kill|hang|cut|overdose)\s+(myself|me)\b",
    r"\bbest\s+way\s+to\s+(die|kill\s+myself|end\s+(my\s+)?life)\b",
    r"\bsuicide\s+(method|note|plan)\b",
    r"\bencourage.*suicide\b",
]
_HIGH_ILLEGAL = [
    r"\bhow\s+to\s+(make|cook|synthesize)\s+(meth|crack|cocaine|heroin|fentanyl)\b",
    r"\bbuy\s+(meth|cocaine|heroin|fentanyl|guns?\s+illegally)\b",
    r"\bpirat(e|ed|ing)\s+(movie|software|game|netflix)\b",
    r"\bcrack(ed)?\s+(software|key|license|netflix|spotify)\b",
    r"\bchild\s+(porn|abuse\s+material|sexual)\b",
]
_HIGH_IMPERSONATION = [
    # Trademarked franchises/brands often appearing in clone bios. We redirect users
    # to original characters. Brand mentions in conversation are fine; impersonation isn't.
    r"\b(i\s+am|i'm|act\s+as|pretend\s+to\s+be|roleplay\s+as)\s+(mickey\s+mouse|donald\s+duck|elsa|anna|spider[\s-]?man|iron[\s-]?man|batman|superman|naruto|goku|pikachu|harry\s+potter|james\s+bond)\b",
    r"\b(i\s+am|i'm)\s+(elon\s+musk|donald\s+trump|barack\s+obama|taylor\s+swift|justin\s+bieber|kim\s+kardashian|mr\s*beast)\b",
    r"\bclone\s+of\s+(elon\s+musk|donald\s+trump|barack\s+obama|taylor\s+swift|justin\s+bieber|kim\s+kardashian|mr\s*beast)\b",
]

# Strong but rewritable profanity (medium severity → blocked from public posts, allowed in private with rewrite)
_MEDIUM_PROFANITY = [
    r"\bfuck(ing|ed|er)?\b", r"\bshit(ty|ed)?\b", r"\basshole[s]?\b",
    r"\bbitch(es)?\b", r"\bbastard[s]?\b", r"\bdick(head)?\b",
    r"\bcock(sucker)?\b", r"\bcunt[s]?\b", r"\bwhore[s]?\b", r"\bslut[s]?\b",
    r"\bmotherfucker[s]?\b",
]


def _compile(pats: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in pats]


_PATTERNS = {
    "sexual": _compile(_HIGH_SEXUAL),
    "violence": _compile(_HIGH_VIOLENCE),
    "hate": _compile(_HIGH_HATE),
    "self_harm": _compile(_HIGH_SELF_HARM),
    "illegal": _compile(_HIGH_ILLEGAL),
    "impersonation": _compile(_HIGH_IMPERSONATION),
    "profanity": _compile(_MEDIUM_PROFANITY),
}

# Categories whose hits are HIGH severity (block immediately)
_HIGH_CATEGORIES = {"sexual", "violence", "hate", "self_harm", "illegal", "impersonation"}


def _hash_input(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()[:16]


def _scan(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (category, severity) of the first match, or (None, None)."""
    if not text:
        return (None, None)
    for cat, patterns in _PATTERNS.items():
        for p in patterns:
            if p.search(text):
                sev = "high" if cat in _HIGH_CATEGORIES else "medium"
                return (cat, sev)
    return (None, None)


def sanitize_user_input(text: str) -> str:
    """Light cleanup for storage: strip control chars, collapse whitespace."""
    if not text:
        return ""
    t = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def moderate_user_input(text: str) -> dict:
    """
    Pre-flight check on user input. Returns:
      {action: "allow" | "block", category, severity, reason, snippet, input_hash}
    Block on any high-severity category. Profanity-only inputs are allowed through
    (the AI's reply will avoid mirroring them; output moderation catches anything
    that slips).
    """
    cat, sev = _scan(text or "")
    if cat is None:
        return {"action": "allow", "category": None, "severity": None, "reason": "", "snippet": "", "input_hash": _hash_input(text or "")}
    if sev == "high":
        return {
            "action": "block",
            "category": cat,
            "severity": sev,
            "reason": f"input_{cat}",
            "snippet": (text or "")[:60],
            "input_hash": _hash_input(text or ""),
        }
    return {
        "action": "allow",
        "category": cat,
        "severity": sev,
        "reason": "input_profanity_soft",
        "snippet": (text or "")[:60],
        "input_hash": _hash_input(text or ""),
    }


def moderate_ai_output(text: str) -> dict:
    """
    Output safety. ANY hit is blocked — the AI must never emit profanity to a public surface.
    Returns: {action: "allow" | "rewrite" | "block", ...}
    """
    cat, sev = _scan(text or "")
    if cat is None:
        return {"action": "allow", "category": None, "severity": None}
    # Output: even soft profanity gets rewritten (we don't echo strong language).
    return {
        "action": "block" if sev == "high" else "rewrite",
        "category": cat,
        "severity": sev,
        "reason": f"output_{cat}",
    }


def block_if_unsafe(text: str) -> Optional[str]:
    """Convenience: returns SAFE_FALLBACK_REPLY if unsafe, else None."""
    r = moderate_user_input(text)
    return SAFE_FALLBACK_REPLY if r["action"] == "block" else None


async def log_moderation_event(db, *, user_id: Optional[str], route: str, source: str, result: dict, action_taken: str) -> None:
    """Log a moderation event. Stores a content hash + 60-char snippet, NOT the full text."""
    if result.get("action") == "allow" and result.get("severity") != "medium":
        # Don't log clean inputs — only flagged ones.
        return
    try:
        await db.safety_moderation_events.insert_one({
            "event_id": uuid.uuid4().hex,
            "user_id": user_id,
            "route": route,
            "source": source,  # e.g. "user_input" | "ai_output"
            "input_hash": result.get("input_hash", ""),
            "snippet": result.get("snippet", "")[:60],
            "category": result.get("category"),
            "severity": result.get("severity"),
            "reason": result.get("reason", ""),
            "action_taken": action_taken,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        logger.exception("safety_moderation_events insert failed")


def safe_chat_response_fallback(reason: str = "") -> str:
    return SAFE_FALLBACK_REPLY


def rewrite_to_safe_text(text: str) -> str:
    """
    Last-resort masking when output_moderate says 'rewrite'.
    Replace medium-severity profanity tokens with neutral fillers, keep meaning.
    """
    if not text:
        return text
    out = text
    for p in _PATTERNS["profanity"]:
        out = p.sub("[—]", out)
    return out
