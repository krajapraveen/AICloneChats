"""
profile_aliases.py — Thin alias router exposing the existing My-Profile
endpoints under the spec-mandated `/api/profile/*` prefix.

Why aliases instead of rename?
- The original endpoints (under `/api/auth/*`, `/api/support/*`, `/api/me/*`,
  `/api/clones/mine`) are already deployed and used by other clients (the
  current frontend + admin tools + tests). Renaming them risks regression.
- Aliasing is a single file that re-uses the existing handlers, keeping one
  source of truth for behaviour and validation.

Endpoints exposed:
  GET    /api/profile/my-space          → clones.list_my_clones
  GET    /api/profile/subscriptions     → billing_api.my_orders
  POST   /api/profile/change-password   → password_reset.change_password
  GET    /api/profile/inbox             → support_inbox.list_my_threads
  GET    /api/profile/concerns          → support_inbox.list_my_threads (same data)
  POST   /api/profile/concerns          → support_inbox.create_thread

Admin aliases:
  GET    /api/admin/concerns            → support_inbox.admin_list_threads
  GET    /api/admin/concerns/{id}       → support_inbox.admin_get_thread
  POST   /api/admin/concerns/{id}/reply → support_inbox.admin_reply
  PATCH  /api/admin/concerns/{id}/status→ support_inbox.admin_set_status
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from auth import get_current_user
from admin import get_admin_user

router = APIRouter(prefix="/api/profile", tags=["profile"])
admin_router = APIRouter(prefix="/api/admin/concerns", tags=["admin"])


# ─────────────── User aliases ───────────────

@router.get("/my-space")
async def my_space(user: dict = Depends(get_current_user)):
    from clones import list_my_clones
    return await list_my_clones(user=user)


@router.get("/subscriptions")
async def subscriptions(user: dict = Depends(get_current_user), limit: int = Query(default=50, ge=1, le=200)):
    from billing_api import my_orders
    return await my_orders(user=user, limit=limit)


@router.post("/change-password")
async def change_password_alias(payload: dict, request: Request, user: dict = Depends(get_current_user)):
    from password_reset import change_password, ChangePasswordReq
    return await change_password(payload=ChangePasswordReq(**payload), request=request, user=user)


@router.get("/inbox")
async def inbox(user: dict = Depends(get_current_user), limit: int = Query(default=50, ge=1, le=200)):
    from support_inbox import list_my_threads
    return await list_my_threads(user=user, limit=limit)


@router.get("/concerns")
async def list_concerns(user: dict = Depends(get_current_user), limit: int = Query(default=50, ge=1, le=200)):
    from support_inbox import list_my_threads
    return await list_my_threads(user=user, limit=limit)


@router.post("/concerns")
async def create_concern(payload: dict, request: Request, user: dict = Depends(get_current_user)):
    from support_inbox import create_thread, ThreadCreate
    return await create_thread(payload=ThreadCreate(**payload), request=request, user=user)


# ─────────────── Admin aliases ───────────────

@admin_router.get("")
async def admin_list_concerns(admin: dict = Depends(get_admin_user), status: str | None = None,
                              unread_only: bool = False, limit: int = Query(default=100, ge=1, le=500)):
    from support_inbox import admin_list_threads
    return await admin_list_threads(admin=admin, status=status, unread_only=unread_only, limit=limit)


@admin_router.get("/{concern_id}")
async def admin_get_concern(concern_id: str, admin: dict = Depends(get_admin_user)):
    from support_inbox import admin_get_thread
    return await admin_get_thread(thread_id=concern_id, admin=admin)


@admin_router.post("/{concern_id}/reply")
async def admin_reply_concern(concern_id: str, payload: dict, admin: dict = Depends(get_admin_user)):
    from support_inbox import admin_reply, ThreadReply
    return await admin_reply(thread_id=concern_id, payload=ThreadReply(**payload), admin=admin)


@admin_router.patch("/{concern_id}/status")
async def admin_set_concern_status(concern_id: str, payload: dict, admin: dict = Depends(get_admin_user)):
    from support_inbox import admin_set_status, ThreadStatusUpdate
    return await admin_set_status(thread_id=concern_id, payload=ThreadStatusUpdate(**payload), admin=admin)
