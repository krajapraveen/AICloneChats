"""
Video Avatar Chat — admin/QA-gated emotional product layer.

Pipeline:
  user message → existing clone AI (text reply) → TTS audio → lip-sync video → MP4

Each stage is independent. Failure at any stage falls back gracefully to text:
- TTS fails → plain text bubble (audio_url=null, video_url=null)
- Lip-sync fails / no FAL_KEY → audio bubble (audio_url set, video_url=null)
- Both succeed → video bubble

Storage:
- Audio: served via /api/avatar-chat/files/{message_id}/audio (local disk, /app/backend/storage/avatar_audio/)
- Video: served via /api/avatar-chat/files/{message_id}/video (local disk, /app/backend/storage/avatar_videos/)

Feature gating:
- AVATAR_CHAT_ENABLED env var must be "true"
- Endpoints check this flag per-request and return 503 Service Unavailable when off
- Admin/QA users (role=admin) ALWAYS see the feature regardless of flag (per the override spec)

Strict analytics separation: experience_variant="avatar_chat_v1".
"""
from __future__ import annotations

import os
import uuid
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from db import db
from auth import get_current_user, get_optional_user
from credit_guard import charge_credits_or_402, fresh_user
from models import now_iso
from safety_filter import (
    SAFETY_CLAUSE,
    moderate_user_input,
    moderate_ai_output,
    log_moderation_event,
    safe_chat_response_fallback,
    rewrite_to_safe_text,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/avatar-chat", tags=["avatar-chat"])
admin_router = APIRouter(prefix="/api/admin/avatar-chat", tags=["avatar-chat-admin"])

EXPERIENCE_VARIANT = "avatar_chat_v1"
AVATAR_CHAT_ENABLED = os.environ.get("AVATAR_CHAT_ENABLED", "false").lower() == "true"
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "openai")  # only "openai" implemented
LIPSYNC_PROVIDER = os.environ.get("LIPSYNC_PROVIDER", "fal")  # only "fal" implemented
MAX_AVATAR_VIDEO_RETRIES = int(os.environ.get("MAX_AVATAR_VIDEO_RETRIES", "3"))
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
FAL_KEY = os.environ.get("FAL_KEY", "")

STORAGE_ROOT = Path(__file__).parent / "storage"
AUDIO_DIR = STORAGE_ROOT / "avatar_audio"
VIDEO_DIR = STORAGE_ROOT / "avatar_videos"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)


# ---- Models ----
class AvatarSendRequest(BaseModel):
    clone_id_or_slug: str
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: Optional[str] = None
    visitor_name: Optional[str] = None
    avatar_id: Optional[str] = None  # explicit avatar profile, else clone default


class AvatarRetryRequest(BaseModel):
    pass


class AvatarProfileCreate(BaseModel):
    avatar_name: str = Field(min_length=1, max_length=60)
    avatar_image_url: str = Field(min_length=1, max_length=2000)
    default_voice_id: str = Field(default="alloy")
    animation_style: str = Field(default="natural")  # natural | cinematic | expressive
    clone_id: Optional[str] = None
    is_default: bool = False


class AvatarProfileUpdate(BaseModel):
    avatar_name: Optional[str] = None
    avatar_image_url: Optional[str] = None
    default_voice_id: Optional[str] = None
    animation_style: Optional[str] = None


# ---- Feature gate ----
def _is_feature_available(user: Optional[dict]) -> bool:
    """Public users blocked unless AVATAR_CHAT_ENABLED. Admins always allowed."""
    if AVATAR_CHAT_ENABLED:
        return True
    return bool(user and user.get("role") == "admin")


def _require_feature(user: Optional[dict]) -> None:
    if not _is_feature_available(user):
        raise HTTPException(
            status_code=503,
            detail="avatar_chat_unavailable",
        )


def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


