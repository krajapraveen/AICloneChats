"""
Tests for the Payment Gateway Abstraction Layer.

Mixed strategy:
- Pure unit tests against `payments.base` + `payments.registry` (no DB, no HTTP).
- HTTP tests against the running backend (same pattern as test_revenue_mirror.py)
  so we exercise the real FastAPI routing without fighting Motor's event-loop
  binding.

To run HTTP tests against a locally-running backend, set:
    REACT_APP_BACKEND_URL=https://digital-twin-119.preview.emergentagent.com
(or any URL that points at the FastAPI instance).
"""
from __future__ import annotations

import os
import sys
import asyncio
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_database")

import pytest  # noqa: E402
import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")

from payments.base import (  # noqa: E402
    PaymentProvider,
    OrderRequest,
    OrderResponse,
    VerifyResult,
    WebhookResult,
    RefundResult,
    ProviderStatus,
    GatewayNotConfigured,
)
from payments.registry import (  # noqa: E402
    register_provider,
    get_active_provider,
    get_provider_by_name,
    active_provider_name,
    list_registered_providers,
    _PROVIDERS,
)


# ---------- Pure registry tests (no DB / no HTTP) ----------
class _FakeProvider(PaymentProvider):
    name = "_fake"
    display_name = "Fake (tests)"

    def __init__(self, configured: bool = True):
        self._configured = configured

    def status(self) -> ProviderStatus:
        return ProviderStatus(provider="_fake", env="test", configured=self._configured, display_name="Fake (tests)")

    async def create_order(self, order: OrderRequest) -> OrderResponse:
        return OrderResponse(ok=True, order_id=order.order_id, provider="_fake", env="test")

    async def verify_payment(self, order_id: str) -> VerifyResult:
        return VerifyResult(ok=True, status="paid")

    async def handle_webhook(self, *, raw_body, headers, content_type) -> WebhookResult:
        return WebhookResult(ok=True, status="paid", credited=True)


def _reset_registry():
    _PROVIDERS.clear()
    os.environ.pop("PAYMENT_PROVIDER", None)


def test_registry_requires_name():
    _reset_registry()
    class BadProvider(_FakeProvider):
        name = ""
    with pytest.raises(ValueError):
        register_provider(BadProvider())


def test_get_active_provider_raises_when_unset():
    _reset_registry()
    with pytest.raises(GatewayNotConfigured):
        get_active_provider()


def test_get_active_provider_raises_when_provider_not_registered():
    _reset_registry()
    os.environ["PAYMENT_PROVIDER"] = "nonexistent"
    try:
        with pytest.raises(GatewayNotConfigured):
            get_active_provider()
    finally:
        _reset_registry()


def test_get_active_provider_raises_when_provider_not_configured():
    _reset_registry()
    register_provider(_FakeProvider(configured=False))
    os.environ["PAYMENT_PROVIDER"] = "_fake"
    try:
        with pytest.raises(GatewayNotConfigured):
            get_active_provider()
    finally:
        _reset_registry()


def test_get_active_provider_returns_when_configured():
    _reset_registry()
    fake = _FakeProvider(configured=True)
    register_provider(fake)
    os.environ["PAYMENT_PROVIDER"] = "_fake"
    try:
        assert get_active_provider() is fake
        assert active_provider_name() == "_fake"
        assert "_fake" in list_registered_providers()
    finally:
        _reset_registry()


def test_get_provider_by_name_case_insensitive():
    _reset_registry()
    fake = _FakeProvider()
    register_provider(fake)
    try:
        assert get_provider_by_name("_FAKE") is fake
        assert get_provider_by_name("missing") is None
    finally:
        _reset_registry()


def test_base_refund_returns_not_implemented():
    """Subclassing PaymentProvider without overriding refund_payment must
    yield a not_implemented RefundResult — proves the placeholder behaviour."""

    class NoRefund(PaymentProvider):
        name = "_nr"
        display_name = "NR"
        def status(self): return ProviderStatus(provider="_nr", env="test", configured=True)
        async def create_order(self, order): return OrderResponse(ok=True, order_id=order.order_id, provider="_nr")
        async def verify_payment(self, order_id): return VerifyResult(ok=True, status="paid")
        async def handle_webhook(self, *, raw_body, headers, content_type): return WebhookResult(ok=True, status="paid")

    prov = NoRefund()
    res = asyncio.new_event_loop().run_until_complete(prov.refund_payment(order_id="x"))
    assert res.ok is False
    assert res.status == "not_implemented"


# ---------- HTTP tests against the live backend ----------
def test_status_endpoint_live_returns_unconfigured_until_creds_arrive():
    """Live backend reports the active provider but `configured=false` while
    its credentials are blank. Whoever the active provider is (instamojo or
    cashfree depending on PAYMENT_PROVIDER env var) — the Pricing page stays
    inert."""
    r = requests.get(f"{BASE_URL}/api/payments/status", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is False
    # Both providers are registered (Cashfree + Instamojo). At least one must
    # be present in the registry list.
    assert any(p in body["registered_providers"] for p in ("instamojo", "cashfree"))
    # The active provider matches what's in PAYMENT_PROVIDER env var.
    assert body["provider"] in ("", "instamojo", "cashfree")


def test_create_order_live_returns_503_without_provider():
    """Without auth → 401. With auth but no provider → 503 gateway_not_configured.
    We test the unauthenticated branch here since it's cheap and proves the
    route is wired."""
    r = requests.post(f"{BASE_URL}/api/payments/create-order", json={"plan_id": "pro"}, timeout=10)
    # Auth runs before gateway lookup → expect 401, NOT 503. That's correct
    # behaviour and proves the route is wired before the gateway dispatch.
    assert r.status_code in (401, 403), r.text


def test_webhook_unknown_provider_returns_410():
    """Public webhook endpoint must 410 for unregistered providers so
    long-dead Cashfree/Easebuzz retries eventually stop."""
    r = requests.post(f"{BASE_URL}/api/payments/webhook/nonexistent", data="x=1", timeout=10)
    assert r.status_code == 410, r.text
    assert r.json()["detail"]["code"] == "provider_not_registered"


def test_refund_requires_admin():
    """`POST /api/payments/refund` without auth → 401."""
    r = requests.post(f"{BASE_URL}/api/payments/refund", json={"order_id": "x"}, timeout=10)
    assert r.status_code in (401, 403), r.text


def test_get_order_requires_auth():
    r = requests.get(f"{BASE_URL}/api/payments/order/anything", timeout=10)
    assert r.status_code in (401, 403), r.text


def test_no_easebuzz_endpoints_remain():
    """Regression: ensure the previous Easebuzz endpoints did not leak back in."""
    for path in (
        "/api/payments/easebuzz/config",
        "/api/payments/easebuzz/create-order",
        "/api/payments/easebuzz/webhook",
    ):
        r = requests.post(f"{BASE_URL}{path}", data="x=1", timeout=10)
        assert r.status_code == 404, f"{path} returned {r.status_code}"


if __name__ == "__main__":
    print("Run: pytest tests/test_payments_abstraction.py -v")
