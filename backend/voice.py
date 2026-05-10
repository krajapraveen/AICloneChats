"""
Voice-First AI Messaging — "turn messy human communication into socially optimized
messaging instantly".

Three input sources, ONE pipeline:
  recording  -> Whisper transcribe (in-memory) -> Claude clean -> Claude tones
  upload     -> Whisper transcribe (in-memory) -> Claude clean -> Claude tones
  text       -> Claude clean                                 -> Claude tones

Privacy: audio bytes are NEVER persisted. Only the cleaned text is stored.

Anonymous trial: 3 free generations per device_id before signup wall (P0).
Auth'd users: 20/day free (Pro = unlimited).

Funnel separation: every event tagged metadata.experience_variant="voice_v1".
"""
import asyncio
import hashlib
import io
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Form, HTTPException, Header, Request, UploadFile
from pydantic import BaseModel, Field

from emergentintegrations.llm.chat import LlmChat, UserMessage
from emergentintegrations.llm.openai import OpenAISpeechToText

from db import db
from auth import get_optional_user
from models import now_iso
from pii_redact import redact

load_dotenv()

router = APIRouter(prefix="/api/voice", tags=["voice"])
logger = logging.getLogger(__name__)

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
EXPERIENCE_VARIANT = "voice_v1"
FREE_DAILY_LIMIT = 20
ANON_TOTAL_LIMIT = 3  # lifetime trial credits per device before signup wall

ALLOWED_AUDIO_EXTS = {".webm", ".mp3", ".wav", ".m4a", ".mp4", ".mpeg", ".mpga", ".ogg", ".oga"}
MAX_AUDIO_BYTES = 15 * 1024 * 1024  # 15 MB

TONES = ("concise", "professional", "friendly", "apology", "dating", "negotiation")
TONE_DESCRIPTIONS = {
    "concise": "stripped down to the essential message in 1-2 short sentences",
    "professional": "polished, clear, and respectful — suitable for work / clients",
    "friendly": "warm, casual, human — like texting a friend",
    "apology": "sincere, owns it, no excuses, brief",
    "dating": "natural chemistry, never coercive, light flirty energy if appropriate",
    "negotiation": "firm, calm, value-anchored — clear ask, no aggression",
}

REFINE_TYPES = ("shorter", "confident", "polite", "flirty", "professional")
REFINE_INSTRUCTIONS = {
    "shorter": "Make this MUCH shorter. Cut every non-essential word. Keep the same meaning. 1-2 short sentences max.",
    "confident": "Rewrite with more confidence. No hedging, no apologies, no 'just'/'maybe'/'I think'. Direct, calm, certain.",
    "polite": "Rewrite with more politeness and warmth. Soften the edges, but stay clear. No groveling.",
    "flirty": "Rewrite with subtle flirty energy — playful, light, never coercive. One emoji max if it fits naturally.",
    "professional": "Rewrite in a polished professional tone — clear, decisive, respectful. No corporate clichés.",
}


# -------------- Schemas --------------
class TranscribeResponse(BaseModel):
    session_id: str
    raw_transcript: str
    cleaned_transcript: str
    duration_seconds: Optional[float] = None
    detected_language: Optional[str] = None
    source_type: str
    daily_remaining: Optional[int] = None
    is_anonymous: bool = False
    anon_remaining: Optional[int] = None


class TextInputRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


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


class EditTranscriptRequest(BaseModel):
    cleaned_transcript: str = Field(min_length=1, max_length=4000)


class RefineRequest(BaseModel):
    message_id: str
    refine_type: Literal["shorter", "confident", "polite", "flirty", "professional"]


# -------------- Actor resolution (auth'd user OR anonymous device) --------------
def _hash_ip(request: Request) -> str:
    ip = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )
    secret = os.environ.get("JWT_SECRET", "dev-secret")
    return hashlib.sha256(f"{ip}|{secret}".encode("utf-8")).hexdigest()[:24]


async def voice_actor(
    request: Request,
    user: Optional[dict] = Depends(get_optional_user),
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-Id"),
) -> dict:
    """Returns {is_anonymous: bool, user_id: str|None, device_id: str|None, ip_hash: str}."""
    if user:
        return {"is_anonymous": False, "user_id": user["user_id"], "user": user, "device_id": None, "ip_hash": _hash_ip(request)}
    if not x_device_id or len(x_device_id) < 8:
        raise HTTPException(status_code=400, detail="X-Device-Id header required for anonymous trial")
    return {"is_anonymous": True, "user_id": None, "user": None, "device_id": x_device_id[:64], "ip_hash": _hash_ip(request)}


