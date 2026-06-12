"""
Mood-Based Chat — Emotional intelligence layer for clone chats.

Field naming (DO NOT collide with /explore metadata.mood for share cards):
- emotion_state  : per-message emotion analysis on visitor messages
- chat_mood_state: rolling smoothed state on conversation document
- mood_ui        : transient UI config returned in chat API response
"""
import os
import re
import json
import logging
from typing import Optional, List
from pydantic import BaseModel, Field

from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
MOOD_ENABLED = os.environ.get("MOOD_CHAT_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
MOOD_MODEL = ("anthropic", "claude-sonnet-4-5-20250929")
MOOD_VERSION = "mood-v1"

CONFIDENCE_THRESHOLD = 0.65  # UI changes only above this
SESSION_DOMINANT_THRESHOLD = 0.55  # session-level dominant tone gate

DOMINANT_TONES = {"neutral", "stressed", "happy", "frustrated", "sad", "excited", "playful", "calm", "angry", "confused"}


# ============ MODELS ============
class SafetyFlags(BaseModel):
    self_harm: bool = False
    panic: bool = False
    abuse: bool = False


class EmotionScores(BaseModel):
    stress: float = Field(default=0.0, ge=0.0, le=1.0)
    happiness: float = Field(default=0.0, ge=0.0, le=1.0)
    frustration: float = Field(default=0.0, ge=0.0, le=1.0)
    sadness: float = Field(default=0.0, ge=0.0, le=1.0)
    excitement: float = Field(default=0.0, ge=0.0, le=1.0)
    playfulness: float = Field(default=0.0, ge=0.0, le=1.0)
    calmness: float = Field(default=0.0, ge=0.0, le=1.0)


class EmotionState(BaseModel):
    analyzed: bool = True
    dominant_tone: str = "neutral"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    scores: EmotionScores = Field(default_factory=EmotionScores)
    safety_flags: SafetyFlags = Field(default_factory=SafetyFlags)
    analyzer_version: str = MOOD_VERSION


class MoodUIConfig(BaseModel):
    enabled: bool = True
    dominant_state: str = "neutral"
    theme: str = "default"
    confidence: float = 0.0
    animation_level: str = "normal"
    accent_style: str = "default"
    show_mood_pill: bool = False
    microcopy: Optional[str] = None


# ============ MAPS ============
SCORE_TO_TONE = {
    "stress": "stressed", "happiness": "happy", "frustration": "frustrated",
    "sadness": "sad", "excitement": "excited", "playfulness": "playful", "calmness": "calm",
}

THEME_MAP = {
    "stressed": "calm", "frustrated": "focused", "sad": "soft",
    "happy": "bright", "excited": "bright", "playful": "playful", "calm": "calm",
}

UI_CONFIG_MAP = {
    "stressed":   {"theme": "calm",    "animation_level": "low",    "accent_style": "soft",    "microcopy": "Taking it gently."},
    "frustrated": {"theme": "focused", "animation_level": "low",    "accent_style": "minimal", "microcopy": "Keeping it clear."},
    "sad":        {"theme": "soft",    "animation_level": "low",    "accent_style": "warm",    "microcopy": "Here with you."},
    "happy":      {"theme": "bright",  "animation_level": "normal", "accent_style": "warm",    "microcopy": "Good energy."},
    "excited":    {"theme": "bright",  "animation_level": "normal", "accent_style": "vivid",   "microcopy": "Big energy."},
    "playful":    {"theme": "playful", "animation_level": "normal", "accent_style": "fun",     "microcopy": "Matching the vibe."},
    "calm":       {"theme": "calm",    "animation_level": "normal", "accent_style": "soft",    "microcopy": "Easy flow."},
}

PROMPT_INSTRUCTIONS = {
    "stressed":   "The visitor appears stressed. Keep your personality intact, but respond with calmness, validation, and practical clarity. Avoid overwhelming them with options or sharp humor.",
    "frustrated": "The visitor appears frustrated. Be concise, clear, and useful. Avoid defensiveness, teasing, or unnecessary flourish.",
    "sad":        "The visitor appears sad. Be warm, gentle, and validating. Do not diagnose or overpromise. Soften any savage edges of the clone's personality.",
    "happy":      "The visitor appears happy. Match the positive energy while keeping your voice.",
    "excited":    "The visitor appears excited. Mirror enthusiasm and momentum.",
    "playful":    "The visitor appears playful. Witty and casual is welcome here.",
    "calm":       "The visitor appears calm. Keep the response balanced and natural.",
    "angry":      "The visitor appears angry. Stay grounded, do not escalate. Be direct, useful, and de-escalating.",
}

SAFETY_PROMPT = (
    "SAFETY OVERRIDE: The visitor may be in distress. Respond with warmth and care. "
    "Gently encourage them to reach out to a trusted person or local emergency services. "
    "Do not joke, tease, or use savage tone. Do not diagnose. Stay supportive and brief."
)


# ============ ANALYZER ============
async def analyze_emotion(message: str, recent_messages: Optional[List[str]] = None) -> EmotionState:
    """Analyze emotional cues. Returns neutral default on empty/short input."""
    if not message or not message.strip() or len(message.strip()) < 3:
        return EmotionState(scores=EmotionScores(), confidence=0.0, dominant_tone="neutral")

    if EMERGENT_LLM_KEY:
        try:
            return await _analyze_with_llm(message, recent_messages or [])
        except Exception as e:
            logger.warning("LLM mood analysis failed, using fallback: %s", e)
    return _fallback_analyze(message)


async def _analyze_with_llm(message: str, recent_messages: List[str]) -> EmotionState:
    sys = (
        "You are an emotion classifier. Analyze the latest user message for emotional cues. "
        "Return ONLY valid JSON, no markdown fences, no explanation.\n"
        'Schema: {"stress":0-1,"happiness":0-1,"frustration":0-1,"sadness":0-1,"excitement":0-1,'
        '"playfulness":0-1,"calmness":0-1,'
        '"dominant_tone":"neutral|stressed|happy|frustrated|sad|excited|playful|calm|angry|confused",'
        '"confidence":0-1,"safety_flags":{"self_harm":bool,"panic":bool,"abuse":bool}}\n'
        "Rules: Infer ONLY from text. Do not diagnose. Use floats 0–1. "
        "If the message is too short or ambiguous, return neutral with confidence < 0.4."
    )

    context = ""
    if recent_messages:
        context = "Recent visitor messages (oldest→newest):\n" + "\n".join(f"- {m}" for m in recent_messages[-3:]) + "\n\n"
    user_text = f"{context}Latest message:\n{message}\n\nReturn JSON only."

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"mood_{abs(hash(message)) % (10 ** 12)}",
        system_message=sys,
    ).with_model(MOOD_MODEL[0], MOOD_MODEL[1]).with_max_tokens(300)

    raw = await chat.send_message(UserMessage(text=user_text))
    # Provider-metered cost ingestion (best-effort)
    try:
        from provider_cost_recorder import record_llm_call
        await record_llm_call(
            user_id=None, request_id=None,
            feature="chat", surface="mood_chat",
            provider=MOOD_MODEL[0], model=MOOD_MODEL[1],
            input_text=(sys + "\n" + user_text),
            output_text=raw or "",
        )
    except Exception:
        pass
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    # If model wrapped in extra text, try to extract first {...}
    if not raw.startswith("{"):
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            raw = m.group(0)
    data = json.loads(raw)

    tone = (data.get("dominant_tone") or "neutral").lower()
    if tone not in DOMINANT_TONES:
        tone = "neutral"

    return EmotionState(
        analyzed=True,
        dominant_tone=tone,
        confidence=_clip01(data.get("confidence", 0.0)),
        scores=EmotionScores(
            stress=_clip01(data.get("stress", 0.0)),
            happiness=_clip01(data.get("happiness", 0.0)),
            frustration=_clip01(data.get("frustration", 0.0)),
            sadness=_clip01(data.get("sadness", 0.0)),
            excitement=_clip01(data.get("excitement", 0.0)),
            playfulness=_clip01(data.get("playfulness", 0.0)),
            calmness=_clip01(data.get("calmness", 0.0)),
        ),
        safety_flags=SafetyFlags(
            self_harm=bool(data.get("safety_flags", {}).get("self_harm", False)),
            panic=bool(data.get("safety_flags", {}).get("panic", False)),
            abuse=bool(data.get("safety_flags", {}).get("abuse", False)),
        ),
    )


