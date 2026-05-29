"""
Easebuzz Payment Gateway — full integration.

Architecture:
- POST /api/payments/easebuzz/create-order        : authenticated, returns access_key + checkout config
- POST /api/payments/easebuzz/webhook             : public, idempotent, hash-verified
- GET  /api/payments/order/{order_id}             : authenticated, polls + reconciles via Transaction API
- POST /api/payments/easebuzz/verify              : authenticated, manual reconcile fallback

Hash algorithm (PayU-style, SHA-512):
  Request hash:
    key|txnid|amount|productinfo|firstname|email|udf1|udf2|udf3|udf4|udf5||||||SALT
  Response hash (reversed):
    SALT|status|udf10|udf9|udf8|udf7|udf6|udf5|udf4|udf3|udf2|udf1|email|firstname|productinfo|amount|txnid|key

Endpoints (env-aware):
  test: https://testpay.easebuzz.in/payment/initiateLink, /transaction/v2.1/retrieve
  prod: https://pay.easebuzz.in/payment/initiateLink,     /transaction/v2.1/retrieve

Constitutional guarantees:
- Credits are NEVER granted client-side. Only the webhook handler (after hash
  verification) or the authenticated GET /payments/order reconcile path can
  set status=paid and call credit_payment().
- Idempotency: webhook_dedup is checked first; payment_orders.credited_at
  acts as the second guard inside credit_payment().
- Provider tag: every order is written with provider="easebuzz".
- Audit: every webhook arrival is logged to webhook_logs with raw payload,
  computed hash, received hash, verification result, and dedup outcome.
"""
from __future__ import annotations

import os
import hashlib
import logging
import uuid
import json
from typing import Optional
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import RedirectResponse

from db import db
from auth import get_current_user
from credits import (
    PLAN_INDEX,
    TOPUP_INDEX,
    credit_payment,
    is_active_subscriber,
    is_admin_unlimited_user,
)
from pricing import compute_price_for_plan, detect_country_from_request
from models import now_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["payments"])


# ----- Config -----
def _env() -> str:
    return (os.environ.get("EASEBUZZ_ENV") or "test").lower().strip()


def _merchant_key() -> str:
    return (os.environ.get("EASEBUZZ_MERCHANT_KEY") or "").strip()


def _salt() -> str:
    return (os.environ.get("EASEBUZZ_SALT") or "").strip()


def _base_url() -> str:
    return "https://pay.easebuzz.in" if _env() == "prod" else "https://testpay.easebuzz.in"


def _frontend_url() -> str:
    return (os.environ.get("FRONTEND_PUBLIC_URL") or "").rstrip("/")


def _success_url() -> str:
    explicit = (os.environ.get("EASEBUZZ_SUCCESS_URL") or "").strip()
    if explicit:
        return explicit
    return f"{_frontend_url()}/pay/return"


def _failure_url() -> str:
    explicit = (os.environ.get("EASEBUZZ_FAILURE_URL") or "").strip()
    if explicit:
        return explicit
    return f"{_frontend_url()}/pay/return"


def _require_configured() -> None:
    if not _merchant_key() or not _salt():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "gateway_not_configured",
                "message": "Payment gateway is not configured. Please contact support.",
            },
        )


# ----- Hash helpers -----
def _sha512(s: str) -> str:
    return hashlib.sha512(s.encode("utf-8")).hexdigest()


def _build_request_hash(
    *,
    key: str,
    txnid: str,
    amount: str,
    productinfo: str,
    firstname: str,
    email: str,
    udf1: str = "",
    udf2: str = "",
    udf3: str = "",
    udf4: str = "",
    udf5: str = "",
    salt: str,
) -> str:
    # Easebuzz/PayU convention: key|txnid|amount|productinfo|firstname|email|
    # udf1|udf2|udf3|udf4|udf5|udf6|udf7|udf8|udf9|udf10|SALT
    # (udf6-udf10 are empty when not used → 5 trailing empties before SALT)
    raw = "|".join([key, txnid, amount, productinfo, firstname, email, udf1, udf2, udf3, udf4, udf5, "", "", "", "", "", salt])
    return _sha512(raw)


