"""
Voice-First AI Messaging.

Flow:
  user speaks -> POST /api/voice/transcribe (multipart) -> Whisper -> raw + cleaned transcript
  user picks tone -> POST /api/voice/generate -> Claude Sonnet 4.5 -> polished message
  user picks "all" -> POST /api/voice/generate-all -> 6 messages in parallel

Privacy: audio is transcribed in-memory and never persisted. Only the transcript text is stored.

Funnel separation: every voice_usage_event is tagged metadata.experience_variant="voice_v1".
"""
import asyncio
import io
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from emergentintegrations.llm.chat import LlmChat, UserMessage
from emergentintegrations.llm.openai import OpenAISpeechToText

from db import db
from auth import get_current_user
from models import now_iso

load_dotenv()

router = APIRouter(prefix="/api/voice", tags=["voice"])
logger = logging.getLogger(__name__)

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
EXPERIENCE_VARIANT = "voice_v1"
FREE_DAILY_LIMIT = 20

ALLOWED_AUDIO_TYPES = {"audio/webm", "audio/mp3", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/ogg"}
ALLOWED_AUDIO_EXTS = {".webm", ".mp3", ".wav", ".m4a", ".mp4", ".mpeg", ".mpga", ".ogg"}
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB

TONES = ("concise", "professional", "friendly", "apology", "dating", "negotiation")
TONE_DESCRIPTIONS = {
    "concise": "stripped down to the essential message in 1-2 short sentences",
    "professional": "polished, clear, and respectful — suitable for work / clients",
    "friendly": "warm, casual, human — like texting a friend",
    "apology": "sincere, owns it, no excuses, brief",
    "dating": "natural chemistry, never coercive, light flirty energy if appropriate",
    "negotiation": "firm, calm, value-anchored — clear ask, no aggression",
}


# -------------- Schemas --------------
class TranscribeResponse(BaseModel):
    session_id: str
    raw_transcript: str
    cleaned_transcript: str
    duration_seconds: Optional[float] = None
    detected_language: Optional[str] = None


class GenerateRequest(BaseModel):
    session_id: str
    tone: Literal["concise", "professional", "friendly", "apology", "dating", "negotiation"]


class GenerateResponse(BaseModel):
    message_id: str
    tone: str
    generated_message: str


class GenerateAllRequest(BaseModel):
    session_id: str


class CopyEventRequest(BaseModel):
    message_id: str


# -------------- Helpers --------------
async def _emit(user_id: Optional[str], event_name: str, props: Optional[dict] = None):
    await db.voice_usage_events.insert_one({
        "event_id": uuid.uuid4().hex,
        "user_id": user_id,
        "event_name": event_name,
        "metadata": {**(props or {}), "experience_variant": EXPERIENCE_VARIANT},
        "created_at": now_iso(),
    })


async def _check_and_consume(user_id: str, on_success: bool = True) -> dict:
    """Daily usage gate. on_success=False just checks, doesn't consume."""
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    is_pro = user.get("subscription_status") == "pro"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last_day = user.get("voice_daily_day")
    count = int(user.get("voice_daily_count", 0)) if last_day == today else 0

    if not is_pro and count >= FREE_DAILY_LIMIT:
        await _emit(user_id, "voice_usage_limit_hit", {"daily_count": count})
        raise HTTPException(
            status_code=402,
            detail={"code": "usage_limit_reached", "limit": FREE_DAILY_LIMIT, "remaining": 0},
        )

    if on_success and not is_pro:
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "voice_daily_count": count + 1,
                "voice_daily_day": today,
                "updated_at": now_iso(),
            }},
        )

    return {"is_pro": is_pro, "remaining": -1 if is_pro else max(0, FREE_DAILY_LIMIT - (count + (1 if on_success else 0)))}


def _strip_obj_id(doc):
    if not doc:
        return doc
    return {k: v for k, v in doc.items() if k != "_id"}


# -------------- Whisper --------------
async def _transcribe_audio(audio_bytes: bytes, filename: str) -> dict:
    """Returns {'text': str, 'duration': float|None, 'language': str|None}."""
    if not EMERGENT_LLM_KEY:
        raise HTTPException(status_code=500, detail="LLM not configured")

    stt = OpenAISpeechToText(api_key=EMERGENT_LLM_KEY)
    bio = io.BytesIO(audio_bytes)
    bio.name = filename or "audio.webm"
    try:
        resp = await stt.transcribe(
            file=bio,
            model="whisper-1",
            response_format="verbose_json",
            language="en",
            temperature=0.0,
        )
    except Exception as e:
        logger.exception("Whisper failed")
        raise HTTPException(status_code=502, detail=f"Transcription failed: {type(e).__name__}")

    text = (getattr(resp, "text", None) or "").strip()
    duration = getattr(resp, "duration", None)
    language = getattr(resp, "language", None)
    return {"text": text, "duration": duration, "language": language}


