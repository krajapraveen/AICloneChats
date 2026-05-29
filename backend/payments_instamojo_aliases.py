"""
Instamojo-specific endpoint aliases requested in the P0 spec.

These are THIN wrappers over the generic abstraction router so the public
URL contract the operations team asked for stays stable:
  POST /api/payments/instamojo/create-order
  POST /api/payments/instamojo/webhook
  GET  /api/payments/instamojo/order/{order_id}

All real logic lives in `payments/router.py` (dispatch) and
`payments/providers/instamojo.py` (provider). Removing Instamojo later is
just removing this file + the provider file + the env vars.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from auth import get_current_user
from payments.router import (
    create_order as _generic_create_order,
    webhook as _generic_webhook,
    get_order as _generic_get_order,
)

router = APIRouter(prefix="/api/payments/instamojo", tags=["payments-instamojo"])


@router.post("/create-order")
async def instamojo_create_order(payload: dict, request: Request, user: dict = Depends(get_current_user)):
    """Aliases POST /api/payments/create-order — Instamojo flavor."""
    return await _generic_create_order(payload, request, user)


@router.post("/webhook")
async def instamojo_webhook(request: Request):
    """Aliases POST /api/payments/webhook/instamojo for the URL shape the
    operations team registers in the Instamojo dashboard."""
    return await _generic_webhook("instamojo", request)


@router.get("/order/{order_id}")
async def instamojo_get_order(order_id: str, user: dict = Depends(get_current_user)):
    """Aliases GET /api/payments/order/{order_id} — reads the order and
    reconciles via Instamojo Payment Details API when still pending."""
    return await _generic_get_order(order_id, user)
