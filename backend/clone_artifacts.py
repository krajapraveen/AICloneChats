"""
Clone Conversation Artifacts.

Philosophy:
- Pull, not push. Artifacts are extracted ONLY when the user asks ("Extract artifacts").
  No background job. No scheduler. No notification. No reactivation digest.
- The clone helps you remember what mattered. Time and intention belong to the user.
- Tasks have an optional `due_at` for the user's reference only — nothing watches it.

Surfaces:
- Visitors (no auth) can extract artifacts from their own conversation, scoped by
  conversation_id + visitor_id (the same anon identity already used by clone chat).
- Authenticated users (auth header present) get the artifacts scoped by user_id.

Strict analytics separation: experience_variant=clone_artifacts_v1.
"""
from __future__ import annotations

import os
import json
import re
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, Field

from db import db
from auth import get_optional_user, get_current_user
from models import now_iso
from safety_filter import moderate_user_input, log_moderation_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/clone-artifacts", tags=["clone-artifacts"])
admin_router = APIRouter(prefix="/api/admin/clone-artifacts", tags=["clone-artifacts-admin"])

EXPERIENCE_VARIANT = "clone_artifacts_v1"
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
MAX_MESSAGES_FOR_EXTRACTION = 80
MAX_EXTRACTIONS_PER_CONVERSATION = 20


# ---- Models ----
class ExtractRequest(BaseModel):
    conversation_id: str
    visitor_id: Optional[str] = None  # required if not authenticated
    note: Optional[str] = Field(default=None, max_length=500)


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None  # open | in_progress | done | cancelled
    priority: Optional[str] = None  # low | medium | high
    due_at: Optional[str] = None


def _require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def _identity(user: Optional[dict], visitor_id: Optional[str]) -> tuple[str, str]:
    """Returns (owner_kind, owner_value). Owner_kind: 'user' | 'visitor'."""
    if user and user.get("user_id"):
        return ("user", user["user_id"])
    if visitor_id and len(visitor_id) >= 4:
        return ("visitor", visitor_id[:80])
    raise HTTPException(401, "Identity required (auth or visitor_id)")


async def _emit(event_name: str, *, conversation_id: Optional[str] = None, owner_kind: Optional[str] = None, owner_value: Optional[str] = None, metadata: Optional[dict] = None) -> None:
    await db.clone_artifact_events.insert_one({
        "event_id": uuid.uuid4().hex,
        "event_name": event_name,
        "conversation_id": conversation_id,
        "owner_kind": owner_kind,
        "owner_value": owner_value,
        "metadata": {**(metadata or {}), "experience_variant": EXPERIENCE_VARIANT},
        "created_at": now_iso(),
    })


# ---- Extraction ----
EXTRACTION_SYSTEM_PROMPT = """You are an artifact-extraction assistant.

Read the conversation transcript and identify only the elements the human
explicitly committed to or decided. Do NOT invent tasks. Do NOT add nudges.
If the conversation contains no real tasks/decisions/follow-ups, return empty arrays.
You return STRICT JSON ONLY with this exact shape:

{
  "tasks": [{"title": str, "description": str, "priority": "low"|"medium"|"high", "due_at": ISO8601|null}],
  "decisions": [{"title": str, "reason": str}],
  "follow_ups": [{"title": str, "context": str}],
  "summary": str,
  "unresolved_questions": [str]
}

Rules:
- "tasks": only things the human said they would DO. Skip suggestions the AI offered.
- "decisions": choices the human stated. Not advice.
- "follow_ups": things to revisit later — but with NO scheduling, NO reminders.
- "summary": ≤ 80 words, neutral tone, what mattered emotionally and operationally.
- "unresolved_questions": real open threads, not rhetorical.
- If nothing qualifies, the array is empty. Do not pad.
- Output ONLY the JSON object. No prose, no preamble, no markdown fences.
"""


def _coerce_priority(p: Optional[str]) -> str:
    p = (p or "").lower().strip()
    return p if p in ("low", "medium", "high") else "medium"