def _clip01(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except Exception:
        return 0.0


def _fallback_analyze(message: str) -> EmotionState:
    text = message.lower()
    stress_terms = ["stressed", "overwhelmed", "anxious", "panic", "too much", "burned out", "burnt out", "freaking out", "can't cope", "cant cope"]
    happy_terms = ["happy", "great", "amazing", "love this", "awesome", "wonderful", "yay", "fantastic", "thrilled"]
    frustrated_terms = ["annoyed", "angry", "frustrated", "hate", "not working", "wtf", "sucks", "stupid", "ridiculous"]
    sad_terms = ["sad", "lonely", "hurt", "cry", "crying", "hopeless", "depressed", "lost", "miserable"]
    excited_terms = ["excited", "can't wait", "cant wait", "pumped", "stoked", "let's go", "lets go"]
    playful_terms = ["lol", "lmao", "haha", "funny", "joke", "lololol", "rofl", "kek", "hehe"]

    def s(terms): return min(1.0, sum(1 for t in terms if t in text) * 0.4)

    scores = EmotionScores(
        stress=s(stress_terms),
        happiness=s(happy_terms),
        frustration=s(frustrated_terms),
        sadness=s(sad_terms),
        excitement=max(s(excited_terms), 0.5 if re.search(r"!{2,}", message) else 0.0),
        playfulness=s(playful_terms),
        calmness=0.0,
    )
    sd = scores.model_dump()
    dom_key = max(sd, key=sd.get)
    conf = sd[dom_key]
    if conf < 0.35:
        dom_tone, conf = "neutral", 0.3
    else:
        dom_tone = SCORE_TO_TONE.get(dom_key, "neutral")

    safety = SafetyFlags(
        self_harm=any(t in text for t in ["kill myself", "end it all", "want to die", "suicide"]),
        panic=any(t in text for t in ["panic", "panicking"]),
        abuse=any(t in text for t in ["abuse", "abused", "threatened"]),
    )
    return EmotionState(dominant_tone=dom_tone, confidence=conf, scores=scores, safety_flags=safety)


# ============ STATE SMOOTHING ============
SMOOTH_PREV = 0.7
SMOOTH_CURR = 0.3


def update_session_mood_state(previous: Optional[dict], current: EmotionState) -> dict:
    if not previous:
        return {
            "enabled": True,
            "dominant_tone": current.dominant_tone,
            "confidence": float(current.confidence),
            "scores": current.scores.model_dump(),
            "theme": THEME_MAP.get(current.dominant_tone, "default"),
            "message_count_analyzed": 1,
        }

    prev_scores = previous.get("scores", {}) or {}
    curr_scores = current.scores.model_dump()
    merged = {}
    for k, cv in curr_scores.items():
        pv = float(prev_scores.get(k, 0.0))
        merged[k] = pv * SMOOTH_PREV + cv * SMOOTH_CURR

    dom_key = max(merged, key=merged.get)
    dom_tone = SCORE_TO_TONE.get(dom_key, "neutral")
    confidence = max(float(current.confidence), float(previous.get("confidence", 0.0)) * 0.7)
    confidence = min(confidence, 1.0)

    return {
        "enabled": True,
        "dominant_tone": dom_tone if confidence >= SESSION_DOMINANT_THRESHOLD else "neutral",
        "confidence": confidence,
        "scores": merged,
        "theme": THEME_MAP.get(dom_tone, "default"),
        "message_count_analyzed": int(previous.get("message_count_analyzed", 0)) + 1,
    }


# ============ PROMPT ADAPTER ============
def build_mood_instruction(session_mood: Optional[dict], current_safety: Optional[SafetyFlags] = None) -> str:
    if current_safety and current_safety.self_harm:
        return SAFETY_PROMPT
    if not session_mood:
        return ""
    tone = session_mood.get("dominant_tone", "neutral")
    confidence = float(session_mood.get("confidence", 0.0))
    if confidence < CONFIDENCE_THRESHOLD or tone == "neutral":
        return ""
    return PROMPT_INSTRUCTIONS.get(tone, "")


# ============ UI CONFIG ============
def build_mood_ui(
    session_mood: Optional[dict],
    clone_settings: Optional[dict] = None,
    current_safety: Optional[SafetyFlags] = None,
) -> MoodUIConfig:
    clone_settings = clone_settings or {}
    if not MOOD_ENABLED or clone_settings.get("enabled") is False:
        return MoodUIConfig(enabled=False)

    # Safety override → soft theme
    if current_safety and current_safety.self_harm:
        return MoodUIConfig(
            enabled=True, dominant_state="sad", theme="soft", confidence=1.0,
            animation_level="low", accent_style="warm",
            show_mood_pill=clone_settings.get("show_mood_pill", True),
            microcopy="Here with you.",
        )

    if not session_mood:
        return MoodUIConfig(enabled=True)

    tone = session_mood.get("dominant_tone", "neutral")
    confidence = float(session_mood.get("confidence", 0.0))

    if confidence < CONFIDENCE_THRESHOLD:
        return MoodUIConfig(
            enabled=True, dominant_state="neutral", theme="default",
            confidence=confidence, show_mood_pill=False,
        )

    cfg = UI_CONFIG_MAP.get(tone)
    if not cfg:
        return MoodUIConfig(enabled=True, dominant_state=tone, theme="default", confidence=confidence)

    return MoodUIConfig(
        enabled=True,
        dominant_state=tone,
        theme=cfg["theme"],
        confidence=confidence,
        animation_level=cfg["animation_level"],
        accent_style=cfg["accent_style"],
        show_mood_pill=clone_settings.get("show_mood_pill", True),
        microcopy=cfg["microcopy"],
    )
