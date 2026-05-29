"""
One-shot migration: tag any payment_orders + webhook_logs with provider='easebuzz'
as legacy. Symmetric to tag_cashfree_history.py.

Easebuzz integration was added 2026-05-12 and removed 2026-05-12 before any
live credentials were activated. This migration is defensive — runs idempotently
and only stamps documents that exist (test data may have leaked rows during
unit-test runs against the shared database).

Preserves every historical record; nothing is deleted.

Run: `cd /app/backend && python3 -m migrations.tag_easebuzz_history`
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from db import db  # noqa: E402
from models import now_iso  # noqa: E402


async def main() -> dict:
    summary = {}
    res = await db.payment_orders.update_many(
        {"provider": "easebuzz", "legacy_tagged_at": {"$exists": False}},
        {"$set": {"legacy": True, "legacy_tagged_at": now_iso()}},
    )
    summary["payment_orders_marked_legacy"] = res.modified_count

    res2 = await db.webhook_logs.update_many(
        {"provider": "easebuzz", "legacy_tagged_at": {"$exists": False}},
        {"$set": {"legacy": True, "legacy_tagged_at": now_iso()}},
    )
    summary["webhook_logs_marked_legacy"] = res2.modified_count
    return summary


if __name__ == "__main__":
    result = asyncio.run(main())
    print("Easebuzz history tagging complete:")
    for k, v in result.items():
        print(f"  {k}: {v}")