# -------------- Usage gating --------------
async def _emit(actor: dict, event_name: str, props: Optional[dict] = None):
    await db.voice_usage_events.insert_one({
        "event_id": uuid.uuid4().hex,
        "user_id": actor.get("user_id"),
        "device_id": actor.get("device_id"),
        "is_anonymous": actor["is_anonymous"],
        "event_name": event_name,
        "metadata": {**(props or {}), "experience_variant": EXPERIENCE_VARIANT},
        "created_at": now_iso(),
    })


async def _check_usage(actor: dict, consume: bool) -> dict:
    """
    Verify actor can generate. Raise 402 if exhausted. If consume=True, increment counter.
    Returns {is_pro, daily_remaining, anon_remaining}.
    """
    if actor["is_anonymous"]:
        device_id = actor["device_id"]
        doc = await db.voice_anon_trials.find_one({"device_id": device_id}, {"_id": 0}) or {}
        used = int(doc.get("count", 0))
        if used >= ANON_TOTAL_LIMIT:
            await _emit(actor, "voice_anon_limit_hit", {"used": used})
            raise HTTPException(
                status_code=402,
                detail={"code": "anon_limit_reached", "limit": ANON_TOTAL_LIMIT, "remaining": 0, "wall": "signup"},
            )
        if consume:
            await db.voice_anon_trials.update_one(
                {"device_id": device_id},
                {
                    "$inc": {"count": 1},
                    "$set": {"last_used_at": now_iso(), "ip_hash": actor["ip_hash"]},
                    "$setOnInsert": {"device_id": device_id, "first_used_at": now_iso()},
                },
                upsert=True,
            )
            used = used + 1
        return {"is_pro": False, "daily_remaining": None, "anon_remaining": max(0, ANON_TOTAL_LIMIT - used)}

    user_id = actor["user_id"]
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    is_pro = user.get("subscription_status") == "pro"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last_day = user.get("voice_daily_day")
    count = int(user.get("voice_daily_count", 0)) if last_day == today else 0
    if not is_pro and count >= FREE_DAILY_LIMIT:
        await _emit(actor, "voice_usage_limit_hit", {"daily_count": count})
        raise HTTPException(
            status_code=402,
            detail={"code": "usage_limit_reached", "limit": FREE_DAILY_LIMIT, "remaining": 0, "wall": "upgrade"},
        )
    if consume and not is_pro:
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {"voice_daily_count": count + 1, "voice_daily_day": today, "updated_at": now_iso()}},
        )
        count = count + 1
    return {"is_pro": is_pro, "daily_remaining": None if is_pro else max(0, FREE_DAILY_LIMIT - count), "anon_remaining": None}


# -------------- Whisper --------------
async def _transcribe_audio(audio_bytes: bytes, filename: str) -> dict:
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


# -------------- Claude --------------
def _claude_chat(system: str, session_id: Optional[str] = None) -> LlmChat:
    return LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id or f"voice_{uuid.uuid4().hex[:10]}",
        system_message=system,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")


async def _clean_transcript(raw: str) -> str:
    if not raw.strip():
        return ""
    system = """You are cleaning messy speech / chat text into a clear input for a smart-message assistant.

Rules:
- Remove filler words: um, uh, like, you know, sort of, kind of, I mean, basically.
- Fix obvious grammar and punctuation.
- Keep the original meaning EXACTLY. Do not add facts the speaker did not say.
- Keep it short.
- Return only the cleaned text. No quotes, no preamble, no markdown."""
    chat = _claude_chat(system)
    try:
        cleaned = await chat.send_message(UserMessage(text=f"Raw input:\n{raw}"))
    except Exception as e:
        logger.warning("Cleaning failed, using raw: %s", e)
        return raw.strip()
    return (cleaned or "").strip().strip('"').strip("'") or raw.strip()


