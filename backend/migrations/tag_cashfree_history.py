"""
One-shot migration: tag legacy payment_orders + webhook_logs with provider='cashfree'.

Runs idempotently — only stamps documents that lack the `provider` field.
Preserves every historical record; nothing is deleted.

Run: `cd /app/backend && python3 -m migrations.tag_cashfree_history`
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Allow running as a script: `python3 migrations/tag_cashfree_history.py`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from db import db  # noqa: E402
from models import now_iso  # noqa: E402


async def main() -> dict:
    summary = {}
    # Stamp every existing payment_orders doc that has no provider yet
    res = await db.payment_orders.update_many(
        {"provider": {"$exists": False}},
        {"$set": {"provider": "cashfree", "provider_tagged_at": now_iso()}},
    )
    summary["payment_orders_tagged"] = res.modified_count

    # Stamp every existing webhook_logs doc that has no provider yet
    res2 = await db.webhook_logs.update_many(
        {"provider": {"$exists": False}},
        {"$set": {"provider": "cashfree", "provider_tagged_at": now_iso()}},
    )
    summary["webhook_logs_tagged"] = res2.modified_count
    return summary


if __name__ == "__main__":
    result = asyncio.run(main())
    print("Cashfree history tagging complete:")
    for k, v in result.items():
        print(f"  {k}: {v}")
