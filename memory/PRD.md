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