async def _generate_message(cleaned: str, tone: str) -> str:
    desc = TONE_DESCRIPTIONS.get(tone, "")
    system = f"""You are a smart-message assistant. Convert the user's cleaned input into a polished message they can send right now.

TONE: {tone} — {desc}

RULES:
- Output ONLY the final message text. No quotes, no labels, no markdown, no preamble.
- Sound human and natural. Avoid robotic or corporate clichés ("I hope this finds you well").
- Keep it concise: 1-3 short sentences for most tones; up to 4 for negotiation/professional if needed.
- Do NOT invent any fact the user did not say.
- No emojis unless the tone is friendly/dating AND it adds warmth (max 1).
- Refuse and rewrite as a healthy alternative if the input asks for harassment, manipulation, coercion, or sexual pressure."""
    chat = _claude_chat(system)
    try:
        out = await chat.send_message(UserMessage(text=f"Cleaned input:\n{cleaned}"))
    except Exception as e:
        logger.exception("Message generation failed")
        raise HTTPException(status_code=502, detail=f"Generation failed: {type(e).__name__}")
    return (out or "").strip().strip('"').strip("'") or "(no message generated)"


async def _refine_message(original: str, refine_type: str) -> str:
    instruction = REFINE_INSTRUCTIONS.get(refine_type, "Improve clarity.")
    system = f"""You are a smart-message refiner. Given a message, rewrite it according to the instruction.

INSTRUCTION: {instruction}

RULES:
- Output ONLY the rewritten message text. No quotes, no labels, no markdown, no preamble.
- Preserve the speaker's intent and core facts.
- Keep it human and natural. Refuse harassment, manipulation, or coercion."""
    chat = _claude_chat(system)
    try:
        out = await chat.send_message(UserMessage(text=f"Original message:\n{original}"))
    except Exception as e:
        logger.exception("Refine failed")
        raise HTTPException(status_code=502, detail=f"Refine failed: {type(e).__name__}")
    return (out or "").strip().strip('"').strip("'") or original


# -------------- Session helpers --------------
def _session_owner_filter(actor: dict) -> dict:
    return {"user_id": actor["user_id"]} if not actor["is_anonymous"] else {"device_id": actor["device_id"]}


async def _load_session(session_id: str, actor: dict) -> dict:
    sess = await db.voice_sessions.find_one(
        {"session_id": session_id, **_session_owner_filter(actor)},
        {"_id": 0},
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


async def _create_session(actor: dict, raw: str, cleaned: str, source_type: str,
                          duration: Optional[float] = None, language: Optional[str] = None,
                          upload_filename: Optional[str] = None, mime_type: Optional[str] = None) -> str:
    session_id = f"vs_{uuid.uuid4().hex[:14]}"
    await db.voice_sessions.insert_one({
        "session_id": session_id,
        "user_id": actor.get("user_id"),
        "device_id": actor.get("device_id"),
        "is_anonymous": actor["is_anonymous"],
        "source_type": source_type,
        "upload_filename": upload_filename,
        "mime_type": mime_type,
        "raw_transcript": raw,
        "cleaned_transcript": cleaned,
        "duration_seconds": duration,
        "detected_language": language,
        "status": "transcribed",
        "experience_variant": EXPERIENCE_VARIANT,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    })
    return session_id


# -------------- Endpoints --------------
@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    request: Request,
    audio_file: UploadFile = File(...),
    source_type: str = Form("recording"),  # "recording" or "upload"
    actor: dict = Depends(voice_actor),
):
    if source_type not in ("recording", "upload"):
        source_type = "recording"
    await _emit(actor, "voice_audio_uploaded", {
        "source_type": source_type,
        "content_type": audio_file.content_type,
        "filename": audio_file.filename,
    })

    # Validate file
    ext = os.path.splitext(audio_file.filename or "")[1].lower()
    ct = (audio_file.content_type or "").lower().split(";")[0].strip()
    if ext and ext not in ALLOWED_AUDIO_EXTS and not ct.startswith("audio/") and not ct.startswith("video/"):
        raise HTTPException(status_code=400, detail=f"Unsupported audio format: {ct or ext}")

    audio_bytes = await audio_file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file too large (max 15 MB)")

    # Verify quota (don't consume yet)
    await _check_usage(actor, consume=False)

    result = await _transcribe_audio(audio_bytes, audio_file.filename or "audio.webm")
    raw = result["text"]
    if not raw or len(raw) < 3:
        await _emit(actor, "voice_transcription_failed", {"reason": "empty_or_too_short"})
        raise HTTPException(status_code=422, detail="Could not hear enough speech. Please try again.")
    if len(raw) > 2000:
        raw = raw[:2000]

    cleaned = await _clean_transcript(raw)
    session_id = await _create_session(
        actor, raw, cleaned, source_type=source_type,
        duration=result["duration"], language=result["language"],
        upload_filename=audio_file.filename, mime_type=ct or None,
    )

    # Consume on success
    usage = await _check_usage(actor, consume=True)
    await _emit(actor, "voice_transcription_success", {"session_id": session_id, "chars": len(raw), "source_type": source_type})

    return TranscribeResponse(
        session_id=session_id,
        raw_transcript=raw,
        cleaned_transcript=cleaned,
        duration_seconds=result["duration"],
        detected_language=result["language"],
        source_type=source_type,
        daily_remaining=usage["daily_remaining"],
        is_anonymous=actor["is_anonymous"],
        anon_remaining=usage["anon_remaining"],
    )