# ---- Vendor adapters (graceful degradation) ----
async def _generate_tts(text: str, voice: str = "alloy") -> Optional[bytes]:
    """Returns audio bytes (mp3) or None on failure / missing key."""
    if not EMERGENT_LLM_KEY:
        logger.warning("TTS skipped: EMERGENT_LLM_KEY missing")
        return None
    try:
        from emergentintegrations.llm.openai import OpenAITextToSpeech  # type: ignore
        tts = OpenAITextToSpeech(api_key=EMERGENT_LLM_KEY)
        audio = await tts.generate_speech(text=text[:4000], model="tts-1", voice=voice)
        return audio
    except Exception as e:
        logger.warning("TTS generation failed: %s", e)
        return None


async def _generate_lipsync_video(image_url: str, audio_bytes: bytes) -> tuple[Optional[str], str]:
    """Returns (provider's MP4 URL, debug_reason).

    Uploads `audio_bytes` to fal.ai's CDN (so production's ephemeral disk
    doesn't matter — fal serves the file itself), then submits sync-lipsync.

    Image is passed by URL because clones' avatar images already live on a
    persistent storage CDN (/api/storage/files/) that fal CAN reach.
    """
    logger.error(
        "lipsync_attempt | provider=%s key_present=%s image_url_kind=%s audio_bytes=%d",
        LIPSYNC_PROVIDER, bool(FAL_KEY),
        "abs" if (image_url or "").startswith("http") else "rel" if image_url else "empty",
        len(audio_bytes or b""),
    )
    if not FAL_KEY:
        return None, "no_fal_key"
    if LIPSYNC_PROVIDER != "fal":
        return None, f"wrong_provider:{LIPSYNC_PROVIDER}"
    if not image_url or not image_url.startswith("http"):
        return None, f"bad_image_url:{(image_url or '')[:80]}"
    if not audio_bytes:
        return None, "empty_audio_bytes"
    try:
        import fal_client  # type: ignore
        import tempfile
        os.environ["FAL_KEY"] = FAL_KEY

        def _sync_call() -> tuple[Optional[str], str]:
            try:
                # Write the audio bytes to a tempfile and upload to fal.ai's
                # CDN. fal_client.upload_file expects a path (the SDK doesn't
                # have an `upload_bytes` helper as of v1.0). The tempfile is
                # auto-cleaned when the `with` block exits.
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                    tf.write(audio_bytes)
                    tmp_path = tf.name
                try:
                    fal_audio_url = fal_client.upload_file(tmp_path)
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                logger.error("fal_client_audio_uploaded | url=%s", fal_audio_url[:120])
                handler = fal_client.submit(
                    "fal-ai/sync-lipsync",
                    arguments={"video_url": image_url, "audio_url": fal_audio_url, "model": "lipsync-1.9.0-beta"},
                )
                result = handler.get()
                if not isinstance(result, dict):
                    return None, f"result_not_dict:{type(result).__name__}"
                video_url = (result.get("video") or {}).get("url")
                if not video_url:
                    return None, f"no_video_in_result:{str(result)[:200]}"
                return video_url, "ok"
            except Exception as inner:
                logger.exception("fal_client_submit_failed")
                return None, f"submit_exception:{type(inner).__name__}:{str(inner)[:200]}"

        return await asyncio.to_thread(_sync_call)
    except ImportError:
        return None, "fal_client_not_installed"
    except Exception as e:
        return None, f"unexpected:{type(e).__name__}:{str(e)[:200]}"


# ---- Analytics ----
async def _emit(event_name: str, *, message_id: Optional[str] = None, user_id: Optional[str] = None, clone_id: Optional[str] = None, metadata: Optional[dict] = None) -> None:
    await db.avatar_chat_events.insert_one({
        "event_id": uuid.uuid4().hex,
        "event_name": event_name,
        "message_id": message_id,
        "user_id": user_id,
        "clone_id": clone_id,
        "metadata": {**(metadata or {}), "experience_variant": EXPERIENCE_VARIANT},
        "created_at": now_iso(),
    })


