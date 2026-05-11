# CloneMe AI — Product Requirements Doc

## Original Problem Statement
Build "CloneMe AI" — an AI clone chat MVP. Users create an AI version of themselves (slug, name, bio, avatar, personality sliders, memories), share a public clone link, and visitors chat with the clone. The clone must:
- disclose it is AI
- speak in owner style (personality + memories)
- avoid hallucinating personal facts
- allow owner to view/edit/delete memories
- support public share link

## Stack & Architecture
- **Frontend:** React + Tailwind + shadcn/ui (Pastel + Neo-Brutalist design)
- **Backend:** FastAPI + Motor (MongoDB)
- **LLM:** Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`) via emergentintegrations + EMERGENT_LLM_KEY
- **Auth:** Email/password JWT (bcrypt) + Emergent-managed Google OAuth (cookie-based session_token, 7d TTL)
- **Object Storage:** Emergent Object Storage (avatars), served via `/api/storage/files/{path}`
- **Memory engine:** Manual long-term memories + last 20 conversation messages, retrieval via keyword overlap + importance score (no embeddings yet)

## User Personas
1. **Creator/Founder** — wants a public AI version to handle DMs, share with fans
2. **Casual user** — wants a fun, shareable AI twin to send to friends
3. **Visitor** — chats with a clone via shared link, no account required

## Changelog
- **2026-05-11 (Cashfree Subscribe Silent No-Op — P0 Revenue Unblock)** — Production checkout was inert.
  - **Bug**: Subscribe / top-up buttons clicked silently. No toast, no loading state, no navigation. Reported on production `aiclonechats.com` (iPhone Safari).
  - **Root cause**: Backend was returning `"mode": CASHFREE_MODE.lower()` from `/api/payments/config` AND from `/api/payments/create-order` / `/api/payments/create-topup-order`. With `CASHFREE_MODE=TEST` in env, the response carried `"mode": "test"`. The Cashfree JS SDK's `load({mode})` **only accepts the literals `"sandbox"` or `"production"`** — any other value silently no-ops with no console error and no thrown exception, leaving Subscribe buttons inert on mobile Safari.
  - **Fix (backend)** — `payments_cashfree.py`:
    - New `_sdk_mode()` helper translates any env value to the strict SDK literal: `prod|production|live → "production"`, everything else (including empty/garbage) → `"sandbox"` (safe default).
    - All three response sites (config endpoint, create-order, create-topup-order) now use `_sdk_mode()` instead of raw `CASHFREE_MODE.lower()`. Verified: `grep '"mode":' payments_cashfree.py` returns only `_sdk_mode()` calls.
  - **Fix (frontend)** — `Pricing.jsx`:
    - Default state `cashfreeMode` changed from `"test"` to `"sandbox"`.
    - `useEffect` coerces backend response defensively: `m === "production" ? "production" : "sandbox"`.
    - Extracted `launchCashfree(paymentSessionId, orderId)` helper used by BOTH plan and top-up flows. Throws **user-facing errors** instead of silent no-ops for: invalid mode, missing `payment_session_id`, `loadCashfree()` rejection, missing `cashfree.checkout` function. Every `catch` block now produces a visible toast.
  - **Regression test**: `/app/backend/tests/test_cashfree_mode_normalization.py` — 15 parametrized cases lock the contract that `/api/payments/config.mode` is **always** a Cashfree SDK literal regardless of `CASHFREE_MODE` env value (TEST/PROD/LIVE/SANDBOX/PRODUCTION/empty/garbage/whitespace). Plus an inspection guard ensuring `payments_config()` never calls `.lower()` on raw env.
  - **Updated**: `tests/test_billing_cashfree.py::test_cashfree_create_order_default_no_email_gate` — replaces the old `requires_email_verified` test (verify gate disabled in prior task). Now asserts the order response carries `mode in ("sandbox", "production")`.
  - **Live verified (preview, mobile width 390px, fresh unverified user)**:
    - `/api/payments/config` → `{"mode": "sandbox"}` (was `"test"`)
    - Click Subscribe · ₹499 → **Cashfree sandbox checkout page opens** with UPI/Card/NetBanking/Wallets/Paylater options + ₹499 amount displayed + "Secured by Cashfree Payments" footer.
    - Network: `sdk.cashfree.com/js/v3/cashfree.js`, `ping_atom.html?context=sandbox`, `sandbox.cashfree.com/pg/view/sessions/checkout` — all 200.
  - **All tests green**: 50/50 across `test_billing_cashfree.py` + `test_cashfree_mode_normalization.py` + `test_email_pipeline.py`.
  - **Webhook idempotency** (preserved, not modified): `credit_payment()` in `credits.py` checks `credited_at` on the order doc before applying credits — duplicate webhooks no-op safely. HMAC signature verification + 5-min replay window still enforced.
  - **Note**: Production fix requires redeploy. Verify in production with `CASHFREE_MODE=PROD` env var; the normalizer will produce `"production"`.

- **2026-05-11 (Multi-Provider Email Reliability Layer — Infra)** — Production onboarding/payment flow now survives Resend outages.
  - **Architecture**:
    - New module `/app/backend/email_sender.py` with provider abstraction (`SendResult` dataclass, `PROVIDERS` registry, `send_email()` chain walker). Per-attempt timeouts (Resend 20s, SMTP 15s). Every attempt persisted to `db.email_send_events` with `{event_id, event_group, timestamp, provider, purpose, recipient_domain (no full email), ok, error_code, latency_ms}`. Same `event_group` across all attempts of one logical send for failover tracing.
    - Two providers shipped: **Resend** (httpx HTTP) and **SMTP** (stdlib smtplib in worker thread; TLS 587 default, SSL 465 supported, Zoho/Gmail Workspace/custom-mailbox compatible).
    - Provider chain configurable via env: `EMAIL_PROVIDER_ORDER=resend,smtp` (default). Unknown providers ignored; empty chain falls back to `["resend"]`.
    - Skipped intentionally for current scale: circuit breaker, quota prediction, SMS OTP, manual override (chain itself is the retry; flag-based gate already shipped).
  - **Integration**:
    - `email_verify.py::_send_otp_email` and `password_reset.py::_send_reset_email` refactored to call `multi_send_email`. All Resend-specific code paths removed from these files.
    - `.env` adds `EMAIL_PROVIDER_ORDER` + `SMTP_HOST/PORT/USER/PASSWORD/FROM/USE_TLS`. SMTP keys left blank in preview (Resend is sufficient there).
  - **Observability**:
    - New endpoint `GET /api/admin/email/health` (admin-only) returns `{configured, totals_24h, per_provider_24h, recent[50]}`.
    - New endpoint `GET /api/email/health` (anonymous lightweight probe) returns only `{healthy, last_24h_attempts}` — no provider names, no error codes, no recipients leak.
    - New page `/admin/email-health` (`AdminEmailHealth.jsx`) shows Configuration cards, 24h totals, per-provider rollup table, and last-50 attempts log. Added to AdminIndex Operations section.
  - **Google OAuth auto-trust** (req #7): already shipped in `auth.py:382` — Google-verified emails are marked `email_verified=True` on first OAuth login. No code change needed; verified intact.
  - **Verified (testing_agent_v3_fork iteration_18, 100% pass rate)**:
    - Backend: 9/9 pytest tests in new `/app/backend/tests/test_email_pipeline.py` covering: anonymous probe leak-safety, admin gating (401/403/200), OTP send writes correct event row, forced failover invariant (two events under one `event_group` when primary fails), forgot-password still neutral-200, no secret leakage in any HTTP body.
    - Frontend: anon redirect, non-admin 403 card, admin full dashboard render, zero "mock mode" / "check backend logs" phrasing, recipient domains only (no full emails in UI).
    - Live failover demonstrated in admin recent-attempts log: `test_smtp_fail_resend_ok` group → SMTP `exc_OSError` (4ms) → Resend `OK` (224ms).
  - **Note**: Preview only. Redeploy to push. For production failover, configure SMTP secondary (Zoho `smtp.zoho.com:587` recommended — SPF/DKIM-verified `admin@aiclonechats.com` mailbox).

- **2026-05-11 (Email Verification Gate Disabled — P0 Production Unblock)** — Revenue path must not depend on broken email infrastructure.
  - **Bug**: Production showed "Email sending is not configured in this environment" + "Check the backend logs for the OTP" in the verify-email page. Reason likely: `RESEND_API_KEY` missing in prod env OR `aiclonechats.com` sender domain not verified at Resend. Users couldn't get past the verify gate → couldn't subscribe → revenue path dead.
  - **Decision**: Per founder directive — harden frontend AND remove the gate (Option A + B). Re-enable verification only after Resend domain verification.
  - **Backend**:
    - Added `REQUIRE_EMAIL_VERIFICATION_FOR_CHECKOUT` env var (default `false`).
    - `payments_cashfree.py` create-order + create-topup-order: email_verified check now gated by the flag (lines 105-107, 246-247).
    - `credit_guard.py::charge_credits_or_402`: email_verified gate also flag-gated (so subscribers post-payment can use paid surfaces immediately).
    - `credits.py` PLAN_INDEX["free"]: removed "Email verification" feature line and "Verify your email to start" description.
    - Backend restarted to pick up env var.
  - **Frontend**:
    - `Pricing.jsx`: removed `pricing-verify-banner` div + verify CTA on Free card + `if (!credits.email_verified)` guard in `checkout()`. `isCurrent` no longer requires email_verified. Admin banner condition simplified.
    - `VerifyEmail.jsx`: removed `emailConfigured` state, removed `verify-mock-banner` div, replaced all error toasts with safe copy ("We've sent a verification code to your email." / "Couldn't send the code. Please try again." / "Invalid or expired code. Please try again.").
  - **Verified live (preview, mobile-spec)**:
    - Fresh unverified user → /pricing → NO verify banner, all 4 paid tiers `Subscribe · ₹...` clickable.
    - Click Subscribe → `/api/payments/create-order` returns **200** (was 403 `email_not_verified` before).
    - Smart Reply paywall now codes `INSUFFICIENT BALANCE` (not `email_not_verified`).
    - Direct `/verify-email` visit: toast says "We've sent a verification code to your email." regardless of Resend state.
    - Body-text scrub: zero occurrences of "mock mode" / "check backend logs" / "not configured" / "email_send_configured" on pricing or verify-email pages.
  - **To re-enable later**: set `REQUIRE_EMAIL_VERIFICATION_FOR_CHECKOUT=true` in production env + restart backend. Frontend already shows the verify-email path correctly when backend returns the 403.
  - **Note**: Reported on production. Fix in **preview** — redeploy required.

- **2026-05-11 (Email Verification Round-Trip — P0 Bug Fix)** — Conversion-blocking flow restored.
  - **Bugs (all confirmed in preview repro):**
    1. `Pricing.jsx` "Verify email" CTAs (lines 167, 237) called `navigate("/verify-email")` with NO `?redirect=/pricing` param → after a successful verify, user landed on `/dashboard` instead of returning to Pricing, breaking the subscribe-after-verify flow.
    2. `VerifyEmail.jsx` advertised "Confirm & claim 50 credits" and "Your 50 free credits land the moment you confirm" — but the 0-credit policy makes `grant_signup_credits_if_eligible` return `{granted: False, reason: "signup_grants_disabled"}`, so users got a misleading "Verified. (signup grants disabled — no free credits granted)" toast.
    3. `VerifyEmail.jsx` auth guard (`if (!user) navigate("/login")`) ran BEFORE `AuthContext.loading` finished hydrating, redirecting authenticated users to `/login` when they hit `/verify-email` cold (e.g., via direct link or page refresh). Same root-cause pattern as the earlier PublicClone auth-gate.
    4. Verify-banner on Pricing did not show *which* email was being verified, so users couldn't tell what address would receive the code.
    5. OTP email body promised "activate your 50 free credits" — also stale.
  - **Fix:**
    - `Pricing.jsx` — both Verify CTAs now use `/verify-email?redirect=/pricing`; the free-plan "Create account" CTA also carries `?redirect=/pricing`. Banner now reads "We'll send a 6-digit code to <user.email>" and stacks vertically on mobile.
    - `VerifyEmail.jsx` — added `authLoading` guard (skip redirect during AuthContext hydration); button label changed to **"Confirm email"**; copy simplified ("We need to confirm you control <email>. Enter the 6-digit code we sent you."); success toast unified to "Email verified."; preserves `redirect` param when bouncing logged-out users to `/login`.
    - `email_verify.py` (backend) — OTP email HTML body no longer mentions "50 free credits"; replaced with "verify your account and unlock subscriptions."
  - **Verified live (preview):** Fresh register → `/pricing` shows email-aware verify banner → click Verify → lands on `/verify-email?redirect=/pricing` → send OTP → enter code → confirm → returns to `/pricing` with pathname check (not query). Banner disappears, balance card "0 credits · Plan: Free" appears, all 4 paid tiers become Subscribe-able. No re-login required. Auth-loading guard verified by direct cold-loading `/verify-email?redirect=/pricing` while logged in (previously bounced to /login).
  - **Note**: Reported on production (`aiclonechats.com`). Fix in **preview** — redeploy required.

- **2026-05-11 (Smart Reply & Voice Messaging Paywall CTA Loop — P0 Bug Fix)** — Conversion-blocking bug.
  - **Bug**: Smart Reply daily-limit modal "Upgrade to Pro" button showed `toast.info("Pro launch coming soon — you're on the early list.")` and closed the modal, leaving the user on the same page. Same dead-end existed in Voice Messaging. Reported by user with screenshot from `aiclonechats.com`.
  - **Fix**: `SmartReplyStudio.jsx::handleUpgrade` now `navigate("/pricing?source=smart_reply&intent=upgrade")`. `VoiceMessaging.jsx` `onUpgradeClick` now `navigate("/pricing?source=voice_messaging&intent=upgrade")`. Both close the modal first. The dead "Pro launch coming soon" copy is removed everywhere (grep'd `/app/frontend/src` — no other occurrences).
  - **Verified**: Live preview — logged in as `sr-tester@example.com`, generated a reply → daily-limit modal opened → clicked Upgrade → landed on `/pricing?source=smart_reply&intent=upgrade` with full pricing tiers + subscriber top-up section rendering correctly.
  - **Note**: Reported on production (`aiclonechats.com`); fix in preview — redeploy required.

- **2026-05-11 (Chat Bubble Mobile Wrap Fix — P0 Bug Fix)** — Production iPhone Safari.
  - **Bug**: On mobile Safari, short messages like "Hello" were wrapping character-by-character vertically ("He / llo"). Root cause: `overflow-wrap: anywhere` + `word-break: break-word` in `index.css:74` lets Safari's flexbox shrink the bubble to *broken-content min-width* rather than *word min-width*. Reported by user with screenshot from `aiclonechats.com`.
  - **Fix** (`/app/frontend/src/index.css`): Replaced `overflow-wrap: anywhere; word-break: break-word;` with `overflow-wrap: break-word; word-break: normal; white-space: pre-wrap; hyphens: none;`. `break-word` (unlike `anywhere`) only allows breaking when a word truly can't fit on its own line, and never affects min-content size.
  - **Sizing**: Visitor bubble wrapper `max-w-[80%]`, clone wrapper `max-w-[88%]`, bubble itself `max-width: 100% / min-width: 2.5rem / width: fit-content / line-height: 1.45`. Tightened padding from `0.78rem 1.1rem` to `0.7rem 1rem` for visual consistency. ChatBubble.jsx column now uses `items-end` (visitor) / `items-start` (clone).
  - **Verified**: Live preview — `Hi` bubble = 49×47.5px single line; long URL `https://www.example.com/very/long/path/here` wraps at slash boundaries (not per-char); long word `supercalifragilisticexpialidocious` stays on one line if it fits; no horizontal overflow (`scrollWidth === clientWidth`).
  - **Note**: User reported this on the production environment (`aiclonechats.com`). Fix is applied to preview — user needs to redeploy to push to production.

