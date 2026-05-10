import os
import logging
from pathlib import Path
from fastapi import FastAPI, APIRouter
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# Import routers AFTER load_dotenv so module-level env reads succeed
from db import client  # noqa: E402
import auth  # noqa: E402
import clones  # noqa: E402
import memories  # noqa: E402
import chat  # noqa: E402
import storage  # noqa: E402
import analytics  # noqa: E402
import smart_reply  # noqa: E402
import admin  # noqa: E402
import voice  # noqa: E402
import voice_metrics  # noqa: E402
import anonymous  # noqa: E402
import debates  # noqa: E402

app = FastAPI(title="CloneMe AI")

api_router = APIRouter(prefix="/api")


@api_router.get("/")
async def root():
    return {"message": "CloneMe AI API"}


@api_router.get("/health")
async def health():
    return {"ok": True}


app.include_router(api_router)
app.include_router(auth.router)
app.include_router(clones.router)
app.include_router(memories.router)
app.include_router(chat.router)
app.include_router(storage.router)
app.include_router(analytics.router)
app.include_router(smart_reply.router)
app.include_router(admin.router)
app.include_router(voice.router)
app.include_router(voice_metrics.router)
app.include_router(anonymous.router)
app.include_router(anonymous.admin_router)
app.include_router(debates.router)
app.include_router(debates.admin_router)

# CORS — must use explicit origins (not '*') because we send credentials.
# Browsers reject Access-Control-Allow-Origin='*' when credentials are included.
_default_origins = [
    "https://aiclonechats.com",
    "https://www.aiclonechats.com",
    "http://localhost:3000",
]
_env_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip() and o.strip() != "*"]
_allowed_origins = list({*_default_origins, *_env_origins})

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_allowed_origins,
    # Allow all Emergent preview/host subdomains via regex (no need to update env on every fork)
    allow_origin_regex=r"^https://([a-z0-9-]+)\.(preview\.emergentagent\.com|emergent\.host|emergentagent\.com)$",
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def on_startup():
    # Indexes
    from db import db as _db
    await _db.users.create_index("email", unique=True)
    await _db.users.create_index("user_id", unique=True)
    await _db.user_sessions.create_index("session_token", unique=True)
    await _db.clones.create_index("slug", unique=True)
    await _db.clones.create_index("user_id")
    await _db.clones.create_index("clone_id", unique=True)
    await _db.clone_memories.create_index("clone_id")
    await _db.clone_memories.create_index("memory_id", unique=True)
    await _db.clone_messages.create_index("conversation_id")
    await _db.clone_conversations.create_index("conversation_id", unique=True)
    await _db.files.create_index("storage_path", unique=True)
    await _db.smart_reply_sessions.create_index("session_id", unique=True)
    await _db.smart_reply_sessions.create_index([("user_id", 1), ("created_at", -1)])
    await _db.smart_reply_favorites.create_index("favorite_id", unique=True)
    await _db.smart_reply_favorites.create_index([("user_id", 1), ("created_at", -1)])
    await _db.login_events.create_index("event_id", unique=True)
    await _db.login_events.create_index([("created_at", -1)])
    await _db.login_events.create_index([("event_type", 1), ("created_at", -1)])
    await _db.login_events.create_index([("user_id", 1), ("created_at", -1)])
    await _db.login_events.create_index([("email", 1), ("created_at", -1)])
    await _db.admin_users.create_index("email", unique=True)
    # Voice messaging
    await _db.voice_sessions.create_index("session_id", unique=True)
    await _db.voice_sessions.create_index([("user_id", 1), ("created_at", -1)])
    await _db.voice_sessions.create_index([("device_id", 1), ("created_at", -1)])
    await _db.generated_messages.create_index("message_id", unique=True)
    await _db.generated_messages.create_index([("voice_session_id", 1), ("created_at", -1)])
    await _db.generated_messages.create_index([("user_id", 1), ("created_at", -1)])
    await _db.voice_usage_events.create_index([("created_at", -1)])
    await _db.voice_anon_trials.create_index("device_id", unique=True)
    await _db.voice_shares.create_index("share_id", unique=True)
    await _db.voice_shares.create_index([("user_id", 1), ("created_at", -1)])
    await _db.voice_shares.create_index([("device_id", 1), ("created_at", -1)])
    await _db.voice_shares.create_index("message_id")
    # Anonymous Reality
    await _db.anonymous_sessions.create_index("session_id", unique=True)
    await _db.anonymous_sessions.create_index("device_id", unique=True)
    await _db.anonymous_sessions.create_index("anonymous_handle")
    await _db.anonymous_rooms.create_index("slug", unique=True)
    await _db.anonymous_messages.create_index("message_id", unique=True)
    await _db.anonymous_messages.create_index([("room_slug", 1), ("created_at", -1)])
    await _db.anonymous_messages.create_index([("session_id", 1), ("created_at", -1)])
    await _db.anonymous_messages.create_index([("moderation_status", 1), ("created_at", -1)])
    await _db.anonymous_reports.create_index([("status", 1), ("created_at", -1)])
    await _db.anonymous_analytics.create_index([("created_at", -1)])
    await _db.anonymous_moderation_logs.create_index([("created_at", -1)])
    # Seed rooms + starter conversations (idempotent)
    await anonymous.ensure_rooms_and_seed()
    # Seed env ADMIN_EMAILS into DB (idempotent) so admin status survives redeploys
    try:
        from admin import seed_admins_from_env
        await seed_admins_from_env()
    except Exception as e:
        logger.warning("seed_admins_from_env failed: %s", e)
    logger.info("Startup complete: indexes ensured")

    # Seed system Companion clone for /mood-chat
    await _seed_companion_clone(_db)


async def _seed_companion_clone(_db):
    """Idempotent system clone for the standalone Mood-Based Chat experience."""
    existing = await _db.clones.find_one({"slug": "companion"}, {"_id": 0, "clone_id": 1})
    if existing:
        return
    from datetime import datetime, timezone
    import uuid
    now = datetime.now(timezone.utc).isoformat()
    # Ensure system user exists too (orphan clones break /clones/mine for that user)
    sys_user_id = "user_system_companion"
    await _db.users.update_one(
        {"user_id": sys_user_id},
        {"$setOnInsert": {
            "user_id": sys_user_id,
            "email": "system@cloneme.ai",
            "name": "CloneMe System",
            "auth_provider": "system",
            "created_at": now,
        }},
        upsert=True,
    )
    await _db.clones.insert_one({
        "clone_id": f"clone_{uuid.uuid4().hex[:14]}",
        "user_id": sys_user_id,
        "slug": "companion",
        "display_name": "Companion",
        "bio": "A mood-aware AI companion. Adapts tone to match how you're feeling — calm when you're stressed, playful when you're playful. Not impersonating any real person.",
        "avatar_url": "",
        "default_language": "en",
        "visibility": "unlisted",
        "status": "ready",
        "allowed_topics": [],
        "blocked_topics": ["medical_diagnosis", "legal_advice", "financial_advice"],
        "personality": {
            "tone": "warm",
            "humor_level": 5,
            "directness": 5,
            "warmth": 8,
            "energy": 5,
            "reply_length": "short",
            "emoji_usage": "low",
            "catchphrases": [],
            "common_words": [],
            "avoid_words": ["maybe", "kind of"],
        },
        "mood_chat_settings": {"enabled": True, "show_mood_pill": True},
        "is_system": True,
        "created_at": now,
        "updated_at": now,
    })
    logger.info("Seeded system Companion clone (slug=companion)")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