# ---- Pipeline ----
async def _run_pipeline(message_id: str) -> None:
    """Background job: TTS → upload audio → lipsync → upload video → mark complete."""
    msg = await db.avatar_chat_messages.find_one({"message_id": message_id}, {"_id": 0})
    if not msg:
        return

    job_id = msg.get("job_id") or f"avj_{uuid.uuid4().hex[:14]}"
    await db.avatar_generation_jobs.update_one(
        {"job_id": job_id},
        {"$set": {
            "job_id": job_id,
            "message_id": message_id,
            "user_id": msg.get("user_id"),
            "clone_id": msg.get("clone_id"),
            "status": "running",
            "stage": "audio",
            "progress_percent": 10,
            "attempts": (msg.get("attempts") or 0) + 1,
            "started_at": now_iso(),
            "updated_at": now_iso(),
        }, "$setOnInsert": {"created_at": now_iso()}},
        upsert=True,
    )
    await db.avatar_chat_messages.update_one(
        {"message_id": message_id},
        {"$set": {"video_status": "generating_audio", "updated_at": now_iso()}},
    )
    await _emit("avatar_generation_started", message_id=message_id, user_id=msg.get("user_id"), clone_id=msg.get("clone_id"))

    text = msg.get("ai_response_text") or ""
    voice = msg.get("voice_id") or "alloy"
    avatar_image = msg.get("avatar_image_url") or ""

    # ---- Stage 1: TTS ----
    audio_bytes = await _generate_tts(text, voice=voice)
    if not audio_bytes:
        await db.avatar_chat_messages.update_one(
            {"message_id": message_id},
            {"$set": {
                "video_status": "failed",
                "error_code": "tts_failed",
                "error_message": "TTS generation unavailable. Falling back to text.",
                "updated_at": now_iso(),
            }},
        )
        await db.avatar_generation_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "failed", "stage": "audio", "error_code": "tts_failed", "completed_at": now_iso(), "updated_at": now_iso()}},
        )
        await _emit("avatar_video_failed", message_id=message_id, metadata={"stage": "audio"})
        return

    # Save audio locally (best-effort; container disk may be ephemeral) AND
    # to persistent object storage so playback survives container restarts
    # and so fal.ai has a guaranteed-reachable URL.
    audio_path = AUDIO_DIR / f"{message_id}.mp3"
    try:
        audio_path.write_bytes(audio_bytes)
    except OSError:
        pass  # local-disk write is purely a fast-path optimisation
    # Persist to the same object store that successfully hosts avatar images.
    try:
        from urllib.parse import quote as _quote
        import storage as _storage
        storage_path = f"cloneme/audio/{message_id}.mp3"
        _storage._put(storage_path, audio_bytes, "audio/mpeg")
        audio_url = f"/api/storage/files/{_quote(storage_path, safe='')}"
        logger.info("audio_persisted_to_objstore | message_id=%s url=%s", message_id, audio_url)
    except Exception:
        logger.exception("audio_persist_to_objstore_failed; falling back to local-disk url")
        audio_url = f"/api/avatar-chat/files/{message_id}/audio"

    await db.avatar_chat_messages.update_one(
        {"message_id": message_id},
        {"$set": {"audio_url": audio_url, "video_status": "rendering_video", "updated_at": now_iso()}},
    )
    await db.avatar_generation_jobs.update_one(
        {"job_id": job_id},
        {"$set": {"audio_url": audio_url, "stage": "video", "progress_percent": 50, "updated_at": now_iso()}},
    )
    await _emit("avatar_audio_generated", message_id=message_id, user_id=msg.get("user_id"))

    # ---- Stage 2: Lipsync video ----
    if not avatar_image:
        # No avatar image → audio-only bubble
        logger.error("lipsync_skip_no_avatar_image | message_id=%s clone_id=%s", message_id, msg.get("clone_id"))
        await db.avatar_chat_messages.update_one(
            {"message_id": message_id},
            {"$set": {"video_status": "completed", "completed_at": now_iso(), "updated_at": now_iso()}},
        )
        await db.avatar_generation_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "completed", "stage": "audio_only", "progress_percent": 100, "completed_at": now_iso(), "updated_at": now_iso()}},
        )
        await _emit("avatar_video_completed", message_id=message_id, metadata={"audio_only": True, "reason": "no_avatar_image"})
        return

    # Need a publicly fetchable audio URL for fal.ai. Use BACKEND_PUBLIC_URL or fall back.
    public_base = os.environ.get("BACKEND_PUBLIC_URL", "").rstrip("/")
    if not public_base:
        # Skip lipsync — audio-only fallback
        logger.error("lipsync_skip_no_backend_public_url | message_id=%s avatar_image=%s", message_id, (avatar_image or "")[:200])
        await db.avatar_chat_messages.update_one(
            {"message_id": message_id},
            {"$set": {"video_status": "completed", "completed_at": now_iso(), "updated_at": now_iso()}},
        )
        await db.avatar_generation_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "completed", "stage": "audio_only", "progress_percent": 100, "completed_at": now_iso(), "updated_at": now_iso()}},
        )
        await _emit("avatar_video_completed", message_id=message_id, metadata={"audio_only": True, "reason": "no_public_base"})
        return

    audio_public = f"{public_base}{audio_url}"
    image_public = avatar_image if avatar_image.startswith("http") else f"{public_base}{avatar_image}"
    logger.error("lipsync_resolved_urls | message_id=%s image_public=%s audio_public=%s", message_id, image_public[:200], audio_public[:200])

    # Pass audio_bytes directly — we'll upload them to fal.ai's CDN inside the
    # helper so production's ephemeral disk (where our local audio file may
    # already be gone by the time fal tries to fetch it) doesn't matter.
    video_provider_url, lipsync_debug = await _generate_lipsync_video(image_public, audio_bytes)
    if not video_provider_url:
        logger.error("lipsync_completed_audio_only | message_id=%s reason=%s", message_id, lipsync_debug)
        # Audio-only completion (graceful degrade) — persist the specific
        # debug reason so the operator can `curl /api/avatar-chat/job/<id>`
        # without needing log access.
        await db.avatar_chat_messages.update_one(
            {"message_id": message_id},
            {"$set": {"video_status": "completed", "completed_at": now_iso(), "updated_at": now_iso(),
                      "error_code": "lipsync_unavailable", "error_message": "Lip-sync provider unavailable. Audio-only reply.",
                      "lipsync_debug": lipsync_debug}},
        )
        await db.avatar_generation_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "completed", "stage": "audio_only", "progress_percent": 100, "completed_at": now_iso(), "updated_at": now_iso(),
                      "error_code": "lipsync_unavailable", "lipsync_debug": lipsync_debug}},
        )
        await _emit("avatar_video_completed", message_id=message_id, metadata={"audio_only": True, "reason": "lipsync_unavailable", "lipsync_debug": lipsync_debug})
        return

    # ---- Download MP4 from provider, store locally ----
    try:
        import requests
        r = requests.get(video_provider_url, timeout=120)
        r.raise_for_status()
        video_path = VIDEO_DIR / f"{message_id}.mp4"
        video_path.write_bytes(r.content)
        video_url = f"/api/avatar-chat/files/{message_id}/video"
    except Exception as e:
        logger.warning("Video download failed: %s", e)
        await db.avatar_chat_messages.update_one(
            {"message_id": message_id},
            {"$set": {"video_status": "completed", "completed_at": now_iso(), "updated_at": now_iso(),
                      "error_code": "video_download_failed", "error_message": str(e)[:200]}},
        )
        await db.avatar_generation_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "completed", "stage": "audio_only", "progress_percent": 100, "completed_at": now_iso(), "updated_at": now_iso()}},
        )
        return

    await db.avatar_chat_messages.update_one(
        {"message_id": message_id},
        {"$set": {"video_url": video_url, "video_status": "completed", "completed_at": now_iso(), "updated_at": now_iso()}},
    )
    await db.avatar_generation_jobs.update_one(
        {"job_id": job_id},
        {"$set": {"video_url": video_url, "status": "completed", "stage": "video", "progress_percent": 100, "completed_at": now_iso(), "updated_at": now_iso()}},
    )
    await _emit("avatar_video_completed", message_id=message_id, metadata={"audio_only": False})


