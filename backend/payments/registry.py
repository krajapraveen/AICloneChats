"""
Provider registry — single source of truth for which gateway is active.

A new gateway integration is exactly:
  1. Write `payments/providers/<name>.py` exposing a class that subclasses
     `PaymentProvider` and sets `name = "<name>"`.
  2. In that file's module scope: `register_provider(MyProvider())`.
  3. Set `PAYMENT_PROVIDER=<name>` in `backend/.env`.

The registry is intentionally process-local (a dict). No threading concerns
because provider modules import once at startup. The active provider is
re-resolved on every call so flipping env vars + restarting the supervisor
is sufficient — no code edits required to swap gateways.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from .base import PaymentProvider, GatewayNotConfigured

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, PaymentProvider] = {}


def register_provider(provider: PaymentProvider) -> None:
    """Idempotent — re-registering the same name replaces the old instance."""
    if not provider.name:
        raise ValueError("Provider must set a non-empty `name`.")
    _PROVIDERS[provider.name.lower()] = provider
    logger.info("payments registry: registered provider name=%s display=%s", provider.name, provider.display_name)


def get_provider_by_name(name: str) -> Optional[PaymentProvider]:
    """Look up a registered provider by stable machine name. Returns None if
    not found. Used by the webhook router to dispatch by URL path so a stale
    webhook for a removed gateway returns a clean 404 rather than crashing."""
    return _PROVIDERS.get((name or "").lower())


def list_registered_providers() -> list[str]:
    return sorted(_PROVIDERS.keys())


def active_provider_name() -> str:
    """Read `PAYMENT_PROVIDER` from env. Empty string when unset."""
    return (os.environ.get("PAYMENT_PROVIDER") or "").lower().strip()


def get_active_provider() -> PaymentProvider:
    """Return the active provider or raise `GatewayNotConfigured`. Always read
    env fresh so a supervisor restart is enough to flip gateways. Also requires
    the provider's own `.status().configured` to be True — guards against the
    case where the env var is set but credentials are missing.
    """
    name = active_provider_name()
    if not name:
        raise GatewayNotConfigured("No payment provider configured (PAYMENT_PROVIDER env var is empty).")
    provider = _PROVIDERS.get(name)
    if not provider:
        raise GatewayNotConfigured(
            f"PAYMENT_PROVIDER='{name}' is set but no provider with that name is registered."
        )
    if not provider.status().configured:
        raise GatewayNotConfigured(f"Provider '{name}' is registered but missing credentials.")
    return provider