def _coerce_due(s: Optional[str]) -> Optional[str]:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _coerce_extraction(payload: dict) -> dict:
    """Defensive coercion — tolerate sloppy LLM output."""
    if not isinstance(payload, dict):
        payload = {}
    return {
        "tasks": [
            {
                "title": str(t.get("title", "")).strip()[:240],
                "description": str(t.get("description", "")).strip()[:1200],
                "priority": _coerce_priority(t.get("priority")),
                "due_at": _coerce_due(t.get("due_at")),
            }
            for t in (payload.get("tasks") or []) if isinstance(t, dict) and str(t.get("title", "")).strip()
        ][:25],
        "decisions": [
            {
                "title": str(d.get("title", "")).strip()[:240],
                "reason": str(d.get("reason", "")).strip()[:600],
            }
            for d in (payload.get("decisions") or []) if isinstance(d, dict) and str(d.get("title", "")).strip()
        ][:25],
        "follow_ups": [
            {
                "title": str(f.get("title", "")).strip()[:240],
                "context": str(f.get("context", "")).strip()[:600],
            }
            for f in (payload.get("follow_ups") or []) if isinstance(f, dict) and str(f.get("title", "")).strip()
        ][:25],
        "summary": str(payload.get("summary") or "").strip()[:1500],
        "unresolved_questions": [
            str(q).strip()[:300] for q in (payload.get("unresolved_questions") or []) if isinstance(q, str) and str(q).strip()
        ][:15],
    }


async def _run_extraction(conversation_id: str, transcript: list[dict]) -> dict:
    """Send transcript through LLM, return coerced JSON or empty fallback."""
    fallback = {"tasks": [], "decisions": [], "follow_ups": [], "summary": "", "unresolved_questions": []}
    if not EMERGENT_LLM_KEY or not transcript:
        return fallback

    transcript_text = "\n".join(
        f"{(m.get('sender') or m.get('role') or 'user').upper()}: {m.get('text') or m.get('content') or ''}"
        for m in transcript
    )[-12000:]

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"artifact_{conversation_id}_{uuid.uuid4().hex[:8]}",
            system_message=EXTRACTION_SYSTEM_PROMPT,
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")
        raw = await chat.send_message(UserMessage(text=f"Conversation transcript:\n\n{transcript_text}"))
        if not raw:
            return fallback
        # Strip code fences if model added them despite instructions
        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
        parsed = json.loads(cleaned)
        return _coerce_extraction(parsed)
    except json.JSONDecodeError:
        logger.warning("Artifact extraction returned non-JSON")
        return fallback
    except Exception as e:
        logger.warning("Artifact extraction failed: %s", e)
        return fallback