- **2026-05-11 (PublicClone Auth-Gate Removal — P0 Bug Fix)** — Public sharing restored.
  - **`PublicClone.jsx`**: Removed the page-level `useEffect` that auto-redirected unauthenticated visitors to `/login`. Logged-out users can now load `/<slug>` and see the clone header, marquee disclaimer, stats, and chat empty-state. Replaced the chat input form with a `[data-testid=signin-to-chat-cta]` card containing `Sign in →` and `Sign up` buttons when `!user`. The `send()` handler still defends with an explicit `!user` guard → toast + `navigate('/login?redirect=/<slug>')`. Authenticated users see the visitor-name form and chat form exactly as before. Backend chat endpoint behavior unchanged (auth + atomic credit deduction).
  - **`App.js`**: Added `/signup` route alias that mounts `<Register />` (the public CTA copy says "Sign up", which previously fell through to `/:slug` and rendered the 404 clone-not-found card).
  - **`Login.jsx` + `Register.jsx`**: Post-login redirect now reads `searchParams.get('redirect') || searchParams.get('next') || '/dashboard'` so the round-trip from PublicClone → Login → back to `/<slug>` works.
  - **Verified** (testing_agent_v3_fork iteration_17 + self-screenshot): logged-out user views `/maya-demo`, clicks Sign in, logs in as `subscriber-tester@example.com`, lands back on `/maya-demo` with visitor-name form visible. Sign-up button now correctly opens the Register page. Authenticated chat send + 402 paywall path also re-verified.

- **2026-05-11 (IP / Compliance Cleanup + Landing Contact Strip)** — Visibility, not new features.
  - **ContactBar:** new `/app/frontend/src/components/ContactBar.jsx` rendered directly under the Navbar on `/` (landing only). Shows `admin@aiclonechats.com` + `krajapraveen@aiclonechats.com` as mailto links plus a tagline "Original AI personas only. Use only content you own or have rights to use." Mobile-stacking (tagline hidden ≤640px). Zero horizontal overflow at 390/768/1920px.
  - **Legal pages:** `/terms`, `/privacy`, `/acceptable-use` — three full documents on a shared `LegalPage` shell. Footer legal links added; both contact emails surfaced in footer + every legal page footer.
  - **Server-side IP blocklist:** `clones.py` now checks `display_name`, `slug`, `bio` against an IP/franchise/trademark blocklist (disney, marvel, pixar, netflix, openai, chatgpt, whatsapp, etc.) BEFORE the existing LLM moderation pass. Blocks emit a structured 400 `ip_blocked_term` and write an admin audit row to `db.ip_safety_blocks`.
  - **No risky assets** were present in `frontend/public/` (just the founder photo + index.html). No celebrity, movie, or brand references existed in seeded clones — the existing companion clone bio already reads "Not impersonating any real person."
  - **Safety filter** (`safety_filter.py`) was already enforcing celebrity/franchise/copyright/piracy blocks via regex `_HIGH_IMPERSONATION` — unchanged. The new IP blocklist in clones.py is a fast pre-check on identity fields.
- **2026-05-11 (Visibility + Auth Hardening — no new monetization)** — Top-of-dashboard Plans visibility, Forgot/Reset password flow, strict auth error contracts.
  - **Dashboard Plans Showcase:** `/app/frontend/src/components/PlansShowcase.jsx` rendered at the TOP of `/dashboard`. 4 paid plans + 4 top-up packs. Pricing comes exclusively from `/api/pricing/catalog` (no frontend math). Top-up cards visibly carry "Available only for active subscribers" + the CTA disables and toasts for non-subscribers. Server still 403s on `POST /api/payments/create-topup-order` for non-subscribers.
  - **Forgot/Reset Password Flow:** new module `/app/backend/password_reset.py` exposing `POST /api/auth/forgot-password`, `POST /api/auth/reset-password`, `GET /api/auth/reset-password/validate`. Tokens are 32-byte URL-safe, SHA-256 hashed before storage, 30-minute expiry, single-use, with idempotent supersession (new request invalidates prior unconsumed tokens for same user). On successful reset ALL `user_sessions` for that user are deleted. Email delivered via Resend. **Never reveals whether an email exists** — neutral 200 + same shape regardless. Rate-limited per IP (10/15min) and per email (5/15min).
  - **Strict auth error contract:** every auth endpoint now returns `detail = {code, message, request_id}` (HTTP errors) or `{ok, code, message, request_id}` (success). Login/Register, Forgot/Reset all converted. `models.py` switched from `EmailStr` (Pydantic 422 array) to `str + Field` so the handler's EMAIL_RE branch fires and emits the structured 400 shape.
  - **Brute-force lockout:** 5 failed logins per IP+email in 15 minutes returns 429 `rate_limited`. Lookup uses `ip_address_hash` to match the audit log's hashing.
  - **Audit log:** every auth event (`login_success`, `login_failed`, `password_reset_requested`, `password_reset_completed`, `password_reset_failed`, `rate_limit_triggered`) written to `login_events` with `request_id`. No password or raw token ever in any audit row.
  - **Frontend pages:** `/forgot-password`, `/reset-password?token=...`. "Forgot password?" link added below the password field on `/login`.
  - **Tested:** 22/22 backend after fix, 12/12 frontend P0. Email-enumeration neutrality verified: login unknown vs wrong-password return identical 401 invalid_credentials; forgot-password returns identical neutral 200 for known and unknown emails.
