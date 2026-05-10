"""
AI Debate Rooms — Phase 1.

Public debate rooms with AI scoring + crowd voting + live ranking.

Architecture mirror of Anonymous Reality:
- Same domain, route-based (/api/debates/...)
- Polling-based realtime (no WS)
- Strict analytics separation (every event tagged metadata.experience_variant="debate_v1")
- AI scoring via Emergent LLM key (Claude Sonnet 4.5)
- Auth: optional. Anonymous users can browse + vote, but submitting an argument
  requires a logged-in account (so debate_participants has a stable id).

Operator constraints:
- 8 seeded debates
- AI is the JUDGE (final_score) but the CROWD is the JURY (votes)
- Civility cap: civility < 40 caps final_score at 40
- One vote per user per argument; users can change vote
- Hidden arguments are returned but content is masked
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from db import db
from auth import get_current_user, get_optional_user
from models import now_iso
import debates_scoring as scoring_svc
from debates_seed import DEBATES as SEED_DEBATES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/debates", tags=["debates"])
admin_router = APIRouter(prefix="/api/admin/debates", tags=["debates-admin"])

EXPERIENCE_VARIANT = "debate_v1"
MAX_ARGUMENT_LEN = 4000
MIN_ARGUMENT_LEN = 10
RATE_LIMIT_ARGS_PER_HOUR = 30
DEFAULT_DURATION_DAYS = 7


# ---------- Models ----------
class CreateDebateRequest(BaseModel):
    title: str = Field(min_length=8, max_length=200)
    description: str = Field(min_length=10, max_length=1000)
    category: str = Field(default="general", max_length=50)
    side_a_label: str = Field(default="For", min_length=1, max_length=40)
    side_b_label: str = Field(default="Against", min_length=1, max_length=40)
    duration_days: int = Field(default=DEFAULT_DURATION_DAYS, ge=1, le=30)


class JoinSideRequest(BaseModel):
    side: str  # "A" | "B"


class SubmitArgumentRequest(BaseModel):
    side: str  # "A" | "B"
    content: str = Field(min_length=MIN_ARGUMENT_LEN, max_length=MAX_ARGUMENT_LEN)


class VoteRequest(BaseModel):
    vote_type: str  # "up" | "down" | "clear"


class ReportRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class TrackRequest(BaseModel):
    event_name: str
    metadata: Optional[dict] = None


class AdminAction(BaseModel):
    reason: Optional[str] = None
    is_featured: Optional[bool] = None
    status: Optional[str] = None  # active | ended | hidden
    moderation_status: Optional[str] = None  # visible | hidden | flagged


# ---------- Helpers ----------
def _public_debate(d: dict) -> dict:
    return {
        "debate_id": d.get("debate_id"),
        "slug": d.get("slug"),
        "title": d.get("title"),
        "description": d.get("description"),
        "category": d.get("category"),
        "status": d.get("status", "active"),
        "side_a_label": d.get("side_a_label", "For"),
        "side_b_label": d.get("side_b_label", "Against"),
        "is_featured": bool(d.get("is_featured", False)),
        "is_public": d.get("is_public", True),
        "starts_at": d.get("starts_at"),
        "ends_at": d.get("ends_at"),
        "participant_count": int(d.get("participant_count", 0) or 0),
        "argument_count": int(d.get("argument_count", 0) or 0),
        "vote_count": int(d.get("vote_count", 0) or 0),
        "winner_side": d.get("winner_side"),
    }


def _public_argument(a: dict, my_user_id: Optional[str] = None, my_vote: Optional[str] = None) -> dict:
    is_hidden = a.get("moderation_status") == "hidden"
    return {
        "argument_id": a.get("argument_id"),
        "debate_id": a.get("debate_id"),
        "side": a.get("side"),
        "anonymous_handle": a.get("anonymous_handle"),
        "is_mine": bool(my_user_id and a.get("user_id") == my_user_id),
        "content": "[hidden by moderation]" if is_hidden else (a.get("content") or ""),
        "ai_score": int(a.get("ai_score") or 0),
        "ai_score_breakdown": a.get("ai_score_breakdown") or {},
        "ai_feedback": a.get("ai_feedback") or "",
        "vote_count": int(a.get("vote_count") or 0),
        "upvotes": int(a.get("upvotes") or 0),
        "downvotes": int(a.get("downvotes") or 0),
        "rank_score": float(a.get("rank_score") or 0.0),
        "moderation_status": a.get("moderation_status") or "visible",
        "created_at": a.get("created_at"),
        "my_vote": my_vote,
    }


def _gen_anonymous_handle(user_id: str) -> str:
    """Stable per-user-per-debate handle. Computed at join time."""
    import random
    rng = random.Random(user_id)
    adjs = ["Sharp", "Clear", "Bold", "Steady", "Quiet", "Bright", "Patient", "Wild", "Honest", "Wry"]
    nouns = ["Falcon", "Raven", "Tide", "Forge", "Spark", "Storm", "River", "Pine", "Ember", "Anchor"]
    return f"{rng.choice(adjs)}{rng.choice(nouns)}{rng.randint(10, 99)}"


async def _emit(event_name: str, *, debate_id: Optional[str] = None, argument_id: Optional[str] = None, user_id: Optional[str] = None, metadata: Optional[dict] = None) -> None:
    await db.debate_analytics_events.insert_one({
        "event_id": uuid.uuid4().hex,
        "event_name": event_name,
        "debate_id": debate_id,
        "argument_id": argument_id,
        "user_id": user_id,
        "metadata": {**(metadata or {}), "experience_variant": EXPERIENCE_VARIANT},
        "created_at": now_iso(),
    })


def _rank_score(ai_score: int, upvotes: int, downvotes: int, participation_bonus: int = 0) -> float:
    return round((ai_score * 0.65) + (upvotes * 3) - (downvotes * 2) + participation_bonus, 2)


async def _recompute_argument_rank(argument_id: str) -> None:
    a = await db.debate_arguments.find_one({"argument_id": argument_id}, {"_id": 0, "ai_score": 1, "upvotes": 1, "downvotes": 1})
    if not a:
        return
    rs = _rank_score(int(a.get("ai_score") or 0), int(a.get("upvotes") or 0), int(a.get("downvotes") or 0))
    await db.debate_arguments.update_one({"argument_id": argument_id}, {"$set": {"rank_score": rs}})


async def seed_debates_if_needed() -> None:
    existing = await db.debate_rooms.count_documents({})
    if existing >= len(SEED_DEBATES):
        return
    now = datetime.now(timezone.utc)
    for s in SEED_DEBATES:
        if await db.debate_rooms.find_one({"slug": s["slug"]}, {"_id": 1}):
            continue
        await db.debate_rooms.insert_one({
            "debate_id": f"db_{uuid.uuid4().hex[:14]}",
            "slug": s["slug"],
            "title": s["title"],
            "description": s["description"],
            "category": s.get("category", "general"),
            "status": "active",
            "side_a_label": s.get("side_a_label", "For"),
            "side_b_label": s.get("side_b_label", "Against"),
            "created_by_user_id": None,
            "is_featured": s.get("is_featured", False),
            "is_public": True,
            "moderation_status": "visible",
            "participant_count": 0,
            "argument_count": 0,
            "vote_count": 0,
            "winner_side": None,
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(days=DEFAULT_DURATION_DAYS)).isoformat(),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        })
    logger.info("Seeded debate rooms")


async def _check_arg_rate_limit(user_id: str) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    count = await db.debate_arguments.count_documents({"user_id": user_id, "created_at": {"$gt": cutoff}})
    if count >= RATE_LIMIT_ARGS_PER_HOUR:
        raise HTTPException(429, "You're submitting arguments too fast. Slow down.")


# ---------- Public read ----------
@router.get("")
async def list_debates(category: Optional[str] = Query(default=None), status_q: str = Query(default="active", alias="status"), limit: int = Query(default=50, ge=1, le=100)):
    await seed_debates_if_needed()
    q: dict = {"is_public": True, "moderation_status": {"$ne": "hidden"}}
    if status_q and status_q != "all":
        q["status"] = status_q
    if category:
        q["category"] = category
    cursor = db.debate_rooms.find(q, {"_id": 0}).sort([("is_featured", -1), ("argument_count", -1), ("created_at", -1)]).limit(limit)
    rows = [_public_debate(r) for r in await cursor.to_list(limit)]
    return {"debates": rows}


@router.get("/{slug}")
async def get_debate(slug: str, user: Optional[dict] = Depends(get_optional_user)):
    d = await db.debate_rooms.find_one({"slug": slug, "is_public": True}, {"_id": 0})
    if not d or d.get("moderation_status") == "hidden":
        raise HTTPException(404, "Debate not found")
    out = _public_debate(d)
    # User-specific: have they joined? what side?
    my_side = None
    my_handle = None
    if user:
        p = await db.debate_participants.find_one({"debate_id": d["debate_id"], "user_id": user["user_id"]}, {"_id": 0, "side": 1, "anonymous_name": 1})
        if p:
            my_side = p.get("side")
            my_handle = p.get("anonymous_name")
    out["my_side"] = my_side
    out["my_handle"] = my_handle
    return out


@router.post("/{slug}/join")
async def join_debate(slug: str, payload: JoinSideRequest, user: dict = Depends(get_current_user)):
    side = (payload.side or "").upper()
    if side not in ("A", "B"):
        raise HTTPException(400, "Invalid side")
    d = await db.debate_rooms.find_one({"slug": slug, "is_public": True}, {"_id": 0})
    if not d or d.get("status") != "active":
        raise HTTPException(404, "Debate not active")
    existing = await db.debate_participants.find_one({"debate_id": d["debate_id"], "user_id": user["user_id"]}, {"_id": 0})
    if existing:
        # Idempotent: same side returns existing; switching sides forbidden after first argument
        if existing.get("side") == side:
            return {"ok": True, "side": side, "anonymous_handle": existing.get("anonymous_name")}
        if int(existing.get("argument_count") or 0) > 0:
            raise HTTPException(409, "You can't switch sides after submitting an argument.")
        await db.debate_participants.update_one(
            {"debate_id": d["debate_id"], "user_id": user["user_id"]},
            {"$set": {"side": side, "updated_at": now_iso()}},
        )
        return {"ok": True, "side": side, "anonymous_handle": existing.get("anonymous_name")}
    handle = _gen_anonymous_handle(user["user_id"])
    await db.debate_participants.insert_one({
        "participant_id": f"dp_{uuid.uuid4().hex[:14]}",
        "debate_id": d["debate_id"],
        "user_id": user["user_id"],
        "anonymous_name": handle,
        "side": side,
        "joined_at": now_iso(),
        "argument_count": 0,
        "total_score": 0,
        "total_votes": 0,
    })
    await db.debate_rooms.update_one({"debate_id": d["debate_id"]}, {"$inc": {"participant_count": 1}})
    await _emit("debate_joined", debate_id=d["debate_id"], user_id=user["user_id"], metadata={"side": side, "slug": slug})
    return {"ok": True, "side": side, "anonymous_handle": handle}


@router.get("/{slug}/arguments")
async def list_arguments(slug: str, side: Optional[str] = Query(default=None), sort: str = Query(default="rank"), limit: int = Query(default=50, ge=1, le=200), user: Optional[dict] = Depends(get_optional_user)):
    d = await db.debate_rooms.find_one({"slug": slug}, {"_id": 0, "debate_id": 1, "is_public": 1, "moderation_status": 1})
    if not d or not d.get("is_public") or d.get("moderation_status") == "hidden":
        raise HTTPException(404, "Debate not found")
    q: dict = {"debate_id": d["debate_id"], "moderation_status": {"$in": ["visible", "flagged"]}}
    if side and side.upper() in ("A", "B"):
        q["side"] = side.upper()
    sort_spec = [("rank_score", -1), ("created_at", -1)] if sort == "rank" else [("created_at", -1)]
    cursor = db.debate_arguments.find(q, {"_id": 0, "raw_model_response": 0}).sort(sort_spec).limit(limit)
    rows = await cursor.to_list(limit)
    my_votes_map: dict = {}
    if user and rows:
        ids = [r["argument_id"] for r in rows]
        votes = await db.debate_votes.find({"user_id": user["user_id"], "argument_id": {"$in": ids}}, {"_id": 0, "argument_id": 1, "vote_type": 1}).to_list(len(ids))
        my_votes_map = {v["argument_id"]: v["vote_type"] for v in votes}
    return {"arguments": [_public_argument(r, my_user_id=user["user_id"] if user else None, my_vote=my_votes_map.get(r["argument_id"])) for r in rows]}


@router.post("/{slug}/arguments")
async def submit_argument(slug: str, payload: SubmitArgumentRequest, user: dict = Depends(get_current_user)):
    side = (payload.side or "").upper()
    if side not in ("A", "B"):
        raise HTTPException(400, "Invalid side")
    d = await db.debate_rooms.find_one({"slug": slug}, {"_id": 0})
    if not d or d.get("status") != "active" or d.get("moderation_status") == "hidden":
        raise HTTPException(404, "Debate not active")
    # Auto-join if not already a participant
    p = await db.debate_participants.find_one({"debate_id": d["debate_id"], "user_id": user["user_id"]}, {"_id": 0})
    if not p:
        handle = _gen_anonymous_handle(user["user_id"])
        p = {
            "participant_id": f"dp_{uuid.uuid4().hex[:14]}",
            "debate_id": d["debate_id"],
            "user_id": user["user_id"],
            "anonymous_name": handle,
            "side": side,
            "joined_at": now_iso(),
            "argument_count": 0,
            "total_score": 0,
            "total_votes": 0,
        }
        await db.debate_participants.insert_one(p)
        await db.debate_rooms.update_one({"debate_id": d["debate_id"]}, {"$inc": {"participant_count": 1}})
    elif p.get("side") != side:
        raise HTTPException(409, "You're on the other side of this debate.")

    await _check_arg_rate_limit(user["user_id"])

    # Score with AI (always returns a stable dict — never raises)
    side_label = d["side_a_label"] if side == "A" else d["side_b_label"]
    score = await scoring_svc.score_argument(payload.content, d["title"], side, side_label)

    argument_id = f"da_{uuid.uuid4().hex[:14]}"
    rs = _rank_score(int(score["final_score"]), 0, 0)
    breakdown = {
        "clarity": score["clarity"],
        "logic": score["logic"],
        "evidence": score["evidence"],
        "originality": score["originality"],
        "civility": score["civility"],
        "persuasiveness": score["persuasiveness"],
    }
    arg_doc = {
        "argument_id": argument_id,
        "debate_id": d["debate_id"],
        "user_id": user["user_id"],
        "participant_id": p["participant_id"],
        "anonymous_handle": p["anonymous_name"],
        "side": side,
        "content": (payload.content or "").strip(),
        "ai_score": int(score["final_score"]),
        "ai_score_breakdown": breakdown,
        "ai_feedback": score["feedback"],
        "vote_count": 0,
        "upvotes": 0,
        "downvotes": 0,
        "rank_score": rs,
        "moderation_status": "visible" if score["moderation_status"] == "ok" else "hidden",
        "moderation_reason": score["moderation_reason"],
        "scoring_version": score["scoring_version"],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.debate_arguments.insert_one(arg_doc)
    await db.debate_score_events.insert_one({
        "score_event_id": uuid.uuid4().hex,
        "debate_id": d["debate_id"],
        "argument_id": argument_id,
        "raw_model_response": score.get("raw_model_response", ""),
        "parsed_score": {**breakdown, "final_score": int(score["final_score"])},
        "scoring_version": score["scoring_version"],
        "created_at": now_iso(),
    })
    await db.debate_rooms.update_one({"debate_id": d["debate_id"]}, {"$inc": {"argument_count": 1}, "$set": {"updated_at": now_iso()}})
    await db.debate_participants.update_one(
        {"participant_id": p["participant_id"]},
        {"$inc": {"argument_count": 1, "total_score": int(score["final_score"])}, "$set": {"updated_at": now_iso()}},
    )
    await _emit("debate_argument_submitted", debate_id=d["debate_id"], argument_id=argument_id, user_id=user["user_id"], metadata={"side": side, "slug": slug, "score": int(score["final_score"]), "moderation": arg_doc["moderation_status"]})
    await _emit("debate_argument_scored", debate_id=d["debate_id"], argument_id=argument_id, user_id=user["user_id"], metadata={"final_score": int(score["final_score"]), "civility": breakdown["civility"]})
    return {"argument": _public_argument(arg_doc, my_user_id=user["user_id"], my_vote=None)}


@router.post("/{slug}/track")
async def track(slug: str, payload: TrackRequest, user: Optional[dict] = Depends(get_optional_user)):
    d = await db.debate_rooms.find_one({"slug": slug}, {"_id": 0, "debate_id": 1})
    debate_id = d["debate_id"] if d else None
    await _emit(payload.event_name, debate_id=debate_id, user_id=(user or {}).get("user_id"), metadata={**(payload.metadata or {}), "slug": slug})
    return {"ok": True}


@router.get("/{slug}/leaderboard")
async def leaderboard(slug: str):
    d = await db.debate_rooms.find_one({"slug": slug}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Debate not found")
    debate_id = d["debate_id"]
    # Per-side stats
    out_sides = {}
    for side_key in ("A", "B"):
        cursor = db.debate_arguments.find(
            {"debate_id": debate_id, "side": side_key, "moderation_status": "visible"},
            {"_id": 0, "argument_id": 1, "rank_score": 1, "ai_score": 1, "upvotes": 1, "downvotes": 1, "anonymous_handle": 1, "content": 1, "created_at": 1},
        ).sort("rank_score", -1).limit(10)
        top = await cursor.to_list(10)
        side_score = 0.0
        if top:
            avg_top = sum(float(r.get("rank_score") or 0) for r in top) / len(top)
            side_score += avg_top
        votes_total = await db.debate_votes.count_documents({"debate_id": debate_id, "vote_type": "up"})
        side_score += votes_total * 0.1
        participants = await db.debate_participants.count_documents({"debate_id": debate_id, "side": side_key})
        side_score += participants * 0.5
        out_sides[side_key] = {
            "label": d.get(f"side_{side_key.lower()}_label"),
            "side_score": round(side_score, 2),
            "participants": participants,
            "top_arguments": [
                {
                    "argument_id": r["argument_id"],
                    "anonymous_handle": r.get("anonymous_handle"),
                    "rank_score": float(r.get("rank_score") or 0),
                    "ai_score": int(r.get("ai_score") or 0),
                    "upvotes": int(r.get("upvotes") or 0),
                    "downvotes": int(r.get("downvotes") or 0),
                    "content_preview": (r.get("content") or "")[:240],
                    "created_at": r.get("created_at"),
                }
                for r in top
            ],
        }
    leading = "A" if out_sides["A"]["side_score"] > out_sides["B"]["side_score"] else ("B" if out_sides["B"]["side_score"] > out_sides["A"]["side_score"] else None)
    return {"slug": slug, "title": d.get("title"), "status": d.get("status"), "leading_side": leading, "sides": out_sides, "generated_at": now_iso()}


@router.get("/{slug}/results")
async def results(slug: str):
    d = await db.debate_rooms.find_one({"slug": slug}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Debate not found")
    lb = await leaderboard(slug)
    # Determine winner: explicit winner_side OR side with higher side_score
    winner = d.get("winner_side") or lb.get("leading_side")
    winner_label = d.get(f"side_{winner.lower()}_label") if winner else None
    return {**lb, "winner_side": winner, "winner_label": winner_label, "ended": d.get("status") == "ended", "ends_at": d.get("ends_at")}


@router.post("/arguments/{argument_id}/vote")
async def vote_argument(argument_id: str, payload: VoteRequest, user: dict = Depends(get_current_user)):
    vt = (payload.vote_type or "").lower()
    if vt not in ("up", "down", "clear"):
        raise HTTPException(400, "Invalid vote_type")
    arg = await db.debate_arguments.find_one({"argument_id": argument_id}, {"_id": 0})
    if not arg or arg.get("moderation_status") == "hidden":
        raise HTTPException(404, "Argument not found")
    # No self-voting
    if arg.get("user_id") == user["user_id"]:
        raise HTTPException(403, "You can't vote on your own argument.")
    existing = await db.debate_votes.find_one({"argument_id": argument_id, "user_id": user["user_id"]}, {"_id": 0})
    inc: dict = {}
    new_vote = None
    if vt == "clear":
        if not existing:
            return {"ok": True, "my_vote": None}
        if existing["vote_type"] == "up":
            inc["upvotes"] = -1
        else:
            inc["downvotes"] = -1
        inc["vote_count"] = -1
        await db.debate_votes.delete_one({"argument_id": argument_id, "user_id": user["user_id"]})
    else:
        new_vote = vt
        if existing:
            if existing["vote_type"] == vt:
                return {"ok": True, "my_vote": vt}
            # Switching: subtract old, add new
            if existing["vote_type"] == "up":
                inc["upvotes"] = -1
            else:
                inc["downvotes"] = -1
            await db.debate_votes.update_one(
                {"argument_id": argument_id, "user_id": user["user_id"]},
                {"$set": {"vote_type": vt, "updated_at": now_iso()}},
            )
        else:
            inc["vote_count"] = 1
            await db.debate_votes.insert_one({
                "vote_id": uuid.uuid4().hex,
                "debate_id": arg["debate_id"],
                "argument_id": argument_id,
                "user_id": user["user_id"],
                "vote_type": vt,
                "created_at": now_iso(),
            })
            await db.debate_rooms.update_one({"debate_id": arg["debate_id"]}, {"$inc": {"vote_count": 1}})
        if vt == "up":
            inc["upvotes"] = inc.get("upvotes", 0) + 1
        else:
            inc["downvotes"] = inc.get("downvotes", 0) + 1
    if inc:
        await db.debate_arguments.update_one({"argument_id": argument_id}, {"$inc": inc, "$set": {"updated_at": now_iso()}})
    await _recompute_argument_rank(argument_id)
    await _emit("debate_vote_clicked", debate_id=arg["debate_id"], argument_id=argument_id, user_id=user["user_id"], metadata={"vote_type": vt})
    return {"ok": True, "my_vote": new_vote}


@router.post("/arguments/{argument_id}/report")
async def report_argument(argument_id: str, payload: ReportRequest, user: dict = Depends(get_current_user)):
    arg = await db.debate_arguments.find_one({"argument_id": argument_id}, {"_id": 0, "argument_id": 1, "debate_id": 1})
    if not arg:
        raise HTTPException(404, "Argument not found")
    await db.debate_reports.insert_one({
        "report_id": uuid.uuid4().hex,
        "debate_id": arg["debate_id"],
        "argument_id": argument_id,
        "user_id": user["user_id"],
        "reason": (payload.reason or "")[:500],
        "status": "open",
        "created_at": now_iso(),
    })
    await db.debate_arguments.update_one({"argument_id": argument_id}, {"$set": {"moderation_status": "flagged"}})
    await _emit("debate_report_submitted", debate_id=arg["debate_id"], argument_id=argument_id, user_id=user["user_id"], metadata={"reason_len": len(payload.reason or "")})
    return {"ok": True}


# ---------- Admin ----------
async def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


@admin_router.get("")
async def admin_list_debates(_admin: dict = Depends(_require_admin), limit: int = Query(default=200, ge=1, le=500)):
    cursor = db.debate_rooms.find({}, {"_id": 0}).sort("updated_at", -1).limit(limit)
    return {"debates": [_public_debate(r) for r in await cursor.to_list(limit)]}


@admin_router.post("")
async def admin_create_debate(payload: CreateDebateRequest, admin: dict = Depends(_require_admin)):
    slug = (payload.title.lower().strip()
            .replace(" ", "-").replace("?", "").replace("!", "").replace(".", "")
            .replace(",", "").replace("'", "").replace('"', ""))[:80]
    if await db.debate_rooms.find_one({"slug": slug}, {"_id": 1}):
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc)
    doc = {
        "debate_id": f"db_{uuid.uuid4().hex[:14]}",
        "slug": slug,
        "title": payload.title,
        "description": payload.description,
        "category": payload.category,
        "status": "active",
        "side_a_label": payload.side_a_label,
        "side_b_label": payload.side_b_label,
        "created_by_user_id": admin["user_id"],
        "is_featured": False,
        "is_public": True,
        "moderation_status": "visible",
        "participant_count": 0,
        "argument_count": 0,
        "vote_count": 0,
        "winner_side": None,
        "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(days=payload.duration_days)).isoformat(),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    await db.debate_rooms.insert_one(doc)
    return {"debate": _public_debate(doc)}


@admin_router.patch("/{debate_id}")
async def admin_update_debate(debate_id: str, payload: AdminAction, _admin: dict = Depends(_require_admin)):
    upd: dict = {"updated_at": now_iso()}
    if payload.is_featured is not None:
        upd["is_featured"] = bool(payload.is_featured)
    if payload.status in ("active", "ended", "hidden"):
        upd["status"] = payload.status
    if payload.moderation_status in ("visible", "hidden", "flagged"):
        upd["moderation_status"] = payload.moderation_status
    res = await db.debate_rooms.update_one({"debate_id": debate_id}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(404, "Debate not found")
    return {"ok": True}


@admin_router.patch("/arguments/{argument_id}")
async def admin_moderate_argument(argument_id: str, payload: AdminAction, admin: dict = Depends(_require_admin)):
    upd: dict = {"updated_at": now_iso()}
    if payload.moderation_status in ("visible", "hidden", "flagged"):
        upd["moderation_status"] = payload.moderation_status
    if not upd:
        raise HTTPException(400, "Nothing to update")
    res = await db.debate_arguments.update_one({"argument_id": argument_id}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(404, "Argument not found")
    await db.debate_admin_actions.insert_one({"action_id": uuid.uuid4().hex, "type": "moderate_argument", "target": argument_id, "admin": admin["email"], "reason": payload.reason or "", "moderation_status": payload.moderation_status, "created_at": now_iso()})
    return {"ok": True}


@admin_router.get("/reports")
async def admin_reports(_admin: dict = Depends(_require_admin), status_q: str = Query(default="open", alias="status"), limit: int = Query(default=100, ge=1, le=500)):
    cursor = db.debate_reports.find({"status": status_q}, {"_id": 0}).sort("created_at", -1).limit(limit)
    rows = await cursor.to_list(limit)
    out = []
    for r in rows:
        a = await db.debate_arguments.find_one({"argument_id": r["argument_id"]}, {"_id": 0})
        out.append({**r, "argument": _public_argument(a) if a else None})
    return {"reports": out}


@admin_router.get("/metrics")
async def admin_metrics(_admin: dict = Depends(_require_admin), days: int = Query(default=7, ge=1, le=90)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    debates_total = await db.debate_rooms.count_documents({})
    debates_active = await db.debate_rooms.count_documents({"status": "active"})
    args_total = await db.debate_arguments.count_documents({"created_at": {"$gte": since}})
    args_visible = await db.debate_arguments.count_documents({"created_at": {"$gte": since}, "moderation_status": "visible"})
    args_hidden = await db.debate_arguments.count_documents({"created_at": {"$gte": since}, "moderation_status": "hidden"})
    votes = await db.debate_votes.count_documents({"created_at": {"$gte": since}})
    participants = await db.debate_participants.count_documents({"joined_at": {"$gte": since}})
    reports = await db.debate_reports.count_documents({"created_at": {"$gte": since}})
    return {
        "window_days": days,
        "debates_total": debates_total,
        "debates_active": debates_active,
        "arguments_total": args_total,
        "arguments_visible": args_visible,
        "arguments_hidden": args_hidden,
        "votes": votes,
        "participants_joined": participants,
        "reports": reports,
        "hidden_rate_pct": round(100 * args_hidden / max(1, args_total), 1),
    }



# ---------- Retention / Behavioral Observability (read-only) ----------
# Operator note: this is INSTRUMENTATION, not product expansion.
# Aggregates only over EXISTING collections + analytics_events.
# Used to test the operator's behavioral hypotheses (intellectual / tribal /
# performative). NO scaffolding that would shape behavior.
@admin_router.get("/retention")
async def admin_retention(_admin: dict = Depends(_require_admin), days: int = Query(default=14, ge=1, le=90)):
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    since_24h = (now - timedelta(days=1)).isoformat()

    # ----- The 5 funnel ratios -----
    # We emit `debate_list_viewed`, `debate_room_opened`, `debate_joined`,
    # `debate_argument_submitted`, `debate_vote_clicked`. Compute distinct-user
    # counts at each stage so the ratios reflect humans, not pageviews.
    async def _distinct_users(event_name: str, since_iso: str) -> int:
        pipeline = [
            {"$match": {"event_name": event_name, "created_at": {"$gte": since_iso}, "user_id": {"$ne": None}}},
            {"$group": {"_id": "$user_id"}},
            {"$count": "n"},
        ]
        rows = await db.debate_analytics_events.aggregate(pipeline).to_list(1)
        return rows[0]["n"] if rows else 0

    async def _total_events(event_name: str, since_iso: str) -> int:
        return await db.debate_analytics_events.count_documents({"event_name": event_name, "created_at": {"$gte": since_iso}})

    list_viewed_users = await _distinct_users("debate_list_viewed", since)
    opened_users = await _distinct_users("debate_room_opened", since)
    joined_users = await _distinct_users("debate_joined", since)
    submitted_users = await _distinct_users("debate_argument_submitted", since)
    voted_users = await _distinct_users("debate_vote_clicked", since)

    list_viewed_evt = await _total_events("debate_list_viewed", since)
    opened_evt = await _total_events("debate_room_opened", since)
    joined_evt = await _total_events("debate_joined", since)
    submitted_evt = await _total_events("debate_argument_submitted", since)
    voted_evt = await _total_events("debate_vote_clicked", since)

    def _ratio(num: int, den: int):
        if den <= 0:
            return None
        return round(100 * num / den, 1)

    funnel = {
        # users
        "list_viewed_users": list_viewed_users,
        "opened_users": opened_users,
        "joined_users": joined_users,
        "submitted_users": submitted_users,
        "voted_users": voted_users,
        # events
        "list_viewed_events": list_viewed_evt,
        "opened_events": opened_evt,
        "joined_events": joined_evt,
        "submitted_events": submitted_evt,
        "voted_events": voted_evt,
        # ratios (user-level)
        "open_rate_pct": _ratio(opened_users, list_viewed_users),
        "join_rate_pct": _ratio(joined_users, opened_users),
        "argument_rate_pct": _ratio(submitted_users, joined_users),
        "vote_rate_pct": _ratio(voted_users, opened_users),
    }

    # ----- Return-to-defend (THE gold signal) -----
    # Definition: a user who submitted at least one argument in window AND later
    # generated ANY event on that same debate (open/vote/submit) at least 30
    # minutes after their last submission. No notifications exist, so any return
    # is unprompted by definition.
    return_window_30m = timedelta(minutes=30)
    submitters = await db.debate_arguments.find(
        {"created_at": {"$gte": since}},
        {"_id": 0, "user_id": 1, "debate_id": 1, "created_at": 1},
    ).to_list(50000)
    # Bucket per (user, debate) keep the EARLIEST submission time
    earliest: dict = {}
    for s in submitters:
        key = (s.get("user_id"), s.get("debate_id"))
        ts = s.get("created_at")
        if not key[0] or not key[1] or not ts:
            continue
        if key not in earliest or ts < earliest[key]:
            earliest[key] = ts

    returners = 0
    if earliest:
        # Pull all events for these (user, debate) pairs in window
        user_ids = list({k[0] for k in earliest.keys()})
        evts = await db.debate_analytics_events.find(
            {"user_id": {"$in": user_ids}, "created_at": {"$gte": since}, "debate_id": {"$ne": None}},
            {"_id": 0, "user_id": 1, "debate_id": 1, "created_at": 1, "event_name": 1},
        ).to_list(200000)
        # Group events by key
        by_key: dict = {}
        for e in evts:
            k = (e.get("user_id"), e.get("debate_id"))
            by_key.setdefault(k, []).append(e.get("created_at"))
        for k, first_sub in earliest.items():
            try:
                first_dt = datetime.fromisoformat(first_sub.replace("Z", "+00:00"))
            except Exception:
                continue
            for ts in by_key.get(k, []):
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    continue
                if dt - first_dt >= return_window_30m:
                    returners += 1
                    break
    return_to_defend_pct = _ratio(returners, len(earliest))

    # ----- Cohort: first-debate category per user -----
    # First debate per user (any debate_room_opened) → debate.category
    first_open_pipeline = [
        {"$match": {"event_name": "debate_room_opened", "user_id": {"$ne": None}, "debate_id": {"$ne": None}}},
        {"$sort": {"created_at": 1}},
        {"$group": {"_id": "$user_id", "first_debate_id": {"$first": "$debate_id"}, "first_at": {"$first": "$created_at"}}},
    ]
    first_opens = await db.debate_analytics_events.aggregate(first_open_pipeline).to_list(50000)
    cohort_buckets: dict = {}
    for fo in first_opens:
        d = await db.debate_rooms.find_one({"debate_id": fo["first_debate_id"]}, {"_id": 0, "category": 1, "slug": 1})
        cat = (d or {}).get("category") or "unknown"
        cohort_buckets.setdefault(cat, {"category": cat, "users": 0, "returned": 0, "submitted": 0, "voted": 0})
        cohort_buckets[cat]["users"] += 1

    if first_opens:
        # For each user, did they later submit/vote/return?
        first_open_map = {fo["_id"]: fo for fo in first_opens}
        user_ids = list(first_open_map.keys())
        evts2 = await db.debate_analytics_events.find(
            {"user_id": {"$in": user_ids}},
            {"_id": 0, "user_id": 1, "event_name": 1, "created_at": 1, "debate_id": 1},
        ).to_list(500000)
        per_user: dict = {}
        for e in evts2:
            per_user.setdefault(e["user_id"], []).append(e)
        for uid, fo in first_open_map.items():
            d = await db.debate_rooms.find_one({"debate_id": fo["first_debate_id"]}, {"_id": 0, "category": 1})
            cat = (d or {}).get("category") or "unknown"
            user_evts = per_user.get(uid, [])
            submitted_any = any(x.get("event_name") == "debate_argument_submitted" for x in user_evts)
            voted_any = any(x.get("event_name") == "debate_vote_clicked" for x in user_evts)
            # returned: any event >= 24h after first_at on a different timestamp
            try:
                first_dt = datetime.fromisoformat(fo["first_at"].replace("Z", "+00:00"))
                returned = any(
                    (datetime.fromisoformat((x.get("created_at") or "").replace("Z", "+00:00")) - first_dt) >= timedelta(hours=24)
                    for x in user_evts if x.get("created_at")
                )
            except Exception:
                returned = False
            if submitted_any:
                cohort_buckets[cat]["submitted"] += 1
            if voted_any:
                cohort_buckets[cat]["voted"] += 1
            if returned:
                cohort_buckets[cat]["returned"] += 1

    cohort_rows = []
    for c in cohort_buckets.values():
        users = max(1, c["users"])
        cohort_rows.append({
            **c,
            "submit_rate_pct": round(100 * c["submitted"] / users, 1),
            "vote_rate_pct": round(100 * c["voted"] / users, 1),
            "d1_return_pct": round(100 * c["returned"] / users, 1),
        })
    cohort_rows.sort(key=lambda x: x["users"], reverse=True)

    # ----- Repeat behavior -----
    # % of submitters with >1 argument
    multi_pipeline = [
        {"$match": {"created_at": {"$gte": since}, "moderation_status": {"$in": ["visible", "flagged"]}}},
        {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
    ]
    multi_rows = await db.debate_arguments.aggregate(multi_pipeline).to_list(20000)
    submitters_count = len(multi_rows)
    multi_submitters = sum(1 for r in multi_rows if r["n"] > 1)
    avg_args_per_submitter = round(sum(r["n"] for r in multi_rows) / submitters_count, 2) if submitters_count else 0.0

    # ----- Avg argument length -----
    arg_lengths = await db.debate_arguments.find(
        {"created_at": {"$gte": since}, "moderation_status": {"$in": ["visible", "flagged"]}},
        {"_id": 0, "content": 1},
    ).to_list(20000)
    avg_arg_len = round(sum(len((a.get("content") or "")) for a in arg_lengths) / max(1, len(arg_lengths)), 0) if arg_lengths else 0

    # ----- D1 / D7 retention (event-based) -----
    # D1: user who emitted any event in [now-2d, now-1d) AND another event >= 24h later
    async def _retention(window_back_days: int) -> dict:
        cohort_start = (now - timedelta(days=window_back_days + 1)).isoformat()
        cohort_end = (now - timedelta(days=window_back_days)).isoformat()
        cohort_users_rows = await db.debate_analytics_events.aggregate([
            {"$match": {"created_at": {"$gte": cohort_start, "$lt": cohort_end}, "user_id": {"$ne": None}}},
            {"$group": {"_id": "$user_id", "first_at": {"$min": "$created_at"}}},
        ]).to_list(20000)
        if not cohort_users_rows:
            return {"eligible": 0, "returned": 0, "pct": None}
        ids = [r["_id"] for r in cohort_users_rows]
        first_map = {r["_id"]: r["first_at"] for r in cohort_users_rows}
        evs = await db.debate_analytics_events.find(
            {"user_id": {"$in": ids}}, {"_id": 0, "user_id": 1, "created_at": 1},
        ).to_list(200000)
        by_user = {}
        for e in evs:
            by_user.setdefault(e["user_id"], []).append(e["created_at"])
        returned = 0
        for uid, first_at in first_map.items():
            try:
                fdt = datetime.fromisoformat(first_at.replace("Z", "+00:00"))
            except Exception:
                continue
            for ts in by_user.get(uid, []):
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    continue
                if (dt - fdt).total_seconds() >= window_back_days * 86400:
                    returned += 1
                    break
        return {"eligible": len(cohort_users_rows), "returned": returned, "pct": round(100 * returned / max(1, len(cohort_users_rows)), 1)}

    d1 = await _retention(1)
    d7 = await _retention(7)

    # ----- Lurker percentage -----
    # opened_users that did NOT vote AND did NOT submit
    opener_ids_rows = await db.debate_analytics_events.aggregate([
        {"$match": {"event_name": "debate_room_opened", "created_at": {"$gte": since}, "user_id": {"$ne": None}}},
        {"$group": {"_id": "$user_id"}},
    ]).to_list(20000)
    opener_ids = {r["_id"] for r in opener_ids_rows}
    voter_ids_rows = await db.debate_analytics_events.aggregate([
        {"$match": {"event_name": "debate_vote_clicked", "created_at": {"$gte": since}, "user_id": {"$ne": None}}},
        {"$group": {"_id": "$user_id"}},
    ]).to_list(20000)
    voter_ids = {r["_id"] for r in voter_ids_rows}
    submitter_ids_rows = await db.debate_analytics_events.aggregate([
        {"$match": {"event_name": "debate_argument_submitted", "created_at": {"$gte": since}, "user_id": {"$ne": None}}},
        {"$group": {"_id": "$user_id"}},
    ]).to_list(20000)
    submitter_ids = {r["_id"] for r in submitter_ids_rows}
    lurkers = opener_ids - voter_ids - submitter_ids
    lurker_pct = _ratio(len(lurkers), len(opener_ids))

    # ----- Top fastest-rising arguments (qualitative observation) -----
    fastest = await db.debate_arguments.find(
        {"created_at": {"$gte": since_24h}, "moderation_status": "visible"},
        {"_id": 0, "argument_id": 1, "debate_id": 1, "anonymous_handle": 1, "content": 1, "ai_score": 1, "upvotes": 1, "downvotes": 1, "rank_score": 1, "side": 1, "created_at": 1},
    ).sort("rank_score", -1).limit(10).to_list(10)
    most_reported_rows = await db.debate_reports.aggregate([
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": "$argument_id", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 10},
    ]).to_list(10)
    most_reported = []
    for r in most_reported_rows:
        a = await db.debate_arguments.find_one({"argument_id": r["_id"]}, {"_id": 0, "argument_id": 1, "content": 1, "anonymous_handle": 1, "side": 1, "ai_score": 1})
        if a:
            most_reported.append({**a, "report_count": r["n"]})

    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "today_utc": now.strftime("%Y-%m-%d"),
        "operator_note": "Read-only retention instrumentation. No notifications, no behavior shaping. Funnel uses distinct users.",
        "funnel": funnel,
        "return_to_defend": {
            "submitter_debate_pairs": len(earliest),
            "returned": returners,
            "pct": return_to_defend_pct,
        },
        "engagement": {
            "submitters": submitters_count,
            "multi_submitters": multi_submitters,
            "multi_submitter_pct": _ratio(multi_submitters, submitters_count),
            "avg_args_per_submitter": avg_args_per_submitter,
            "avg_argument_length_chars": int(avg_arg_len),
            "lurker_pct": lurker_pct,
            "lurkers": len(lurkers),
            "openers": len(opener_ids),
        },
        "retention": {
            "d1": d1,
            "d7": d7,
        },
        "cohorts_first_category": cohort_rows,
        "qualitative": {
            "fastest_rising": fastest,
            "most_reported": most_reported,
        },
    }


@admin_router.get("/events/export")
async def admin_events_export(_admin: dict = Depends(_require_admin), days: int = Query(default=14, ge=1, le=90), event_name: Optional[str] = None, limit: int = Query(default=20000, ge=1, le=100000)):
    """Raw event export for offline analysis. JSON only (CSV done client-side)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    q: dict = {"created_at": {"$gte": since}}
    if event_name:
        q["event_name"] = event_name
    cursor = db.debate_analytics_events.find(q, {"_id": 0}).sort("created_at", 1).limit(limit)
    rows = await cursor.to_list(limit)
    return {"window_days": days, "count": len(rows), "events": rows}
