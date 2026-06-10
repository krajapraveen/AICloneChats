"""
Cashfree-specific endpoint aliases requested in the P0 spec.

Thin wrappers over the generic abstraction router so the public URL contract
matches what operations registers in the Cashfree dashboard:
  POST /api/payments/cashfree/create-order
  POST /api/payments/cashfree/webhook
  GET  /api/payments/cashfree/order/{order_id}

All real logic lives in `payments/router.py` (dispatch) and
`payments/providers/cashfree.py` (provider).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from auth import get_current_user
from payments.router import (
    create_order as _generic_create_order,
    webhook as _generic_webhook,
    get_order as _generic_get_order,
)

router = APIRouter(prefix="/api/payments/cashfree", tags=["payments-cashfree"])


@router.post("/create-order")
async def cashfree_create_order(payload: dict, request: Request, user: dict = Depends(get_current_user)):
    return await _generic_create_order(payload, request, user)


@router.post("/webhook")
async def cashfree_webhook(request: Request):
    return await _generic_webhook("cashfree", request)


@router.get("/order/{order_id}")
async def cashfree_get_order(order_id: str, user: dict = Depends(get_current_user)):
    return await _generic_get_order(order_id, user)