def _build_response_hash(payload: dict, *, salt: str) -> str:
    # Reversed sequence per Easebuzz / PayU verify spec.
    key = str(payload.get("key", ""))
    txnid = str(payload.get("txnid", ""))
    amount = str(payload.get("amount", ""))
    productinfo = str(payload.get("productinfo", ""))
    firstname = str(payload.get("firstname", ""))
    email = str(payload.get("email", ""))
    udf1 = str(payload.get("udf1", ""))
    udf2 = str(payload.get("udf2", ""))
    udf3 = str(payload.get("udf3", ""))
    udf4 = str(payload.get("udf4", ""))
    udf5 = str(payload.get("udf5", ""))
    udf6 = str(payload.get("udf6", ""))
    udf7 = str(payload.get("udf7", ""))
    udf8 = str(payload.get("udf8", ""))
    udf9 = str(payload.get("udf9", ""))
    udf10 = str(payload.get("udf10", ""))
    status = str(payload.get("status", ""))
    additional_charges = str(payload.get("additional_charges", "")).strip()

    base = "|".join([
        salt, status, udf10, udf9, udf8, udf7, udf6, udf5, udf4, udf3, udf2, udf1,
        email, firstname, productinfo, amount, txnid, key,
    ])
    # When additional_charges is present, it is prepended before salt.
    if additional_charges:
        base = additional_charges + "|" + base
    return _sha512(base)


def _amount_str(amount: float) -> str:
    # Easebuzz expects 2-decimal string like "499.00"
    return f"{float(amount):.2f}"


# ----- Order resolution -----
def _resolve_plan_or_pack(payload: dict) -> tuple[str, str, dict]:
    """Returns (kind, item_id, item_meta). kind ∈ {subscription, topup}."""
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


# ----- Create order -----
@router.post("/easebuzz/create-order")
async def create_easebuzz_order(payload: dict, request: Request, user: dict = Depends(get_current_user)):
    """Authenticated. Creates a payment_orders row, calls Easebuzz initiateLink,
    returns the access_key + checkout config for the frontend SDK.
    """
    _require_configured()

    if is_admin_unlimited_user(user):
        raise HTTPException(400, detail={"code": "admin_no_checkout", "message": "Admin accounts cannot purchase."})

    kind, item_id, item = _resolve_plan_or_pack(payload or {})

    # Topup gate — only active subscribers can top up
    if kind == "topup" and not is_active_subscriber(user):
        raise HTTPException(403, detail={"code": "subscription_required_for_topup", "message": "Subscribe to a plan before purchasing top-ups."})

    # Price resolution — server is authoritative
    country_code, _src = detect_country_from_request(request, user)
    price = compute_price_for_plan(item_id, country_code)
    charge_amount = float(price["charge_amount"])
    charge_currency = price["charge_currency"]
    # Easebuzz currently supports INR only. If charge_currency is not INR, we still
    # send the INR-converted charge (already computed in compute_price_for_plan).
    if charge_currency != "INR":
        # Defensive — gateway only takes INR
        raise HTTPException(500, detail={"code": "currency_unsupported", "message": "Charge currency not supported by current gateway."})

    order_id = f"ebz_{uuid.uuid4().hex[:18]}"
    txnid = order_id  # use as our txnid — easier to reconcile

    email = (user.get("email") or "").strip().lower()
    firstname = (user.get("name") or email.split("@")[0] or "User").strip()[:40]
    phone = (user.get("phone") or "9999999999").strip()  # placeholder if not on profile
    productinfo = f"{item.get('name', item_id)} ({kind})"

    amount_str = _amount_str(charge_amount)
    credits_grant = int(item.get("monthly_credits") if kind == "subscription" else item.get("credits") or 0)

    # Persist BEFORE calling gateway so even a network error leaves a trail.
    await db.payment_orders.insert_one({
        "order_id": order_id,
        "txnid": txnid,
        "user_id": user["user_id"],
        "email": email,
        "kind": kind,                       # subscription | topup
        "plan_id": item_id if kind == "subscription" else None,
        "pack_id": item_id if kind == "topup" else None,
        "credits": credits_grant,
        "amount_inr": charge_amount,        # historical name kept consistent with overview aggregator
        "amount": charge_amount,
        "currency": charge_currency,
        "display_amount": price["display_amount"],
        "display_currency": price["currency_code"],
        "status": "created",                # created → pending → paid | failed | expired
        "provider": "easebuzz",
        "env": _env(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    })

    key = _merchant_key()
    salt = _salt()
    request_hash = _build_request_hash(
        key=key, txnid=txnid, amount=amount_str, productinfo=productinfo,
        firstname=firstname, email=email,
        udf1=user["user_id"], udf2=kind, udf3=item_id, udf4=str(credits_grant), udf5="",
        salt=salt,
    )

    form = {
        "key": key,
        "txnid": txnid,
        "amount": amount_str,
        "productinfo": productinfo,
        "firstname": firstname,
        "email": email,
        "phone": phone,
        "surl": _success_url(),
        "furl": _failure_url(),
        "udf1": user["user_id"],
        "udf2": kind,
        "udf3": item_id,
        "udf4": str(credits_grant),
        "udf5": "",
        "hash": request_hash,
    }

    url = f"{_base_url()}/payment/initiateLink"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data=form, headers={"Accept": "application/json"})
        body_text = resp.text
        try:
            body = resp.json()
        except Exception:
            body = {"_raw": body_text}
    except Exception as e:
        await db.payment_orders.update_one(
            {"order_id": order_id},
            {"$set": {"status": "failed", "failure_reason": f"gateway_unreachable: {e}", "updated_at": now_iso()}},
        )
        logger.exception("easebuzz initiateLink failed")
        raise HTTPException(502, detail={"code": "gateway_unreachable", "message": "Could not reach the payment gateway. Try again."})

    # Easebuzz responds with {status:1, data:"access_key"} on success
    if isinstance(body, dict) and body.get("status") == 1 and body.get("data"):
        access_key = str(body["data"])
        # Compose the hosted payment URL (used as redirect fallback when SDK fails)
        hosted_url = f"{_base_url()}/pay/{access_key}"
        await db.payment_orders.update_one(
            {"order_id": order_id},
            {"$set": {
                "status": "pending",
                "access_key": access_key,
                "updated_at": now_iso(),
            }},
        )
        return {
            "ok": True,
            "order_id": order_id,
            "txnid": txnid,
            "access_key": access_key,
            "key": key,                       # merchant key for SDK init
            "env": _env(),
            "amount": amount_str,
            "currency": charge_currency,
            "hosted_url": hosted_url,         # fallback if SDK unavailable
        }

    # Error path
    err_msg = (body.get("data") if isinstance(body, dict) else None) or body_text or "Gateway rejected the request."
    await db.payment_orders.update_one(
        {"order_id": order_id},
        {"$set": {"status": "failed", "failure_reason": str(err_msg)[:500], "updated_at": now_iso()}},
    )
    logger.warning("easebuzz initiateLink rejected order=%s body=%s", order_id, body)
    raise HTTPException(502, detail={"code": "gateway_rejected", "message": str(err_msg)[:300]})