# -------------- Claude cleaning + generation --------------
def _claude_chat(system: str, session_id: Optional[str] = None) -> LlmChat:
    return LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id or f"voice_{uuid.uuid4().hex[:10]}",
        system_message=system,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")


async def _clean_transcript(raw: str) -> str:
    if not raw.strip():
        return ""
    system = """You are cleaning a raw speech transcript so it can be used to generate a polished written message.

Rules:
- Remove filler words: um, uh, like, you know, sort of, kind of, I mean, basically.
- Fix obvious grammar.
- Keep the original meaning EXACTLY. Do not add facts the speaker did not say.
- Keep it short.
- Return only the cleaned text. No quotes, no preamble, no markdown."""
    chat = _claude_chat(system)
    try:
        cleaned = await chat.send_message(UserMessage(text=f"Raw transcript:\n{raw}"))
    except Exception as e:
        logger.warning("Cleaning failed, falling back to raw: %s", e)
        return raw.strip()
    return (cleaned or "").strip().strip('"').strip("'") or raw.strip()


async def _generate_message(cleaned: str, tone: str) -> str:
    desc = TONE_DESCRIPTIONS.get(tone, "")
    system = f"""You are a smart-message assistant. Convert the user's cleaned speech into a polished message they can send.

TONE: {tone} — {desc}

RULES:
- Output ONLY the final message text. No quotes, no labels, no markdown, no preamble.
- Sound human and natural. Avoid robotic / corporate clichés.
- Keep it concise: 1-3 short sentences for most tones; up to 4 for negotiation/professional if needed.
- Do NOT invent any fact the speaker did not say.
- No emojis unless the tone is friendly/dating AND it adds warmth (max 1).
- Refuse and rewrite as a healthy alternative if the input asks for harassment, manipulation, coercion, or sexual pressure."""
    chat = _claude_chat(system)
    try:
        out = await chat.send_message(UserMessage(text=f"Cleaned speech:\n{cleaned}"))
    except Exception as e:
        logger.exception("Message generation failed")
        raise HTTPException(status_code=502, detail=f"Generation failed: {type(e).__name__}")
    return (out or "").strip().strip('"').strip("'") or "(no message generated)"


