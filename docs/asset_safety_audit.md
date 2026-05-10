# Asset Safety & Brand Audit — aiclonechats.com

**Last audited:** 2026-02-12

## Summary
- ✅ No "Powered by Emergent" or "Built with Emergent" text exists in any user-facing surface (frontend, public assets, OpenGraph, manifest, footer, navbar, toasts, console).
- ✅ No copyrighted images, celebrity faces, brand logos, or trademarked characters are bundled in the codebase.
- ✅ No third-party watermarked assets in `frontend/public/`, `frontend/src/`.
- ✅ Single user-uploaded image (`frontend/public/founder.jpg`) is the founder's own portrait.
- ✅ All seeded text content (debate topics, anonymous room titles, sample copy) is original and brand-safe.

## Frontend assets

| Path | Status | Notes |
|---|---|---|
| `frontend/public/founder.jpg` | ✅ KEEP | Founder's own portrait (Raja Praveen Katta). Owned. |
| `frontend/public/index.html` | ✅ CLEAN | No Emergent branding. References favicon/manifest that aren't yet generated — harmless missing files, browser shows defaults. |
| `frontend/src/assets/` | ✅ ABSENT | No bundled image assets. Uses CSS gradients + Tailwind for visual identity. |
| Iconography | ✅ CSS/text | All UI uses CSS shapes, FontAwesome glyph fallbacks, lucide-react. No proprietary icon packs. |

## Build-tool references (not user-visible)

| Path | Type | Reason kept |
|---|---|---|
| `frontend/package.json` (`@emergentbase/visual-edits`) | dev dependency | Build-time visual editor. Never executes in production bundle and never appears in user-facing UI. |
| `frontend/craco.config.js` | build config | Build-time. Comment-only references. |
| `frontend/src/App.js` (line 37 comment) | code comment | Documents historical refactor. Not visible to users. |

These are explicitly **NOT** user-facing and **NOT** legally relevant to public deployment.

## Backend
- ✅ No celebrity names hardcoded as clone seed data.
- ✅ Centralized safety filter (`backend/safety_filter.py`) blocks user-submitted celebrity impersonation strings (Elon Musk, Donald Trump, Taylor Swift, etc.) and copyrighted franchise impersonation (Mickey, Spider-Man, Naruto, Goku, Pikachu, Harry Potter, James Bond) at input time.
- ✅ All system prompts (clone chat, smart reply, debates, anonymous moderation) include the universal `SAFETY_CLAUSE` forbidding profanity, sexual content, violence, slurs, piracy instructions, trademark misuse, and celebrity impersonation.

## Seed data (debates)
8 seeded debates — all original, safe, family-friendly:
- Is AI creativity real creativity?
- Should AI clones replace influencers?
- Are voice messages better than text?
- Is dating easier or harder with AI?
- Should students use AI for homework?
- Is remote work better than office work?
- Are AI friends dangerous or helpful?
- Should social media hide likes?

## Seed data (anonymous rooms)
- loneliness, family-pressure, money-reality, mental-load, relationships
- Original; no trademark, no celebrity references.

## Removed / replaced
Nothing required removal in this audit pass. The codebase started clean of risky assets.

## Open follow-ups (not blockers)
- Generate brand-owned `favicon.ico`, `logo192.png`, `manifest.json` to remove broken `index.html` references. Browsers tolerate the 404s today.
- Add an automated CI check that fails the build if any of these strings appear in the production bundle: `"powered by emergent"`, `"built with emergent"`, `assets.emergent.sh`.
- Document UGC takedown / DMCA response process when public traffic begins.