def _public_message(m: dict) -> dict:
    return {
        "message_id": m.get("message_id"),
        "conversation_id": m.get("conversation_id"),
        "clone_id": m.get("clone_id"),
        "input_text": m.get("input_text"),
        "ai_response_text": m.get("ai_response_text"),
        "response_mode": m.get("response_mode") or "avatar_video",
        "audio_url": m.get("audio_url"),
        "video_url": m.get("video_url"),
        "video_status": m.get("video_status") or "queued",
        "error_code": m.get("error_code"),
        "error_message": m.get("error_message"),
        # Detailed lipsync failure reason — operator-facing, surfaced so we can
        # diagnose production fal.ai issues without log access.
        "lipsync_debug": m.get("lipsync_debug"),
        "duration_seconds": m.get("duration_seconds"),
        "avatar_image_url": m.get("avatar_image_url"),
        "voice_id": m.get("voice_id"),
        "created_at": m.get("created_at"),
        "updated_at": m.get("updated_at"),
    }


# ---------- Routes: avatar chat ----------
@router.get("/status")
async def feature_status(user: Optional[dict] = Depends(get_optional_user)):
    """Lets the frontend gate UI without trying the endpoint."""
    return {
        "enabled_for_public": AVATAR_CHAT_ENABLED,
        "available_for_user": _is_feature_available(user),
        "tts_configured": bool(EMERGENT_LLM_KEY),
        "lipsync_configured": bool(FAL_KEY) and LIPSYNC_PROVIDER == "fal",
        "tts_provider": TTS_PROVIDER,
        "lipsync_provider": LIPSYNC_PROVIDER,
    }