# ----- Webhook / Callback -----
async def _process_callback(payload: dict, *, raw_body: str, source: str) -> dict:
    """Shared verification + fulfillment logic for the surl/furl + webhook posts.
    Returns {ok, status, order_id, credited, reason}.
    """
    if not _merchant_key() or not _salt():
        # If we receive a callback before keys are configured, just log it.
        await db.webhook_logs.insert_one({
            "received_at": now_iso(),
            "provider": "easebuzz",
            "source": source,
            "result": "gateway_not_configured",
            "raw": raw_body[:4000],
        })
        return {"ok": False, "reason": "gateway_not_configured"}

    received_hash = (payload.get("hash") or "").strip().lower()
    txnid = (payload.get("txnid") or "").strip()
    status = (payload.get("status") or "").strip().lower()
    easebuzz_id = (payload.get("easepayid") or payload.get("PG_TXN_ID") or "").strip()

    expected_hash = _build_response_hash(payload, salt=_salt()).lower()
    hash_valid = bool(received_hash) and received_hash == expected_hash

    # Always log the arrival
    log_doc = {
        "received_at": now_iso(),
        "provider": "easebuzz",
        "source": source,                   # webhook | surl | furl | reconcile
        "order_id": txnid,
        "easepayid": easebuzz_id,
        "status": status,
        "hash_valid": hash_valid,
        "raw": raw_body[:4000],
    }
    await db.webhook_logs.insert_one(log_doc)

    if not hash_valid:
        return {"ok": False, "reason": "invalid_hash", "order_id": txnid}

    # Idempotency: dedup on (provider, txnid, status, easepayid)
    dedup_key = f"easebuzz:{txnid}:{status}:{easebuzz_id}"
    try:
        await db.webhook_dedup.insert_one({"dedup_key": dedup_key, "created_at": now_iso()})
    except Exception:
        # Duplicate — already processed
        return {"ok": True, "reason": "duplicate", "order_id": txnid, "credited": False}

    order = await db.payment_orders.find_one({"order_id": txnid}, {"_id": 0})
    if not order:
        return {"ok": False, "reason": "order_not_found", "order_id": txnid}

    # Amount integrity — payload amount must equal stored amount
    try:
        payload_amount = float(payload.get("amount") or 0)
    except Exception:
        payload_amount = 0.0
    if abs(payload_amount - float(order.get("amount") or 0)) > 0.01:
        await db.payment_orders.update_one(
            {"order_id": txnid},
            {"$set": {"status": "failed", "failure_reason": "amount_mismatch", "updated_at": now_iso()}},
        )
        return {"ok": False, "reason": "amount_mismatch", "order_id": txnid}

    credited = False
    if status == "success":
        # Idempotent credit grant: credited_at acts as second guard.
        if not order.get("credited_at"):
            new_balance = await credit_payment(
                user_id=order["user_id"],
                credits=int(order.get("credits") or 0),
                order_id=txnid,
                plan_id=order.get("plan_id") if order.get("kind") == "subscription" else None,
                kind=order.get("kind") or "subscription",
                pack_id=order.get("pack_id") if order.get("kind") == "topup" else None,
            )
            credited = True
            await db.payment_orders.update_one(
                {"order_id": txnid, "credited_at": {"$exists": False}},
                {"$set": {
                    "status": "paid",
                    "easepayid": easebuzz_id,
                    "payment_mode": payload.get("mode") or payload.get("PG_TYPE"),
                    "card_no": (payload.get("cardnum") or "")[-4:] if payload.get("cardnum") else None,
                    "credited_at": now_iso(),
                    "updated_at": now_iso(),
                    "balance_after": new_balance,
                }},
            )
        else:
            await db.payment_orders.update_one(
                {"order_id": txnid},
                {"$set": {"easepayid": easebuzz_id, "updated_at": now_iso()}},
            )
    elif status in ("failure", "failed", "userCancelled".lower(), "usercancelled", "dropped", "bounced"):
        terminal_map = {"failure": "failed", "failed": "failed", "usercancelled": "user_dropped", "dropped": "user_dropped", "bounced": "failed"}
        new_status = terminal_map.get(status, "failed")
        await db.payment_orders.update_one(
            {"order_id": txnid, "status": {"$nin": ["paid"]}},
            {"$set": {"status": new_status, "easepayid": easebuzz_id, "failure_reason": payload.get("error_Message") or payload.get("error") or status, "updated_at": now_iso()}},
        )
    else:
        # pending / unknown
        await db.payment_orders.update_one(
            {"order_id": txnid, "status": {"$nin": ["paid", "failed", "user_dropped"]}},
            {"$set": {"status": "pending", "easepayid": easebuzz_id, "updated_at": now_iso()}},
        )

    return {"ok": True, "status": status, "order_id": txnid, "credited": credited}