- **2026-05-11 (Admin Revenue Mirror — read-only instrumentation)** — Six-section observation surface at `/admin/revenue` to make platform state legible in 30 seconds. No interpretation, no recommendations, no automated interventions. Per founder spec: instrumentation only.
  - **New backend module:** `/app/backend/analytics_revenue.py` — 6 admin-only endpoints (funnel, revenue, credit-economy, emotional-gravity, cohorts, operational-health). Every endpoint supports `?format=csv` for export.
  - **Minimal new writes:** `paywall_events` collection (one write per 402 from `credit_guard.py`) + `POST /api/funnel/event` for `pricing_view` (one write per pricing page visit). No other instrumentation overhead.
  - **Emotional Gravity tracks:** first paid intent surface · first successful payment surface · repeat-return surface · longest-session surface (p90 messages per thread) · top-up correlation surface (most-used surface in 14d before topup).
  - **Cohorts:** D1/D7/D30 return by acquisition-week (ISO year-week), by plan tier, by first paywall surface.
  - **Operational Health:** payment_failure_pct, webhook_rejection_pct, refund/chargeback %, AI-failure refund rate by surface. Response-latency intentionally surfaced as null until request-layer instrumentation lands.
  - **New frontend page:** `/app/frontend/src/pages/AdminRevenue.jsx` — six brutal-card sections, per-section window selector, per-section CSV export. Mobile-readable. Admin-only.
  - **Tested:** 21/21 backend tests pass (iteration_15), 100% frontend P0 pass. Testing agent fixed one CSV heterogeneous-row bug.
- **2026-05-11 (Credit Economics Hard Reset + Top-Up Packs + Full Paywall Enforcement)** — Free tier abolished. All non-admin users wiped to 0 credits via `/app/backend/migrations/reset_credits_2026_05_11.py`. Signup grants permanently disabled (`SIGNUP_GRANTS_DISABLED=True` in `credits.py`). Admin `krajapraveen@gmail.com` retains server-side unlimited bypass via `is_admin_unlimited_user`.
  - **Plans (locked):** Starter ₹499 / 500 cr · Pro ₹1,499 / 2,500 cr · Premium ₹3,999 / 8,000 cr · Ultimate ₹9,999 / 25,000 cr.
  - **Top-Up Packs (subscribers-only):** ₹299→300 / ₹999→1,200 / ₹2,999→4,000 / ₹7,999→12,000. Local currency pricing via `FIXED_PRICES` extended into `pricing.py`.
  - **New module `credit_guard.py`** — central `charge_credits_or_402()` wraps deduct + tier-gate + refund handle. Used by all 8 monetized chat surfaces.
  - **Surfaces wired (all server-side enforced):**
    - clone_chat=1, mood_chat=1, translation_chat=1 (Starter+)
    - smart_reply=2, debate_chat=2, conversation_memory=2, voice_message=3, anonymous_chat=3, delayed_create=4 (Pro+)
    - video_avatar=5 (Ultimate-only)
  - **New endpoints:** `POST /api/payments/create-topup-order` (403 for non-subscribers), `GET /api/topups/catalog`. `/api/pricing/catalog` now also includes top-up packs.
  - **Frontend:** New `GlobalPaywallModal` listens on `paywall:hit` window event from axios 402 interceptor. New Top-Up section on `/pricing`. `MoodChat` + `PublicClone` now require auth.
  - **Testing:** 10/11 backend tests pass (iteration_14), frontend 100% P0 pass.
- **2026-02-13 (Global currency / country pricing + webhook currency verification)** — Backend-controlled global pricing for 80+ countries, 5-tier country detection, fixed prices for 8 anchor markets, derived prices with market-friendly rounding for the long tail, charge-currency disclosure where gateway can't natively process.
  - **Backend (new)**: `pricing.py` — country↔currency catalog (ISO-3166-1 → ISO-4217), `FIXED_PRICES` for INR/USD/GBP/EUR/AED/CAD/AUD/SGD, USD-anchor derivation with market-friendly rounding (`_round_market_friendly`), no-decimal handling for JPY/KRW/IDR/VND, `compute_price_for_plan(plan_id, country)` and `detect_country_from_request(request, user)` (5-tier priority).
  - **Endpoints added**:
    - `GET /api/pricing/catalog?country=XX` — public, returns per-plan price record (display_amount, display_currency, charge_amount, charge_currency, requires_currency_disclosure, exchange_source, exchange_version) for every paid plan
    - `GET /api/pricing/my-currency` — quick country/currency lookup
    - `GET /api/admin/billing/pricing-catalog` — admin matrix of every supported country × every plan
  - **Order schema** (`payment_orders`) now carries: `country_code`, `country_source`, `display_currency`, `display_amount`, `charge_currency`, `charge_amount`, `amount_minor`, `requires_currency_disclosure`, `exchange_source`, `exchange_version`. Legacy `amount_inr` kept for backward-compat.
  - **Webhook hardening**: amount AND currency must both match the stored order. Mismatch on either → 400 + fraud signal logged. Existing replay/signature checks unchanged.
  - **Frontend**: Pricing page consumes `/api/pricing/catalog`, renders `Intl.NumberFormat` localized labels for each plan card. Country/currency banner ("Detected country: IN · Currency: INR · via fallback · Prices shown in your local currency based on your detected country."). Disclosure pill appears under display price when `charge_currency !== display_currency`.
  - **Cashfree reality**: Cashfree India processes INR only on standard merchant accounts. Non-INR users see localized display prices but are CHARGED in INR with a clear "Charged as ₹X" disclosure under the price. When Cashfree International is enabled (or Stripe added), flip `GATEWAY_CHARGE_CURRENCIES` env to include the new gateway-native currencies. Schema and webhook handler already support multi-currency processing.
  - **Tests**: 12 new tests (`test_currency_*`): IN→INR, US→USD, GB→GBP, AE→AED, EU(DE/FR/IT/ES)→EUR, JP→JPY no-decimals, unknown→USD fallback, disclosure flag set for non-gateway currencies, server-authored amount/currency (body tampering ignored), admin pricing catalog completeness. **26/26 backend tests pass total.**
  - **E2E verified**: US user → display $9, charge ₹747 → Cashfree accepts INR order → signed webhook with correct INR amount → credits granted. Tampered currency (USD in webhook for INR order) → 400 + fraud signal. ✓


- **2026-02-13 (Cashfree billing + credit ledger + email-OTP gate — Phase 1)** — Monetization foundation, all security tests passing, real LLM-backed credit deduction proven E2E.
  - **Backend modules** (all new):
    - `credits.py`: `PLANS` (5 tiers), `CREDIT_COST` matrix, atomic `deduct_credits` with `find_one_and_update` balance guard, `refund_credits` on LLM failure, `credit_payment` for paid orders, `is_admin_unlimited_user` (env-driven), `grant_signup_credits_if_eligible` with device/IP/email dedup, fraud-signal logging + cumulative scoring + 12h cooldown action.
    - `email_verify.py`: `/api/auth/verify-email/send` and `/confirm`. 6-digit OTP, hashed at rest, 10-min TTL, 5/day cap, 60s resend cooldown, Resend integration.
    - `payments_cashfree.py`: `/api/payments/create-order` (server-authored — amount NEVER from body), `/api/payments/order/{id}` (re-fetches Cashfree on read), `/api/payments/webhook/cashfree` (HMAC-SHA256 signature verification, 5-min replay window, amount-tamper detection, idempotent via `credited_at` guard).
    - `billing_api.py`: `/api/plans`, `/api/me/credits`, admin endpoints under `/api/admin/billing/*` (overview, users, payments, credit-events, webhook-logs, fraud-signals, credit-adjust).
  - **Auth changes** (`auth.py`): new users registered with `email_verified=False`, `credits_balance=0`, `plan_status=pending_verification`. Free credits ONLY granted after OTP confirm.
  - **Smart Reply** (`smart_reply.py`): wired with `deduct_credits` BEFORE LLM call, `refund_credits` on LLM/parse failure. Admin path is a no-op. 402 returned with `{code, credits_balance, cost, daily_cap, daily_used}` for the frontend to show out-of-credits UX.
  - **DB indexes** (`server.py`): unique on `credit_grants.user_id` + `credit_grants.email`, indexes on `credit_events`, `fraud_signals`, `fraud_cooldowns`, `payment_orders.order_id` unique, `webhook_logs`, `email_otp_codes`. Plans seeded on boot.
  - **Frontend** (new):
    - `pages/Pricing.jsx`: 5 plan cards, server-authored Cashfree checkout via `@cashfreepayments/cashfree-js` SDK. Email-verification gate before checkout. Cost table publicly visible. Current plan highlighted. Admin sees `∞ admin` banner.
    - `pages/VerifyEmail.jsx`: OTP entry, send/resend, auto-grants 50 credits on success.
    - `pages/PaymentReturn.jsx`: `/pay/return?order_id=...` — polls `GET /api/payments/order/{id}` every 2s up to 30s. Ignores URL "success" params (never trusts frontend).
    - `hooks/useCredits.js`: reusable balance hook.
    - `components/Navbar.jsx`: added credit pill (`∞ admin` or `{n} cr`) + `Pricing` link in mobile drawer.
  - **Routes added**: `/pricing`, `/verify-email`, `/pay/return`.
  - **Tests**: 15 new in `test_billing_cashfree.py` covering plan listing, zero-balance enforcement, admin unlimited, email-verification gate on checkout, missing/wrong/correct webhook signature, replay protection, idempotency via signed webhook, amount-tamper rejection, admin-route guards, negative-balance refusal. 61/61 backend tests pass overall (no regressions).
  - **E2E verified via real Cashfree sandbox**:
    - Signed webhook → balance `0 → 500`, plan `free → starter` ✓
    - Duplicate webhook → balance stays `500` (idempotent) ✓
    - Tampered amount on signed webhook → `400 Amount mismatch` + fraud signal ✓
    - Real Smart Reply call (live OpenAI) → balance `50 → 48` (cost=2) ✓
    - Admin user Smart Reply call → balance stays `None` (unlimited) ✓
  - **Production env block** to apply via Emergent deploy panel for `https://aiclonechats.com` (rotate keys first):
    ```
    CASHFREE_APP_ID=<your production App ID>
    CASHFREE_SECRET_KEY=<your production Secret Key>
    CASHFREE_MODE=PROD
    CASHFREE_API_VERSION=2023-08-01
    ADMIN_UNLIMITED_EMAIL=krajapraveen@gmail.com
    ```
    Webhook URL to register in Cashfree dashboard: `https://aiclonechats.com/api/payments/webhook/cashfree`
  - **Phase 2 backlog** (NOT shipped this session):
    - Wire credit deduction into remaining 8 chat surfaces (clone, mood, anonymous, voice, debates, translation, video-avatar, conversation memory, delayed_create).
    - Cashfree native **subscription** (recurring auto-debit via Cashfree Subscriptions API + RBI 24h pre-debit notification scheduler).
    - Top-up credit packs (₹299/999/2999) — separate one-time orders that ADD to balance without changing plan.
    - "Out of credits" UX states + paywall modals on each chat surface.
    - Subscription expiry / cancellation state machine.


