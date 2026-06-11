"""
Generic FastAPI router that delegates to whichever PaymentProvider is active.

Every route here is gateway-agnostic. When a new provider is added, this file
does not change.

Constitutional guarantees (re-stated so a new agent reading just this file
understands the rules):
- Credits are NEVER granted from a route handler. Only the active provider's
  `handle_webhook` / `verify_payment` can call `credit_payment()`, and only
  after signature verification + amount equality + idempotency.
- `GET /api/payments/status` must NEVER make a network call (called on every
  Pricing page load). It reads env vars only.
- Mutating endpoints fail closed: when no provider is configured, they return
  HTTP 503 with `code=gateway_not_configured` so the frontend stays in the
  inert "Payments offline" state.
"""
from __future__ import annotations

import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from db import db
from auth import get_current_user
from credits import (
    PLAN_INDEX,
    TOPUP_INDEX,
    is_active_subscriber,
    is_admin_unlimited_user,
)
from pricing import compute_price_for_plan, detect_country_from_request
from models import now_iso

from .base import (
    OrderRequest,
    ProviderStatus,
    GatewayNotConfigured,
)
from .registry import (
    get_active_provider,
    get_provider_by_name,
    active_provider_name,
    list_registered_providers,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["payments"])


# ----- Public status -----
@router.get("/status")
async def payments_status():
    """Public, no-auth. Read-only. Called on every Pricing page load — keep
    this cheap (no network calls).

    Shape:
      {
        "provider": "razorpay" | "instamojo" | "",
        "env": "test" | "prod" | "",
        "configured": bool,
        "display_name": "Razorpay" | "",
        "registered_providers": ["razorpay", "instamojo"],
      }

    When `configured=false`, the frontend keeps the "Payments offline" banner
    and renders Subscribe/Top-up CTAs as inert placeholders.
    """
    name = active_provider_name()
    if name:
        prov = get_provider_by_name(name)
        if prov:
            st: ProviderStatus = prov.status()
            return {
                "provider": st.provider,
                "env": st.env,
                "configured": st.configured,
                "display_name": st.display_name,
                "registered_providers": list_registered_providers(),
            }
    return {
        "provider": "",
        "env": "",
        "configured": False,
        "display_name": "",
        "registered_providers": list_registered_providers(),
    }


# ----- Create order -----
def _resolve_plan_or_pack(payload: dict) -> tuple[str, str, dict]:
    plan_id = (payload.get("plan_id") or "").strip().lower()
    pack_id = (payload.get("pack_id") or "").strip().lower()
    if plan_id and pack_id:
        raise HTTPException(400, detail={"code": "ambiguous_order", "message": "Send either plan_id or pack_id, not both."})
    if plan_id:
        plan = PLAN_INDEX.get(plan_id)
        if not plan or plan_id == "free":
            raise HTTPException(400, detail={"code": "invalid_plan", "message": "Unknown plan."})
        return "subscription", plan_id, plan
    if pack_id:
        pack = TOPUP_INDEX.get(pack_id)
        if not pack:
            raise HTTPException(400, detail={"code": "invalid_topup", "message": "Unknown top-up pack."})
        return "topup", pack_id, pack
    raise HTTPException(400, detail={"code": "missing_item", "message": "Provide plan_id or pack_id."})