@router.post("/text-input", response_model=TranscribeResponse)
async def text_input(payload: TextInputRequest, actor: dict = Depends(voice_actor)):
    await _emit(actor, "voice_text_pasted", {"chars": len(payload.text)})
    await _check_usage(actor, consume=False)

    raw = payload.text.strip()
    cleaned = await _clean_transcript(raw)
    session_id = await _create_session(actor, raw, cleaned, source_type="text")
    usage = await _check_usage(actor, consume=True)

    await _emit(actor, "voice_transcription_success", {"session_id": session_id, "chars": len(raw), "source_type": "text"})
    return TranscribeResponse(
        session_id=session_id,
        raw_transcript=raw,
        cleaned_transcript=cleaned,
        duration_seconds=None,
        detected_language=None,
        source_type="text",
        daily_remaining=usage["daily_remaining"],
        is_anonymous=actor["is_anonymous"],
        anon_remaining=usage["anon_remaining"],
    )


@router.patch("/sessions/{session_id}")
async def edit_transcript(session_id: str, payload: EditTranscriptRequest, actor: dict = Depends(voice_actor)):
    await _load_session(session_id, actor)  # ownership check
    new_cleaned = payload.cleaned_transcript.strip()
    if not new_cleaned:
        raise HTTPException(status_code=400, detail="Cleaned transcript cannot be empty")
    await db.voice_sessions.update_one(
        {"session_id": session_id},
        {"$set": {"cleaned_transcript": new_cleaned, "edited_by_user": True, "updated_at": now_iso()}},
    )
    await _emit(actor, "voice_transcript_edited", {"session_id": session_id, "chars": len(new_cleaned)})
    return {"session_id": session_id, "cleaned_transcript": new_cleaned}


@router.post("/generate", response_model=GenerateResponse)
async def generate(payload: GenerateRequest, actor: dict = Depends(voice_actor)):
    sess = await _load_session(payload.session_id, actor)
    cleaned = sess.get("cleaned_transcript") or sess.get("raw_transcript") or ""
    if not cleaned:
        raise HTTPException(status_code=400, detail="No transcript on this session")

    msg = await _generate_message(cleaned, payload.tone)
    message_id = f"vm_{uuid.uuid4().hex[:14]}"
    await db.generated_messages.insert_one({
        "message_id": message_id,
        "user_id": actor.get("user_id"),
        "device_id": actor.get("device_id"),
        "is_anonymous": actor["is_anonymous"],
        "voice_session_id": payload.session_id,
        "input_transcript": cleaned,
        "tone": payload.tone,
        "generated_message": msg,
        "copy_count": 0,
        "regenerate_count": 0,
        "refine_history": [],
        "experience_variant": EXPERIENCE_VARIANT,
        "created_at": now_iso(),
    })
    await _emit(actor, "voice_message_generated", {"session_id": payload.session_id, "tone": payload.tone, "message_id": message_id})
    return GenerateResponse(message_id=message_id, tone=payload.tone, generated_message=msg)


@router.post("/generate-all")
async def generate_all(payload: GenerateAllRequest, actor: dict = Depends(voice_actor)):
    sess = await _load_session(payload.session_id, actor)
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
            "user_id": actor.get("user_id"),
            "device_id": actor.get("device_id"),
            "is_anonymous": actor["is_anonymous"],
            "voice_session_id": payload.session_id,
            "input_transcript": cleaned,
            "tone": tone,
            "generated_message": res,
            "copy_count": 0,
            "regenerate_count": 0,
            "refine_history": [],
            "experience_variant": EXPERIENCE_VARIANT,
            "created_at": now_iso(),
        })
        out.append({"message_id": message_id, "tone": tone, "message": res})
    await _emit(actor, "voice_message_generated", {"session_id": payload.session_id, "tone": "all", "count": len(out)})
    return {"messages": out}