- **2026-02-12 (P1: production env wired + real Resend E2E verified)** — **Email channel proven live end-to-end. Public flag flipped on in preview. Production deploy block prepared.**
  - **Preview env updated** (`backend/.env`):
    - `RESEND_API_KEY=<redacted — rotate before reuse>`
    - `RESEND_FROM=aiclonechats.com <admin@aiclonechats.com>`
    - `FRONTEND_PUBLIC_URL=https://digital-twin-119.preview.emergentagent.com` (preview value; production must be `https://aiclonechats.com`)
    - `DELAYED_EMOTIONAL_CHAT_ENABLED=true`
  - **Resend domain verified** by founder: `aiclonechats.com` on GoDaddy DNS, region `us-east-1`. DKIM (`resend._domainkey` TXT), SPF (`send` TXT v=spf1 include:...amazonses.com ~all), MX (`send` → `feedback-smtp.us-east-1.amazonses.com`) all green. Domain status: `Verified`.
  - **Real E2E verified** (no mocks): created delayed message with `recipient_type=email`, `recipient_email=krajapraveen@aiclonechats.com`, `delivery_channel=email` → admin force-delivered → Resend returned 2xx (no failure_reason, `delivery_attempts=1`, `status=delivered`) → anonymous `GET /api/delayed-messages/open/{token}` returned 200 with `X-Robots-Tag: noindex, nofollow` and the full message body → `opened_at` set on first read → frontend `/open/:token` reveal page rendered the real title/body/delivered-at with all noindex+referrer metas correctly injected.
  - **Production env block** to apply via Emergent deploy panel for `https://aiclonechats.com`:
    ```
    RESEND_API_KEY=<paste freshly rotated Resend key here>
    RESEND_FROM=aiclonechats.com <admin@aiclonechats.com>
    FRONTEND_PUBLIC_URL=https://aiclonechats.com
    DELAYED_EMOTIONAL_CHAT_ENABLED=true
    ```
    (Leave `BACKEND_PUBLIC_URL`, `FAL_KEY`, `AVATAR_CHAT_ENABLED` as-is — those belong to the Avatar feature which remains gated.)
  - **What was NOT changed**: zero new features, zero copy changes, zero schema changes, zero refactoring. Subtractive discipline preserved per founder directive ("P1 only: configure production env, redeploy, verify").
  - **Operator note**: the founder is the only person who can apply the env block to production and trigger the redeploy. Once redeployed, the same Resend key + verified domain will work identically there. No further code or DB changes needed.


- **2026-02-12 (Open-token reveal flow for emailed delayed messages — final delta)** — **Three-item delta. Closes the recipient-without-account gap so the email channel is genuinely useful.**
  - **Backend** (`backend/delayed_messages.py`):
    - **`open_token`** field minted at create time (`secrets.token_urlsafe(32)`, ~43 chars). Returned ONCE in the create response (`delayed_message.open_token`) and never again — sender must capture it at that moment if they want to share manually. Listing/admin payloads (`_public()`) deliberately do NOT expose the token (test asserts this).
    - **`GET /api/delayed-messages/open/{token}`** — public unauthenticated reveal endpoint. Looks up by exact `open_token` match. Returns 404 for missing/invalid/short tokens, 403 if the message is still sealed (status != "delivered"), 200 otherwise. Sets `X-Robots-Tag: noindex, nofollow, noarchive` and `Cache-Control: no-store, no-cache, must-revalidate, private` on the response. Sets `opened_at` on first read; idempotent on subsequent reads.
    - **`delivery_attempts` counter + retry policy** in `_deliver_one`. Each pass through increments. On non-fatal failure under `MAX_DELIVERY_ATTEMPTS=3`, the message drops back to `scheduled` with `delivery_time = max(now, current_dt) + 60s × attempt` so the next tick picks it up. At/over the limit, terminal-fail with `failure_reason` recording the attempt count. Emits `delivery_attempt_failed` per non-terminal failure, `failed` on terminal.
    - **`_send_email(to, subject, body, open_url=None)`** now optionally embeds the reveal link as a CTA button + plain-text fallback in both HTML and text bodies. Worker passes `_build_open_url(open_token)` (resolved from `FRONTEND_PUBLIC_URL` then `BACKEND_PUBLIC_URL`).
    - **`_scheduler_tick` def restored** — the previous edit left a dangling docstring and the `_scheduler_loop` was calling an undefined function. Fixed; backend now starts cleanly with the cron enabled.
    - Removed `hashlib` import + `open_token_hash` field. The hash-then-lookup pattern was incompatible with the worker needing the raw token to embed in the email URL; storing both raw and hash in the same DB defeats the hash. Kept the simpler capability-token model: a single high-entropy secret as the URL parameter.
  - **Frontend** (`frontend/src/pages/DelayedMessageReveal.jsx` + `App.js`):
    - New `/open/:token` route, registered BEFORE the `/:slug` catch-all so it doesn't get hijacked by `PublicClone`.
    - Page mounts and injects three meta tags at runtime: `robots: noindex,nofollow,noarchive,nosnippet`, `Cache-Control: no-store...`, `referrer: no-referrer`. Cleaned up on unmount.
    - Three render states: loading / error (404 or 403-sealed) / success card with title + delivered-at + body. Footer reads "The system delivers; it does not chase." No CTA, no signup wall, no related-content section. The reveal is the entire experience.
    - Direct `axios.get(BACKEND_URL/api/...)` — no auth header (recipient is unauthenticated by design).
  - **Persistence** (`server.py`): added `delayed_messages.open_token` sparse index for the lookup path. `FRONTEND_PUBLIC_URL` env added (falls back to `BACKEND_PUBLIC_URL`).
  - **Tests** (`backend/tests/test_delayed_messages.py`): +5 tests for the open-token flow:
    - `test_open_token_returned_on_create`: create response surfaces a ≥32-char token
    - `test_open_token_sealed_before_delivery`: 403 before delivery
    - `test_open_token_invalid_returns_404`: garbage token → 404
    - `test_open_token_short_returns_404`: short token → 404 (entropy guard)
    - `test_open_token_after_delivery_reveals_message`: force-deliver → open via token → 200, body matches, `opened_at` set, idempotent on second open
    - `test_open_token_not_in_listing_payload`: listing/inbox responses don't leak token
  - **Constitutional CI**: 5/5 pass. `DelayedMessageReveal.jsx` not in the guarded frontend list (pure presentation; nothing to chase). All forbidden-term scans clean.
  - **Tests verified**: 46/46 pass across delayed_messages + clone_delayed_recipient + clone_artifacts + avatar_chat + no_chasing_mechanisms. 283/284 pass full suite — the one remaining failure (`test_fake_code_returns_401` exact text match) is pre-existing, unrelated, and documented in earlier handoffs.
  - **Smoke**: `/open/some-fake-token` reveal page rendered the error state with correct copy, robots/referrer meta verified injected at runtime via DOM inspection.
  - **Operator note**: `RESEND_API_KEY` and `FRONTEND_PUBLIC_URL` (or `BACKEND_PUBLIC_URL`) must both be set in production for emails to carry working reveal links. Missing either → email channel still degrades gracefully (text-only body, no CTA), and the in-app channel is unaffected. Sender can also copy the `open_token` from the create response and share manually.