@router.post("/create-order")
async def create_order(payload: dict, request: Request, user: dict = Depends(get_current_user)):
    """Authenticated. Dispatches to the active provider's `create_order`.

    Server is authoritative for price + credits. The request body only needs
    `plan_id` OR `pack_id`. Frontend never sends an amount.
    """
    # Anti-abuse guard — payments are high-value: strict caps. Admin emails
    # are blocked from checkout by design (admin_no_checkout below) but
    # exempt from rate limiting so a friend on a shared NAT can't lock them out.
    from anti_abuse import guard_expensive_action
    await guard_expensive_action(
        user=user, scope="payment.create_order", request=request,
        max_per_user_per_min=5, max_per_user_per_hour=20,
        endpoint="POST /api/payments/create-order",
    )

    try:
        provider = get_active_provider()
    except GatewayNotConfigured as e:
        raise HTTPException(503, detail={"code": "gateway_not_configured", "message": e.message})

    if is_admin_unlimited_user(user):
        raise HTTPException(400, detail={"code": "admin_no_checkout", "message": "Admin accounts cannot purchase."})

    kind, item_id, item = _resolve_plan_or_pack(payload or {})
    if kind == "topup" and not is_active_subscriber(user):
        raise HTTPException(403, detail={"code": "subscription_required_for_topup", "message": "Subscribe to a plan before purchasing top-ups."})

    # Server-side price resolution — single source of truth.
    country_code, _src = detect_country_from_request(request, user)
    price = compute_price_for_plan(item_id, country_code)
    if price["charge_currency"] != "INR":
        raise HTTPException(500, detail={"code": "currency_unsupported", "message": "Charge currency not supported."})

    order_id = f"{provider.name[:4]}_{uuid.uuid4().hex[:18]}"
    credits_grant = int(item.get("monthly_credits") if kind == "subscription" else item.get("credits") or 0)

    # Compose the success/failure return URLs — provider modules may override
    # internally but defaults are routed through our PaymentReturn page.
    from os import environ
    frontend = (environ.get("FRONTEND_PUBLIC_URL") or "").rstrip("/")
    success_url = f"{frontend}/pay/return?order_id={order_id}"
    failure_url = f"{frontend}/pay/return?order_id={order_id}"

    req = OrderRequest(
        order_id=order_id,
        user_id=user["user_id"],
        user_email=(user.get("email") or "").lower(),
        user_name=(user.get("name") or (user.get("email") or "User").split("@")[0])[:40],
        user_phone=user.get("phone"),
        kind=kind,
        plan_id=item_id if kind == "subscription" else None,
        pack_id=item_id if kind == "topup" else None,
        item_name=item.get("name", item_id),
        credits=credits_grant,
        amount=float(price["charge_amount"]),
        currency=price["charge_currency"],
        display_amount=float(price["display_amount"]),
        display_currency=price["currency_code"],
        success_url=success_url,
        failure_url=failure_url,
    )

    try:
        resp = await provider.create_order(req)
    except Exception:
        logger.exception("payments.create_order failed provider=%s order=%s", provider.name, order_id)
        raise HTTPException(502, detail={"code": "gateway_unreachable", "message": "Could not reach the payment gateway. Try again."})

    if not resp.ok:
        raise HTTPException(502, detail={"code": resp.error_code or "gateway_rejected", "message": resp.error or "Gateway rejected the request."})

    return {
        "ok": True,
        "order_id": resp.order_id,
        "provider": resp.provider,
        "env": resp.env,
        "checkout_url": resp.checkout_url,
        "access_key": resp.access_key,
        "merchant_key": resp.merchant_key,
        "payload": resp.provider_payload,
    }


# ----- Order status / reconcile -----
@router.get("/order/{order_id}")
async def get_order(order_id: str, user: dict = Depends(get_current_user)):
    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, detail={"code": "order_not_found", "message": "Order not found."})
    if order.get("user_id") != user.get("user_id") and not is_admin_unlimited_user(user):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Not your order."})

    # Only the order's original provider can authoritatively reconcile it.
    if order.get("status") in ("created", "pending"):
        provider_name = (order.get("provider") or "").lower()
        prov = get_provider_by_name(provider_name)
        if prov:
            try:
                vres = await prov.verify_payment(order_id)
                if vres and vres.ok:
                    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0}) or order
            except Exception:
                logger.exception("verify_payment failed provider=%s order=%s", provider_name, order_id)
        else:
            logger.warning("order %s was created by provider=%s which is no longer registered", order_id, provider_name)

    return {"order": order}