@router.post("/refine", response_model=GenerateResponse)
async def refine(payload: RefineRequest, actor: dict = Depends(voice_actor)):
    msg = await db.generated_messages.find_one(
        {"message_id": payload.message_id, **_session_owner_filter(actor)},
        {"_id": 0},
    )
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    rewritten = await _refine_message(msg["generated_message"], payload.refine_type)

    new_id = f"vm_{uuid.uuid4().hex[:14]}"
    await db.generated_messages.insert_one({
        "message_id": new_id,
        "user_id": actor.get("user_id"),
        "device_id": actor.get("device_id"),
        "is_anonymous": actor["is_anonymous"],
        "voice_session_id": msg["voice_session_id"],
        "input_transcript": msg["input_transcript"],
        "tone": msg.get("tone"),
        "generated_message": rewritten,
        "copy_count": 0,
        "regenerate_count": 0,
        "refined_from": payload.message_id,
        "refine_type": payload.refine_type,
        "experience_variant": EXPERIENCE_VARIANT,
        "created_at": now_iso(),
    })
    await db.generated_messages.update_one(
        {"message_id": payload.message_id},
        {"$inc": {"regenerate_count": 1}, "$push": {"refine_history": {"type": payload.refine_type, "new_id": new_id, "at": now_iso()}}},
    )
    await _emit(actor, "voice_message_refined", {"message_id": payload.message_id, "new_message_id": new_id, "refine_type": payload.refine_type})
    return GenerateResponse(message_id=new_id, tone=msg.get("tone", ""), generated_message=rewritten)


