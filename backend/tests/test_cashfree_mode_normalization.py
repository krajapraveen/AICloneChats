"""
Regression: Cashfree SDK mode normalization.

Bug history (2026-05-11):
  Backend was returning `mode: CASHFREE_MODE.lower()` straight from env, so
  `CASHFREE_MODE=TEST` produced `"test"`. The Cashfree JS SDK's load()
  silently no-ops on any mode other than the strict literals "sandbox" or
  "production" — Subscribe buttons appeared inert on mobile Safari with no
  console error and no toast.

  Fix: backend `_sdk_mode()` normalizes any env value to one of the two
  strict SDK literals before exposing it via /api/payments/config.

This test locks the contract: GET /api/payments/config.mode is ALWAYS one of
{"sandbox", "production"}, regardless of how CASHFREE_MODE is set in env.
"""
from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

# Ensure the backend env (MONGO_URL et al.) is loaded before importing the
# module under test — its sibling modules at import-time read MONGO_URL.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import payments_cashfree  # noqa: E402


@pytest.mark.parametrize(
    "raw_env,expected",
    [
        ("TEST", "sandbox"),
        ("test", "sandbox"),
        ("Test", "sandbox"),
        ("SANDBOX", "sandbox"),
        ("sandbox", "sandbox"),
        ("PROD", "production"),
        ("prod", "production"),
        ("PRODUCTION", "production"),
        ("production", "production"),
        ("LIVE", "production"),
        ("live", "production"),
        ("", "sandbox"),  # empty/missing env defaults to sandbox (safe)
        ("garbage_unknown", "sandbox"),  # unknown defaults to sandbox (safe)
        ("   PROD  ", "production"),  # whitespace tolerant
    ],
)
def test_sdk_mode_normalization(monkeypatch, raw_env, expected):
    """_sdk_mode() must always return a Cashfree SDK literal regardless of env."""
    monkeypatch.setattr(payments_cashfree, "CASHFREE_MODE", raw_env)
    actual = payments_cashfree._sdk_mode()
    assert actual == expected, (
        f"CASHFREE_MODE={raw_env!r} must produce {expected!r}, got {actual!r}"
    )


def test_payments_config_only_exposes_sdk_literal_mode():
    """The /api/payments/config response must never contain raw env strings."""
    # Inspection-based: ensure the route function uses _sdk_mode() not raw env
    import inspect
    src = inspect.getsource(payments_cashfree.payments_config)
    assert "CASHFREE_MODE.lower()" not in src, (
        "payments_config must not expose raw CASHFREE_MODE.lower() — it MUST go through _sdk_mode()"
    )
    assert "_sdk_mode()" in src, "payments_config must call _sdk_mode()"
    # And _sdk_mode() must always return one of the two SDK literals
    for raw in ("TEST", "PROD", "live", "", "garbage", "Sandbox", "Production"):
        # call with module-level constant temporarily swapped
        original = payments_cashfree.CASHFREE_MODE
        try:
            payments_cashfree.CASHFREE_MODE = raw
            mode = payments_cashfree._sdk_mode()
            assert mode in ("sandbox", "production"), f"mode must be SDK literal, got {mode!r} for raw {raw!r}"
        finally:
            payments_cashfree.CASHFREE_MODE = original
