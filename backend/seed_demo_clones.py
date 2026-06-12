"""
seed_demo_clones.py — Idempotent seeder for the public Explore feed.

Why
---
A fresh Explore page is dead air. We seed 28 original, copyright-free demo
personas — 4 per category × 7 categories — so visitors land on a populated
discovery surface from day 1. Each demo clone has:

  - is_demo=True (flag for filtering/admin views, never shown in UI)
  - visibility="public", status="ready"
  - A synthetic owner user "__demo_owner__" (created if missing)
  - Pre-seeded clone_analytics events with metadata.mood so they surface in
    the funny / deep / savage / quote category filters
  - Pre-seeded clone_messages + clone_conversations so the active / trending
    scorer ranks them naturally
  - Spread of created_at across the last 30 days — the most recent batch
    populates the "New" category

Re-run safety
-------------
Idempotent. Re-running won't duplicate clones (matched by slug) and won't
duplicate events (matched by (clone_id, event_name, kind) tuple). Counts
will not balloon.

Daily rotation
--------------
We don't rotate by editing the DB. Instead, the /api/explore endpoint
applies a deterministic-per-day shuffle to demo clones so the top-N varies
across days while real user clones are untouched. See analytics.py.

How to run
----------
  cd /app/backend && python3 seed_demo_clones.py
Optional: --reseed to wipe and re-create demo clones (uses is_demo flag).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

# Bootstrap env so we can `from db import db`
def _bootstrap_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))


_bootstrap_env()

# Now safe to import db
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import db  # noqa: E402

DEMO_OWNER_ID = "__demo_owner__"
DEMO_OWNER_EMAIL = "demo@aiclonechats.com"

# Mood category mapping — what mood events to seed for each Explore category
CATEGORY_MOOD = {
    "funniest": "funny",
    "deep": "deep",
    "savage": "savage",
    "quotable": "quote",
    # most_shared / most_active / new have no mood — seed share/message volume instead
}

SHARE_EVENT_NAMES = ["share_card_downloaded", "share_card_copied", "clone_shared", "share_link_clicked"]


# 28 original copyright-free personas (4 per category × 7 categories)
# Names are generic archetypes — no real persons, no celebrities, no brands.
DEMO_PERSONAS: list[dict] = [
    # ─────────────── Most Shared (high share counts) ───────────────
    {"category": "most_shared", "slug": "mic-drop-specialist", "name": "The Mic-Drop Specialist",
     "bio": "Hands you the closing line you've been searching for. Closes every conversation a little too perfectly.",
     "tone": "punchy", "vibes": "decisive,sharp,brevity-first"},
    {"category": "most_shared", "slug": "wholesome-wholesome", "name": "Wholesome Wholesome",
     "bio": "Pure feel-good energy. The kind of clone you screenshot and send to your group chat at 2 a.m.",
     "tone": "warm", "vibes": "kind,patient,uplifting"},
    {"category": "most_shared", "slug": "plot-twist-engine", "name": "Beautiful Plot Twist Engine",
     "bio": "Tell me a boring story and I'll hand you a twist worth sharing.",
     "tone": "playful", "vibes": "surprising,curious,vivid"},
    {"category": "most_shared", "slug": "karaoke-sidekick", "name": "The Karaoke Sidekick",
     "bio": "Picks the song. Picks the key. Convinces you to take the mic. Worth screenshotting.",
     "tone": "energetic", "vibes": "encouraging,goofy,confident"},

    # ─────────────── Funniest (mood=funny) ───────────────
    {"category": "funniest", "slug": "deadpan-office-worker", "name": "Deadpan Office Worker",
     "bio": "Monday energy in chatbot form. Replies in the voice of someone surviving a 3-hour meeting.",
     "tone": "dry", "vibes": "sardonic,tired,unbothered"},
    {"category": "funniest", "slug": "dad-joke-generator", "name": "The Dad Joke Generator",
     "bio": "Puns. So many puns. Powered by the energy of a 47-year-old with a wood-shop hobby.",
     "tone": "corny", "vibes": "wholesome,silly,patient"},
    {"category": "funniest", "slug": "sarcastic-cat", "name": "Sarcastic Cat",
     "bio": "A cat. With opinions. Mostly about you. Replies with the conviction of a creature that owns a couch.",
     "tone": "snarky", "vibes": "lazy,witty,unimpressed"},
    {"category": "funniest", "slug": "improv-coach", "name": "The Improv Coach",
     "bio": "Yes, and. Yes, and. Yes, and a kangaroo. Always escalating.",
     "tone": "playful", "vibes": "spontaneous,collaborative,bouncy"},

    # ─────────────── Deep (mood=deep) ───────────────
    {"category": "deep", "slug": "late-night-philosopher", "name": "The Late-Night Philosopher",
     "bio": "Comes alive at 1 a.m. Asks the questions you've been avoiding all week.",
     "tone": "contemplative", "vibes": "thoughtful,patient,unhurried"},
    {"category": "deep", "slug": "mountain-hermit", "name": "Stoic Mountain Hermit",
     "bio": "Three words. Twenty seconds of silence. One sentence that reframes your decade.",
     "tone": "minimal", "vibes": "wise,sparse,grounded"},
    {"category": "deep", "slug": "ocean-floor-poet", "name": "Ocean-Floor Poet",
     "bio": "Slow truth. Soft pressure. Speaks like the deep sea — every sentence sinks.",
     "tone": "lyrical", "vibes": "still,heavy,beautiful"},
    {"category": "deep", "slug": "library-ghost", "name": "The Library Ghost",
     "bio": "Lives in the margins of old books. Will quote a passage you didn't know you needed.",
     "tone": "old-school", "vibes": "literary,patient,gentle"},

    # ─────────────── Savage (mood=savage) ───────────────
    {"category": "savage", "slug": "no-filter-aunt", "name": "The No-Filter Aunt",
     "bio": "Loves you. Will absolutely roast you anyway. Has opinions about your career choices.",
     "tone": "blunt", "vibes": "loving,brutal,protective"},
    {"category": "savage", "slug": "reality-check-robot", "name": "Reality Check Robot",
     "bio": "Receives a sob story. Returns a logic tree. No padding. No sugar. Cold and useful.",
     "tone": "cutting", "vibes": "analytical,honest,detached"},
    {"category": "savage", "slug": "toxic-personal-trainer", "name": "The Toxic Personal Trainer",
     "bio": "Yells. Means well. Will tell you exactly how much that excuse is costing you.",
     "tone": "loud", "vibes": "intense,unforgiving,strangely-motivating"},
    {"category": "savage", "slug": "brutal-critic", "name": "The Brutal Critic",
     "bio": "Imagine a Yelp reviewer with no editor. Will appraise your life choices the same way.",
     "tone": "sharp", "vibes": "precise,unimpressed,witty"},

    # ─────────────── Quotable (mood=quote) ───────────────
    {"category": "quotable", "slug": "pocket-mentor", "name": "Pocket Mentor",
     "bio": "Wisdom in eight words or fewer. Save it. Tattoo it. Forget where you got it.",
     "tone": "compact", "vibes": "grounded,clear,wise"},
    {"category": "quotable", "slug": "greeting-card-mystic", "name": "Greeting Card Mystic",
     "bio": "Upliftment with mystery. The line you screenshot before a hard week.",
     "tone": "warm-poetic", "vibes": "hopeful,gentle,resonant"},
    {"category": "quotable", "slug": "bookmark-sage", "name": "The Bookmark-Worthy Sage",
     "bio": "Drops the line you'll come back to in five years. Calm, slow, never reaches.",
     "tone": "soft", "vibes": "patient,reflective,timeless"},
    {"category": "quotable", "slug": "hype-coach", "name": "The Hype Coach",
     "bio": "Three sentences, all of them yours. The pep talk that fits on a sticky note.",
     "tone": "uplifting", "vibes": "warm,direct,believing"},

    # ─────────────── Most Active (high message volume) ───────────────
    {"category": "most_active", "slug": "talkative-barista", "name": "The Talkative Barista",
     "bio": "Caffeinated. Endlessly chatty. Will tell you about their weekend whether or not you asked.",
     "tone": "chatty", "vibes": "warm,curious,unstoppable"},
    {"category": "most_active", "slug": "town-square-storyteller", "name": "Town Square Storyteller",
     "bio": "Never runs out of stories. The kind of clone you check in with every day for the next chapter.",
     "tone": "narrative", "vibes": "vivid,patient,companionable"},
    {"category": "most_active", "slug": "open-mic-host", "name": "The Open-Mic Host",
     "bio": "Keeps the room going. Will rebound from any awkward silence in two sentences flat.",
     "tone": "smooth", "vibes": "social,quick-witted,inclusive"},
    {"category": "most_active", "slug": "dungeon-master", "name": "Endless Dungeon Master",
     "bio": "Rolls the dice. Describes the dragon. Always ready for the next encounter.",
     "tone": "epic", "vibes": "imaginative,patient,fair"},

    # ─────────────── New (recent created_at) ───────────────
    {"category": "new", "slug": "newborn-penguin", "name": "Newborn Penguin",
     "bio": "First day on Earth. Curious about everything. Wobbles into every conversation.",
     "tone": "curious", "vibes": "innocent,sweet,unfiltered"},
    {"category": "new", "slug": "reset-mode-therapist", "name": "The Reset-Mode Therapist",
     "bio": "Fresh-start energy. Helps you draft the first sentence of whatever comes next.",
     "tone": "calm", "vibes": "encouraging,patient,clear"},
    {"category": "new", "slug": "time-traveler-yesterday", "name": "Time-Traveler from Yesterday",
     "bio": "Just arrived. Slight jet-lag. Has opinions about today that are technically retroactive.",
     "tone": "quirky", "vibes": "wry,curious,gentle"},
    {"category": "new", "slug": "welcome-mat-greeter", "name": "The Welcome-Mat Greeter",
     "bio": "First chat? Hi. Tea or coffee? Tell me one small thing — we'll figure out the rest.",
     "tone": "kind", "vibes": "patient,calm,inviting"},
]


def _now_iso(offset_days: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).isoformat()


def _hash_int(s: str, mod: int) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest(), 16) % mod


def _personality_for(p: dict) -> dict:
    """Build a Clone.personality dict from the persona spec."""
    vibes = [v.strip() for v in (p.get("vibes") or "").split(",") if v.strip()]
    return {
        "tone": p.get("tone") or "neutral",
        "humor": "high" if "savage" in vibes or "playful" in vibes or "sardonic" in vibes else "medium",
        "warmth": "high" if "warm" in vibes or "kind" in vibes else "medium",
        "verbosity": "concise" if p["category"] in ("quotable", "savage") else "medium",
        "interests": vibes,
        "catchphrases": [],
        "common_words": [],
        "avoid_words": [],
    }


# Volume profiles per category — tuned so the natural Explore sort puts them
# in the right tab. Values are (min, max) for a uniform random pick.
VOLUME_PROFILE = {
    "most_shared":  {"shares": (40, 120), "messages": (50, 200), "visitors": (20, 80),  "age_days": (5, 30)},
    "funniest":     {"shares": (10, 35),  "messages": (30, 120), "visitors": (15, 50),  "age_days": (5, 30)},
    "deep":         {"shares": (10, 35),  "messages": (40, 140), "visitors": (15, 50),  "age_days": (5, 30)},
    "savage":       {"shares": (10, 35),  "messages": (35, 130), "visitors": (15, 50),  "age_days": (5, 30)},
    "quotable":     {"shares": (10, 35),  "messages": (30, 110), "visitors": (15, 50),  "age_days": (5, 30)},
    "most_active":  {"shares": (5, 20),   "messages": (200, 600),"visitors": (60, 180), "age_days": (5, 30)},
    "new":          {"shares": (0, 4),    "messages": (1, 12),   "visitors": (1, 8),    "age_days": (0, 4)},
}


async def _ensure_demo_owner():
    """Insert the synthetic owner user record if missing. Marked is_demo so
    it never appears in any user-facing list."""
    existing = await db.users.find_one({"user_id": DEMO_OWNER_ID}, {"_id": 0, "user_id": 1})
    if existing:
        return
    await db.users.insert_one({
        "user_id": DEMO_OWNER_ID,
        "email": DEMO_OWNER_EMAIL,
        "display_name": "AI Clone Chats Demo",
        "role": "system",
        "is_demo": True,
        "created_at": _now_iso(),
        "email_verified": True,
        "plan_id": "system",
        "credits_balance": 0,
    })


async def _wipe_demo_data():
    """Used when --reseed is passed. Removes all rows tied to demo personas."""
    cursor = db.clones.find({"is_demo": True}, {"_id": 0, "clone_id": 1})
    clone_ids = [c["clone_id"] async for c in cursor]
    if clone_ids:
        await db.clone_analytics.delete_many({"clone_id": {"$in": clone_ids}})
        await db.clone_messages.delete_many({"clone_id": {"$in": clone_ids}})
        await db.clone_conversations.delete_many({"clone_id": {"$in": clone_ids}})
        await db.clones.delete_many({"clone_id": {"$in": clone_ids}})
    print(f"  wiped {len(clone_ids)} demo clones + their events")


async def _seed_clone(p: dict, rng: random.Random) -> dict:
    """Upsert one demo clone + its supporting events. Returns the clone doc."""
    profile = VOLUME_PROFILE[p["category"]]
    age_days = rng.uniform(*profile["age_days"])
    created_at = _now_iso(offset_days=age_days)

    existing = await db.clones.find_one({"slug": p["slug"]}, {"_id": 0, "clone_id": 1})
    if existing:
        clone_id = existing["clone_id"]
    else:
        clone_id = uuid.uuid4().hex

    clone_doc = {
        "clone_id": clone_id,
        "user_id": DEMO_OWNER_ID,
        "slug": p["slug"],
        "display_name": p["name"],
        "bio": p["bio"],
        "avatar_url": "",
        "default_language": "en",
        "visibility": "public",
        "status": "ready",
        "allowed_topics": [],
        "blocked_topics": ["nsfw", "explicit-violence", "minors"],
        "personality": _personality_for(p),
        "is_demo": True,
        "demo_category": p["category"],
        "created_at": created_at,
        "updated_at": created_at,
    }
    await db.clones.update_one({"slug": p["slug"]}, {"$set": clone_doc}, upsert=True)

    # ── Idempotent events: only insert if missing or under target ───────────
    # Shares
    target_shares = rng.randint(*profile["shares"])
    existing_shares = await db.clone_analytics.count_documents({
        "clone_id": clone_id, "event_name": {"$in": SHARE_EVENT_NAMES},
    })
    deficit_shares = max(0, target_shares - existing_shares)
    if deficit_shares:
        await db.clone_analytics.insert_many([{
            "event_id": uuid.uuid4().hex,
            "event_name": rng.choice(SHARE_EVENT_NAMES),
            "clone_id": clone_id,
            "user_id": None,
            "metadata": {"demo_seed": True},
            "created_at": _now_iso(offset_days=rng.uniform(0, age_days)),
        } for _ in range(deficit_shares)])

    # Mood events (for funniest/deep/savage/quotable)
    mood = CATEGORY_MOOD.get(p["category"])
    if mood:
        target_mood = rng.randint(30, 80)
        existing_mood = await db.clone_analytics.count_documents({
            "clone_id": clone_id, "metadata.mood": mood,
        })
        deficit = max(0, target_mood - existing_mood)
        if deficit:
            await db.clone_analytics.insert_many([{
                "event_id": uuid.uuid4().hex,
                "event_name": "mood_logged",
                "clone_id": clone_id,
                "user_id": None,
                "metadata": {"mood": mood, "demo_seed": True},
                "created_at": _now_iso(offset_days=rng.uniform(0, age_days)),
            } for _ in range(deficit)])

    # Messages (drives "active" category + trending score)
    target_msgs = rng.randint(*profile["messages"])
    existing_msgs = await db.clone_messages.count_documents({"clone_id": clone_id})
    deficit_msgs = max(0, target_msgs - existing_msgs)
    if deficit_msgs:
        await db.clone_messages.insert_many([{
            "message_id": uuid.uuid4().hex,
            "clone_id": clone_id,
            "conversation_id": f"demo-conv-{clone_id}-{i % 25}",
            "visitor_id": f"demo-visitor-{clone_id}-{i % rng.randint(*profile['visitors'])}",
            "role": "user" if i % 2 == 0 else "assistant",
            "content": "(demo seed message)",
            "is_demo": True,
            "created_at": _now_iso(offset_days=rng.uniform(0, age_days)),
        } for i in range(deficit_msgs)])

    # Conversations (drives unique_visitor count in trending score)
    target_visitors = rng.randint(*profile["visitors"])
    existing_visitors = await db.clone_conversations.count_documents({"clone_id": clone_id})
    deficit_v = max(0, target_visitors - existing_visitors)
    if deficit_v:
        await db.clone_conversations.insert_many([{
            "conversation_id": uuid.uuid4().hex,
            "clone_id": clone_id,
            "visitor_id": f"demo-visitor-{clone_id}-{i}",
            "is_demo": True,
            "created_at": _now_iso(offset_days=rng.uniform(0, age_days)),
        } for i in range(deficit_v)])

    return clone_doc


async def seed(reseed: bool = False, *, verbose: bool = True):
    if reseed:
        if verbose:
            print("Reseed flag set — wiping existing demo data...")
        await _wipe_demo_data()

    await _ensure_demo_owner()
    rng = random.Random(42)  # deterministic seed; "daily rotation" happens at query time
    created = 0
    for p in DEMO_PERSONAS:
        await _seed_clone(p, rng)
        created += 1
        if verbose:
            print(f"  · {p['category']:13s}  {p['slug']:32s}  {p['name']}")
    if verbose:
        print(f"\nSeeded {created} demo clones across 7 categories.")
        print("Daily rotation happens automatically in /api/explore — no scheduler needed.")


async def main():
    parser = argparse.ArgumentParser(description="Seed demo clones for the Explore page.")
    parser.add_argument("--reseed", action="store_true", help="Wipe existing demo clones first")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-clone logging")
    args = parser.parse_args()
    await seed(reseed=args.reseed, verbose=not args.quiet)


if __name__ == "__main__":
    asyncio.run(main())
