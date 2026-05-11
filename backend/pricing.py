"""
Global pricing — country detection → currency → display amount → charge amount.

Architecture:
  1. country_currency_catalog (ISO-3166-1 alpha-2 → ISO-4217)
  2. FIXED_PRICES per (plan_id, currency) for the 8 anchor markets
  3. Long-tail countries derive from USD anchor via _derive_from_usd() with
     currency-specific rounding rules (no ugly decimals, market-friendly endings)
  4. Backend-only authority: frontend NEVER passes amount or currency.
     Order creation calls compute_price_for_user(user, plan_id) which is the
     single source of truth.

Cashfree India processes INR only. For non-INR display we:
  - Show local price in the user's currency (display_amount, display_currency)
  - Charge in INR (charge_amount = display_amount converted at locked rate)
  - Surface this in the order so the frontend can show the disclosure banner
When Cashfree International is enabled OR Stripe is added, flip
GATEWAY_CHARGE_CURRENCIES to include the new currencies.

Detection priority (compute_user_country):
  1. user.currency_preference (manual override the user set in profile)
  2. user.country_code (assigned on first login from IP)
  3. payment customer signal (future: payment provider returns country)
  4. IP geolocation header (Cloudflare cf-ipcountry, X-Country, etc.)
  5. Fallback: "IN" (founder is in India; most likely first-purchase country)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---- Locked exchange rates (anchor: 1 USD = X currency) ----
# These are PRICING rates, not market rates. They are intentionally rounded
# friendly. Refresh quarterly or hook into a real FX API later. The
# `exchange_source` and `exchange_version` fields are stamped onto every
# order so we can audit historical pricing.
EXCHANGE_SOURCE = "manual_2026q1"
EXCHANGE_VERSION = "1"
USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "INR": 83.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "AED": 3.67,
    "CAD": 1.36,
    "AUD": 1.52,
    "SGD": 1.34,
    "JPY": 150.0,
    "KRW": 1330.0,
    "IDR": 15800.0,
    "VND": 24500.0,
    "BRL": 5.0,
    "MXN": 17.5,
    "ZAR": 19.0,
    "NGN": 1500.0,
    "EGP": 47.0,
    "TRY": 33.0,
    "RUB": 95.0,
    "CHF": 0.89,
    "SEK": 10.5,
    "NOK": 10.8,
    "DKK": 6.9,
    "PLN": 4.0,
    "CZK": 23.0,
    "HUF": 360.0,
    "RON": 4.5,
    "BGN": 1.8,
    "HRK": 6.95,
    "ILS": 3.7,
    "SAR": 3.75,
    "QAR": 3.64,
    "KWD": 0.31,
    "BHD": 0.376,
    "OMR": 0.385,
    "JOD": 0.71,
    "LBP": 89500.0,
    "PKR": 280.0,
    "BDT": 110.0,
    "LKR": 320.0,
    "NPR": 133.0,
    "PHP": 56.0,
    "THB": 36.0,
    "MYR": 4.7,
    "TWD": 32.0,
    "HKD": 7.8,
    "CNY": 7.2,
    "NZD": 1.65,
    "ARS": 1000.0,
    "CLP": 950.0,
    "COP": 4000.0,
    "PEN": 3.8,
    "UYU": 39.0,
    "VES": 36.0,
    "DZD": 134.0,
    "MAD": 10.0,
    "TND": 3.1,
    "KES": 130.0,
    "GHS": 15.0,
    "UGX": 3800.0,
    "TZS": 2600.0,
}

# Currencies that have NO subunit (no decimals) — use whole numbers
NO_DECIMAL_CURRENCIES = {"JPY", "KRW", "IDR", "VND", "CLP", "HUF", "TWD", "UGX", "VND"}

# ---- ISO country → currency catalog (~190 entries, abbreviated) ----
COUNTRY_TO_CURRENCY: dict[str, str] = {
    "IN": "INR", "US": "USD", "GB": "GBP", "CA": "CAD", "AU": "AUD", "NZ": "NZD",
    "AE": "AED", "SA": "SAR", "QA": "QAR", "KW": "KWD", "BH": "BHD", "OM": "OMR", "JO": "JOD", "LB": "LBP",
    "SG": "SGD", "MY": "MYR", "TH": "THB", "ID": "IDR", "PH": "PHP", "VN": "VND",
    "JP": "JPY", "KR": "KRW", "CN": "CNY", "HK": "HKD", "TW": "TWD", "MO": "HKD",
    "PK": "PKR", "BD": "BDT", "LK": "LKR", "NP": "NPR",
    "BR": "BRL", "MX": "MXN", "AR": "ARS", "CL": "CLP", "CO": "COP", "PE": "PEN", "UY": "UYU", "VE": "VES",
    "ZA": "ZAR", "NG": "NGN", "EG": "EGP", "KE": "KES", "GH": "GHS", "UG": "UGX", "TZ": "TZS", "DZ": "DZD", "MA": "MAD", "TN": "TND",
    "TR": "TRY", "RU": "RUB", "IL": "ILS", "CH": "CHF",
    "SE": "SEK", "NO": "NOK", "DK": "DKK", "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON", "BG": "BGN", "HR": "HRK",
    # Eurozone (EUR)
    "DE": "EUR", "FR": "EUR", "IT": "EUR", "ES": "EUR", "NL": "EUR", "BE": "EUR", "PT": "EUR", "GR": "EUR",
    "IE": "EUR", "AT": "EUR", "FI": "EUR", "LU": "EUR", "MT": "EUR", "CY": "EUR", "SK": "EUR", "SI": "EUR",
    "EE": "EUR", "LV": "EUR", "LT": "EUR", "MC": "EUR", "AD": "EUR", "SM": "EUR", "VA": "EUR",
}


# ---- FIXED PRICES for the 8 anchor markets (no derivation) ----
# Display value is the integer; we add zero or two decimals at format time.
FIXED_PRICES: dict[str, dict[str, int]] = {
    "starter":  {"INR": 499,   "USD": 9,    "GBP": 7,    "EUR": 8,    "AED": 33,   "CAD": 12,   "AUD": 14,   "SGD": 12},
    "pro":      {"INR": 1499,  "USD": 29,   "GBP": 22,   "EUR": 26,   "AED": 99,   "CAD": 39,   "AUD": 44,   "SGD": 39},
    "premium":  {"INR": 3999,  "USD": 79,   "GBP": 59,   "EUR": 69,   "AED": 269,  "CAD": 109,  "AUD": 119,  "SGD": 109},
    "ultimate": {"INR": 9999,  "USD": 199,  "GBP": 149,  "EUR": 179,  "AED": 729,  "CAD": 269,  "AUD": 299,  "SGD": 269},
    # Top-up packs — same fixed-price mechanism so users see clean local prices.
    "topup_small":  {"INR": 299,   "USD": 5,    "GBP": 4,    "EUR": 5,    "AED": 19,   "CAD": 7,    "AUD": 8,    "SGD": 7},
    "topup_medium": {"INR": 999,   "USD": 19,   "GBP": 15,   "EUR": 17,   "AED": 69,   "CAD": 25,   "AUD": 29,   "SGD": 25},
    "topup_large":  {"INR": 2999,  "USD": 59,   "GBP": 45,   "EUR": 52,   "AED": 199,  "CAD": 79,   "AUD": 89,   "SGD": 79},
    "topup_mega":   {"INR": 7999,  "USD": 159,  "GBP": 119,  "EUR": 139,  "AED": 549,  "CAD": 209,  "AUD": 239,  "SGD": 209},
}

# USD anchor used to derive long-tail country prices.
USD_ANCHOR_PLAN: dict[str, float] = {pid: float(prices["USD"]) for pid, prices in FIXED_PRICES.items()}

# Gateway currencies — the currencies in which Cashfree (or whatever provider
# we're using) can actually charge the customer. Everything else is shown for
# transparency but charged via conversion to one of these.
GATEWAY_CHARGE_CURRENCIES: set[str] = set(
    (os.environ.get("GATEWAY_CHARGE_CURRENCIES") or "INR").split(",")
)


def _round_market_friendly(usd_price: float, currency: str) -> int:
    """Round derived prices to market-friendly endings.

    - JPY/KRW/IDR/VND/etc.: whole number, round to nearest 100 (or 1000 for VND/KRW)
    - 0/2-decimal currencies: round to nearest 9-ending (e.g., 9.99 → 9; 14.20 → 19)
    """
    rate = USD_RATES.get(currency)
    if not rate:
        # Unknown currency — return USD price as int
        return max(1, round(usd_price))

    converted = usd_price * rate

    if currency in NO_DECIMAL_CURRENCIES:
        # Big-number currencies — round to nearest 100 below 5000, else nearest 1000
        if converted < 5000:
            return max(100, int(round(converted / 100.0)) * 100)
        return max(1000, int(round(converted / 1000.0)) * 1000)

    # Small-number Western/SEA currencies — prefer 9-endings
    if converted < 10:
        return max(1, int(round(converted)))
    if converted < 100:
        # Round to nearest "9" ending in tens: 24 → 29, 41 → 39, 56 → 59
        tens = int(converted / 10) * 10
        return max(9, tens + 9 if (converted - tens) > 4.5 else tens - 1)
    if converted < 1000:
        # Round to nearest 9-ending hundred-half: 234 → 249, 312 → 299
        return max(99, int(round(converted / 50.0)) * 50 - 1)
    # 4-digit+ — round to nearest 99
    return max(999, int(round(converted / 100.0)) * 100 - 1)


def country_to_currency(country_code: Optional[str]) -> str:
    if not country_code:
        return "USD"
    cc = country_code.upper().strip()
    return COUNTRY_TO_CURRENCY.get(cc, "USD")


def detect_country_from_request(request, user: Optional[dict]) -> tuple[str, str]:
    """5-tier priority. Returns (country_code, source)."""
    # 1) User's saved currency preference (highest)
    if user and user.get("currency_preference"):
        # If they set a currency directly, we need to find a country for it.
        pref_cc = user.get("currency_preference")
        for cc, cur in COUNTRY_TO_CURRENCY.items():
            if cur == pref_cc:
                return cc, "preference"
        # Fall through
    # 2) Saved country on user
    if user and user.get("country_code"):
        return user["country_code"].upper(), "profile"
    # 3) Payment customer country (future hook)
    # 4) IP / proxy header
    for h in ("cf-ipcountry", "x-country", "x-vercel-ip-country"):
        v = request.headers.get(h) if request else None
        if v and v != "XX":
            return v.upper(), "ip_header"
    # 5) Fallback
    return "IN", "fallback"


def compute_price_for_plan(plan_id: str, country_code: str) -> dict:
    """Single source of truth. Returns a fully resolved price record.

    Output keys:
      country_code, currency_code, display_amount, display_amount_minor,
      charge_amount, charge_currency, amount_minor (charge), exchange_source,
      exchange_version, requires_currency_disclosure (bool), display_label,
      charge_label
    """
    cc = (country_code or "IN").upper()
    currency = country_to_currency(cc)

    # Fixed-price markets
    if plan_id in FIXED_PRICES and currency in FIXED_PRICES[plan_id]:
        display_amount = float(FIXED_PRICES[plan_id][currency])
    else:
        # Derive from USD anchor
        usd_anchor = USD_ANCHOR_PLAN.get(plan_id)
        if usd_anchor is None:
            raise ValueError(f"Unknown plan_id: {plan_id}")
        display_amount = float(_round_market_friendly(usd_anchor, currency))

    # Determine charge currency. If gateway can take this currency, charge in
    # it. Otherwise convert to INR (or whichever first GATEWAY currency).
    if currency in GATEWAY_CHARGE_CURRENCIES:
        charge_currency = currency
        charge_amount = display_amount
        requires_disclosure = False
    else:
        # Pick the first available gateway currency
        target = "INR" if "INR" in GATEWAY_CHARGE_CURRENCIES else next(iter(GATEWAY_CHARGE_CURRENCIES))
        charge_currency = target
        # Convert display→USD→target
        display_to_usd = display_amount / USD_RATES.get(currency, 1.0) if currency != "USD" else display_amount
        charge_amount = round(display_to_usd * USD_RATES.get(target, 1.0), 2)
        requires_disclosure = True

    decimals = 0 if currency in NO_DECIMAL_CURRENCIES else 2
    return {
        "country_code": cc,
        "currency_code": currency,
        "display_amount": display_amount,
        "display_amount_minor": int(round(display_amount * (10 ** decimals))),
        "display_decimals": decimals,
        "display_label": f"{currency} {display_amount:,.0f}" if decimals == 0 else f"{currency} {display_amount:,.2f}",
        "charge_currency": charge_currency,
        "charge_amount": float(charge_amount),
        "amount_minor": int(round(charge_amount * 100)),
        "requires_currency_disclosure": requires_disclosure,
        "exchange_source": EXCHANGE_SOURCE,
        "exchange_version": EXCHANGE_VERSION,
    }


def catalog_for_country(country_code: str, plan_ids: list[str]) -> dict:
    cc = (country_code or "IN").upper()
    currency = country_to_currency(cc)
    return {
        "country_code": cc,
        "currency_code": currency,
        "prices": {pid: compute_price_for_plan(pid, cc) for pid in plan_ids},
    }
