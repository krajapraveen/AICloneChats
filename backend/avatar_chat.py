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
# Fal lipsync endpoint and optional model parameter.
#   FAL_LIPSYNC_ENDPOINT     → default `fal-ai/sadtalker` (image-to-talking-avatar,
#                              verified live in fal's official catalog).
#   FAL_LIPSYNC_MODEL        → if set, sent as the `model` arg (sadtalker ignores).
#   FAL_LIPSYNC_IMAGE_FIELD  → schema key for the avatar image. Default
#                              "source_image_url" (sadtalker). Use "video_url"
#                              for sync-lipsync (will MP4-transcode upstream).
#   FAL_LIPSYNC_AUDIO_FIELD  → schema key for the audio. Default
#                              "driven_audio_url" (sadtalker). Other models
#                              use "audio_url".
FAL_LIPSYNC_ENDPOINT = os.environ.get("FAL_LIPSYNC_ENDPOINT", "fal-ai/sadtalker").strip()
FAL_LIPSYNC_MODEL = os.environ.get("FAL_LIPSYNC_MODEL", "").strip()  # empty = omit
FAL_LIPSYNC_IMAGE_FIELD = os.environ.get("FAL_LIPSYNC_IMAGE_FIELD", "source_image_url").strip().lower()
FAL_LIPSYNC_AUDIO_FIELD = os.environ.get("FAL_LIPSYNC_AUDIO_FIELD", "driven_audio_url").strip().lower()
# sync_mode controls how fal handles audio/video duration mismatch. Only
# meaningful for video endpoints (sync-lipsync); harmless on VEED.
# Options: cut_off | loop | bounce | silence | remap (per fal docs).
FAL_LIPSYNC_SYNC_MODE = os.environ.get("FAL_LIPSYNC_SYNC_MODE", "loop")

# Register HEIF/HEIC opener so Pillow can decode iPhone photos. Avatar
# uploads commonly arrive as .heic from iOS — without this PIL raises
# UnidentifiedImageError and the MP4 transcode silently fails (which is
# exactly the production bug iter30 surfaced via mp4_dbg=UnidentifiedImageError).
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:
    pass

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


async def _fetch_image_bytes(image_url: str) -> tuple[Optional[bytes], Optional[str], str]:
    """Resolve an avatar image URL to bytes + content-type.

    Supports:
      - http(s)://...                   → requests.get
      - /api/storage/files/<storage_path> → look up db.files directly so we
        never depend on our own ingress being reachable from fal.ai's egress
        network.

    Returns (bytes, content_type, debug_reason).
    """
    if not image_url:
        return None, None, "no_image_url"
    try:
        # Local storage path → bypass HTTP and pull straight from Mongo
        marker = "/api/storage/files/"
        if marker in image_url:
            from urllib.parse import unquote
            storage_path = unquote(image_url.split(marker, 1)[1])
            try:
                import storage as _storage
                blob, blob_ct = _storage._get(storage_path)
            except Exception as e:
                return None, None, f"storage_get_failed:{type(e).__name__}:{str(e)[:120]}"
            if not blob:
                return None, None, f"storage_blob_missing:{storage_path[:80]}"
            row = await db.files.find_one({"storage_path": storage_path}, {"_id": 0, "content_type": 1})
            ct = (row or {}).get("content_type") or blob_ct or "image/jpeg"
            return blob, ct, "ok_storage"
        if image_url.startswith("http"):
            import requests
            r = requests.get(image_url, timeout=30)
            if r.status_code >= 400:
                return None, None, f"http_{r.status_code}"
            ct = r.headers.get("content-type", "image/jpeg").split(";")[0].strip() or "image/jpeg"
            return r.content, ct, "ok_http"
        return None, None, f"unsupported_image_url:{image_url[:80]}"
    except Exception as e:
        return None, None, f"fetch_exception:{type(e).__name__}:{str(e)[:120]}"


def _ext_for_ct(content_type: str) -> str:
    ct = (content_type or "").lower()
    if "mp4" in ct:
        return ".mp4"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    return ".jpg"


def _image_bytes_to_gif(image_bytes: bytes) -> tuple[bytes, str]:
    """Convert a still image (JPG/PNG/WEBP) to a 1-frame GIF.

    NOTE: kept for backward compat / fallback only. fal-ai/sync-lipsync's
    ffprobe step rejects a 1-frame GIF with "Failed to read video metadata".
    Prefer `_image_bytes_to_mp4()` which produces a real H.264 MP4.
    """
    try:
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(image_bytes))
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (0, 0, 0))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        if max(img.size) > 1024:
            img.thumbnail((1024, 1024))
        buf = _io.BytesIO()
        img.save(buf, format="GIF", optimize=True)
        return buf.getvalue(), "image/gif"
    except Exception as e:
        logger.warning("image_to_gif_failed | will_pass_original | err=%s", e)
        return image_bytes, "image/jpeg"


