"""Shared pytest configuration — one event loop across all payment tests so
Motor's binding doesn't break when multiple test modules touch the DB."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_database")

# Module-level shared loop. Each test file's `_LOOP` should reference THIS one.
_SHARED_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_SHARED_LOOP)


def get_shared_loop():
    return _SHARED_LOOP
