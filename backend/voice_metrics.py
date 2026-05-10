"""
Voice Messaging — admin metrics dashboard.

Surfaces ONE-PAGE evidence so we don't hallucinate PMF from anecdotes.

North-star metric: Generation -> Copy Rate (per tone)
Secondary: D1 retention, edit-before-copy rate, anonymous->signup conversion,
funnel drop-off at every stage.

Read-only. Admin gated. Powered by aggregations on existing collections —
zero schema migrations.
"""
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from db import db
from auth import get_current_user

router = APIRouter(prefix="/api/admin/voice", tags=["admin", "voice-metrics"])
logger = logging.getLogger(__name__)


async def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _actor_key(doc: dict) -> Optional[str]:
    """Stable identifier across user_id and device_id."""
    if doc.get("user_id"):
        return f"u:{doc['user_id']}"
    if doc.get("device_id"):
        return f"d:{doc['device_id']}"
    return None


@router.get("/metrics")
async def metrics(_admin: dict = Depends(_require_admin), days: int = Query(7, ge=1, le=90)):
    since_iso = _since(days)

    # ---------- Funnel ----------
    # Pull all events from the window (cheap — single collection scan with index)
    cursor = db.voice_usage_events.find(
        {"created_at": {"$gte": since_iso}},
        {"_id": 0, "user_id": 1, "device_id": 1, "event_name": 1, "metadata": 1, "created_at": 1},
    )
    events = await cursor.to_list(50000)

    # Per-actor sets — counted as "did the actor reach this stage at all"
    stage_actors: dict = defaultdict(set)
    stage_event_map = {
        "viewed": {"voice_page_viewed"},
        "input_started": {"voice_audio_uploaded", "voice_text_pasted", "voice_record_started"},
        "transcription_completed": {"voice_transcription_success"},
        "generated": {"voice_message_generated"},
        "copied": {"voice_message_copied"},
    }
    # Daily activity per actor for retention / repeat usage
    actor_active_days: dict = defaultdict(set)
    # Source split (record/upload/text)
    source_counter: Counter = Counter()
    # Anonymous and authed actor pools
    anon_actors: set = set()
    authed_actors: set = set()
    # Edited-then-copied tracking
    edited_session_ids: set = set()
    copied_session_ids: set = set()
    # Refines used
    refine_count = 0

    for e in events:
        actor = _actor_key(e)
        if not actor:
            continue
        if actor.startswith("d:"):
            anon_actors.add(actor)
        else:
            authed_actors.add(actor)
        try:
            day = e["created_at"][:10]
            actor_active_days[actor].add(day)
        except Exception:  # noqa: BLE001
            pass
        en = e.get("event_name")
        for stage, names in stage_event_map.items():
            if en in names:
                stage_actors[stage].add(actor)
        if en == "voice_transcription_success":
            st = (e.get("metadata") or {}).get("source_type")
            if st:
                source_counter[st] += 1
        if en == "voice_transcript_edited":
            sid = (e.get("metadata") or {}).get("session_id")
            if sid:
                edited_session_ids.add(sid)
        if en == "voice_message_copied":
            mid = (e.get("metadata") or {}).get("message_id")
            if mid:
                copied_session_ids.add(mid)
        if en == "voice_message_refined":
            refine_count += 1

    # 2nd generation same day (per actor)
    actors_with_2nd_gen = 0
    for actor, days_set in actor_active_days.items():
        # Count generated events per (actor, day)
        gens_by_day: Counter = Counter()
        for e in events:
            if _actor_key(e) == actor and e.get("event_name") == "voice_message_generated":
                gens_by_day[e["created_at"][:10]] += 1
        if any(c >= 2 for c in gens_by_day.values()):
            actors_with_2nd_gen += 1

    # D1 retention — actors who were active on day X AND day X+1 (within window)
    actors_returned = 0
    for actor, day_set in actor_active_days.items():
        if len(day_set) < 2:
            continue
        sorted_days = sorted(day_set)
        for i in range(len(sorted_days) - 1):
            d1 = datetime.fromisoformat(sorted_days[i]).date()
            d2 = datetime.fromisoformat(sorted_days[i + 1]).date()
            if (d2 - d1).days == 1:
                actors_returned += 1
                break

    funnel = [
        {"stage": "viewed", "actors": len(stage_actors["viewed"])},
        {"stage": "input_started", "actors": len(stage_actors["input_started"])},
        {"stage": "transcription_completed", "actors": len(stage_actors["transcription_completed"])},
        {"stage": "generated", "actors": len(stage_actors["generated"])},
        {"stage": "copied", "actors": len(stage_actors["copied"])},
        {"stage": "second_gen_same_day", "actors": actors_with_2nd_gen},
        {"stage": "returned_next_day", "actors": actors_returned},
    ]
    # Drop-off
    prev_count = funnel[0]["actors"] or 1
    for row in funnel:
        row["pct_of_top"] = round(100.0 * row["actors"] / max(1, funnel[0]["actors"]), 1)
        row["drop_from_prev_pct"] = round(100.0 * (prev_count - row["actors"]) / max(1, prev_count), 1) if prev_count else 0.0
        prev_count = row["actors"]

    # ---------- Generation -> Copy Rate (north star) ----------
    gen_cursor = db.generated_messages.find(
        {"created_at": {"$gte": since_iso}},
        {"_id": 0, "tone": 1, "copy_count": 1, "voice_session_id": 1, "user_id": 1, "device_id": 1, "created_at": 1, "input_transcript": 1},
    )
    gens = await gen_cursor.to_list(50000)

    total_gens = len(gens)
    total_copies = sum(1 for g in gens if int(g.get("copy_count", 0) or 0) > 0)
    overall_copy_rate = round(100.0 * total_copies / total_gens, 1) if total_gens else 0.0

    by_tone: dict = defaultdict(lambda: {"generated": 0, "copied": 0})
    for g in gens:
        t = g.get("tone") or "unknown"
        by_tone[t]["generated"] += 1
        if int(g.get("copy_count", 0) or 0) > 0:
            by_tone[t]["copied"] += 1
    tone_rows = []
    for tone, c in by_tone.items():
        rate = round(100.0 * c["copied"] / c["generated"], 1) if c["generated"] else 0.0
        tone_rows.append({"tone": tone, "generated": c["generated"], "copied": c["copied"], "copy_rate_pct": rate})
    tone_rows.sort(key=lambda r: -r["copy_rate_pct"])
    best_tone = tone_rows[0] if tone_rows else None
    worst_tone = tone_rows[-1] if tone_rows else None

    # ---------- Edit-then-copy ----------
    # An edit signal = the actor edited the transcript (PATCH /sessions/{id}) THEN copied.
    # We approximate: of all sessions whose messages were copied, how many had edit events.
    # session_ids covered by copied messages
    sessions_with_copy = {g.get("voice_session_id") for g in gens if int(g.get("copy_count", 0) or 0) > 0 and g.get("voice_session_id")}
    edited_and_copied = len(sessions_with_copy & edited_session_ids)
    edit_before_copy_pct = round(100.0 * edited_and_copied / len(sessions_with_copy), 1) if sessions_with_copy else 0.0

    # ---------- Anonymous -> signup conversion ----------
    # An anon device_id "converted" if a user later registered/logged in from the same device.
    # We don't store device_id on user_sessions yet, so we approximate: % of devices whose
    # later events bear a user_id under the same actor stream.
    user_streams: dict = defaultdict(list)
    for e in sorted(events, key=lambda x: x.get("created_at", "")):
        if e.get("device_id"):
            user_streams[e["device_id"]].append(e)
    # approximate: device "converted" if events in the same window from same device_id ALSO have a user_id
    converted_devices = 0
    total_anon_devices = 0
    for dev, stream in user_streams.items():
        had_user = any(s.get("user_id") for s in stream)
        had_anon = any(s.get("is_anonymous") and not s.get("user_id") for s in stream)
        if had_anon:
            total_anon_devices += 1
            if had_user:
                converted_devices += 1
    anon_conversion_pct = round(100.0 * converted_devices / total_anon_devices, 1) if total_anon_devices else 0.0

    # ---------- Daily Active Actors ----------
    daily_active: dict = defaultdict(set)
    for actor, days_set in actor_active_days.items():
        for day in days_set:
            daily_active[day].add(actor)
    daily_active_rows = sorted(
        [{"day": day, "actors": len(actors)} for day, actors in daily_active.items()],
        key=lambda r: r["day"],
    )

    return {
        "window_days": days,
        "since": since_iso,
        "funnel": funnel,
        "north_star": {
            "label": "Generation -> Copy Rate",
            "overall_copy_rate_pct": overall_copy_rate,
            "messages_generated": total_gens,
            "messages_copied": total_copies,
        },
        "tone_performance": {
            "rows": tone_rows,
            "best_tone": best_tone,
            "worst_tone": worst_tone,
        },
        "trust_signals": {
            "edit_before_copy_pct": edit_before_copy_pct,
            "edited_then_copied_sessions": edited_and_copied,
            "total_copied_sessions": len(sessions_with_copy),
            "refine_actions": refine_count,
        },
        "source_split": [{"source": k, "count": v} for k, v in source_counter.most_common()],
        "actors": {
            "total_anonymous": len(anon_actors),
            "total_authed": len(authed_actors),
            "anonymous_to_signup_conversion_pct": anon_conversion_pct,
            "converted_devices": converted_devices,
            "anon_devices_in_window": total_anon_devices,
        },
        "daily_active_actors": daily_active_rows,
        "retention": {
            "actors_with_2nd_gen_same_day": actors_with_2nd_gen,
            "actors_returned_next_day": actors_returned,
            "d1_return_rate_pct": round(100.0 * actors_returned / max(1, len(actor_active_days)), 1),
        },
    }