@router.post("/send")
async def send_avatar_message(payload: AvatarSendRequest, user: dict = Depends(get_current_user)):
    _require_feature(user)

    # Resolve clone
    clone = await db.clones.find_one({"slug": payload.clone_id_or_slug.lower()}, {"_id": 0})
    if not clone:
        clone = await db.clones.find_one({"clone_id": payload.clone_id_or_slug}, {"_id": 0})
    if not clone:
        raise HTTPException(404, "Clone not found")
    if clone.get("status") == "paused":
        raise HTTPException(403, "Clone paused")

    clone_id = clone["clone_id"]

    # Safety pre-flight on user input
    in_check = moderate_user_input(payload.message)
    if in_check["action"] == "block":
        await log_moderation_event(db, user_id=user["user_id"], route="avatar_chat", source="user_input", result=in_check, action_taken="block_input")
        raise HTTPException(400, "This message could not be sent because it may violate safety rules.")

    # ---- Credit gate (video_avatar = 5 credits, refunded if pipeline can't even queue) ----
    user_doc = await fresh_user(user)
    credit_handle = await charge_credits_or_402(user_doc, surface="video_avatar")  # noqa: F841

    # Conversation
    conversation_id = payload.conversation_id or f"conv_{uuid.uuid4().hex[:14]}"
    await db.clone_conversations.update_one(
        {"conversation_id": conversation_id},
        {"$setOnInsert": {
            "conversation_id": conversation_id,
            "clone_id": clone_id,
            "visitor_id": user["user_id"],
            "visitor_name": payload.visitor_name or user.get("name") or "User",
            "channel": "avatar_chat",
            "created_at": now_iso(),
        }, "$set": {"updated_at": now_iso()}},
        upsert=True,
    )

    # Save visitor msg
    visitor_msg_id = f"msg_{uuid.uuid4().hex[:14]}"
    await db.clone_messages.insert_one({
        "message_id": visitor_msg_id,
        "conversation_id": conversation_id,
        "clone_id": clone_id,
        "sender": "visitor",
        "text": payload.message,
        "channel": "avatar_chat",
        "created_at": now_iso(),
    })

    # Generate AI text reply (reuse clone chat building blocks)
    from chat import build_clone_system_prompt, retrieve_memories, get_recent_messages
    memories = await retrieve_memories(clone_id, payload.message, limit=6)
    recent = await get_recent_messages(conversation_id, limit=20)
    if recent and recent[-1].get("sender") == "visitor" and recent[-1].get("text") == payload.message:
        recent = recent[:-1]
    system_prompt = build_clone_system_prompt(clone, memories, recent)

    reply_text = "(LLM is not configured.)"
    if EMERGENT_LLM_KEY:
        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore
            chat = LlmChat(api_key=EMERGENT_LLM_KEY, session_id=conversation_id, system_message=system_prompt).with_model("anthropic", "claude-sonnet-4-5-20250929")
            r = await chat.send_message(UserMessage(text=payload.message))
            reply_text = (r or "").strip() or "Hmm, give me a sec — try saying that again?"
        except Exception as e:
            logger.exception("Avatar AI call failed")
            reply_text = f"(I hit a snag generating a reply. {type(e).__name__})"

    # Safety post-flight on AI output
    out_check = moderate_ai_output(reply_text)
    if out_check["action"] == "block":
        await log_moderation_event(db, user_id=user["user_id"], route="avatar_chat", source="ai_output", result={**out_check, "input_hash": "", "snippet": reply_text[:60]}, action_taken="block_output")
        reply_text = safe_chat_response_fallback()
    elif out_check["action"] == "rewrite":
        await log_moderation_event(db, user_id=user["user_id"], route="avatar_chat", source="ai_output", result={**out_check, "input_hash": "", "snippet": reply_text[:60]}, action_taken="rewrite_output")
        reply_text = rewrite_to_safe_text(reply_text)

    # Resolve avatar profile
    avatar_doc = None
    if payload.avatar_id:
        avatar_doc = await db.avatar_profiles.find_one({"avatar_id": payload.avatar_id, "user_id": user["user_id"]}, {"_id": 0})
    if not avatar_doc:
        avatar_doc = await db.avatar_profiles.find_one({"clone_id": clone_id, "is_default": True}, {"_id": 0})
    if not avatar_doc:
        avatar_doc = await db.avatar_profiles.find_one({"user_id": user["user_id"], "is_default": True}, {"_id": 0})

    avatar_image_url = (avatar_doc or {}).get("avatar_image_url") or clone.get("avatar_url") or ""
    voice_id = (avatar_doc or {}).get("default_voice_id") or "alloy"

    # Save clone reply
    avatar_msg_id = f"avm_{uuid.uuid4().hex[:14]}"
    await db.avatar_chat_messages.insert_one({
        "message_id": avatar_msg_id,
        "conversation_id": conversation_id,
        "user_id": user["user_id"],
        "clone_id": clone_id,
        "input_text": payload.message,
        "ai_response_text": reply_text,
        "response_mode": "avatar_video",
        "avatar_id": (avatar_doc or {}).get("avatar_id"),
        "avatar_image_url": avatar_image_url,
        "voice_id": voice_id,
        "tts_provider": TTS_PROVIDER,
        "lipsync_provider": LIPSYNC_PROVIDER,
        "audio_url": None,
        "video_url": None,
        "video_status": "queued",
        "error_code": None,
        "error_message": None,
        "attempts": 0,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    })

    # Mirror clone reply text into clone_messages so existing transcript views show it
    await db.clone_messages.insert_one({
        "message_id": f"msg_{uuid.uuid4().hex[:14]}",
        "conversation_id": conversation_id,
        "clone_id": clone_id,
        "sender": "clone",
        "text": reply_text,
        "channel": "avatar_chat",
        "avatar_chat_message_id": avatar_msg_id,
        "created_at": now_iso(),
    })

    await _emit("avatar_message_submitted", message_id=avatar_msg_id, user_id=user["user_id"], clone_id=clone_id)

    # Kick off background pipeline (non-blocking)
    asyncio.create_task(_run_pipeline(avatar_msg_id))

    msg = await db.avatar_chat_messages.find_one({"message_id": avatar_msg_id}, {"_id": 0})
    return {
        "conversation_id": conversation_id,
        "message": _public_message(msg or {}),
    }


