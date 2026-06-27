"""Regression: `seed_demo_clones._seed_clone` must NOT reset an existing
clone's `avatar_url` on re-run. Operators backfill avatars via the
admin endpoint and that backfill must survive every subsequent server
startup-seed.

We can't easily call the seeder in-process (its motor client is bound to a
different event loop than pytest's), so we spec-pin the Mongo behaviour
directly: the update must use `$setOnInsert` (NOT `$set`) for avatar_url.
That's the precise contract this regression test enforces."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_seed_clone_uses_set_on_insert_for_avatar_url():
    """Read the source of `_seed_clone` and assert the upsert never carries
    avatar_url inside a top-level `$set` (which would reset it on every run).
    Instead, avatar_url must live in `$setOnInsert` so it only writes once."""
    import inspect
    from seed_demo_clones import _seed_clone

    src = inspect.getsource(_seed_clone)
    # Whitespace-normalize for robust matching against future formatting nits.
    normalized = " ".join(src.split())

    # Required: $setOnInsert with avatar_url
    assert "$setOnInsert" in normalized, (
        "_seed_clone must use $setOnInsert for write-once fields. "
        "Operator backfills via /api/admin/avatars/backfill-clones will be "
        "reset on every startup if avatar_url sits inside top-level $set."
    )

    # Required: the clone_doc dict (which IS overwritten every run via $set)
    # must NOT contain avatar_url. We check the function body for
    # `"avatar_url"` appearing in a $set-only context.
    # Strategy: find the `clone_doc = {` block, ensure avatar_url isn't in it.
    assert '"avatar_url": ""' not in normalized.split('"$set": clone_doc')[0], (
        "avatar_url leaked back into the clone_doc dict that's passed to $set. "
        "Move it to the $setOnInsert clause."
    )


def test_seed_clone_initialises_avatar_url_on_insert():
    """Conversely: fresh inserts must seed avatar_url to an empty string so
    the field exists and downstream code can rely on it (rather than $exists
    checks)."""
    import inspect
    from seed_demo_clones import _seed_clone

    src = inspect.getsource(_seed_clone)
    normalized = " ".join(src.split())

    # The $setOnInsert block must initialise avatar_url to "".
    assert '"avatar_url": ""' in normalized, (
        "Fresh clone inserts must initialise avatar_url='' inside $setOnInsert "
        "so the field exists on every newly-seeded row."
    )
