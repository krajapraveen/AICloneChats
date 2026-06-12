"""
Backend tests for Explore page demo clone seeding (iteration 22).
Covers:
- /api/explore for 7 categories with seeded demo personas
- Idempotency of seed_demo_clones.py
- Daily rotation determinism
- Copyright safety of demo personas
"""
import os
import subprocess
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://digital-twin-119.preview.emergentagent.com").rstrip("/")

# Forbidden tokens - must NOT appear in any demo display_name / slug
FORBIDDEN_TOKENS = [
    "taylor", "elon", "beyonce", "biden", "trump", "musk",
    "disney", "marvel", "pokemon", "kardashian", "ronaldo", "messi", "apple inc",
]

EXPECTED = {
    "trending": ["mic-drop-specialist", "wholesome-wholesome", "plot-twist-engine", "karaoke-sidekick"],
    "funny": ["deadpan-office-worker", "dad-joke-generator", "sarcastic-cat", "improv-coach"],
    "deep": ["late-night-philosopher", "mountain-hermit", "ocean-floor-poet", "library-ghost"],
    "savage": ["no-filter-aunt", "reality-check-robot", "toxic-personal-trainer", "brutal-critic"],
    "quote": ["pocket-mentor", "greeting-card-mystic", "bookmark-sage", "hype-coach"],
    "active": ["talkative-barista", "town-square-storyteller", "open-mic-host", "dungeon-master"],
    "recent": ["newborn-penguin", "reset-mode-therapist", "time-traveler-yesterday", "welcome-mat-greeter"],
}


def _fetch(category, limit=30):
    r = requests.get(f"{BASE_URL}/api/explore", params={"category": category, "limit": limit}, timeout=30)
    assert r.status_code == 200, f"explore {category} status {r.status_code}: {r.text[:300]}"
    data = r.json()
    return data.get("clones") or data.get("items") or []


@pytest.mark.parametrize("category", list(EXPECTED.keys()))
def test_explore_category_has_demo_clones(category):
    items = _fetch(category)
    assert len(items) > 0, f"category {category} returned no clones (empty state should never appear)"
    demos = [i for i in items if i.get("is_demo")]
    assert len(demos) >= 4, f"category {category} expects >=4 demos, got {len(demos)}"

    slugs = {i.get("slug") for i in items}
    missing = [s for s in EXPECTED[category] if s not in slugs]
    assert not missing, f"category {category} missing expected demo slugs: {missing}. Got: {sorted(slugs)[:20]}"


def test_trending_demos_high_in_list():
    items = _fetch("trending", limit=30)
    top10_slugs = [i.get("slug") for i in items[:10]]
    matches = [s for s in EXPECTED["trending"] if s in top10_slugs]
    # at least 2 of the 4 expected trending demos should be in top 10
    assert len(matches) >= 2, f"trending demos not surfacing high; top10={top10_slugs}"


def test_active_demos_lead_by_message_count():
    items = _fetch("active", limit=30)
    top_slugs = [i.get("slug") for i in items[:8]]
    matches = [s for s in EXPECTED["active"] if s in top_slugs]
    assert len(matches) >= 3, f"active demos not on top by messages; top={top_slugs}"


def test_recent_demos_lead_by_created_at():
    items = _fetch("recent", limit=30)
    top_slugs = [i.get("slug") for i in items[:8]]
    matches = [s for s in EXPECTED["recent"] if s in top_slugs]
    assert len(matches) >= 3, f"recent demos not on top by created_at; top={top_slugs}"


def test_mood_filter_funny():
    items = _fetch("funny")
    # at least 4 demos should have funny mood signal
    demos_funny = [i for i in items if i.get("is_demo") and (
        i.get("primary_mood") == "funny"
        or (i.get("mood_counts") or {}).get("funny", 0) > 0
    )]
    assert len(demos_funny) >= 4, f"funny mood demos insufficient: {len(demos_funny)}"


# Copyright safety
def test_copyright_safety_all_categories():
    all_items = []
    for cat in EXPECTED:
        all_items.extend(_fetch(cat, limit=30))
    demos = {i.get("slug"): i for i in all_items if i.get("is_demo")}
    violations = []
    for slug, item in demos.items():
        name = (item.get("display_name") or item.get("name") or "").lower()
        slug_lc = (slug or "").lower()
        for tok in FORBIDDEN_TOKENS:
            if tok in name or tok in slug_lc:
                violations.append((slug, name, tok))
    assert not violations, f"Copyright violations found: {violations}"


# Daily rotation determinism
def test_trending_order_deterministic_same_day():
    r1 = _fetch("trending", limit=10)
    r2 = _fetch("trending", limit=10)
    slugs1 = [i.get("slug") for i in r1[:5]]
    slugs2 = [i.get("slug") for i in r2[:5]]
    assert slugs1 == slugs2, f"First 5 should be deterministic within a day. r1={slugs1} r2={slugs2}"


def test_daily_rotation_boost_helper_deterministic():
    import sys
    sys.path.insert(0, "/app/backend")
    from analytics import _daily_rotation_boost
    sample_ids = ["abc123", "demo-clone-xyz", "fake-uuid-0001"]
    for cid in sample_ids:
        v1 = _daily_rotation_boost(cid)
        v2 = _daily_rotation_boost(cid)
        assert v1 == v2, f"_daily_rotation_boost not deterministic for {cid}: {v1} vs {v2}"


# Idempotency of seeder
def test_seeder_idempotency():
    """Run seeder twice; total is_demo=true clones must remain exactly 28."""
    def _run():
        proc = subprocess.run(
            ["python3", "/app/backend/seed_demo_clones.py", "--quiet"],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, f"seeder failed: rc={proc.returncode} stderr={proc.stderr[:500]}"

    _run()
    _run()

    # Count via DB directly
    import asyncio
    import sys
    sys.path.insert(0, "/app/backend")
    from db import db as mongo_db

    async def _count():
        return await mongo_db.clones.count_documents({"is_demo": True})

    count = asyncio.get_event_loop().run_until_complete(_count())
    assert count == 28, f"Expected exactly 28 demo clones after double seed, got {count}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
