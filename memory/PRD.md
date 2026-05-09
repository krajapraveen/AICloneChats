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