# -------------- Endpoints --------------
@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(audio_file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    await _emit(user_id, "voice_audio_uploaded", {"content_type": audio_file.content_type, "filename": audio_file.filename})

    # Validate
    ext = os.path.splitext(audio_file.filename or "")[1].lower()
    ct = (audio_file.content_type or "").lower().split(";")[0].strip()
    # Accept either matching content-type or matching extension
    if ct and ct not in ALLOWED_AUDIO_TYPES and ext not in ALLOWED_AUDIO_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported audio format: {ct or ext}")

    audio_bytes = await audio_file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file too large (max 10 MB)")

    # Usage gate (consume only on successful transcription further down)
    await _check_and_consume(user_id, on_success=False)

    result = await _transcribe_audio(audio_bytes, audio_file.filename or "audio.webm")
    raw = result["text"]
    if not raw or len(raw) < 5:
        await _emit(user_id, "voice_transcription_failed", {"reason": "empty_or_too_short"})
        raise HTTPException(status_code=422, detail="Could not hear enough speech. Please try again.")
    if len(raw) > 2000:
        raw = raw[:2000]

    cleaned = await _clean_transcript(raw)
    session_id = f"vs_{uuid.uuid4().hex[:14]}"
    await db.voice_sessions.insert_one({
        "session_id": session_id,
        "user_id": user_id,
        "raw_transcript": raw,
        "cleaned_transcript": cleaned,
        "duration_seconds": result["duration"],
        "detected_language": result["language"],
        "status": "transcribed",
        "experience_variant": EXPERIENCE_VARIANT,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    })

    # Consume one usage credit on success
    await _check_and_consume(user_id, on_success=True)
    await _emit(user_id, "voice_transcription_success", {"session_id": session_id, "chars": len(raw)})

    return TranscribeResponse(
        session_id=session_id,
        raw_transcript=raw,
        cleaned_transcript=cleaned,
        duration_seconds=result["duration"],
        detected_language=result["language"],
    )


async def _load_session(session_id: str, user_id: str) -> dict:
    sess = await db.voice_sessions.find_one(
        {"session_id": session_id, "user_id": user_id},
        {"_id": 0},
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


@router.post("/generate", response_model=GenerateResponse)
async def generate(payload: GenerateRequest, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    sess = await _load_session(payload.session_id, user_id)
    cleaned = sess.get("cleaned_transcript") or sess.get("raw_transcript") or ""
    if not cleaned:
        raise HTTPException(status_code=400, detail="No transcript on this session")

    msg = await _generate_message(cleaned, payload.tone)
    message_id = f"vm_{uuid.uuid4().hex[:14]}"
    await db.generated_messages.insert_one({
        "message_id": message_id,
        "user_id": user_id,
        "voice_session_id": payload.session_id,
        "input_transcript": cleaned,
        "tone": payload.tone,
        "generated_message": msg,
        "copy_count": 0,
        "regenerate_count": 0,
        "experience_variant": EXPERIENCE_VARIANT,
        "created_at": now_iso(),
    })
    await _emit(user_id, "voice_message_generated", {"session_id": payload.session_id, "tone": payload.tone, "message_id": message_id})
    return GenerateResponse(message_id=message_id, tone=payload.tone, generated_message=msg)


@router.post("/generate-all")
async def generate_all(payload: GenerateAllRequest, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    sess = await _load_session(payload.session_id, user_id)
    cleaned = sess.get("cleaned_transcript") or sess.get("raw_transcript") or ""
    if not cleaned:
        raise HTTPException(status_code=400, detail="No transcript on this session")

    results = await asyncio.gather(
        *[_generate_message(cleaned, t) for t in TONES],
        return_exceptions=True,
    )
    out = []
    for tone, res in zip(TONES, results):
        if isinstance(res, Exception):
            logger.warning("generate-all tone=%s failed: %s", tone, res)
            continue
        message_id = f"vm_{uuid.uuid4().hex[:14]}"
        await db.generated_messages.insert_one({
            "message_id": message_id,
            "user_id": user_id,
            "voice_session_id": payload.session_id,
            "input_transcript": cleaned,
            "tone": tone,
            "generated_message": res,
            "copy_count": 0,
            "regenerate_count": 0,
            "experience_variant": EXPERIENCE_VARIANT,
            "created_at": now_iso(),
        })
        out.append({"message_id": message_id, "tone": tone, "message": res})
    await _emit(user_id, "voice_message_generated", {"session_id": payload.session_id, "tone": "all", "count": len(out)})
    return {"messages": out}


@router.post("/copy-event")
async def copy_event(payload: CopyEventRequest, user: dict = Depends(get_current_user)):
    res = await db.generated_messages.update_one(
        {"message_id": payload.message_id, "user_id": user["user_id"]},
        {"$inc": {"copy_count": 1}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Message not found")
    await _emit(user["user_id"], "voice_message_copied", {"message_id": payload.message_id})
    return {"ok": True}


@router.get("/history")
async def history(user: dict = Depends(get_current_user), limit: int = 50):
    cursor = (
        db.voice_sessions.find({"user_id": user["user_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .limit(min(max(limit, 1), 100))
    )
    sessions = await cursor.to_list(100)
    # Attach last 6 messages per session
    if sessions:
        sids = [s["session_id"] for s in sessions]
        msgs_cursor = db.generated_messages.find(
            {"user_id": user["user_id"], "voice_session_id": {"$in": sids}},
            {"_id": 0},
        ).sort("created_at", -1)
        all_msgs = await msgs_cursor.to_list(500)
        by_sid: dict = {}
        for m in all_msgs:
            by_sid.setdefault(m["voice_session_id"], []).append(m)
        for s in sessions:
            s["messages"] = by_sid.get(s["session_id"], [])[:6]
    return {"sessions": sessions}


@router.get("/usage")
async def usage(user: dict = Depends(get_current_user)):
    info = await _check_and_consume(user["user_id"], on_success=False)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    user = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})
    used = int(user.get("voice_daily_count", 0)) if user.get("voice_daily_day") == today else 0
    return {
        "is_pro": info["is_pro"],
        "daily_limit": None if info["is_pro"] else FREE_DAILY_LIMIT,
        "daily_used": used,
        "daily_remaining": None if info["is_pro"] else max(0, FREE_DAILY_LIMIT - used),
    }


@router.post("/track")
async def track(event_name: str = Form(...), user: dict = Depends(get_current_user)):
    allowed = {
        "voice_page_viewed", "voice_record_started", "voice_record_stopped",
        "voice_history_opened", "voice_message_regenerated",
    }
    if event_name not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported event")
    await _emit(user["user_id"], event_name)
    return {"ok": True}
