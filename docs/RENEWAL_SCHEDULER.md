# Renewal Reminder Scheduler — Production Setup

The renewal reminder system fires a one-shot email 3 days before a paid
subscription expires. The actual sender is idempotent: the same user is
never reminded twice for the same `order_id`, even if the scheduler
runs hundreds of times per day. This document explains how to wire up an
**external daily scheduler** so reminders are not dependent on a backend
restart.

## What the scheduler does

It POSTs to this admin endpoint, once a day, with an admin bearer token:

```
POST https://aiclonechats.com/api/admin/billing/run-renewal-reminders
Authorization: Bearer <admin-session-token>
Content-Type: application/json
```

Optional query: `?dry_run=true` to evaluate candidates without sending.

The response body is a JSON summary that the scheduler should log:

```json
{
  "run_id": "run_xxxxxxxxxxxxxxxx",
  "ran_at": "2026-06-12T09:00:00+00:00",
  "triggered_by": "scheduler:krajapraveen@gmail.com",
  "examined": 12,
  "sent": 4,
  "skipped_admin": 1,
  "skipped_already": 7,
  "failures": 0,
  "dry_run": false,
  "failure_samples": []
}
```

Every successful invocation is persisted to `db.renewal_reminder_run_logs`,
visible at `/admin/renewal-reminders` in the admin console.

## Why "run multiple times daily" is safe

The dedup key is `renewal_reminder_sent_for: <order_id>` on the user
document. Once a reminder is sent (or fails permanently) for a given
order cycle, no further attempt is made. So a scheduler outage followed
by 5 catch-up runs in an hour will still result in exactly one email per
subscription cycle.

## Recommended schedule

**Daily at 09:00 UTC** — covers all reasonable user timezones during normal
business hours. The 3-day reminder window means even a 72-hour scheduler
outage is fully recovered by the next successful run.

## Auth token — how to mint a long-lived one

The endpoint requires admin role. Production approach:

1. Sign in as the admin (`krajapraveen@gmail.com`) at https://aiclonechats.com/login
2. Open DevTools → Application → Local Storage → copy the value of
   `session_token`
3. Sessions expire after 7 days by default. For automation, either:
   - Set `SESSION_TTL_DAYS=365` in `backend/.env` (rotate yearly), or
   - Use the dedicated admin-mint script in `/app/scripts/mint_admin_session.py`
     (not committed yet — request when needed).

Store the token in your scheduler's secret manager. Never commit it.

---

## Option A — Cloudflare Cron Trigger (recommended)

Create a Cloudflare Worker with a daily cron schedule. The whole thing is
~15 lines.

`wrangler.toml`:
```toml
name = "aiclonechats-renewal-cron"
main = "src/index.js"
compatibility_date = "2026-01-01"

[triggers]
crons = ["0 9 * * *"]   # 09:00 UTC every day

[vars]
TARGET = "https://aiclonechats.com/api/admin/billing/run-renewal-reminders"
```

`src/index.js`:
```js
export default {
  async scheduled(event, env, ctx) {
    const res = await fetch(env.TARGET, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.ADMIN_TOKEN}`,
        "Content-Type": "application/json",
      },
    });
    const body = await res.text();
    console.log(`renewal-reminders ${res.status}: ${body}`);
    if (!res.ok) throw new Error(`scheduler failed: ${res.status}`);
  },
};
```

`wrangler secret put ADMIN_TOKEN` then `wrangler deploy`. Done.

Cloudflare cron triggers are free for the first 100k req/day — overkill.

---

## Option B — GitHub Actions Scheduled Workflow

For projects that already use GitHub Actions, this is the zero-extra-cost
choice. Note: GitHub Actions cron is "best effort" — runs can be delayed by
up to 30 minutes during peak load. Acceptable for renewals because of the
3-day window.

`.github/workflows/renewal-reminders.yml`:
```yaml
name: Renewal reminders
on:
  schedule:
    - cron: "0 9 * * *"    # 09:00 UTC daily
  workflow_dispatch:        # allow manual triggers from the Actions tab

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: POST renewal-reminders
        env:
          ADMIN_TOKEN: ${{ secrets.AICLONECHATS_ADMIN_TOKEN }}
        run: |
          set -euo pipefail
          curl -fsS -X POST \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            https://aiclonechats.com/api/admin/billing/run-renewal-reminders \
            | tee /dev/stderr
```

Add `AICLONECHATS_ADMIN_TOKEN` under Repo Settings → Secrets and variables → Actions.

---

## Option C — Hosting-provider scheduled job

If you ever migrate off the current host to a provider with a built-in
scheduler (Render Jobs, Fly Machines + cron, Hetzner Cloud + systemd timer),
the recipe is the same: one HTTP POST per day with the admin bearer token.

Example systemd timer (for any Linux box):

`/etc/systemd/system/renewal-reminders.service`:
```ini
[Service]
Type=oneshot
EnvironmentFile=/etc/aiclonechats/scheduler.env
ExecStart=/usr/bin/curl -fsS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" https://aiclonechats.com/api/admin/billing/run-renewal-reminders
```

`/etc/systemd/system/renewal-reminders.timer`:
```ini
[Unit]
Description=Daily renewal reminders POST

[Timer]
OnCalendar=*-*-* 09:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`sudo systemctl enable --now renewal-reminders.timer`.

---

## Verifying it works

1. Open https://aiclonechats.com/admin/renewal-reminders
2. Scroll to "Recent runs" — you should see a row added daily.
3. The "Sent today" tile should match the response payload's `sent` field.
4. If `failures > 0`, expand the row to see `failure_samples` (user_id +
   recipient domain only — no PII leak).

## Rollback

If the scheduler misbehaves or the admin token is leaked:
1. Rotate the admin password (forces a new session token; old one stops working).
2. Set `SCHEDULER_DISABLED=1` in `backend/.env` (future enhancement — not
   yet wired; for now just delete the cron / Cloudflare Worker).

The platform's user-facing behaviour is **unchanged** if the scheduler is
disabled — the on-startup hook still fires once per pod boot. The scheduler
is purely a reliability layer.