def _image_bytes_to_mp4(image_bytes: bytes, *, fps: int = 25, duration_sec: float = 2.0) -> tuple[Optional[bytes], str]:
    """Convert a still image into a short H.264 MP4 with valid video metadata.

    The previous GIF approach was rejected by fal-ai/sync-lipsync's ffprobe
    step with `Failed to read video metadata. Ensure the video is valid.`
    fal needs a real video container with codec metadata — a 1-frame GIF
    doesn't qualify even though the suffix is in the allowlist.

    We use `imageio` + the bundled `imageio-ffmpeg` (portable wheel binary,
    no system ffmpeg required) to write a libx264 MP4 with `duration_sec`
    repetitions of the same frame. fal's `sync_mode: "loop"` then repeats
    this short clip across the full audio duration.

    Returns (mp4_bytes, content_type) on success, (None, error_detail) on failure.
    """
    try:
        from PIL import Image
        import io as _io
        import imageio.v3 as iio  # type: ignore
        import numpy as np
        import tempfile
        # Decode + sanitise the still image
        img = Image.open(_io.BytesIO(image_bytes))
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (0, 0, 0))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        # H.264 requires even dimensions — pad/crop to even px.
        w, h = img.size
        if w > 1024 or h > 1024:
            img.thumbnail((1024, 1024))
            w, h = img.size
        if w % 2 != 0 or h % 2 != 0:
            new_w = w - (w % 2)
            new_h = h - (h % 2)
            img = img.crop((0, 0, new_w, new_h))
        frame = np.asarray(img, dtype=np.uint8)
        n_frames = max(1, int(fps * duration_sec))
        # Write to a tempfile (imageio_ffmpeg writes via subprocess).
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
            mp4_path = tf.name
        try:
            iio.imwrite(
                mp4_path,
                np.stack([frame] * n_frames, axis=0),
                fps=fps,
                codec="libx264",
                pixelformat="yuv420p",
                macro_block_size=1,
                ffmpeg_log_level="error",
            )
            with open(mp4_path, "rb") as f:
                mp4_bytes = f.read()
        finally:
            try:
                os.unlink(mp4_path)
            except OSError:
                pass
        if not mp4_bytes:
            return None, "mp4_empty"
        logger.error("image_to_mp4_ok | bytes=%d frames=%d fps=%d size=%dx%d", len(mp4_bytes), n_frames, fps, img.size[0], img.size[1])
        return mp4_bytes, "video/mp4"
    except Exception as e:
        logger.exception("image_to_mp4_failed")
        return None, f"{type(e).__name__}:{str(e)[:200]}"


# Canonical lipsync error codes — surfaced via `error_code` so ops can grep.
LIPSYNC_ERR_INVALID_PROVIDER_PAYLOAD = "INVALID_PROVIDER_PAYLOAD"  # preflight: we'd be sending image-as-video
# Detail strings go into `lipsync_debug` (verbose, may contain provider text).
LIPSYNC_ERR_PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"   # no FAL_KEY OR fal 401/403
LIPSYNC_ERR_PROVIDER_422 = "PROVIDER_422"                   # fal 4xx unprocessable
LIPSYNC_ERR_INVALID_AVATAR_ID = "INVALID_AVATAR_ID"         # no image url / fetch failed
LIPSYNC_ERR_JOB_TIMEOUT = "JOB_TIMEOUT"                     # polling exceeded budget
LIPSYNC_ERR_POLL_FAILED = "POLL_FAILED"                     # exception during poll
LIPSYNC_ERR_NO_VIDEO_URL = "NO_VIDEO_URL"                   # fal returned but no video.url
LIPSYNC_ERR_RENDER_EXCEPTION = "RENDER_EXCEPTION"           # catch-all
LIPSYNC_ERR_VIDEO_NOT_STARTED = "VIDEO_NOT_STARTED"         # pipeline didn't reach lipsync

# Max wall-clock seconds to wait on fal.ai sync-lipsync job. Beyond this we
# return JOB_TIMEOUT. sadtalker can take 60-180s for a short clip; 300s is
# a safe upper bound that doesn't keep the background pipeline hanging.
LIPSYNC_TIMEOUT_SEC = int(os.environ.get("LIPSYNC_TIMEOUT_SEC", "300"))
# How often we poll fal's queue status URL (and persist progress to the
# message doc). Lower → more responsive UI, higher → fewer fal API calls.
LIPSYNC_POLL_INTERVAL_SEC = float(os.environ.get("LIPSYNC_POLL_INTERVAL_SEC", "5"))