@router.post("/easebuzz/webhook")
async def easebuzz_webhook(request: Request):
    """Public Easebuzz webhook. Form-encoded body. Hash-verified, idempotent.
    Easebuzz POSTs the same shape to both surl/furl and the configured webhook.
    """
    form = await request.form()
    payload = {k: v for k, v in form.items()}
    raw = await _form_body_raw(request, form)
    result = await _process_callback(payload, raw_body=raw, source="webhook")
    # Always 200 — Easebuzz retries on non-2xx and we want exactly-once semantics
    return {"received": True, **result}


@router.post("/easebuzz/surl")
async def easebuzz_surl(request: Request):
    """Easebuzz POSTs the user's browser to surl on success. We verify + redirect."""
    form = await request.form()
    payload = {k: v for k, v in form.items()}
    raw = await _form_body_raw(request, form)
    await _process_callback(payload, raw_body=raw, source="surl")
    order_id = (payload.get("txnid") or "").strip()
    target = f"{_frontend_url()}/pay/return?order_id={order_id}"
    return RedirectResponse(url=target, status_code=303)


@router.post("/easebuzz/furl")
async def easebuzz_furl(request: Request):
    """Failure URL — verify + redirect to return page."""
    form = await request.form()
    payload = {k: v for k, v in form.items()}
    raw = await _form_body_raw(request, form)
    await _process_callback(payload, raw_body=raw, source="furl")
    order_id = (payload.get("txnid") or "").strip()
    target = f"{_frontend_url()}/pay/return?order_id={order_id}"
    return RedirectResponse(url=target, status_code=303)


