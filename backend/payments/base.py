"""
Payment provider interface — the contract every gateway must satisfy.

The shape of these dataclasses is deliberately gateway-agnostic. Vendor-specific
fields (Razorpay's `order_id`, Instamojo's `payment_request_id`, Cashfree's
`cf_order_id`, etc.) belong inside `provider_payload` / `provider_meta` so the
abstraction stays clean.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


class GatewayNotConfigured(Exception):
    """Raised by router when no provider is registered or the active provider
    reports configured=False. Callers should translate this to HTTP 503."""

    def __init__(self, message: str = "Payment gateway is not configured."):
        super().__init__(message)
        self.message = message


@dataclass
class ProviderStatus:
    """What `GET /api/payments/status` returns. Public — never expose secrets."""

    provider: str                 # e.g. "razorpay", "instamojo" — "" when no provider
    env: str                      # "test" | "prod" | ""
    configured: bool              # True only when credentials are present AND non-empty
    display_name: str = ""        # Human-friendly: "Razorpay", "Instamojo"


@dataclass
class OrderRequest:
    """Inputs to `create_order`. Built by the router after server-side pricing
    resolution; the provider never trusts the client for amount/currency.
    """

    order_id: str                 # Our internal ID — typically used as gateway txnid
    user_id: str
    user_email: str
    user_name: str
    user_phone: Optional[str]
    kind: str                     # "subscription" | "topup"
    plan_id: Optional[str]        # Set when kind == "subscription"
    pack_id: Optional[str]        # Set when kind == "topup"
    item_name: str                # Display name shown on the gateway's checkout
    credits: int                  # Credits to grant on success
    amount: float                 # Charge amount (already in INR / charge currency)
    currency: str                 # ISO currency code; gateways may only accept INR
    display_amount: float         # Localized display amount (e.g. USD price)
    display_currency: str         # Localized display currency
    success_url: str              # Where the gateway sends the browser on success
    failure_url: str              # Where the gateway sends the browser on failure


@dataclass
class OrderResponse:
    """Output of `create_order`. The router will return this verbatim to the
    frontend; the SDK (or full-page redirect) takes over from there.
    """

    ok: bool
    order_id: str
    provider: str
    # One of these will be populated depending on integration style:
    checkout_url: Optional[str] = None         # Full-page redirect target
    access_key: Optional[str] = None           # Inline-SDK access key
    merchant_key: Optional[str] = None         # Inline-SDK merchant key (public)
    env: str = "test"
    # Free-form payload the frontend may need to feed into the SDK
    provider_payload: dict = field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None


@dataclass
class VerifyResult:
    """Output of `verify_payment` — used by GET /payments/order/{id} reconcile."""

    ok: bool
    status: str                    # "paid" | "pending" | "failed" | "user_dropped" | "expired" | "unknown"
    credited: bool = False         # True iff this call resulted in a credit grant
    provider_txn_id: Optional[str] = None
    reason: Optional[str] = None
    raw: Optional[dict] = None     # Provider's raw response, for audit logs


@dataclass
class WebhookResult:
    """Output of `handle_webhook`. The router always returns 200 to the gateway
    so retries are not triggered for our internal errors.
    """

    ok: bool
    status: str                    # Same vocabulary as VerifyResult.status
    credited: bool = False
    order_id: Optional[str] = None
    reason: Optional[str] = None   # "invalid_hash" | "duplicate" | "amount_mismatch" | ...
    provider_txn_id: Optional[str] = None


@dataclass
class RefundResult:
    """Output of `refund_payment` — placeholder until a real gateway is wired."""

    ok: bool
    refund_id: Optional[str] = None
    status: str = "not_implemented"
    reason: Optional[str] = None


class PaymentProvider(ABC):
    """Contract for any payment gateway implementation.

    Every method is async because real gateways involve network calls. The
    base class also exposes a `name` and `display_name` so the registry can
    look providers up by env var (`PAYMENT_PROVIDER=razorpay`).

    Implementations MUST:
      - Tag every persisted `payment_orders` row with `provider=self.name`.
      - Only call `credit_payment()` from inside a webhook/verify path AFTER
        signature verification + amount equality + idempotency check.
      - Be safe to receive duplicate webhook deliveries (use webhook_dedup or
        the `credited_at` flag on `payment_orders`).
    """

    #: Stable machine identifier — used in URLs (`/payments/webhook/<name>`),
    #: env vars (`PAYMENT_PROVIDER=<name>`), and DB tags (`provider=<name>`).
    name: str = ""

    #: Human-friendly label surfaced in the Pricing footer + admin dashboards.
    display_name: str = ""

    @abstractmethod
    def status(self) -> ProviderStatus:
        """Cheap, synchronous — read env vars only. Called by GET /payments/status
        on every Pricing page load, so do NOT make network calls here.
        """
        raise NotImplementedError

    @abstractmethod
    async def create_order(self, order: OrderRequest) -> OrderResponse:
        """Hit the gateway's order-creation API and return the artifact the
        frontend needs to launch the checkout (access key + merchant key for
        inline SDKs, or a full checkout_url for redirect-style gateways).

        MUST persist a `payment_orders` row with `provider=self.name` BEFORE
        calling the gateway so a network error still leaves an audit trail.
        """
        raise NotImplementedError

    @abstractmethod
    async def verify_payment(self, order_id: str) -> VerifyResult:
        """Authoritative status check — call the gateway directly. Used by
        GET /payments/order/{id} when the local row is still pending. SHOULD
        be idempotent: calling twice on a paid order must NOT double-credit.
        """
        raise NotImplementedError

    @abstractmethod
    async def handle_webhook(self, *, raw_body: bytes, headers: dict, content_type: str) -> WebhookResult:
        """Process an incoming webhook. MUST verify signature, dedup, and only
        call `credit_payment()` on confirmed success. Always log the arrival
        to `webhook_logs` regardless of verification outcome.
        """
        raise NotImplementedError

    async def refund_payment(self, *, order_id: str, amount: Optional[float] = None, reason: str = "") -> RefundResult:
        """OPTIONAL — default implementation returns `not_implemented`. Real
        providers should override this once the live refund flow is verified.
        """
        return RefundResult(ok=False, status="not_implemented", reason="Refund not implemented for this provider yet.")