# ----- Webhook -----
@router.post("/webhook/{provider_name}")
async def webhook(provider_name: str, request: Request):
    """Public webhook endpoint. Dispatches by URL path so the provider that
    originally created the order is the only one that can fulfill it. Returns
    200 + payload so the gateway does not retry; verification + crediting are
    the provider's job inside `handle_webhook`.

    A webhook arriving for a provider we no longer ship returns 410 Gone so
    the gateway eventually stops retrying after their backoff window.
    """
    prov = get_provider_by_name(provider_name)
    raw_body = await request.body()
    if not prov:
        # Audit log even unknown arrivals — replay-attack visibility.
        await db.webhook_logs.insert_one({
            "received_at": now_iso(),
            "provider": provider_name,
            "source": "webhook_unknown_provider",
            "result": "unknown_provider",
            "raw": (raw_body[:4000] or b"").decode("utf-8", "replace"),
        })
        raise HTTPException(410, detail={"code": "provider_not_registered", "message": f"No provider '{provider_name}' is registered."})

    headers = {k.lower(): v for k, v in request.headers.items()}
    content_type = headers.get("content-type", "")
    try:
        result = await prov.handle_webhook(raw_body=raw_body, headers=headers, content_type=content_type)
    except Exception:
        logger.exception("webhook handler failed provider=%s", provider_name)
        # Still 200 — gateways retry on non-2xx and that creates duplicate work.
        return {"received": True, "ok": False, "reason": "handler_exception"}

    return {"received": True, "ok": result.ok, "status": result.status, "credited": result.credited, "order_id": result.order_id, "reason": result.reason}


# ----- Browser-POST return (surl/furl pattern) -----
@router.post("/return/{provider_name}")
async def browser_return(provider_name: str, request: Request):
    """Some gateways POST the browser to our surl/furl. Verify the same way as
    the webhook would, then 303-redirect to /pay/return so the React app takes
    over and reads the authoritative status via GET /payments/order/{id}.
    """
    prov = get_provider_by_name(provider_name)
    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    content_type = headers.get("content-type", "")

    if prov:
        try:
            await prov.handle_webhook(raw_body=raw_body, headers=headers, content_type=content_type)
        except Exception:
            logger.exception("browser return handler failed provider=%s", provider_name)

    # Pull order_id out of x-www-form-urlencoded or query string for the
    # frontend redirect. Provider modules are encouraged to set it as `txnid`.
    order_id = ""
    try:
        form = await request.form()
        order_id = (form.get("txnid") or form.get("order_id") or "").strip()
    except Exception:
        order_id = (request.query_params.get("order_id") or "").strip()

    from os import environ
    frontend = (environ.get("FRONTEND_PUBLIC_URL") or "").rstrip("/")
    target = f"{frontend}/pay/return?order_id={order_id}" if order_id else f"{frontend}/pay/return"
    return Response(status_code=303, headers={"location": target})


# ----- Refund (admin, placeholder) -----
@router.post("/refund")
async def refund(payload: dict, user: dict = Depends(get_current_user)):
    """Admin-only placeholder. Real providers override `refund_payment` with a
    verified gateway call. Until one does, this returns `not_implemented`."""
    if not is_admin_unlimited_user(user) and user.get("role") != "admin":
        raise HTTPException(403, detail={"code": "admin_only", "message": "Admin only."})
    order_id = (payload or {}).get("order_id")
    amount = (payload or {}).get("amount")
    reason = (payload or {}).get("reason") or ""
    if not order_id:
        raise HTTPException(400, detail={"code": "missing_order_id", "message": "order_id required."})

    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, detail={"code": "order_not_found"})

    provider_name = (order.get("provider") or "").lower()
    prov = get_provider_by_name(provider_name)
    if not prov:
        raise HTTPException(503, detail={"code": "provider_not_registered", "message": f"Provider '{provider_name}' that created this order is not registered."})

    res = await prov.refund_payment(order_id=order_id, amount=amount, reason=reason)
    if not res.ok and res.status == "not_implemented":
        raise HTTPException(501, detail={"code": "refund_not_implemented", "message": res.reason or "Refund not implemented for this provider yet."})
    return {"ok": res.ok, "refund_id": res.refund_id, "status": res.status, "reason": res.reason}