def _classify_fal_exception(err: Exception, *, stage: str, request_id: Optional[str] = None) -> tuple[str, str]:
    """Extract canonical error_code + verbose lipsync_debug from a fal.ai exception.

    Handles `FalClientHTTPError` specifically — pulls `.status_code` and the
    raw response body text (NOT the truncated str(err) repr). This is what
    ops needs to actually debug a 422 / 4xx from fal.

    Returns (canonical_code, debug_detail).
    """
    # Lazy import; fal_client may not be installed at import time in tests.
    try:
        from fal_client import FalClientHTTPError, FalClientTimeoutError  # type: ignore
    except Exception:
        FalClientHTTPError = None  # type: ignore
        FalClientTimeoutError = None  # type: ignore

    rid = f" request_id={request_id}" if request_id else ""

    if FalClientHTTPError is not None and isinstance(err, FalClientHTTPError):
        status = getattr(err, "status_code", None)
        msg = getattr(err, "message", "") or ""
        body_text = ""
        resp = getattr(err, "response", None)
        if resp is not None:
            try:
                body_text = resp.text  # full body, not truncated str(err)
            except Exception:
                body_text = ""
        if not body_text:
            body_text = msg
        # Hard-cap so we never blow up Mongo doc size, but generous enough to
        # capture the entire Pydantic validation list.
        body_text = body_text[:2000]
        detail = f"{stage}:HTTP {status} body={body_text}{rid}"

        if status in (401, 403):
            return LIPSYNC_ERR_PROVIDER_AUTH_FAILED, detail
        if status == 422:
            return LIPSYNC_ERR_PROVIDER_422, detail
        if status == 408 or (status and 500 <= status < 600):
            # 5xx/timeouts from fal — operationally treat as poll/render failures
            return (LIPSYNC_ERR_POLL_FAILED if stage == "poll" else LIPSYNC_ERR_RENDER_EXCEPTION), detail
        return (LIPSYNC_ERR_POLL_FAILED if stage == "poll" else LIPSYNC_ERR_RENDER_EXCEPTION), detail

    if FalClientTimeoutError is not None and isinstance(err, FalClientTimeoutError):
        return LIPSYNC_ERR_JOB_TIMEOUT, f"{stage}:client_timeout{rid}"

    # Generic fallback — last-resort, no truncation of useful info beyond 500ch
    msg = f"{type(err).__name__}:{str(err)[:500]}"
    if "401" in msg or "403" in msg or "unauthor" in msg.lower():
        return LIPSYNC_ERR_PROVIDER_AUTH_FAILED, f"{stage}:{msg}{rid}"
    if "422" in msg:
        return LIPSYNC_ERR_PROVIDER_422, f"{stage}:{msg}{rid}"
    if "timeout" in msg.lower():
        return LIPSYNC_ERR_JOB_TIMEOUT, f"{stage}:{msg}{rid}"
    return (LIPSYNC_ERR_POLL_FAILED if stage == "poll" else LIPSYNC_ERR_RENDER_EXCEPTION), f"{stage}:{msg}{rid}"