# ---- Helpers ----
async def _check_owner(conversation_id: str, owner_kind: str, owner_value: str) -> dict:
    """Verify the requester actually has access to this conversation. Return conversation doc."""
    conv = await db.clone_conversations.find_one({"conversation_id": conversation_id}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if owner_kind == "user":
        # Authenticated owner: either they own the clone or they were the visitor
        if conv.get("visitor_id") == owner_value:
            return conv
        clone_id = conv.get("clone_id")
        if clone_id:
            clone = await db.clones.find_one({"clone_id": clone_id}, {"_id": 0, "owner_id": 1})
            if clone and clone.get("owner_id") == owner_value:
                return conv
        # Anonymous visitor whose visitor_id == user_id (avatar chat path)
        if conv.get("visitor_id") == owner_value:
            return conv
        raise HTTPException(403, "Not your conversation")
    else:
        # Visitor identity must match the conversation's visitor
        if conv.get("visitor_id") != owner_value:
            raise HTTPException(403, "Not your conversation")
        return conv


async def _load_transcript(conversation_id: str) -> list[dict]:
    rows = await db.clone_messages.find(
        {"conversation_id": conversation_id}, {"_id": 0, "sender": 1, "text": 1, "created_at": 1, "message_id": 1},
    ).sort("created_at", 1).to_list(MAX_MESSAGES_FOR_EXTRACTION)
    return rows


def _public_artifact(a: dict) -> dict:
    return {
        "artifact_id": a.get("artifact_id"),
        "conversation_id": a.get("conversation_id"),
        "summary": a.get("summary"),
        "tasks": a.get("tasks", []),
        "decisions": a.get("decisions", []),
        "follow_ups": a.get("follow_ups", []),
        "unresolved_questions": a.get("unresolved_questions", []),
        "message_count_at_extraction": a.get("message_count_at_extraction", 0),
        "created_at": a.get("created_at"),
    }


def _public_task(t: dict) -> dict:
    return {
        "task_id": t.get("task_id"),
        "artifact_id": t.get("artifact_id"),
        "conversation_id": t.get("conversation_id"),
        "title": t.get("title"),
        "description": t.get("description"),
        "status": t.get("status", "open"),
        "priority": t.get("priority", "medium"),
        "due_at": t.get("due_at"),
        "created_at": t.get("created_at"),
        "completed_at": t.get("completed_at"),
        "updated_at": t.get("updated_at"),
    }


# ---- Routes ----
@router.post("/extract")
async def extract_artifacts(
    payload: ExtractRequest,
    user: Optional[dict] = Depends(get_optional_user),
):
    owner_kind, owner_value = _identity(user, payload.visitor_id)
    conv = await _check_owner(payload.conversation_id, owner_kind, owner_value)

    # Soft moderation on optional note
    if payload.note:
        c = moderate_user_input(payload.note)
        if c["action"] == "block":
            await log_moderation_event(db, user_id=owner_value, route="clone_artifacts", source="user_input", result=c, action_taken="block_input")
            raise HTTPException(400, "Note violates safety rules.")

    # Cap extractions per conversation to avoid abuse
    existing = await db.clone_artifacts.count_documents({"conversation_id": payload.conversation_id})
    if existing >= MAX_EXTRACTIONS_PER_CONVERSATION:
        raise HTTPException(429, f"Too many extractions for this conversation ({MAX_EXTRACTIONS_PER_CONVERSATION} max).")

    transcript = await _load_transcript(payload.conversation_id)
    if not transcript:
        raise HTTPException(400, "No conversation messages to extract from.")

    extracted = await _run_extraction(payload.conversation_id, transcript)
    artifact_id = f"art_{uuid.uuid4().hex[:14]}"
    artifact_doc = {
        "artifact_id": artifact_id,
        "conversation_id": payload.conversation_id,
        "clone_id": conv.get("clone_id"),
        "owner_kind": owner_kind,
        "owner_value": owner_value,
        "note": payload.note or None,
        "summary": extracted["summary"],
        "tasks": extracted["tasks"],
        "decisions": extracted["decisions"],
        "follow_ups": extracted["follow_ups"],
        "unresolved_questions": extracted["unresolved_questions"],
        "message_count_at_extraction": len(transcript),
        "created_at": now_iso(),
    }
    await db.clone_artifacts.insert_one(dict(artifact_doc))

    # Persist tasks as separate documents for status-tracking (not as a productivity loop —
    # purely so the user can mark them done as a memory marker, no schedule attached).
    task_docs: list[dict] = []
    for t in extracted["tasks"]:
        task_id = f"atk_{uuid.uuid4().hex[:14]}"
        td = {
            "task_id": task_id,
            "artifact_id": artifact_id,
            "conversation_id": payload.conversation_id,
            "owner_kind": owner_kind,
            "owner_value": owner_value,
            "clone_id": conv.get("clone_id"),
            "title": t["title"],
            "description": t.get("description") or "",
            "priority": t["priority"],
            "due_at": t.get("due_at"),
            "status": "open",
            "created_at": now_iso(),
            "completed_at": None,
            "updated_at": now_iso(),
        }
        await db.clone_artifact_tasks.insert_one(dict(td))
        task_docs.append(td)

    await _emit("artifacts_extracted", conversation_id=payload.conversation_id, owner_kind=owner_kind, owner_value=owner_value, metadata={
        "artifact_id": artifact_id,
        "task_count": len(extracted["tasks"]),
        "decision_count": len(extracted["decisions"]),
        "follow_up_count": len(extracted["follow_ups"]),
        "message_count": len(transcript),
    })

    return {
        "artifact": _public_artifact(artifact_doc),
        "tasks": [_public_task(td) for td in task_docs],
    }


@router.get("")
async def list_artifacts(
    conversation_id: str = Query(...),
    visitor_id: Optional[str] = Query(default=None),
    user: Optional[dict] = Depends(get_optional_user),
):
    owner_kind, owner_value = _identity(user, visitor_id)
    await _check_owner(conversation_id, owner_kind, owner_value)
    rows = await db.clone_artifacts.find(
        {"conversation_id": conversation_id, "owner_kind": owner_kind, "owner_value": owner_value},
        {"_id": 0},
    ).sort("created_at", -1).to_list(50)
    return {"artifacts": [_public_artifact(a) for a in rows]}


@router.get("/tasks")
async def list_tasks(
    conversation_id: Optional[str] = Query(default=None),
    visitor_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    user: Optional[dict] = Depends(get_optional_user),
):
    owner_kind, owner_value = _identity(user, visitor_id)
    q = {"owner_kind": owner_kind, "owner_value": owner_value}
    if conversation_id:
        q["conversation_id"] = conversation_id
    if status:
        q["status"] = status
    rows = await db.clone_artifact_tasks.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"tasks": [_public_task(t) for t in rows]}


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: str,
    payload: TaskUpdate,
    visitor_id: Optional[str] = Query(default=None),
    user: Optional[dict] = Depends(get_optional_user),
):
    owner_kind, owner_value = _identity(user, visitor_id)
    task = await db.clone_artifact_tasks.find_one({"task_id": task_id}, {"_id": 0})
    if not task:
        raise HTTPException(404, "Task not found")
    if task.get("owner_kind") != owner_kind or task.get("owner_value") != owner_value:
        raise HTTPException(403, "Not your task")

    update: dict = {"updated_at": now_iso()}
    if payload.title is not None:
        update["title"] = payload.title.strip()[:240]
    if payload.description is not None:
        update["description"] = payload.description.strip()[:1200]
    if payload.priority is not None:
        update["priority"] = _coerce_priority(payload.priority)
    if payload.due_at is not None:
        update["due_at"] = _coerce_due(payload.due_at)
    if payload.status is not None:
        if payload.status not in ("open", "in_progress", "done", "cancelled"):
            raise HTTPException(400, "Invalid status")
        update["status"] = payload.status
        if payload.status == "done" and not task.get("completed_at"):
            update["completed_at"] = now_iso()
            await _emit("task_completed", conversation_id=task.get("conversation_id"), owner_kind=owner_kind, owner_value=owner_value, metadata={"task_id": task_id})
        if payload.status != "done":
            update["completed_at"] = None

    await db.clone_artifact_tasks.update_one({"task_id": task_id}, {"$set": update})
    fresh = await db.clone_artifact_tasks.find_one({"task_id": task_id}, {"_id": 0})
    return {"task": _public_task(fresh or {})}


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    visitor_id: Optional[str] = Query(default=None),
    user: Optional[dict] = Depends(get_optional_user),
):
    owner_kind, owner_value = _identity(user, visitor_id)
    res = await db.clone_artifact_tasks.delete_one({"task_id": task_id, "owner_kind": owner_kind, "owner_value": owner_value})
    if res.deleted_count == 0:
        raise HTTPException(404, "Task not found")
    return {"ok": True}


