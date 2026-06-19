# Sign in with Apple — Operator Setup Guide

This is your **one-time** setup at the Apple Developer console + the 4 production env vars you'll paste into the deployed backend. Everything code-side is already implemented and tested (10/10 pytests green).

Estimated time: **20–25 minutes** in the Apple console + **2 minutes** pasting env vars.

---

## 1. Apple Developer Console (https://developer.apple.com/account)

Sign in with the Apple ID that owns your Apple Developer enrolment.

### Step 1.1 — Create an App ID  *(skip if you already have one for aiclonechats.com)*
- Go to **Certificates, Identifiers & Profiles → Identifiers**.
- Click **+** → choose **App IDs** → **Continue** → **App** → **Continue**.
- Description: `AI Clone Chats Web`.
- Bundle ID (Explicit): `com.aiclonechats.app` *(pick anything in reverse-DNS form — you won't expose this)*.
- Scroll down to **Capabilities** → tick **Sign In with Apple**.
- Click **Continue** → **Register**.
- **Copy the Team ID** shown at the top-right of the Apple Developer console (10 chars, looks like `9ABCDE1234`). → This is **`APPLE_TEAM_ID`**.

### Step 1.2 — Create a Services ID  *(THIS becomes your `client_id`)*
- Same Identifiers screen → **+** → choose **Services IDs** → **Continue**.
- Description: `AI Clone Chats Sign-in`.
- Identifier: **`com.aiclonechats.signin`** *(exactly this — Apple requires reverse-DNS format)*.
- Click **Continue** → **Register**.
- Click the **Services ID you just created** to edit it.
- Tick **Sign In with Apple** → click **Configure** beside it.
- In the Web Authentication Configuration popup:
  - Primary App ID: select the App ID from Step 1.1.
  - Domains and Subdomains: `aiclonechats.com` *(and add `www.aiclonechats.com` on a new line if you serve that too)*.
  - Return URLs: **`https://aiclonechats.com/api/auth/apple/callback`** *(this must be EXACT — single trailing slash matters)*.
  - Click **Next** → **Done** → **Continue** → **Save**.
- → This Services ID `com.aiclonechats.signin` is **`APPLE_CLIENT_ID`**.

### Step 1.3 — Verify the domain
Right after saving the Services ID, Apple will show **Download** next to your domain — that gives you a file like `apple-developer-domain-association.txt`.

- Download that file.
- **Place it at** `/app/frontend/public/.well-known/apple-developer-domain-association.txt` (a `.well-known/` folder with a placeholder README is already created for you) **OR** send me the file and I'll drop it in.
- Redeploy the frontend so the file goes live at `https://aiclonechats.com/.well-known/apple-developer-domain-association`.
- Back in the Apple console, click **Verify** beside the domain. Status must read **Verified** before you proceed.

### Step 1.4 — Create the private key (`.p8`)
- Left sidebar → **Keys** → **+**.
- Key Name: `AI Clone Chats Sign-in Key`.
- Tick **Sign in with Apple** → click **Configure** beside it.
- Select the same Primary App ID from Step 1.1 → **Save**.
- Click **Continue** → **Register**.
- Click **Download** — you get a single `AuthKey_XXXXXXXXXX.p8` file.
  **This is the ONLY time Apple shows you this file. Save it somewhere safe.**
- The 10-char filename suffix (`XXXXXXXXXX`) is **`APPLE_KEY_ID`**.

### Step 1.5 — You're done in Apple's console
You now have these 4 values handy:
- `APPLE_TEAM_ID` (Step 1.1 → top-right of Apple console, 10 chars)
- `APPLE_CLIENT_ID` = `com.aiclonechats.signin` (the Services ID from Step 1.2)
- `APPLE_KEY_ID` (10 chars from the `.p8` filename in Step 1.4)
- `APPLE_PRIVATE_KEY` = the **full contents** of the `.p8` file (multi-line, starts with `-----BEGIN PRIVATE KEY-----`)

---

## 2. Production env vars (paste into the deployed backend's `.env`)

```bash
APPLE_TEAM_ID=9ABCDE1234
APPLE_KEY_ID=ABC123DEFG
APPLE_CLIENT_ID=com.aiclonechats.signin
APPLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----
MIGT....multi-line-contents-of-AuthKey_XXXXXXXXXX.p8....
-----END PRIVATE KEY-----"
APPLE_REDIRECT_URI=https://aiclonechats.com/api/auth/apple/callback
APPLE_POST_AUTH_REDIRECT=https://aiclonechats.com/
```

> **About the multi-line `.p8`**: most env loaders (dotenv included) treat
> quoted multi-line strings correctly. If your hosting UI doesn't support
> multi-line values, paste the key as a single line with `\n` separators —
> the code already normalises that case.

After pasting, restart the backend. Hit `https://aiclonechats.com/api/auth/apple/config` — it should return `{"configured": true}` once everything is in place.

---

## 3. What the production user sees

1. On `aiclonechats.com/login` and `/register`, a black "Continue with Apple" pill button now sits directly below "Continue with Google".
2. Clicking it → 302 to Apple's authorize page → user signs in / approves → Apple posts back to `/api/auth/apple/callback`.
3. Backend verifies the id_token signature against Apple's JWKS, links/creates the user (primary match by Apple `sub`, fallback by email), mints the session cookie, redirects into the SPA.

On preview (`*.preview.emergentagent.com`) the button stays hidden because Apple won't accept that domain as a redirect URI.

---

## 4. What's already in the code

| Component | Path |
|-----------|------|
| Backend OAuth flow | `/app/backend/auth_apple.py` |
| Backend env scaffolding | `/app/backend/.env` (5 keys, all currently empty) |
| Frontend button | `/app/frontend/src/components/AppleSignInButton.jsx` |
| Wired into Login + Register | `/app/frontend/src/pages/Login.jsx`, `/app/frontend/src/pages/Register.jsx` |
| Tests (10/10 passing) | `/app/backend/tests/test_auth_apple.py` |

---

## 5. Once it's live — quick verification

```bash
# Production env shows configured=true
curl https://aiclonechats.com/api/auth/apple/config

# Visit the login page — Apple button should render
open https://aiclonechats.com/login
```

If anything goes wrong, the failure path is logged with a short error code (`apple_state_invalid`, `apple_token_invalid_client`, `apple_nonce_mismatch`, …) so production logs will tell you exactly what to fix.