async def _generate_lipsync_video(
    image_url: str,
    audio_bytes: bytes,
    *,
    progress: Optional[dict] = None,
) -> tuple[Optional[str], str, str]:
    """Returns (provider's MP4 URL, error_code, debug_detail).

    error_code is one of the LIPSYNC_ERR_* constants (or the literal "ok" on
    success). debug_detail is a free-form string for logs / admin UI.

    If `progress` (a mutable dict) is supplied, the helper writes the
    following keys into it as polling progresses, so the async caller can
    persist them to MongoDB for admin/UI visibility:
      - provider_request_id : fal's job ID (e.g. "abc-1234-def")
      - fal_endpoint        : the configured model ID being called
      - provider_status     : "Queued" | "InProgress" | "Completed"
      - poll_attempts       : count of status() calls made so far
      - last_poll_at        : ISO timestamp of most recent poll
      - final_result_keys   : top-level keys returned by fal (on completion)
    """
    logger.error(
        "lipsync_attempt | provider=%s key_present=%s image_url_kind=%s audio_bytes=%d",
        LIPSYNC_PROVIDER, bool(FAL_KEY),
        "abs" if (image_url or "").startswith("http") else "rel" if image_url else "empty",
        len(audio_bytes or b""),
    )
    if not FAL_KEY:
        return None, LIPSYNC_ERR_PROVIDER_AUTH_FAILED, "no_fal_key"
    if LIPSYNC_PROVIDER != "fal":
        return None, LIPSYNC_ERR_RENDER_EXCEPTION, f"wrong_provider:{LIPSYNC_PROVIDER}"
    if not image_url:
        return None, LIPSYNC_ERR_INVALID_AVATAR_ID, "no_image_url"
    if not audio_bytes:
        return None, LIPSYNC_ERR_VIDEO_NOT_STARTED, "empty_audio_bytes"

    # Fetch the image bytes ourselves so we can re-upload to fal's CDN.
    image_bytes, image_ct, image_dbg = await _fetch_image_bytes(image_url)
    if not image_bytes:
        return None, LIPSYNC_ERR_INVALID_AVATAR_ID, f"image_fetch_failed:{image_dbg}"
    logger.error("lipsync_image_fetched | bytes=%d ct=%s dbg=%s", len(image_bytes), image_ct, image_dbg)

    # --- Provider routing: image-native vs video-native fal model ---
    # If the configured image field name contains "image" (e.g. source_image_url
    # for sadtalker, image_url for others), we pass the avatar bytes DIRECTLY.
    # If it's a video field (e.g. video_url for sync-lipsync), we MP4-transcode.
    image_path_taken = "unknown"
    mp4_dbg = ""
    endpoint_is_image_native = "image" in FAL_LIPSYNC_IMAGE_FIELD

    if endpoint_is_image_native:
        # Pass the original image bytes; sanity-check that PIL can decode it
        # (so we don't upload garbage). Log the magic bytes on failure so we
        # always know what arrived.
        try:
            from PIL import Image
            import io as _io
            Image.open(_io.BytesIO(image_bytes)).verify()
            image_bytes_for_upload = image_bytes
            image_ct_for_upload = image_ct or "image/jpeg"
            image_path_taken = "image_native"
            logger.error("lipsync_image_native | bytes=%d ct=%s endpoint=%s",
                         len(image_bytes), image_ct_for_upload, FAL_LIPSYNC_ENDPOINT)
        except Exception as e_pil:
            magic = image_bytes[:16].hex() if image_bytes else ""
            detail = (
                f"image_decode_failed:{type(e_pil).__name__}:{str(e_pil)[:150]} "
                f"magic_hex={magic} bytes={len(image_bytes)} ct={image_ct} "
                f"endpoint={FAL_LIPSYNC_ENDPOINT}"
            )
            logger.error("lipsync_image_decode_failed | %s", detail)
            return None, LIPSYNC_ERR_INVALID_AVATAR_ID, detail
    else:
        # Video-native endpoint (sync-lipsync): transcode image → H.264 MP4
        # so fal's ffprobe accepts it.
        mp4_bytes, mp4_dbg = _image_bytes_to_mp4(image_bytes)
        if mp4_bytes:
            image_bytes_for_upload = mp4_bytes
            image_ct_for_upload = "video/mp4"
            image_path_taken = "mp4"
            logger.error("lipsync_image_as_mp4 | bytes=%d", len(mp4_bytes))
        else:
            # Fallback to GIF. If GIF helper passes through to raw image
            # bytes (PIL failure), the preflight below catches it.
            gif_bytes, gif_ct = _image_bytes_to_gif(image_bytes)
            image_bytes_for_upload = gif_bytes
            image_ct_for_upload = gif_ct
            image_path_taken = "gif" if gif_ct == "image/gif" else "passthrough_jpeg"
            logger.error("lipsync_image_mp4_fallback_to_gif | mp4_dbg=%s gif_bytes=%d ct=%s path=%s",
                         mp4_dbg, len(gif_bytes), gif_ct, image_path_taken)

        # Preflight reject for the video-native path only — image-native
        # endpoints WANT image/* so we'd skip this.
        if image_ct_for_upload.startswith("image/"):
            magic = image_bytes[:16].hex() if image_bytes else ""
            detail = (
                f"image_as_video_blocked | path={image_path_taken} "
                f"ct={image_ct_for_upload} mp4_dbg={mp4_dbg} "
                f"magic_hex={magic} bytes={len(image_bytes_for_upload)} "
                f"endpoint={FAL_LIPSYNC_ENDPOINT}"
            )
            logger.error("lipsync_preflight_reject | %s", detail)
            return None, LIPSYNC_ERR_INVALID_PROVIDER_PAYLOAD, detail

    try:
        import fal_client  # type: ignore
        import tempfile
        import time as _time
        os.environ["FAL_KEY"] = FAL_KEY

        def _sync_call() -> tuple[Optional[str], str, str]:
            t0 = _time.time()
            audio_tmp = None
            image_tmp = None
            fal_audio_url = None
            fal_image_url = None
            try:
                # --- Upload audio to fal CDN ---
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                    tf.write(audio_bytes)
                    audio_tmp = tf.name
                try:
                    fal_audio_url = fal_client.upload_file(audio_tmp)
                except Exception as e_au:
                    code, dbg = _classify_fal_exception(e_au, stage="audio_upload")
                    logger.exception("fal_audio_upload_failed")
                    return None, code, dbg
                logger.error("fal_client_audio_uploaded | url=%s", (fal_audio_url or "")[:120])

                # --- Upload image to fal CDN (as GIF for sync-lipsync) ---
                img_ext = _ext_for_ct(image_ct_for_upload or "")
                with tempfile.NamedTemporaryFile(suffix=img_ext, delete=False) as tf:
                    tf.write(image_bytes_for_upload)
                    image_tmp = tf.name
                try:
                    fal_image_url = fal_client.upload_file(image_tmp)
                except Exception as e_im:
                    code, dbg = _classify_fal_exception(e_im, stage="image_upload")
                    logger.exception("fal_image_upload_failed")
                    # Image upload failure usually means a bad image, not auth
                    if code not in (LIPSYNC_ERR_PROVIDER_AUTH_FAILED, LIPSYNC_ERR_JOB_TIMEOUT):
                        code = LIPSYNC_ERR_INVALID_AVATAR_ID
                    return None, code, dbg
                logger.error("fal_client_image_uploaded | url=%s ct=%s", (fal_image_url or "")[:120], image_ct_for_upload)

                # --- Hard preflight: refuse to submit if the fal upload URL
                # ends in an image extension AND the endpoint expects a video
                # field. Image-native endpoints WANT image URLs.
                if not endpoint_is_image_native:
                    bad_exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff")
                    lower = (fal_image_url or "").lower()
                    if any(lower.endswith(ext) or (ext + "?") in lower for ext in bad_exts):
                        detail = f"upload_url_is_image:{fal_image_url[:200]} ct={image_ct_for_upload} path={image_path_taken}"
                        logger.error("lipsync_preflight_reject_uploaded | %s", detail)
                        return None, LIPSYNC_ERR_INVALID_PROVIDER_PAYLOAD, detail

                # --- Submit lipsync job ---
                # Build args using the configurable image/audio field names so
                # we can route between image-native (sadtalker: source_image_url
                # + driven_audio_url) and video-native (sync-lipsync: video_url
                # + audio_url) endpoints without code changes.
                submit_args = {
                    FAL_LIPSYNC_IMAGE_FIELD: fal_image_url,
                    FAL_LIPSYNC_AUDIO_FIELD: fal_audio_url,
                }
                # sync_mode is only meaningful for video endpoints (sync-lipsync).
                if FAL_LIPSYNC_SYNC_MODE and not endpoint_is_image_native:
                    submit_args["sync_mode"] = FAL_LIPSYNC_SYNC_MODE
                if FAL_LIPSYNC_MODEL:
                    submit_args["model"] = FAL_LIPSYNC_MODEL
                logger.error("fal_submit_args | endpoint=%s keys=%s image_field=%s audio_field=%s sync_mode=%s model=%s",
                             FAL_LIPSYNC_ENDPOINT, list(submit_args.keys()),
                             FAL_LIPSYNC_IMAGE_FIELD, FAL_LIPSYNC_AUDIO_FIELD,
                             FAL_LIPSYNC_SYNC_MODE or "<omitted>",
                             FAL_LIPSYNC_MODEL or "<omitted>")
                # Build a payload context string we attach to every downstream
                # debug detail so admins can see exactly what we submitted
                # without DevTools (fal_model, payload_keys, input_url_type).
                input_url_ext = (fal_image_url or "").rsplit(".", 1)[-1].split("?")[0][:8]
                payload_ctx = (
                    f"fal_model={FAL_LIPSYNC_ENDPOINT}"
                    f" payload_keys={list(submit_args.keys())}"
                    f" input_url_type=.{input_url_ext}"
                    f" image_path={image_path_taken}"
                )
                try:
                    handler = fal_client.submit(FAL_LIPSYNC_ENDPOINT, arguments=submit_args)
                except Exception as e_sub:
                    code, dbg = _classify_fal_exception(e_sub, stage="submit")
                    logger.exception("fal_submit_failed")
                    return None, code, f"{dbg} | {payload_ctx}"

                request_id = getattr(handler, "request_id", None) or str(handler)[:80]
                logger.error("fal_submit_ok | request_id=%s endpoint=%s", request_id, FAL_LIPSYNC_ENDPOINT)
                # Stash the request_id in the shared progress dict so the
                # async caller (and admin UI) can see it before polling ends.
                if progress is not None:
                    progress["provider_request_id"] = request_id
                    progress["fal_endpoint"] = FAL_LIPSYNC_ENDPOINT

                # --- Explicit polling with progress visibility ---
                # We poll handler.status() every LIPSYNC_POLL_INTERVAL_SEC and
                # write provider_status / poll_attempts / last_poll_at into
                # the shared `progress` dict so the async caller can persist
                # progress to MongoDB (and thus the admin UI sees it live).
                import time as _t
                poll_attempts = 0
                status_history: list[str] = []
                final_status_obj = None
                deadline = t0 + LIPSYNC_TIMEOUT_SEC
                while _t.time() < deadline:
                    poll_attempts += 1
                    try:
                        st = handler.status()
                    except Exception as e_st:
                        code, dbg = _classify_fal_exception(e_st, stage="poll", request_id=request_id)
                        logger.exception("fal_status_call_failed")
                        return None, code, f"{dbg} attempts={poll_attempts} | {payload_ctx}"
                    st_name = type(st).__name__  # "Queued" | "InProgress" | "Completed"
                    status_history.append(st_name)
                    if progress is not None:
                        progress["provider_status"] = st_name
                        progress["poll_attempts"] = poll_attempts
                        progress["last_poll_at"] = now_iso()
                    if st_name == "Completed":
                        final_status_obj = st
                        break
                    _t.sleep(LIPSYNC_POLL_INTERVAL_SEC)
                else:
                    # Loop exited without break = timed out
                    elapsed = round(_t.time() - t0, 1)
                    logger.error("fal_poll_timeout | request_id=%s elapsed=%ss attempts=%d last=%s",
                                 request_id, elapsed, poll_attempts, status_history[-1] if status_history else "?")
                    return None, LIPSYNC_ERR_JOB_TIMEOUT, (
                        f"timeout:{LIPSYNC_TIMEOUT_SEC}s request_id={request_id} "
                        f"attempts={poll_attempts} last_status={status_history[-1] if status_history else '?'} "
                        f"history={','.join(status_history[-5:])} | {payload_ctx}"
                    )

                elapsed = round(_t.time() - t0, 1)
                # `Completed` status carries `error`/`error_type` fields. If
                # fal completed the job with an error (very common with
                # sadtalker — face detection fails etc), surface it cleanly.
                completed_err = getattr(final_status_obj, "error", None)
                completed_err_type = getattr(final_status_obj, "error_type", None)
                if completed_err:
                    detail = (
                        f"provider_completed_with_error:{completed_err_type or '?'}:{str(completed_err)[:300]} "
                        f"request_id={request_id} attempts={poll_attempts} elapsed={elapsed}s | {payload_ctx}"
                    )
                    logger.error("fal_completed_with_error | %s", detail)
                    return None, LIPSYNC_ERR_RENDER_EXCEPTION, detail

                # Fetch the result body.
                try:
                    result = handler.get()
                except Exception as e_g:
                    code, dbg = _classify_fal_exception(e_g, stage="result", request_id=request_id)
                    logger.exception("fal_get_result_failed")
                    return None, code, f"{dbg} attempts={poll_attempts} | {payload_ctx}"

                if not isinstance(result, dict):
                    return None, LIPSYNC_ERR_NO_VIDEO_URL, (
                        f"result_not_dict:{type(result).__name__} request_id={request_id} attempts={poll_attempts} | {payload_ctx}"
                    )
                # Capture which top-level keys fal returned so admins can see
                # the shape on failure (e.g. {"video": {...}} vs {"output_video": ...}).
                result_keys = list(result.keys())
                if progress is not None:
                    progress["final_result_keys"] = result_keys
                # Try the documented sadtalker output shape first, then fall back
                # to other common shapes for sync-lipsync / VEED.
                video_url = None
                if isinstance(result.get("video"), dict):
                    video_url = result["video"].get("url")
                if not video_url and isinstance(result.get("video"), str):
                    video_url = result["video"]
                if not video_url:
                    video_url = result.get("video_url") or result.get("output_video_url")
                if not video_url:
                    return None, LIPSYNC_ERR_NO_VIDEO_URL, (
                        f"missing_video_url result_keys={result_keys} body={str(result)[:300]} "
                        f"request_id={request_id} attempts={poll_attempts} | {payload_ctx}"
                    )
                logger.error("fal_video_ready | request_id=%s elapsed=%ss attempts=%d url=%s",
                             request_id, elapsed, poll_attempts, video_url[:120])
                return video_url, "ok", (
                    f"request_id={request_id} elapsed={elapsed}s attempts={poll_attempts} "
                    f"result_keys={result_keys} | {payload_ctx}"
                )
            except Exception as inner:
                logger.exception("fal_client_unexpected")
                return None, LIPSYNC_ERR_RENDER_EXCEPTION, f"{type(inner).__name__}:{str(inner)[:200]}"
            finally:
                for p in (audio_tmp, image_tmp):
                    if p:
                        try:
                            os.unlink(p)
                        except OSError:
                            pass

        return await asyncio.to_thread(_sync_call)
    except ImportError:
        return None, LIPSYNC_ERR_RENDER_EXCEPTION, "fal_client_not_installed"
    except Exception as e:
        return None, LIPSYNC_ERR_RENDER_EXCEPTION, f"{type(e).__name__}:{str(e)[:200]}"


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
        {"$set": {"video_status": "generating_audio", "job_id": job_id, "updated_at": now_iso()}},
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
        import uuid as _uuid
        storage_path = f"cloneme/audio/{message_id}.mp3"
        _storage._put(storage_path, audio_bytes, "audio/mpeg")
        # CRITICAL: serve_file() at /api/storage/files/{path} only returns
        # bytes when there's a matching row in db.files. Without this insert
        # the path 404s even though the object exists.
        await db.files.insert_one({
            "file_id": _uuid.uuid4().hex,
            "user_id": msg.get("user_id") or "",
            "storage_path": storage_path,
            "content_type": "audio/mpeg",
            "size": len(audio_bytes),
            "purpose": "avatar_chat_audio",
            "is_deleted": False,
        })
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
        # No avatar image → audio-only bubble with explicit error code
        logger.error("lipsync_skip_no_avatar_image | message_id=%s clone_id=%s", message_id, msg.get("clone_id"))
        await db.avatar_chat_messages.update_one(
            {"message_id": message_id},
            {"$set": {"video_status": "completed", "completed_at": now_iso(), "updated_at": now_iso(),
                      "error_code": LIPSYNC_ERR_INVALID_AVATAR_ID,
                      "error_message": "Clone has no avatar image. Audio-only reply.",
                      "lipsync_debug": "no_avatar_image_on_clone"}},
        )
        await db.avatar_generation_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "completed", "stage": "audio_only", "progress_percent": 100,
                      "completed_at": now_iso(), "updated_at": now_iso(),
                      "error_code": LIPSYNC_ERR_INVALID_AVATAR_ID,
                      "lipsync_debug": "no_avatar_image_on_clone"}},
        )
        await _emit("avatar_video_completed", message_id=message_id, metadata={"audio_only": True, "reason": LIPSYNC_ERR_INVALID_AVATAR_ID})
        return

    # We no longer need BACKEND_PUBLIC_URL — both audio and image bytes are
    # uploaded directly to fal.ai's CDN inside _generate_lipsync_video().
    # Just resolve the image URL to whatever the avatar profile stored
    # (either http(s)://… or /api/storage/files/…); _fetch_image_bytes()
    # handles both.
    image_for_fal = avatar_image
    logger.error("lipsync_resolved_urls | message_id=%s image=%s audio_bytes=%d", message_id, (image_for_fal or "")[:200], len(audio_bytes or b""))

    # Pass audio_bytes directly — we'll upload them to fal.ai's CDN inside the
    # helper so production's ephemeral disk (where our local audio file may
    # already be gone by the time fal tries to fetch it) doesn't matter.
    #
    # We run two concurrent tasks:
    # 1) The lipsync helper (writes into a shared `progress` dict as it polls).
    # 2) A "persister" coroutine that copies `progress` → MongoDB every few
    #    seconds so admins / the frontend see real-time `provider_status`,
    #    `poll_attempts`, `last_poll_at`, `provider_request_id`.
    progress: dict = {}

    async def _persist_progress():
        last_snapshot: dict = {}
        while True:
            await asyncio.sleep(2)
            snap = {k: progress.get(k) for k in (
                "provider_request_id", "provider_status", "poll_attempts",
                "last_poll_at", "fal_endpoint", "final_result_keys",
            ) if progress.get(k) is not None}
            if snap != last_snapshot and snap:
                await db.avatar_chat_messages.update_one(
                    {"message_id": message_id}, {"$set": snap}
                )
                last_snapshot = dict(snap)

    persister = asyncio.create_task(_persist_progress())
    try:
        video_provider_url, lipsync_error_code, lipsync_debug = await _generate_lipsync_video(
            image_for_fal, audio_bytes, progress=progress,
        )
    finally:
        persister.cancel()
        # Final flush of whatever progress made it before the helper returned.
        final_snap = {k: progress.get(k) for k in (
            "provider_request_id", "provider_status", "poll_attempts",
            "last_poll_at", "fal_endpoint", "final_result_keys",
        ) if progress.get(k) is not None}
        if final_snap:
            await db.avatar_chat_messages.update_one(
                {"message_id": message_id}, {"$set": final_snap}
            )

    if not video_provider_url:
        logger.error("lipsync_completed_audio_only | message_id=%s code=%s detail=%s", message_id, lipsync_error_code, lipsync_debug)
        # Audio-only completion (graceful degrade) — persist BOTH the
        # canonical error_code (for filtering) AND the verbose lipsync_debug
        # (for admin UI display).
        await db.avatar_chat_messages.update_one(
            {"message_id": message_id},
            {"$set": {"video_status": "completed", "completed_at": now_iso(), "updated_at": now_iso(),
                      "error_code": lipsync_error_code,
                      "error_message": f"Lip-sync failed ({lipsync_error_code}). Audio-only reply.",
                      "lipsync_debug": lipsync_debug,
                      "failure_reason": lipsync_error_code,
                      "video_url_present": False}},
        )
        await db.avatar_generation_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "completed", "stage": "audio_only", "progress_percent": 100,
                      "completed_at": now_iso(), "updated_at": now_iso(),
                      "error_code": lipsync_error_code, "lipsync_debug": lipsync_debug}},
        )
        await _emit("avatar_video_completed", message_id=message_id, metadata={
            "audio_only": True, "reason": lipsync_error_code, "lipsync_debug": lipsync_debug,
        })
        return

    # ---- Download MP4 from provider, persist to object store (db.files) ----
    # Local disk is ephemeral so we mirror the audio fix: write the MP4 to
    # persistent object storage and serve it via /api/storage/files/. This
    # also unblocks production deployments where avatar_videos/ is wiped.
    try:
        import requests
        r = requests.get(video_provider_url, timeout=120)
        r.raise_for_status()
        video_bytes = r.content
        # Best-effort local cache for the legacy /files/{id}/video route.
        try:
            (VIDEO_DIR / f"{message_id}.mp4").write_bytes(video_bytes)
        except OSError:
            pass
        # Persistent storage.
        from urllib.parse import quote as _quote
        import storage as _storage
        import uuid as _uuid
        storage_path = f"cloneme/video/{message_id}.mp4"
        _storage._put(storage_path, video_bytes, "video/mp4")
        await db.files.insert_one({
            "file_id": _uuid.uuid4().hex,
            "user_id": msg.get("user_id") or "",
            "storage_path": storage_path,
            "content_type": "video/mp4",
            "size": len(video_bytes),
            "purpose": "avatar_chat_video",
            "is_deleted": False,
        })
        video_url = f"/api/storage/files/{_quote(storage_path, safe='')}"
        logger.info("video_persisted_to_objstore | message_id=%s url=%s", message_id, video_url)
    except Exception as e:
        logger.warning("Video download/persist failed: %s", e)
        await db.avatar_chat_messages.update_one(
            {"message_id": message_id},
            {"$set": {"video_status": "completed", "completed_at": now_iso(), "updated_at": now_iso(),
                      "error_code": LIPSYNC_ERR_RENDER_EXCEPTION,
                      "error_message": "Video download/persist failed",
                      "lipsync_debug": f"download_failed:{type(e).__name__}:{str(e)[:200]}"}},
        )
        await db.avatar_generation_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "completed", "stage": "audio_only", "progress_percent": 100, "completed_at": now_iso(), "updated_at": now_iso(),
                      "error_code": LIPSYNC_ERR_RENDER_EXCEPTION}},
        )
        return

    await db.avatar_chat_messages.update_one(
        {"message_id": message_id},
        {"$set": {"video_url": video_url, "video_status": "completed",
                  "completed_at": now_iso(), "updated_at": now_iso(),
                  "video_url_present": True,
                  "lipsync_debug": lipsync_debug,
                  "failure_reason": None}},
    )
    await db.avatar_generation_jobs.update_one(
        {"job_id": job_id},
        {"$set": {"video_url": video_url, "status": "completed", "stage": "video", "progress_percent": 100, "completed_at": now_iso(), "updated_at": now_iso()}},
    )
    await _emit("avatar_video_completed", message_id=message_id, metadata={"audio_only": False})