@router.get("/messages/{conversation_id}")
async def list_avatar_messages(conversation_id: str, user: dict = Depends(get_current_user)):
    _require_feature(user)
    rows = await db.avatar_chat_messages.find(
        {"conversation_id": conversation_id, "user_id": user["user_id"]}, {"_id": 0},
    ).sort("created_at", 1).to_list(200)
    return {"messages": [_public_message(r) for r in rows]}


@router.get("/job/{message_id}")
async def get_job(message_id: str, user: dict = Depends(get_current_user)):
    _require_feature(user)
    msg = await db.avatar_chat_messages.find_one({"message_id": message_id, "user_id": user["user_id"]}, {"_id": 0})
    if not msg:
        raise HTTPException(404, "Message not found")
    job = await db.avatar_generation_jobs.find_one({"message_id": message_id}, {"_id": 0})
    return {"message": _public_message(msg), "job": job}


@router.post("/retry/{message_id}")
async def retry_message(message_id: str, user: dict = Depends(get_current_user)):
    _require_feature(user)
    msg = await db.avatar_chat_messages.find_one({"message_id": message_id, "user_id": user["user_id"]}, {"_id": 0})
    if not msg:
        raise HTTPException(404, "Message not found")
    if (msg.get("attempts") or 0) >= MAX_AVATAR_VIDEO_RETRIES:
        raise HTTPException(429, "Max retries reached")
    await db.avatar_chat_messages.update_one(
        {"message_id": message_id},
        {"$set": {"video_status": "queued", "error_code": None, "error_message": None, "updated_at": now_iso()}, "$inc": {"attempts": 1}},
    )
    await _emit("avatar_video_retried", message_id=message_id, user_id=user["user_id"])
    asyncio.create_task(_run_pipeline(message_id))
    return {"ok": True, "message_id": message_id, "status": "queued"}


