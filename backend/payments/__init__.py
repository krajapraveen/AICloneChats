"""
Payment Gateway Abstraction Layer for aiclonechats.com.

Design philosophy:
- The rest of the app talks to ONE interface, never to a vendor SDK directly.
- A new gateway = one file in `payments/providers/<name>.py` that subclasses
  `PaymentProvider` + an env var `PAYMENT_PROVIDER=<name>`. Zero other edits.
- Webhook handling, hash verification, idempotency, and credit granting are
  the provider's responsibility (signature spec differs per vendor), but
  `credit_payment()` from `credits.py` remains the ONLY function that mutates
  user credit balances.
- Tagging: every order is persisted with `provider=<name>` so historical
  Cashfree and Easebuzz audit migrations remain meaningful, and so this layer
  never accidentally fulfills an order from a different provider.
- Failure mode: when no provider is registered, every endpoint behaves
  predictably (`status` reports `configured=false`, mutating endpoints
  return 503 `gateway_not_configured`). The Pricing page already keys off
  exactly this signal.

Public surface (see `payments/router.py`):
  GET  /api/payments/status                        → public, gateway state
  POST /api/payments/create-order                  → auth, dispatches to active provider
  GET  /api/payments/order/{order_id}              → auth, reads + optionally reconciles
  POST /api/payments/refund                        → admin, placeholder until verified
  POST /api/payments/webhook/{provider_name}       → public, hash-verified, idempotent
  POST /api/payments/return/{provider_name}        → browser POST landing (surl/furl)
"""
from .base import (
    PaymentProvider,
    OrderRequest,
    OrderResponse,
    VerifyResult,
    WebhookResult,
    RefundResult,
    ProviderStatus,
    GatewayNotConfigured,
)
from .registry import (
    register_provider,
    get_active_provider,
    get_provider_by_name,
    list_registered_providers,
    active_provider_name,
)

__all__ = [
    "PaymentProvider",
    "OrderRequest",
    "OrderResponse",
    "VerifyResult",
    "WebhookResult",
    "RefundResult",
    "ProviderStatus",
    "GatewayNotConfigured",
    "register_provider",
    "get_active_provider",
    "get_provider_by_name",
    "list_registered_providers",
    "active_provider_name",
]