def _public_message(m: dict) -> dict:
    # Surface job_id under both names so older/newer frontends can read it.
    job_id = m.get("job_id") or m.get("video_job_id")
    return {
        "message_id": m.get("message_id"),
        "conversation_id": m.get("conversation_id"),
        "clone_id": m.get("clone_id"),
        "input_text": m.get("input_text"),
        "ai_response_text": m.get("ai_response_text"),
        "reply_text": m.get("ai_response_text"),  # alias for newer clients
        "response_mode": m.get("response_mode") or "avatar_video",
        "audio_url": m.get("audio_url"),
        "video_url": m.get("video_url"),
        "video_url_present": bool(m.get("video_url")),
        "video_status": m.get("video_status") or "queued",
        "video_job_id": job_id,
        # --- Provider diagnostics (iter33: live polling visibility) ---
        "provider_request_id": m.get("provider_request_id"),
        "provider_status": m.get("provider_status"),
        "poll_attempts": m.get("poll_attempts"),
        "last_poll_at": m.get("last_poll_at"),
        "fal_endpoint": m.get("fal_endpoint"),
        "final_result_keys": m.get("final_result_keys"),
        "failure_reason": m.get("failure_reason") or m.get("error_code"),
        "completed_at": m.get("completed_at"),
        # ---
        "error_code": m.get("error_code"),
        "error_message": m.get("error_message"),
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
        "lipsync_endpoint": FAL_LIPSYNC_ENDPOINT,
        "lipsync_model": FAL_LIPSYNC_MODEL or None,
        "lipsync_image_field": FAL_LIPSYNC_IMAGE_FIELD,
        "lipsync_audio_field": FAL_LIPSYNC_AUDIO_FIELD,
        "lipsync_sync_mode": FAL_LIPSYNC_SYNC_MODE or None,
    }