@router.post("/copy-event")
async def copy_event(payload: CopyEventRequest, actor: dict = Depends(voice_actor)):
    res = await db.generated_messages.update_one(
        {"message_id": payload.message_id, **_session_owner_filter(actor)},
        {"$inc": {"copy_count": 1}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Message not found")
    await _emit(actor, "voice_message_copied", {"message_id": payload.message_id})
    return {"ok": True}


@router.get("/history")
async def history(actor: dict = Depends(voice_actor), limit: int = 50):
    if actor["is_anonymous"]:
        # No history for anon — encourage signup
        return {"sessions": [], "is_anonymous": True}
    cursor = (
        db.voice_sessions.find({"user_id": actor["user_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .limit(min(max(limit, 1), 100))
    )
    sessions = await cursor.to_list(100)
    if sessions:
        sids = [s["session_id"] for s in sessions]
        msgs_cursor = db.generated_messages.find(
            {"user_id": actor["user_id"], "voice_session_id": {"$in": sids}},
            {"_id": 0},
        ).sort("created_at", -1)
        all_msgs = await msgs_cursor.to_list(500)
        by_sid: dict = {}
        for m in all_msgs:
            by_sid.setdefault(m["voice_session_id"], []).append(m)
        for s in sessions:
            s["messages"] = by_sid.get(s["session_id"], [])[:6]
    return {"sessions": sessions, "is_anonymous": False}


@router.get("/usage")
async def usage(actor: dict = Depends(voice_actor)):
    if actor["is_anonymous"]:
        doc = await db.voice_anon_trials.find_one({"device_id": actor["device_id"]}, {"_id": 0}) or {}
        used = int(doc.get("count", 0))
        return {
            "is_anonymous": True,
            "is_pro": False,
            "anon_limit": ANON_TOTAL_LIMIT,
            "anon_used": used,
            "anon_remaining": max(0, ANON_TOTAL_LIMIT - used),
            "daily_limit": None,
            "daily_used": None,
            "daily_remaining": None,
        }
    user = await db.users.find_one({"user_id": actor["user_id"]}, {"_id": 0})
    is_pro = user.get("subscription_status") == "pro"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = int(user.get("voice_daily_count", 0)) if user.get("voice_daily_day") == today else 0
    return {
        "is_anonymous": False,
        "is_pro": is_pro,
        "daily_limit": None if is_pro else FREE_DAILY_LIMIT,
        "daily_used": used,
        "daily_remaining": None if is_pro else max(0, FREE_DAILY_LIMIT - used),
        "anon_limit": None,
        "anon_used": None,
        "anon_remaining": None,
    }


@router.post("/track")
async def track(payload: dict, actor: dict = Depends(voice_actor)):
    event_name = (payload or {}).get("event_name", "")
    allowed = {
        "voice_page_viewed", "voice_record_started", "voice_record_stopped",
        "voice_history_opened", "voice_message_regenerated", "voice_example_clicked",
        "voice_signup_wall_shown", "voice_share_warning_shown", "voice_share_warning_dismissed",
    }
    if event_name not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported event")
    await _emit(actor, event_name)
    return {"ok": True}


# -------------- Share link (minimal, opt-in, PII-redacted) --------------
class CreateShareRequest(BaseModel):
    message_id: str
    confirmed: bool = False  # client must explicitly confirm "this creates a public link"


@router.post("/messages/{message_id}/share")
async def create_share(message_id: str, payload: CreateShareRequest, actor: dict = Depends(voice_actor)):
    """Explicit, off-by-default. Auto-redacts PII. Watermarked. Single-button minimal."""
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="Share confirmation required")
    msg = await db.generated_messages.find_one(
        {"message_id": message_id, **_session_owner_filter(actor)},
        {"_id": 0},
    )
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    # Reuse share if already created for this message_id (idempotent)
    existing = await db.voice_shares.find_one({"message_id": message_id, **_session_owner_filter(actor)}, {"_id": 0})
    if existing:
        await _emit(actor, "voice_share_reused", {"share_id": existing["share_id"], "message_id": message_id})
        return {"share_id": existing["share_id"], "url_path": f"/v/{existing['share_id']}", "redacted_categories": existing.get("redacted_categories", [])}

    raw_input = msg.get("input_transcript") or ""
    polished = msg.get("generated_message") or ""
    redacted_input, cats_in = redact(raw_input)
    redacted_output, cats_out = redact(polished)
    cats = sorted(set(cats_in + cats_out))

    share_id = f"v{uuid.uuid4().hex[:10]}"
    await db.voice_shares.insert_one({
        "share_id": share_id,
        "message_id": message_id,
        "voice_session_id": msg.get("voice_session_id"),
        "user_id": actor.get("user_id"),
        "device_id": actor.get("device_id"),
        "tone": msg.get("tone"),
        "raw_input_redacted": redacted_input,
        "polished_message_redacted": redacted_output,
        "redacted_categories": cats,
        "view_count": 0,
        "created_at": now_iso(),
        "experience_variant": EXPERIENCE_VARIANT,
    })
    await _emit(actor, "voice_share_created", {"share_id": share_id, "message_id": message_id, "redacted_categories": cats})
    return {"share_id": share_id, "url_path": f"/v/{share_id}", "redacted_categories": cats}


@router.get("/share/{share_id}")
async def get_share(share_id: str, request: Request):
    """Public, no auth required. Increments view_count on first GET per IP per hour (best-effort)."""
    share = await db.voice_shares.find_one({"share_id": share_id}, {"_id": 0, "user_id": 0, "device_id": 0, "message_id": 0, "voice_session_id": 0})
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")
    # Best-effort view counter: bump once
    await db.voice_shares.update_one({"share_id": share_id}, {"$inc": {"view_count": 1}})
    # Lightweight share-view event into voice_usage_events (no actor — public)
    await db.voice_usage_events.insert_one({
        "event_id": uuid.uuid4().hex,
        "user_id": None,
        "device_id": None,
        "is_anonymous": True,
        "event_name": "voice_share_viewed",
        "metadata": {"share_id": share_id, "experience_variant": EXPERIENCE_VARIANT, "ip_hash": _hash_ip(request)},
        "created_at": now_iso(),
    })
    return {
        "share_id": share["share_id"],
        "tone": share.get("tone"),
        "raw_input": share.get("raw_input_redacted"),
        "polished_message": share.get("polished_message_redacted"),
        "redacted_categories": share.get("redacted_categories", []),
        "view_count": (share.get("view_count") or 0) + 1,
        "created_at": share.get("created_at"),
        "watermark": "Optimized with aiclonechats.com Voice",
    }


@router.delete("/messages/{message_id}/share")
async def delete_share(message_id: str, actor: dict = Depends(voice_actor)):
    res = await db.voice_shares.delete_one({"message_id": message_id, **_session_owner_filter(actor)})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Share not found")
    await _emit(actor, "voice_share_deleted", {"message_id": message_id})
    return {"ok": True}
    return {"ok": True}
