"""Face-detection preflight utility.

Purpose: prevent `fal-ai/sadtalker` `RENDER_EXCEPTION: No face detected` at
runtime by validating avatar images at upload time. Also used by the admin
audit endpoint to sweep existing clones and flag faceless avatars.

Design notes:
  - Uses OpenCV Haar cascade (frontal + profile). Fast, dependency-light,
    good enough for a preflight signal. False-negative rate is acceptable
    because we render this as a *soft warning* on upload — the user can
    proceed. Video Avatar Chat is hard-gated on the persisted
    `face_detected` flag so users are not blocked from other flows.
  - HEIC input goes through pillow-heif → JPEG conversion before OpenCV,
    since OpenCV can't read HEIC directly.
  - Bytes input, not paths — we never write user images to disk here.
"""
from __future__ import annotations

import io
import logging
import os
from typing import Optional, TypedDict

import cv2
import numpy as np
from PIL import Image

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

_FRONTAL = cv2.CascadeClassifier(
    os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"),
)
_PROFILE = cv2.CascadeClassifier(
    os.path.join(cv2.data.haarcascades, "haarcascade_profileface.xml"),
)

# Minimum absolute face size in pixels. Anything smaller is almost certainly
# noise on a large image and sadtalker will fail on it anyway.
_MIN_FACE_PX = 60


class FaceCheck(TypedDict):
    has_face: bool
    face_count: int
    largest_face_ratio: float  # 0..1 face area / image area
    image_width: int
    image_height: int
    detector: str  # "frontal" | "profile" | "none"
    reason: Optional[str]


def _decode_to_bgr(data: bytes) -> Optional[np.ndarray]:
    """Decode arbitrary image bytes → OpenCV BGR ndarray.

    Falls back through Pillow (which handles HEIC via pillow-heif) if
    OpenCV's own decoder rejects the bytes.
    """
    # First try OpenCV (fast path for JPEG/PNG/WebP).
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is not None:
        return img
    # Fallback: Pillow decode → BGR ndarray.
    try:
        pil = Image.open(io.BytesIO(data))
        pil = pil.convert("RGB")
        rgb = np.array(pil)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception as e:
        logger.warning("face_detect: decode failed: %s", e)
        return None


def detect_face(data: bytes) -> FaceCheck:
    """Return face-detection summary for the given image bytes.

    Never raises. On unreadable images returns has_face=False with a reason.
    """
    img = _decode_to_bgr(data)
    if img is None:
        return {
            "has_face": False,
            "face_count": 0,
            "largest_face_ratio": 0.0,
            "image_width": 0,
            "image_height": 0,
            "detector": "none",
            "reason": "unreadable_image",
        }
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)

    def _run(cascade: cv2.CascadeClassifier) -> np.ndarray:
        # scaleFactor / minNeighbors tuned for headshots at 300-2000px.
        return cascade.detectMultiScale(
            gray,
            scaleFactor=1.15,
            minNeighbors=5,
            minSize=(_MIN_FACE_PX, _MIN_FACE_PX),
        )

    detector = "frontal"
    faces = _run(_FRONTAL)
    if len(faces) == 0:
        faces = _run(_PROFILE)
        detector = "profile" if len(faces) else "none"

    face_count = int(len(faces))
    if face_count == 0:
        return {
            "has_face": False,
            "face_count": 0,
            "largest_face_ratio": 0.0,
            "image_width": w,
            "image_height": h,
            "detector": "none",
            "reason": "no_face_detected",
        }

    # Largest face area / image area — useful signal for "face too small".
    largest = max((fw * fh) for (_, _, fw, fh) in faces)
    ratio = float(largest) / float(max(1, w * h))
    return {
        "has_face": True,
        "face_count": face_count,
        "largest_face_ratio": round(ratio, 4),
        "image_width": w,
        "image_height": h,
        "detector": detector,
        "reason": None,
    }


def detect_face_from_url(url: str, timeout: int = 15) -> FaceCheck:
    """Fetch a public URL and run detect_face on it.

    Used by the admin audit sweep. Returns unreadable_image on network/HTTP
    failures rather than raising, so the sweep never aborts mid-batch.
    """
    import requests
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return detect_face(r.content)
    except Exception as e:
        logger.warning("face_detect: fetch failed for %s: %s", url, e)
        return {
            "has_face": False,
            "face_count": 0,
            "largest_face_ratio": 0.0,
            "image_width": 0,
            "image_height": 0,
            "detector": "none",
            "reason": f"fetch_failed:{type(e).__name__}",
        }