@router.get("/fal-health")
async def fal_health(user: dict = Depends(get_current_user)):
    """Admin probe: validates the configured fal model ID actually exists on
    fal's catalog BEFORE users hit the chat path. Returns 200 with diagnostic
    fields the user can read in the browser.

    The check is HEAD/GET on fal.ai's queue submit endpoint — fal returns
    `Application "<bad>" not found` (404) for invalid model IDs and `Method
    Not Allowed` (405) or a small 4xx for valid ones (because we don't send a
    real payload). 405/422 ≡ model exists; 404 ≡ model id wrong.
    """
    if user.get("role") != "admin":
        raise HTTPException(403, "admin only")
    out = {
        "endpoint": FAL_LIPSYNC_ENDPOINT,
        "image_field": FAL_LIPSYNC_IMAGE_FIELD,
        "audio_field": FAL_LIPSYNC_AUDIO_FIELD,
        "fal_key_present": bool(FAL_KEY),
        "model_exists": None,
        "fal_status": None,
        "fal_body": None,
        "error": None,
    }
    if not FAL_KEY:
        out["error"] = "FAL_KEY not set in env"
        return out
    try:
        import requests
        # fal's queue submit URL. A POST with empty body returns 422 (model
        # exists, args invalid) for valid models and 404 ('Application X not
        # found') for invalid model IDs.
        url = f"https://queue.fal.run/{FAL_LIPSYNC_ENDPOINT}"
        r = requests.post(url, headers={"Authorization": f"Key {FAL_KEY}"}, json={}, timeout=10)
        out["fal_status"] = r.status_code
        out["fal_body"] = r.text[:500]
        out["model_exists"] = r.status_code != 404
    except Exception as e:
        out["error"] = f"{type(e).__name__}:{str(e)[:200]}"
    return out




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


@router.get("/messages")
async def list_recent_messages(
    user: dict = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Recent avatar messages for the current user (newest first).

    Used by clients that want to recover their last few replies without
    knowing a specific conversation_id (e.g. after a hard refresh).
    """
    _require_feature(user)
    rows = await db.avatar_chat_messages.find(
        {"user_id": user["user_id"]}, {"_id": 0},
    ).sort("created_at", -1).limit(limit).to_list(limit)
    return {"messages": [_public_message(r) for r in rows]}


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