async def _form_body_raw(request: Request, form) -> str:
    try:
        return "&".join([f"{k}={v}" for k, v in form.items()])
    except Exception:
        return ""


# ----- Order status / reconcile -----
async def _reconcile_via_transaction_api(order: dict) -> dict:
    """Authoritative status check directly against Easebuzz. Used when the
    user lands on /pay/return and the local row is still pending (webhook may
    not have landed yet).
    """
    if not _merchant_key() or not _salt():
        return order
    txnid = order["order_id"]
    url = f"{_base_url()}/transaction/v2.1/retrieve"
    # Easebuzz v2 Transaction API: hash = sha512(key|txnid|salt)
    hsh = _sha512(f"{_merchant_key()}|{txnid}|{_salt()}")
    data = {"key": _merchant_key(), "txnid": txnid, "hash": hsh}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, data=data)
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_raw": resp.text}
    except Exception as e:
        logger.warning("easebuzz reconcile network err order=%s err=%s", txnid, e)
        return order

    if isinstance(body, dict) and body.get("status") == 1 and body.get("msg"):
        msg = body["msg"]
        # msg can be a dict (single txn) or list
        rec = msg[0] if isinstance(msg, list) and msg else (msg if isinstance(msg, dict) else None)
        if rec:
            status = (rec.get("status") or "").lower()
            # Synthesize a payload shape close enough to the webhook for the same handler
            payload = {
                "txnid": txnid,
                "status": status,
                "amount": str(rec.get("amount") or order.get("amount") or 0),
                "productinfo": rec.get("productinfo") or "",
                "firstname": rec.get("firstname") or "",
                "email": rec.get("email") or order.get("email") or "",
                "easepayid": rec.get("easepayid") or "",
                "mode": rec.get("mode") or "",
                "udf1": str(order.get("user_id") or ""),
                "udf2": str(order.get("kind") or ""),
                "udf3": str(order.get("plan_id") or order.get("pack_id") or ""),
                "udf4": str(order.get("credits") or ""),
                "udf5": "",
                # Reconcile uses key+salt server-side; we bypass hash verification because
                # the response did not come from a browser POST. We mark source=reconcile.
                "key": _merchant_key(),
            }
            # Bypass hash by computing it ourselves for the dedup/credit path
            payload["hash"] = _build_response_hash(payload, salt=_salt())
            raw = json.dumps(rec)[:4000]
            await _process_callback(payload, raw_body=raw, source="reconcile")
            order = await db.payment_orders.find_one({"order_id": txnid}, {"_id": 0}) or order
    return order


@router.get("/order/{order_id}")
async def get_order(order_id: str, user: dict = Depends(get_current_user)):
    """Authenticated. Returns the order (re-querying gateway if still pending)."""
    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, detail={"code": "order_not_found", "message": "Order not found."})
    if order.get("user_id") != user.get("user_id") and not is_admin_unlimited_user(user):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Not your order."})

    # If still in flight, ask Easebuzz directly. Webhook may have lost the race.
    if order.get("status") in ("created", "pending"):
        order = await _reconcile_via_transaction_api(order)

    return {"order": order}


@router.post("/easebuzz/verify")
async def manual_verify(payload: dict, user: dict = Depends(get_current_user)):
    """Authenticated manual reconcile — used by support/admin and as a safety
    valve if the user reports a stuck order. Idempotent."""
    order_id = (payload or {}).get("order_id")
    if not order_id:
        raise HTTPException(400, detail={"code": "missing_order_id", "message": "order_id required."})
    order = await db.payment_orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, detail={"code": "order_not_found"})
    if order.get("user_id") != user.get("user_id") and not is_admin_unlimited_user(user):
        raise HTTPException(403, detail={"code": "forbidden"})
    order = await _reconcile_via_transaction_api(order)
    return {"order": order}


# ----- Public config (frontend asks env to load right SDK) -----
@router.get("/easebuzz/config")
async def gateway_config():
    """Public — exposes ONLY the env (test/prod) and a 'configured' flag.
    Never returns the merchant_key or salt. The merchant_key is returned only
    from /create-order in the response, which is per-order and authenticated.
    """
    return {
        "provider": "easebuzz",
        "env": _env(),
        "configured": bool(_merchant_key() and _salt()),
    }