# ---------- Admin (visibility only — no engagement metric) ----------
@admin_router.get("/metrics")
async def admin_metrics(_admin: dict = Depends(_require_admin), days: int = Query(default=7, ge=1, le=90)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    artifacts_in_window = await db.clone_artifacts.count_documents({"created_at": {"$gte": since}})
    distinct_users_pipeline = [
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": {"k": "$owner_kind", "v": "$owner_value"}}},
        {"$count": "n"},
    ]
    distinct_rows = await db.clone_artifacts.aggregate(distinct_users_pipeline).to_list(1)
    distinct_users = distinct_rows[0]["n"] if distinct_rows else 0
    tasks_in_window = await db.clone_artifact_tasks.count_documents({"created_at": {"$gte": since}})
    tasks_done = await db.clone_artifact_tasks.count_documents({"completed_at": {"$gte": since}, "status": "done"})

    # Repeat-extraction signal: distinct users who extracted ≥2 times. This is the
    # "did the artifact actually mean something to them?" signal, NOT a tasks/min KPI.
    repeat_pipeline = [
        {"$group": {"_id": {"k": "$owner_kind", "v": "$owner_value"}, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gte": 2}}},
        {"$count": "n"},
    ]
    repeat_rows = await db.clone_artifacts.aggregate(repeat_pipeline).to_list(1)
    repeat_extractors = repeat_rows[0]["n"] if repeat_rows else 0

    return {
        "window_days": days,
        "artifacts_extracted_in_window": artifacts_in_window,
        "distinct_extractors_in_window": distinct_users,
        "tasks_extracted_in_window": tasks_in_window,
        "tasks_completed_in_window": tasks_done,
        "repeat_extractors_total": repeat_extractors,
        "operator_note": "Pull-based extraction. No reminders, no notifications, no scheduler. Behavior over activity.",
    }