# ---------- Routes: avatar profiles ----------
@router.post("/profiles")
async def create_profile(payload: AvatarProfileCreate, user: dict = Depends(get_current_user)):
    _require_feature(user)
    avatar_id = f"av_{uuid.uuid4().hex[:14]}"
    if payload.is_default:
        await db.avatar_profiles.update_many({"user_id": user["user_id"]}, {"$set": {"is_default": False}})
    doc = {
        "avatar_id": avatar_id,
        "user_id": user["user_id"],
        "clone_id": payload.clone_id,
        "avatar_name": payload.avatar_name.strip(),
        "avatar_image_url": payload.avatar_image_url.strip(),
        "default_voice_id": payload.default_voice_id,
        "animation_style": payload.animation_style,
        "is_default": payload.is_default,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.avatar_profiles.insert_one(dict(doc))
    return {"profile": doc}


@router.get("/profiles")
async def list_profiles(user: dict = Depends(get_current_user)):
    _require_feature(user)
    rows = await db.avatar_profiles.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(50)
    return {"profiles": rows}


@router.put("/profiles/{avatar_id}")
async def update_profile(avatar_id: str, payload: AvatarProfileUpdate, user: dict = Depends(get_current_user)):
    _require_feature(user)
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(400, "No changes")
    update["updated_at"] = now_iso()
    res = await db.avatar_profiles.update_one({"avatar_id": avatar_id, "user_id": user["user_id"]}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(404, "Profile not found")
    doc = await db.avatar_profiles.find_one({"avatar_id": avatar_id}, {"_id": 0})
    return {"profile": doc}


@router.delete("/profiles/{avatar_id}")
async def delete_profile(avatar_id: str, user: dict = Depends(get_current_user)):
    _require_feature(user)
    res = await db.avatar_profiles.delete_one({"avatar_id": avatar_id, "user_id": user["user_id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Profile not found")
    return {"ok": True}


@router.post("/profiles/{avatar_id}/default")
async def set_default_profile(avatar_id: str, user: dict = Depends(get_current_user)):
    _require_feature(user)
    profile = await db.avatar_profiles.find_one({"avatar_id": avatar_id, "user_id": user["user_id"]}, {"_id": 0})
    if not profile:
        raise HTTPException(404, "Profile not found")
    await db.avatar_profiles.update_many({"user_id": user["user_id"]}, {"$set": {"is_default": False}})
    await db.avatar_profiles.update_one({"avatar_id": avatar_id}, {"$set": {"is_default": True, "updated_at": now_iso()}})
    return {"ok": True}


# ---------- Static file serving (no auth — URLs are unguessable & contain message_id) ----------
@router.get("/files/{message_id}/audio")
async def serve_audio(message_id: str):
    msg = await db.avatar_chat_messages.find_one({"message_id": message_id}, {"_id": 0, "audio_url": 1})
    if not msg or not msg.get("audio_url"):
        raise HTTPException(404, "Audio not found")
    p = AUDIO_DIR / f"{message_id}.mp3"
    if not p.exists():
        raise HTTPException(404, "Audio file missing")
    return Response(content=p.read_bytes(), media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/files/{message_id}/video")
async def serve_video(message_id: str):
    msg = await db.avatar_chat_messages.find_one({"message_id": message_id}, {"_id": 0, "video_url": 1})
    if not msg or not msg.get("video_url"):
        raise HTTPException(404, "Video not found")
    p = VIDEO_DIR / f"{message_id}.mp4"
    if not p.exists():
        raise HTTPException(404, "Video file missing")
    return Response(content=p.read_bytes(), media_type="video/mp4", headers={"Cache-Control": "public, max-age=3600"})


# ---------- Admin ----------
@admin_router.get("/metrics")
async def admin_metrics(_admin: dict = Depends(_require_admin), days: int = Query(default=7, ge=1, le=90)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    total = await db.avatar_chat_messages.count_documents({"created_at": {"$gte": since}})
    completed = await db.avatar_chat_messages.count_documents({"created_at": {"$gte": since}, "video_status": "completed"})
    failed = await db.avatar_chat_messages.count_documents({"created_at": {"$gte": since}, "video_status": "failed"})
    queued = await db.avatar_chat_messages.count_documents({"video_status": {"$in": ["queued", "generating_audio", "rendering_video"]}})
    # Avg render time (completed_at - created_at)
    pipeline = [
        {"$match": {"created_at": {"$gte": since}, "video_status": "completed", "completed_at": {"$ne": None}}},
        {"$project": {"_id": 0, "created_at": 1, "completed_at": 1}},
    ]
    rows = await db.avatar_chat_messages.aggregate(pipeline).to_list(5000)
    durations: list[float] = []
    for r in rows:
        try:
            c = datetime.fromisoformat((r["created_at"] or "").replace("Z", "+00:00"))
            cm = datetime.fromisoformat((r["completed_at"] or "").replace("Z", "+00:00"))
            d = (cm - c).total_seconds()
            if d >= 0:
                durations.append(d)
        except Exception:
            continue
    avg_render_sec = round(sum(durations) / len(durations), 2) if durations else 0.0

    by_error_pipeline = [
        {"$match": {"created_at": {"$gte": since}, "error_code": {"$ne": None}}},
        {"$group": {"_id": "$error_code", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]
    by_err = await db.avatar_chat_messages.aggregate(by_error_pipeline).to_list(50)
    return {
        "window_days": days,
        "total": total,
        "completed": completed,
        "failed": failed,
        "queue_size": queued,
        "avg_render_sec": avg_render_sec,
        "errors_by_code": [{"code": r["_id"], "count": r["n"]} for r in by_err],
        "tts_configured": bool(EMERGENT_LLM_KEY),
        "lipsync_configured": bool(FAL_KEY) and LIPSYNC_PROVIDER == "fal",
        "feature_enabled_public": AVATAR_CHAT_ENABLED,
    }


@admin_router.get("/jobs")
async def admin_jobs(_admin: dict = Depends(_require_admin), status: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500)):
    q: dict = {}
    if status:
        q["video_status"] = status
    rows = await db.avatar_chat_messages.find(q, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return {"jobs": [_public_message(r) for r in rows]}


@admin_router.post("/jobs/{message_id}/retry")
async def admin_retry(message_id: str, _admin: dict = Depends(_require_admin)):
    msg = await db.avatar_chat_messages.find_one({"message_id": message_id}, {"_id": 0})
    if not msg:
        raise HTTPException(404, "Message not found")
    await db.avatar_chat_messages.update_one(
        {"message_id": message_id},
        {"$set": {"video_status": "queued", "error_code": None, "error_message": None, "updated_at": now_iso()}, "$inc": {"attempts": 1}},
    )
    asyncio.create_task(_run_pipeline(message_id))
    return {"ok": True}


@admin_router.post("/jobs/{message_id}/cancel")
async def admin_cancel(message_id: str, _admin: dict = Depends(_require_admin)):
    res = await db.avatar_chat_messages.update_one(
        {"message_id": message_id},
        {"$set": {"video_status": "failed", "error_code": "admin_cancelled", "error_message": "Cancelled by admin.", "updated_at": now_iso()}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Message not found")
    return {"ok": True}
