"""Admin-only one-shot to backfill `clones.avatar_url` so the avatar-chat
lipsync pipeline has a publicly fetchable face image for every clone.

Why this exists:
  The avatar-chat pipeline (`/api/avatar-chat/send`) silently degrades to
  audio-only when `clones.avatar_url` is empty (see avatar_chat.py:288).
  In production every clone had `avatar_url=""`, so `fal_client.submit()`
  was never reached — zero usage on the fal.ai dashboard despite
  `lipsync_configured: true`. This endpoint repairs that state.

Behaviour:
  POST /api/admin/avatars/backfill-clones
    ?force=true|false     # default false. When false, only clones with
                          # empty/null avatar_url are touched.

Mapping rules:
  - Slug or display_name matches "praveen" (case-insensitive, with optional
    word boundaries) → https://aiclonechats.com/founder.jpg
  - Everything else → https://i.pravatar.cc/512?img=N where N is derived
    deterministically from clone_id (so re-runs assign the same face).

Response:
  {
    ok: true,
    scanned: int,         # clones inspected
    eligible: int,        # clones that needed update (or all if force=true)
    updated: int,         # writes that succeeded
    skipped_already_set: int,  # only when force=false
    dry_run: false,
    sample: [             # first 30 updates for operator inspection
      {clone_id, slug, display_name, before, after}
    ],
  }

Idempotent: re-running with the same `force` flag is safe. With force=false
the second call is a no-op (everything already filled).
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from db import db
from auth import get_current_user
from credits import is_admin_unlimited_user
from models import now_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/avatars", tags=["admin-avatars"])

PRAVEEN_AVATAR_URL = "https://aiclonechats.com/founder.jpg"
# pravatar.cc serves img=1..70; pick from a curated subset that visually
# work well for avatar-chat (front-facing, single subject).
_PRAVATAR_POOL = [
    14, 22, 33, 47, 51, 58, 65, 68, 11, 12, 13, 25, 26, 28, 30,
    36, 37, 41, 42, 45, 49, 53, 54, 57, 60, 61, 63, 66, 67, 69,
]


async def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Same shape as admin_chats._require_admin — keep the bar local so this
    module doesn't depend on admin_chats internals."""
    if is_admin_unlimited_user(user):
        return user
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def _is_praveen_clone(clone: dict) -> bool:
    """Match the founder's headline clone by slug OR display_name.

    We intentionally do NOT match by owner user_id because the canonical
    "Praveen" clone may live under a system user account, and we want this
    to find it regardless. The slug + display_name pair is the human
    contract; owner_id is an internal detail.
    """
    slug = (clone.get("slug") or "").lower().strip()
    name = (clone.get("display_name") or "").lower().strip()
    if slug == "praveen":
        return True
    # Match common variants: "Praveen", "Praveen AI", "Raja Praveen", etc.
    # Use word-boundary check (split on whitespace) so we don't match
    # "Improv" or other clones with "praveen" as a substring.
    name_words = name.replace("-", " ").split()
    return "praveen" in name_words


def _placeholder_for(clone_id: str) -> str:
    """Pick a pravatar.cc image deterministically by clone_id so re-runs
    don't shuffle faces around (which would otherwise feel like silent
    breakage on the user's UI)."""
    h = hashlib.sha256(clone_id.encode("utf-8")).digest()
    idx = h[0] % len(_PRAVATAR_POOL)
    return f"https://i.pravatar.cc/512?img={_PRAVATAR_POOL[idx]}"


@router.post("/backfill-clones")
async def backfill_clone_avatars(
    force: bool = Query(default=False),
    dry_run: bool = Query(default=False),
    _admin: dict = Depends(_require_admin),
):
    """Backfill `clones.avatar_url`. See module docstring for full contract.

    Args:
      force: When True, overwrite avatar_url on ALL clones (use this only
             when explicitly rotating placeholders). When False (default),
             only touch clones whose avatar_url is empty / null / missing.
      dry_run: Report what would change without writing.
    """
    scanned = 0
    eligible: list[dict] = []
    skipped_already_set = 0

    cursor = db.clones.find(
        {},
        {"_id": 0, "clone_id": 1, "slug": 1, "display_name": 1, "avatar_url": 1, "user_id": 1},
    )
    async for c in cursor:
        scanned += 1
        existing = (c.get("avatar_url") or "").strip()
        if existing and not force:
            skipped_already_set += 1
            continue
        new_url = PRAVEEN_AVATAR_URL if _is_praveen_clone(c) else _placeholder_for(c["clone_id"])
        if existing == new_url:
            # Force=true but already at the deterministic target — no-op.
            skipped_already_set += 1
            continue
        eligible.append({
            "clone_id": c["clone_id"],
            "slug": c.get("slug"),
            "display_name": c.get("display_name"),
            "before": existing or None,
            "after": new_url,
        })

    updated = 0
    if not dry_run:
        for row in eligible:
            r = await db.clones.update_one(
                {"clone_id": row["clone_id"]},
                {"$set": {"avatar_url": row["after"], "updated_at": now_iso()}},
            )
            if r.modified_count > 0:
                updated += 1
        if updated:
            logger.info(
                "avatars_backfill: scanned=%d eligible=%d updated=%d force=%s actor=%s",
                scanned, len(eligible), updated, force, _admin.get("email"),
            )

    return {
        "ok": True,
        "dry_run": dry_run,
        "force": force,
        "scanned": scanned,
        "eligible": len(eligible),
        "updated": updated if not dry_run else 0,
        "skipped_already_set": skipped_already_set,
        "sample": eligible[:30],
        "praveen_url": PRAVEEN_AVATAR_URL,
    }