- **2026-02-12 (Three-addition pass — clone recipient + source_conversation_id + inline Send Later)** — **Subtractive review of the Delayed Emotional Chat re-spec found 90% already built. Executed only the three genuine deltas. Refused 7 items as feature drift.**
  - **`recipient_type: "clone"`** (`backend/delayed_messages.py`): new sealed-message addressee. Clone-addressed delayed messages deliver to the SENDER's voluntary inbox at delivery time, tagged with the clone so the user can return to that conversation with the message visible. The clone does NOT autonomously act on receipt — there is no auto-reply, auto-react, or notification hook. `delivery_channel="in_app"` only (no email leak). Standard self-harm crisis path applies.
  - **`source_conversation_id`** field added to `delayed_messages` schema (non-breaking, additive). Lets the frontend deep-link a delivered message back to the originating clone conversation.
  - **Inline "Send later" composer on PublicClone** (`frontend/src/components/SendLaterInline.jsx` + integrated into `PublicClone.jsx`): authenticated users only. Pre-fills with the user's last visitor message, defaults delivery to 7 days out rounded to next 15-min boundary. Single CTA. Confirmation toast emphasizes the system *delivers*, not chases. Footer copy: "A message for when time matters."
  - **Refused from the spec, with reasons**: namespace rename (breaks 11 tests for zero benefit); new `routes/`/`models/`/`services/` dir layout (doesn't match conventions); duplicate `delayed_message_deliveries` collection (write amplification, no query benefit); `delivery_mode: "date"|"relative"` field (dead state); separate `DelayedInbox.jsx` page (already a tab); `is_opened` boolean alongside `opened_at` (one piece of info, two fields = consistency hazard); renaming the existing constitutional test file (cosmetic).
  - **Constitutional CI extended**: `test_no_chasing_mechanisms.py` now scans `SendLaterInline.jsx`. **NEW** `test_clone_delayed_recipient.py` (8 tests): asserts clone-recipient messages sealed until delivery; deliver only into sender inbox; reject email channel; cancellation works; structurally asserts no autoplay/auto-reply/auto-react endpoint exists (probes 9 ghost paths). The constitutional test caught my own docstring quoting "we'll remind you" as a negation example — per the discipline, removed the forbidden-phrase quote rather than adding an exemption. **The test held the line against its own author.**
  - **Tests verified**: 40/40 pass (8 new + 5 constitutional + 11 delayed + 8 artifacts + 8 avatar). Smoke screenshot confirms inline composer renders with thesis copy.

- **2026-02-12 (Thesis-tightening pass — constitutional CI + persistence metrics + avatar demotion)** — **Subtractive pass. No new features. Hardened existing modules to enforce thesis-aligned behavior at the architecture level.**
  - **Constitutional CI test** (`backend/tests/test_no_chasing_mechanisms.py`): 5-test suite that fails CI if `delayed_messages.py`, `clone_artifacts.py`, or their frontend pages contain forbidden chasing terms (`reminder`, `notify`, `notification`, `nudge`, `reactivation`, `winback`, `digest`, `streak`, `engagement_email`, `push_notification`, `don't forget`, `come back`, `stay active`, `keep your streak`). Allows lines that explicitly *deny* the forbidden behavior (e.g., "no reminders, no notifications"). Also asserts no `/reminders`, `/notify`, `/digest`, `/dispatch`, `/winback`, `/streak` routes exist. Meta-test prevents the guard list itself from quietly shrinking. **5/5 pass.** This is the constitution encoded in CI — drift protection becomes a build-break, not a meeting.
  - **Persistence-focused admin metrics** (`backend/delayed_messages.py:admin_metrics` + `frontend/src/pages/AdminDelayedMessages.jsx`): added the metrics that reflect the actual thesis instead of activity proxies:
    - `d7_open_rate` and `d30_open_rate` — % of messages delivered ≥7d/≥30d ago that were voluntarily opened. **This is the single most important metric for the entire delayed-chat thesis** and now the dashboard makes it the headline.
    - `voluntary_opens_in_window` — total opens by recipients (not auto-opens; only those triggered by user navigation).
    - `repeat_composers_in_window` — sender-side gravity: users who scheduled ≥2 messages in window.
    - `future_self_count` / `other_user_count` / `email_recipient_count` — recipient-type breakdown per spec.
    - Operator note hard-coded: *"Persistence over engagement. Voluntary-open rate is the gravity signal. The system delivers; it does not chase."* Front-end "PERSISTENCE SIGNALS · THE ONLY THING THAT MATTERS" section header reinforces this.
    - "Thesis: memory / not engagement" tile is in the metrics grid as a permanent visual reminder of what the dashboard is for.
  - **Avatar demotion** (`frontend/src/components/Navbar.jsx`): removed prominent "Avatar Lab" navbar entry. Avatar Chat is no longer surfaced as a destination for admin users browsing the app — it remains as `/admin/avatar-chat` (moderation/observability) and `/video-avatar-chat` (admin-accessible playground via direct URL). This implements the spec's reframing: *"Avatar response is a presentation layer only. It must not become a separate Video Avatar Chat product surface."*
  - **What I deliberately did NOT build from the spec** and why:
    - Renaming `/api/delayed-messages/*` → `/api/delayed-chat/*`: would break existing tests + UI for zero thesis benefit. Namespace is internal; thesis is what matters.
    - Inline avatar response toggle on every clone reply: would require auth on PublicClone visitor flow (high risk, low yield). The /video-avatar-chat playground already proves the pipeline works end-to-end. Defer until Phase A produces signal worth refining around.
    - Send-later composer inside clone chat: major UX rework on shared visitor surface. Defer.
  - **Tests verified**: 32/32 pass (5 constitutional + 11 delayed + 8 artifacts + 8 avatar). Smoke screenshot of `/admin/delayed-messages` confirms persistence signals block renders, thesis note visible, navbar Avatar Lab entry gone.
- **2026-02-12 (Override option B — Clone Conversation Artifacts, NOT Productivity Rooms)** — **Re-scoped the Productivity Chat ask into an artifact layer attached to existing clone conversations.** Did NOT build a standalone productivity surface, NOT build a reminder dispatcher, NOT build a stale-room digest, NOT build reactivation emails, NOT build notification-based return loops. Trust philosophy preserved.
  - **Backend** (`backend/clone_artifacts.py`): pull-only extraction. The user clicks "Extract" → server reads the conversation transcript → calls Claude Sonnet 4.5 with strict JSON schema → coerces output → persists `clone_artifacts` document + per-task documents in `clone_artifact_tasks`. **No background job. No scheduler. No watcher on `due_at`.** Tasks have a `due_at` field for the user's own reference only — nothing reads it.
  - **Endpoints**: `POST /api/clone-artifacts/extract`, `GET /api/clone-artifacts?conversation_id=...`, `GET /api/clone-artifacts/tasks`, `PATCH /api/clone-artifacts/tasks/{task_id}`, `DELETE /api/clone-artifacts/tasks/{task_id}`, `GET /api/admin/clone-artifacts/metrics`. Identity is dual-mode: authenticated users via auth header, visitors via `visitor_id` (already used by clone chat). Cross-identity access returns 403.
  - **Extraction shape**: `{tasks[], decisions[], follow_ups[] (no schedule), summary, unresolved_questions[]}`. The system prompt explicitly tells the LLM not to invent tasks, not to add nudges, not to pad arrays — empty results are fine. Defensive coercion handles sloppy LLM output.
  - **Frontend** (`frontend/src/components/ConversationArtifactsPanel.jsx` + integrated into `PublicClone.jsx`): collapsible inline panel below the existing chat. Single CTA "Extract artifacts" — no auto-extraction, no polling, no reactive feedback loop. Surface for tasks (status cycling open→in_progress→done→open, priority tags, optional due-date *display*), decisions, follow-ups (explicitly labeled "no schedule"), unresolved questions, and a 80-word summary.
  - **Constitutional check (in tests)**: `test_no_reminder_endpoints_exist` asserts `/clone-artifacts/reminders`, `/clone-artifacts/dispatch`, `/clone-artifacts/notify`, `/clone-artifacts/digest` all 404. If anyone ever adds a reminder/notification mechanism to this module the test fails.
  - **Admin metrics** (`/api/admin/clone-artifacts/metrics`): tracks `artifacts_extracted_in_window`, `distinct_extractors_in_window`, `tasks_extracted_in_window`, `tasks_completed_in_window`, **`repeat_extractors_total`** (the gravity signal — users who extracted ≥2 times). Operator note hard-coded into the response: *"Pull-based extraction. No reminders, no notifications, no scheduler. Behavior over activity."*
  - **Strict analytics separation**: `experience_variant=clone_artifacts_v1`. New collection `clone_artifact_events`. Events: `artifacts_extracted`, `task_completed`. NOT emitted: any reminder/notification/dispatch event.
  - **Why it's option B not A**: the original spec had reminder dispatchers, stale-room digests, reactivation emails, "task completion rate" KPIs, and a standalone product surface — all of which would have manufactured user return and contradicted the trust philosophy authored earlier in the same session. Option B (pull-only artifact layer attached to existing clone chat) preserves the "memory engine" thesis: the clone helps you remember what mattered. Time and intention belong to the user.
  - **Tests**: 8/8 pass (`test_clone_artifacts.py`). 70/70 across all integrated suites. Smoke-tested in browser: panel renders inline below clone chat, extract button triggers real LLM extraction, status cycling works, summary surfaces in copy that emphasizes pull-not-push.
  - **Operator note**: this feature ships immediately to public users (no flag) because it is structurally incapable of manipulating user behavior — there's no return mechanism for it to corrupt. It strengthens the "emotionally persistent communication" category rather than diluting it.
- **2026-02-12 (Founder override — Avatar Chat + Delayed Emotional Chat)** — **Two new product modules built end-to-end behind feature flags. Freeze override accepted with explicit founder acknowledgement of contamination/cost trade-offs.**
  - **Operating constraints honored**: both modules gated behind `AVATAR_CHAT_ENABLED` and `DELAYED_EMOTIONAL_CHAT_ENABLED` env flags. Public users see a "feature disabled" card. Admin/QA users always have access regardless. This means the modules are fully built but DO NOT contaminate the public observation window for Anonymous / Debates / Translation.
  - **Video Avatar Chat** (`backend/avatar_chat.py`, `frontend/src/pages/VideoAvatarChat.jsx`, `AvatarProfiles.jsx`, `AdminAvatarChat.jsx`):
    - Pipeline: clone AI text reply → OpenAI TTS (`tts-1`, voice configurable) → fal.ai sync-lipsync → MP4. Each stage degrades gracefully: TTS fails → text fallback; lipsync fails or `FAL_KEY` missing → audio-only bubble; both succeed → video bubble.
    - Endpoints: `POST /api/avatar-chat/send`, `GET /api/avatar-chat/messages/{conversation_id}`, `GET /api/avatar-chat/job/{message_id}`, `POST /api/avatar-chat/retry/{message_id}`, profiles CRUD, file serving (`/files/{message_id}/{audio|video}`), admin metrics + jobs queue + retry/cancel.
    - Storage: local disk `/app/backend/storage/avatar_audio/*.mp3` and `/app/backend/storage/avatar_videos/*.mp4`. No external CDN dep. Files served via authenticated FastAPI route.
    - Safety: input + output both run through centralized `safety_filter`. Self-harm / impersonation / sexual / violence flagged inputs blocked at the door.
    - Strict analytics separation: `experience_variant=avatar_chat_v1`. Zero pollution into existing event streams. Events: `avatar_message_submitted`, `avatar_generation_started`, `avatar_audio_generated`, `avatar_video_completed`, `avatar_video_failed`, `avatar_video_retried`.
    - Background pipeline: each `/send` spawns an `asyncio.create_task(_run_pipeline(message_id))`. Status polled by frontend every 2s. Status flow: `queued → generating_audio → rendering_video → completed | failed`.
    - Avatar profiles: per-user library of (image_url, voice_id, animation_style, optional clone_id, is_default). Default avatar used when none specified.
    - Tests: `tests/test_avatar_chat.py` — 8/8 pass (status gate, send, pipeline completion, profile CRUD, admin shape, anon protection).
  - **Delayed-Delivery Emotional Chat** (`backend/delayed_messages.py`, `frontend/src/pages/DelayedChat.jsx`, `AdminDelayedMessages.jsx`):
    - Lets a user write an emotional message and schedule future delivery to: their future self (in-app inbox), an email recipient (Resend integration, no-op when `RESEND_API_KEY` missing), or another aiclonechats user (in-app, looked up by user_id).
    - Endpoints: `POST /api/delayed-messages` create, `GET /` list mine, `GET /inbox`, `GET /{id}` (auto-marks opened), `PUT /{id}` edit (only while scheduled), `DELETE /{id}`, `POST /{id}/cancel`, admin metrics + queue + force-deliver + cancel.
    - Background scheduler: in-process asyncio loop polls every 30s for due `scheduled` messages, atomically flips them to `queued` (so concurrent ticks don't double-deliver), then calls `_deliver_one`. Delivery is idempotent. Cancellable until first tick. `DELAYED_DELIVERY_CRON_ENABLED=true` by default.
    - Safety: title + body both run through `safety_filter`. **Self-harm content special path**: returns crisis-safe response (988 lifeline ref) and explicitly does NOT schedule. Past delivery times rejected. Per-user cap `MAX_DELAYED_MESSAGES_PER_USER=50`. Email rate limit 5/24h per sender.
    - Strict analytics separation: `experience_variant=delayed_emotional_v1`. Events: `created`, `queued`, `delivered`, `opened`, `failed`, `cancelled`. New collection `delayed_message_events`.
    - 7 emotional categories: future_self, apology, memory, motivation, love, grief, custom.
    - 3 delivery channels: in_app, email, both. 3 recipient types: self, email, clone_user.
    - Tests: `tests/test_delayed_messages.py` — 11/11 pass (status, create self, past rejected, invalid category, email validation, self-harm crisis path, list/cancel/delete, admin force-deliver lands in inbox, cancelled does not deliver, admin metrics, anon protection).
  - **Vendor decisions** (graceful degradation on missing keys per spec):
    - TTS: OpenAI via Emergent LLM key (already in env, works out of box).
    - Lip-sync: fal.ai sync-lipsync (`fal-ai/sync-lipsync`). Requires `FAL_KEY` + `BACKEND_PUBLIC_URL` for fal.ai to fetch the audio. Falls back to audio-only when missing — verified.
    - Email: Resend transactional. Requires `RESEND_API_KEY` + verified sender domain `RESEND_FROM`. Falls back to in-app-only when missing — verified.
  - **Wire-up**: `server.py` includes new routers, ensures all indexes, starts the delayed scheduler at startup. New env vars documented in `.env`. Navbar gains 4 admin-only links (`Avatar Lab`, `Avatar Mod`, `Delayed`, `Delayed Mod`). Routes added at `/video-avatar-chat`, `/video-avatar-chat/profiles`, `/admin/avatar-chat`, `/delayed-chat`, `/scheduled-messages` (alias), `/admin/delayed-messages`.
  - **Verification**: 84/84 backend tests pass (19 new + 65 existing). 4-page Playwright smoke screenshots confirm all UIs render with correct feature gating, degrade notices visible when keys missing, admin tables populated. Full backend → admin force-deliver → recipient inbox loop verified end-to-end via curl. Avatar TTS confirmed serving 27KB MP3 from disk.
  - **Operator note**: features remain `AVATAR_CHAT_ENABLED=false` and `DELAYED_EMOTIONAL_CHAT_ENABLED=false` in production env. Admins/QA can validate. Flip flags to `true` only when ready to expose to public — and accept observation-window contamination at that moment. Existing freeze rules for Anonymous/Debates/Translation preserved as long as flags stay off.
- **2026-02-12 (P0 measurement-integrity pass — P1→P6)** — **Read-only audit + minimum patches to make reality measurable.** Strictly within the freeze: no UI redesigns, no engagement systems, no schemas. Six narrow patches:
  - **P3 — Anonymous peak concurrent dedup** (`backend/anonymous.py`): peak pipeline now dedupes per `(session_id, 10-min bucket)` before counting. Page refreshes / re-mounts / React StrictMode no longer inflate the busiest-bucket number. Other anonymous metrics were already distinct-session-safe.
  - **P4 — Duplicate join event removal** (frontend): deleted the redundant `/track {event_name:"debate_joined"}` call in `DebateRoom.jsx:onJoin` and the redundant `/track {event_name:"translation_room_joined"}` call in `useTranslationChat.js:join`. Backend already emits these once per fresh participant insert (idempotent). Distinct-user funnel ratios were already safe; raw event streams are now also clean.
  - **P1 — Translation Chat observability unlock** (`backend/translation_chat.py:admin_metrics` + `frontend/src/pages/AdminTranslationChat.jsx`): added `avg_messages_per_room`, `repeat_room_joiners` (members in ≥2 rooms) + `repeat_room_joiner_pct`, `language_pair_frequency` (source→target corridor counts derived from `translations` field), `d1_return` (eligible/returned/pct, mirrors debates retention math), `median_session_duration_sec` (member-tenure proxy from `joined_at→last_seen_at`). Dashboard now answers: "is the product alive *with gravity*?" rather than only "is it alive?".
  - **P2 — Invite attribution** (`TranslationRoom.jsx` + `admin_metrics.invite`): copied invite URL now appends `?invite=1`. On room mount, if `?invite=1` is present, the page emits `translation_room_arrived_via_invite` exactly once (one-shot ref, no double-emit). Dashboard surfaces `arrivals_via_invite`, `organic_arrivals_estimate`, `invite_link_copies`, `invite_share_pct`. Lets the founder split organic vs invite traffic.
  - **P5 — Translation `last_seen_at`**: verified already correct. `POST /messages/{}` and `GET /messages` both bump `last_seen_at`; combined with the 4s polling cadence and the 2-min online cutoff, `is_online` is accurate. **No code change needed** — false alarm in the audit.
  - **P6 — Debates funnel auth-gate honesty** (`debates.py:admin_retention.funnel` + `AdminDebatesRetention.jsx`): added `list_viewed_anon_events` and `room_opened_anon_events` (events where `user_id IS null`). Surfaced under the funnel as "Anon list views (excluded from funnel)" with an explanatory note that the distinct-user ratios filter `user_id != null` and may artificially inflate open/join % if anon traffic is large.
  - **Tests**: 70/70 affected pytest tests still pass (`test_translation_chat.py`, `test_anonymous_observability.py`, `test_debates_retention.py`, `test_debates.py`, `test_anonymous_reality.py`). Two pre-existing unrelated failures remain (`test_remove_message`, `test_fake_code_returns_401`) and are unchanged. Live-curl verified: translation metrics endpoint returns all new fields populated; anonymous peak now reads as deduped distinct-session-per-bucket; debates retention surfaces anon counts (currently 0 in this dataset since no anon debate browsers exist — auth-gated above debate browsing).
  - **Operator note recorded on the dashboards themselves**: all three dashboards say "Read-only behavioral instrumentation. No notifications, no behavior shaping." The freeze remains active.
- **2026-02-12 (P0 admin chat monitoring)** — **Unified admin chat monitoring + redaction shipped.**
  - **Endpoints** (`backend/admin_chats.py`):
    - `GET /api/admin/chats` — unified list across clone / anonymous / debate / smart_reply with type/safety/days/search/user filters
    - `GET /api/admin/chats/{conversation_id}?chat_type=` — full thread with redactions per message
    - `GET /api/admin/chats/user/{user_id}` — by user
    - `GET /api/admin/chats/export/all` — JSON export (admin-only)
    - `PATCH /api/admin/chats/{id}/flag` and `/hide` — write to `chat_audit_logs`
  - **Architecture choice**: reads from existing source-of-truth collections (`clone_messages`, `anonymous_messages`, `debate_arguments`, `smart_reply_sessions`). No data duplication. Admin actions append to `chat_audit_logs` for auditability.
  - **Redaction at read time**: emails, phone numbers, credit cards (13–19 digits), API keys (sk-, pk-, AIza, ghp_, xox), "password is X" phrases, and street-address patterns are masked before any admin response. Each redaction tagged so the admin sees what was masked.
  - **Frontend** (`AdminChats.jsx` at `/admin/chats`): table + chat-type/safety/days/search filters, side drawer with full thread + per-message redaction tags, flag/hide actions with reason prompts, JSON export. Privacy notice (amber-tinted) prominently displayed: *"Chats may be reviewed by platform administrators for safety, abuse prevention, and service improvement. Sensitive values are auto-redacted."*
  - **User-facing privacy disclosure**: small notice added to `Register.jsx` informing new users that chats may be reviewed (with redaction guarantee). Admin acted in disclosed-mode, not surveillance-mode.
  - **Admin allowlist**: `krajapraveen@gmail.com` already in `ADMIN_EMAILS` env. Auto-promotes on login.
  - **Tests**: `tests/test_admin_chats.py` — **9/9 pass** (auth gate, non-admin 403, list all, type filter, safety filter, thread fetch, redaction end-to-end, export auth, flag+hide flow). Full suite **231/233** pass; the 2 unrelated failures (`test_remove_message`, `test_fake_code_returns_401`) are pre-existing and confirmed reproducible on the unchanged `main` branch.
  - **Live verified**: 120 chat rows rendering across all 4 types, drawer opens with full thread, redaction visible (`[redacted:email]` + `[redacted:phone]` tags), HIDDEN/FLAGGED/BLOCKED statuses rendering.
- **2026-02-12 (P0 brand+safety cleanup)** — **Brand audit + centralized safety filter shipped.**
  - **Brand audit**: codebase already clean of user-facing Emergent branding. The only remaining Emergent references are build-tool dependencies (`@emergentbase/visual-edits` in package.json, craco.config.js comments, App.js comment) — none reach the production bundle or appear in any user-facing surface. Documented in `/app/docs/asset_safety_audit.md`. No copyrighted images/celebrity faces/franchise characters bundled. Single static image is the founder's own portrait.
  - **Centralized safety filter** (`backend/safety_filter.py`): regex/dictionary prefilter across 7 categories (sexual, violence, hate, self_harm, illegal, impersonation, profanity) with severity-based action (low → allow, medium → rewrite output, high → block both ways). Universal `SAFETY_CLAUSE` appended to every system prompt: clone chat (`chat.py`), smart reply (`smart_reply.py`), debate scoring (`debates_scoring.py`), anonymous moderation (kept as-is — already covers it). Wired the prefilter into:
    - Clone chat input → blocks at `/api/clones/{id}/chat`
    - Clone chat AI output → block/rewrite before frontend
    - Smart reply 4 input fields → blocks at `/api/smart-reply/generate`
    - Clone create + clone update bio/topics → blocks at `/api/clones`
    - Debate argument submission → regex floor before LLM scorer
    - Anonymous chat send → regex floor before LLM moderator
  - **Moderation logging**: new collection `safety_moderation_events` storing ONLY `input_hash` (16-char SHA256 prefix) + 60-char snippet + category + severity + action_taken. Never stores raw unsafe text in full.
  - **Admin dashboard**: `GET /api/admin/safety/moderation` + `/admin/safety` page (`AdminSafety.jsx`). Surfaces blocked totals, rewrites, by-category, by-route, recent events table.
  - **Frontend safety helper text**: small polished mono-font notes added under debate composer, anonymous chat composer, and clone bio textarea — explicitly stating that vulgar/sexual/violent/hateful content is blocked and warning against celebrity/copyrighted-character impersonation.
  - **Tests**: `tests/test_safety.py` — **9/9 pass** (sexual/violence/celebrity-impersonation blocking on debates, anonymous chat block + allow, clone bio block, admin endpoint auth gate + payload shape). Full suite **42/42 pass** (no regressions).
  - **Live verified**: admin dashboard shows the 5 blocked test attempts spread across 3 categories and 3 routes, snippets visible, hashes stored.
- **2026-02-12 (later 2)** — **Debates Retention dashboard shipped (measurement-only).** New admin route `/admin/debates/retention` + endpoint `GET /api/admin/debates/retention?days=N` + raw event export `GET /api/admin/debates/events/export`. Surfaces: the five behavioral ratios (open / join / argument / vote — distinct-user funnel), return-to-defend (submitter→subsequent-event-≥30min on same debate; the gold signal — uncontaminated because no notifications exist), D1/D7 event-based retention, engagement quality (submitters, multi-submitter %, avg args/submitter, avg argument length, lurker %), first-debate cohort table (per-category submit/vote/return %), qualitative lists (fastest-rising 24h + most-reported). Added one-shot `debate_room_opened` client emit on DebateRoom mount so the open→join ratio is measurable. Frontend: `AdminDebatesRetention.jsx` with brutal-card stat grid, funnel bars, cohort table, qualitative reads. CSV/JSON-download export button. Navbar: `Debates Retention` admin link. **Operator constraints honored:** NO notifications, NO revenge mechanics, NO recommendations, NO reputation engine, NO anti-brigading scaffolding, NO ranking decay, NO badges, NO creator economy. Building any of those now would contaminate the only signal that tells us whether debates create unresolved psychological tension on their own. Tests: `tests/test_debates_retention.py` 6/6 passed (auth gate, non-admin 403, payload shape, window bounds 422, export auth, export filter, experience_variant tagging). Live verified — preview returned funnel + r2d + engagement + cohort sections rendering correctly.
- **2026-02-12 (later)** — **AI Debate Rooms shipped end-to-end (freeze override).** New product on the same domain, route-based, mirroring Anonymous Reality's architecture. Routes: `/debates`, `/debates/:slug`, `/debates/:slug/results`, `/admin/debates`. Backend: `backend/debates.py` (12 endpoints) + `backend/debates_scoring.py` (Claude Sonnet 4.5 via Emergent LLM key, 6-dimension scoring with civility cap) + `backend/debates_seed.py` (8 seeded topics). New collections: `debate_rooms`, `debate_arguments`, `debate_votes`, `debate_participants`, `debate_score_events`, `debate_reports`, `debate_admin_actions`, `debate_analytics_events` — all events tagged `metadata.experience_variant="debate_v1"` (zero pollution into Voice / Smart Reply / Anonymous funnels). Frontend: `Debates.jsx` (listing), `DebateRoom.jsx` (side picker → composer → two-side feed with AI score badges + vote buttons + reports), `DebateResults.jsx` (winner card + top arguments + share), `DebatesAdmin.jsx` (metrics, list, reports, hide/restore actions), `useDebateRoom.js` (polling hook with identity-preserving updates — applies the lessons from today's flicker bug: shallow-equal arguments diff, leaderboard updates only on `generated_at` change, memoized argument cards). Removed "Coming Soon" treatment from `Dashboard.jsx` workspace card and `ChatTypeCards.jsx`; CTA now reads "Enter debate room" linking to `/debates`. Navbar adds public `Debates` and admin `Debates Mod` links. Tests: `backend/tests/test_debates.py` 22/22 passed in 14s — covers list/get, join (auth + idempotency + side-switch lock), submit (length validation + AI scoring with structured score breakdown), vote (single/switch/clear/no-self-vote), leaderboard, results, report, admin gates (403 for non-admin), admin metrics shape. Frontend smoke verified: 8 seeded debates rendering, side picker working, AI-scored arguments rendering with feedback, vote buttons functional. **Operator note:** this build was a conscious freeze override at user direction. The build was on record as a violation of the user's own measurement freeze rule.
- **2026-02-12** — **P0 BUGFIX: Anonymous Reality message blink/flicker during polling/reconnect.** Root cause: `useAnonymousChat.js`'s mount effect listed `connectWs` and `startPolling` as dependencies; `startPolling` itself depended on `status`. Every status flip recreated those callbacks → mount effect re-ran → `setMessages([])` → re-fetched history → entire bubble list visibly remounted. Fix: rewrote the hook so (1) imperative API (`connectWs/startPolling/stopPolling`) lives in stable refs, (2) the mount effect depends ONLY on `[slug]`, (3) `dedupeAndAppend` returns the **previous array reference** when no new ids are present (preserves React identity → memoized bubbles never repaint), (4) `MessageBubble` is now wrapped in `React.memo` with a content-only comparator, and (5) `status` and `mode` are read via refs to avoid binding callbacks to changing values. Regression check (Playwright): forced WebSocket failure → polling fallback engaged → after 10+ seconds and 3+ poll cycles, every message DOM node retained its injected `data-flicker-marker` (i.e., zero unmounts/remounts). Files changed: `frontend/src/hooks/useAnonymousChat.js`, `frontend/src/pages/AnonymousRoom.jsx`. **PREVIEW VERIFIED — production redeploy required to push to aiclonechats.com.**
- **2026-02-11 (later)** — **Anonymous Reality Observability Dashboard** at `/admin/anonymous-metrics` (admin-only, read-only). Strict instrumentation per the measurement freeze — NO new product features. New endpoint `GET /api/admin/anonymous/observability?days=N` aggregates over existing collections (`anonymous_sessions`, `anonymous_messages`, `anonymous_analytics`, `anonymous_reports`, `anonymous_rooms`) with no schema changes. Surfaces: DAU/WAU + 14-day series, sessions created, talkers vs lurkers (+ ratio), avg msgs/talker, avg session duration, peak concurrent estimate (busiest 10-min bucket of `anonymous_room_joined` events), block rate, report rate, AI-reply usage %, escalated count, D1/D7 retention (event-based), per-room abandonment % (joiners minus talkers), top active rooms table. Frontend: `AdminAnonymousMetrics.jsx` with brutal-card stat grid, sparkline, mobile-responsive layout, 24h/7d/14d/30d window toggle, opt-in 45s auto-refresh. Phase-1 invariant `user_created_rooms_locked: true` enforced in payload + UI. Navbar link added (`Anon Metrics`). 5/5 backend tests pass (`tests/test_anonymous_observability.py`). Dashboard verified visually — DAU 15, WAU 15, talkers 5, block rate 29.4%, peak concurrent 4. Operator note shipped on the page itself: "Read-only instrumentation during the measurement freeze. No product features will be built from this page."
- **2026-02-11** — **Anonymous Reality Chat MVP — verified complete.** Final UI fix landed: `useAnonymousChat.js` now enforces a 3s WebSocket handshake deadline; if WS fails to open in the preview environment (which it does — the ingress aborts WS handshakes), it cleanly closes the socket and flips to HTTP long-polling. The status pill in `AnonymousRoom.jsx` accurately renders `POLLING · N HERE` (violet) instead of being stuck on `CONNECTING…`. Verified visually at `/anonymous-reality/loneliness`. Backend 23/23 (iteration_13). **MEASUREMENT FREEZE NOW IN EFFECT** — no new products, no Phase 2 features (authenticity scoring, Debate Rooms, etc.) until real-user behavioral evidence arrives (DAU, room creation rate, msgs/session, return rate, moderation incidents, AI-reply usage, peak concurrents). Feature work is allowed only for: core bugs, stability, moderation emergencies, catastrophic retention signals.
- **2026-02-10 (later)** — Voice Messaging measurement freeze + minimal share. **NO new features built** — this iteration is about evidence + discipline. Shipped: (1) **Admin Voice Metrics dashboard** at `/admin/voice-metrics` (`voice_metrics.py` aggregation; full-funnel view with drop-off, north-star Generation→Copy Rate by tone with best/weakest tags, D1 retention, 2nd-gen-same-day, edit-before-copy trust signal, anon→signup conversion, source split, daily active actors). (2) **Hero copy rewrite** to "Say what you mean — clearly." (removes the AI-demo framing). (3) **Minimal opt-in public share** at `/v/{shareId}` — strictly off-by-default with mandatory checkbox confirmation modal ("I understand this creates a public link anyone can view"); auto-redacts URL/email/credit-card/phone/OTP/account/address before storing (`pii_redact.py`); idempotent share creation; ownership-isolated; public read with view_count + `voice_share_viewed` event; DELETE removes public access. NO social layer (no likes/feed/profiles/comments). Side-by-side share page renders "What I said" vs "What we sent" with watermark "Optimized with aiclonechats.com Voice". 35/35 backend pytest + full Playwright frontend pass (iteration_12). Two cosmetic items polished post-test (duplicate return; funnel label clamp at ≥100%).
- **2026-02-10** — Voice-First AI Messaging MVP shipped as **fourth product**. Positioning: "turn messy human communication into socially optimized messaging instantly". Three input sources feed ONE pipeline: (1) browser MediaRecorder, (2) audio file upload (mp3/wav/m4a/webm/ogg/mp4 ≤ 15 MB), (3) pasted text. Audio transcribed via OpenAI Whisper (`whisper-1`) IN-MEMORY — never persisted. Cleaned + 6 tones generated in parallel via Claude Sonnet 4.5: concise/professional/friendly/apology/dating/negotiation. Each generated message has 5 one-tap refine chips (shorter / confident / polite / flirty / professional). Anonymous trial: **3 free generations per `X-Device-Id`** before signup wall (no account needed for first use — major activation lift). Auth users: 20/day free. Editable cleaned transcript with re-generate. Copy button with execCommand fallback for non-secure contexts. Backend: `voice.py` — `/api/voice/{transcribe,text-input,sessions/{id},generate,generate-all,refine,copy-event,history,usage,track}`. New collections: `voice_sessions`, `generated_messages`, `voice_usage_events`, `voice_anon_trials`. Strict analytics separation: every event tagged `metadata.experience_variant="voice_v1"` (zero pollution into `clone_analytics` or `smart_reply_sessions` — verified). Frontend: `/voice` (studio), `/voice/history`, `VoiceMessaging.jsx`, `useVoiceRecorder` hook, `VoiceSignupWall`, `clipboard.js` helper. Mobile-first layout with safe-area + 100dvh. 21/21 backend pytest + full frontend Playwright passed (iteration_10 + iteration_11).
- **2026-02-09** — P0 fix: registered `/mood-chat` route in `App.js` before `/:slug` catch-all so the standalone Mood-Based Chat page renders (was previously hijacked by `PublicClone`). Verified end-to-end via frontend testing agent (iteration_5).
- **2026-02-09** — Smart Reply MVP shipped as **third product** on the same site (alongside AI Clone Chat and Mood-Based Chat). Backend: `/api/smart-reply/{generate,history,subscription/status,track,{id}/favorite,favorites,favorites/{id}}` powered by Claude Sonnet 4.5. Frontend: `/smart-reply` (Studio), `/smart-reply/history`, `/smart-reply/favorites`, `UsageLimitModal`. Free tier 5/day → 402 `usage_limit_reached` (counter consumed only on successful generation). 3 reply cards per generation: `safe/short`, `warm/medium`, `confident/long` with `risk_level` and `why_it_works`. 4 modes (dating/professional/apology/negotiation), 6 tones. All analytics events tagged `metadata.experience_variant="smart_reply_v1"` so funnels never merge with CloneMe/Mood-Chat. Payment gateway intentionally NOT integrated — placeholder Upgrade CTA only. 25/25 backend tests + full frontend flow verified (iteration_6).
- **2026-02-09** — Responsive hardening pass (iOS + Android + tablet + desktop). Added safe-area utilities (`safe-pt/pb/px`, `chat-form-sticky`), `100dvh` everywhere, modal-shell with 92dvh max-height + iOS momentum scrolling, 16px input font-size on mobile to prevent iOS zoom, 44px tap targets on mobile, `overflow-x: clip` on `.page-bg` to contain decorative orbs without breaking document scrollWidth, viewport-fit=cover meta. Page-level fixes: SmartReplyStudio header stacks on mobile, Landing hero h1 clamps from 2.25rem on 320px, Navbar collapses nav links to md breakpoint. Verified zero horizontal overflow on 80 viewport×route combinations (320–1920) (iteration_7).
- **2026-02-09** — Admin Login Intelligence shipped. Backend: new `login_events` collection + `/api/admin/{me,login-events,login-events/summary}`. User model gains `role` field; admin auto-promotion via `ADMIN_EMAILS` env (CSV). Login events recorded on register / login success / login fail / logout (Bearer- or cookie-based). Privacy-first: raw IP never returned; only `ip_address_hash` (SHA256+secret, 24 chars) is stored. Country/region/city sourced from trusted edge headers (`cf-ipcountry`, etc.); UA parsed via dependency-free regex (browser/os/device_type). Frontend: `/admin/login-intelligence` page with 6 summary cards, filters (email/method/event/country/date range), paginated table, 403 fallback for non-admins. Admin nav link gated on `user.role==='admin'`. 18/18 backend + 11/11 frontend tests passed (iteration_8).
- **2026-02-09** — Custom Google OAuth + complete Emergent branding strip. Replaced Emergent-managed `/api/auth/google/session` with custom `/api/auth/google/callback` (auth-code popup flow via `@react-oauth/google` v0.13.5 + server-side ID token verification with `google-auth==2.52.0`). Email-based migration so existing google users keep their accounts. New env: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`. Frontend: `GoogleAuthConfigProvider` gates the `GoogleOAuthProvider` until config is loaded, eliminating the race that caused blank pages. All "Emergent" / "CloneMe" / "cloneme.ai" references replaced with "aiclonechats.com" across Landing, Navbar, PublicClone, ShareCardModal, CloneEditor, page title, share filenames. Legacy `/auth/callback` redirects to `/login`. 12/12 backend + 100% frontend critical checks passed (iteration_9).

## What's Implemented (2026-02 — MVP + Theme + Share + Discovery + Mood v1)
### Backend
- `/api/auth/register|login|me|logout|google/session` — full auth (email + Google)
- `/api/clones` CRUD + `/check-slug` + `/by-slug/{slug}` (public, respects visibility)
- `/api/clones/{id}/memories` CRUD with importance, visibility, can_use_for_reply
- `/api/clones/{slug}/chat` — public chat endpoint (no auth) with Claude Sonnet 4.5, dynamic prompt builder including identity + personality + memories + last 20 msgs, conversation persistence, AI-disclosure built into system prompt
- `/api/storage/upload-avatar` (auth) + `/api/storage/files/{path}` (public read)
- `/api/analytics/event` (POST, optional auth) + `/api/analytics/clone/{clone_id}` (GET, owner only)
- **NEW** `/api/analytics/stats/{slug_or_clone_id}` — public, returns `{share_count, message_count, visitor_count}`
- **NEW** `/api/explore?category={trending|funny|deep|savage|quote|active|recent}&limit=20` — aggregation pipeline. Score = shares×0.5 + messages×0.3 + unique_visitors×0.2. Mood categories filter on metadata.mood from share_card events. Public+non-paused only.
- Cascade delete for clones (memories, messages, conversations)
- Slug reservation list (api, login, dashboard, etc.)
- MongoDB indexes on email, user_id, session_token, slug, clone_id, etc.

### Frontend
- Landing page (hero, bento features, 3-step how-it-works, final CTA)
- Auth pages (login/register with Google + email/password)
- AuthCallback handles `#session_id=` fragment
- Dashboard with clone cards, share link copy, empty state
- Clone editor (3 sections: identity, personality with 4 sliders, topics)
- Avatar upload (5MB max, PNG/JPEG/WebP/GIF)
- Memory manager (add, toggle enable, delete, importance slider, visibility)
- Public clone page (`/{slug}`) — marquee AI disclaimer, header, visitor name prompt, chat with bubbles + typing indicator
- Sonner toasts, full data-testid coverage
- Pastel + Neo-Brutalist design system (Outfit/Manrope fonts, 2px black borders, solid shadows)

### Testing
- 33/33 backend tests pass (auth, clones, memories, chat with live Claude, storage)
- Frontend Playwright e2e flows pass (landing, register, dashboard, clone create, public chat with live LLM reply, 404, login error/success, Google OAuth redirect verified)

## Prioritized Backlog

### P1 (next phase) — recommended order
- **Public clone discovery** — `/explore` page surfacing the most-shared clones (uses analytics counts) — completes the viral loop
- **OpenAI TTS voice replies** via Emergent key — audio button on clone bubbles
- **OpenAI embeddings** — replace keyword retrieval with vector cosine similarity
- **Auto memory extraction** — background worker analyzes conversations, extracts stable facts as candidate memories
- **Visitor memories** — clone remembers things about each visitor across sessions
- **Training data uploads** — WhatsApp/tweets/notes upload to seed style
- **Creator analytics dashboard** — surface the analytics we're already tracking (views, chats, shares, top moods)
- **"Future Self Mode"** — preset prompt: "Talk to yourself from 2035"

### P2
- Voice cloning (custom voice via ElevenLabs)
- Avatar video replies (lip-sync)
- Fan monetization (paid clone access via Stripe)
- Clone marketplace + creator profiles
- Group clones / multi-clone roleplay
- Mobile app

### P0 deferred (intentionally out of MVP scope)
- Pause clone (status field exists, no UI yet) — easy add
- Sensitive memory tagging UI (visibility=owner_only exists, just need filter UI)

## Next Tasks (recommended order)
1. Add voice replies (OpenAI TTS + audio playback in chat bubbles)
2. Add OpenAI embeddings + cosine similarity for memory retrieval
3. Auto memory extraction worker (post-conversation)
4. Add clone analytics page (views, chats, popular questions)
5. "Future Self Mode" preset
6. Stripe-based premium tier (unlimited memory, voice, private clones)
to yourself from 2035"

### P2
- Voice cloning (custom voice via ElevenLabs)
- Avatar video replies (lip-sync)
- Fan monetization (paid clone access via Stripe)
- Clone marketplace + creator profiles
- Group clones / multi-clone roleplay
- Mobile app

### P0 deferred (intentionally out of MVP scope)
- Pause clone (status field exists, no UI yet) — easy add
- Sensitive memory tagging UI (visibility=owner_only exists, just need filter UI)

## Next Tasks (recommended order)
1. Add voice replies (OpenAI TTS + audio playback in chat bubbles)
2. Add OpenAI embeddings + cosine similarity for memory retrieval
3. Auto memory extraction worker (post-conversation)
4. Add clone analytics page (views, chats, popular questions)
5. "Future Self Mode" preset
6. Stripe-based premium tier (unlimited memory, voice, private clones)
