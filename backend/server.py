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
import safety_admin  # noqa: E402
import admin_chats  # noqa: E402
import translation_chat  # noqa: E402
import avatar_chat  # noqa: E402
import delayed_messages  # noqa: E402
import clone_artifacts  # noqa: E402
import email_verify  # noqa: E402
import payments_cashfree  # noqa: E402
import billing_api  # noqa: E402
from credits import ensure_plans_seeded  # noqa: E402

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
app.include_router(safety_admin.admin_router)
app.include_router(admin_chats.admin_router)
app.include_router(translation_chat.router)
app.include_router(translation_chat.admin_router)
app.include_router(avatar_chat.router)
app.include_router(avatar_chat.admin_router)
app.include_router(delayed_messages.router)
app.include_router(delayed_messages.admin_router)
app.include_router(clone_artifacts.router)
app.include_router(clone_artifacts.admin_router)
app.include_router(email_verify.router)
import password_reset  # noqa: E402
app.include_router(password_reset.router)
app.include_router(payments_cashfree.router)
app.include_router(billing_api.public_router)
app.include_router(billing_api.admin_router)
import analytics_revenue  # noqa: E402
app.include_router(analytics_revenue.router)
app.include_router(analytics_revenue.public_router)

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
    await _db.login_events.create_index([("ip_address_hash", 1), ("email", 1), ("event_type", 1), ("created_at", -1)])
    # Password reset
    await _db.password_reset_tokens.create_index("token_hash", unique=True)
    await _db.password_reset_tokens.create_index([("user_id", 1), ("created_at", -1)])
    # TTL on expires_at — Mongo will need a Date type for actual TTL; the string
    # field is kept for read-side validation and a manual sweep is acceptable.
    await _db.password_reset_tokens.create_index([("expires_at", 1)])
    # Auth rate limit buckets
    await _db.auth_rate_limits.create_index([("key", 1), ("created_at", -1)])
    await _db.auth_rate_limits.create_index([("created_at", 1)])
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
    # Avatar Chat indexes
    await _db.avatar_chat_messages.create_index("message_id", unique=True)
    await _db.avatar_chat_messages.create_index([("user_id", 1), ("created_at", -1)])
    await _db.avatar_chat_messages.create_index([("conversation_id", 1), ("created_at", 1)])
    await _db.avatar_chat_messages.create_index([("video_status", 1), ("created_at", -1)])
    await _db.avatar_generation_jobs.create_index("job_id", unique=True)
    await _db.avatar_generation_jobs.create_index([("status", 1), ("updated_at", -1)])
    await _db.avatar_profiles.create_index("avatar_id", unique=True)
    await _db.avatar_profiles.create_index([("user_id", 1), ("created_at", -1)])
    await _db.avatar_chat_events.create_index([("created_at", -1)])
    await _db.avatar_chat_events.create_index([("event_name", 1), ("created_at", -1)])
    # Delayed Messages indexes
    await _db.delayed_messages.create_index("delayed_message_id", unique=True)
    await _db.delayed_messages.create_index([("sender_user_id", 1), ("created_at", -1)])
    await _db.delayed_messages.create_index([("recipient_user_id", 1), ("status", 1), ("delivered_at", -1)])
    await _db.delayed_messages.create_index([("status", 1), ("delivery_time", 1)])
    await _db.delayed_messages.create_index("open_token", sparse=True)
    await _db.delayed_message_events.create_index([("created_at", -1)])
    await _db.delayed_message_events.create_index([("event_type", 1), ("created_at", -1)])
    # Clone Artifacts indexes
    await _db.clone_artifacts.create_index("artifact_id", unique=True)
    await _db.clone_artifacts.create_index([("conversation_id", 1), ("created_at", -1)])
    await _db.clone_artifacts.create_index([("owner_kind", 1), ("owner_value", 1), ("created_at", -1)])
    await _db.clone_artifact_tasks.create_index("task_id", unique=True)
    await _db.clone_artifact_tasks.create_index([("owner_kind", 1), ("owner_value", 1), ("status", 1), ("created_at", -1)])
    await _db.clone_artifact_tasks.create_index([("conversation_id", 1), ("created_at", -1)])
    await _db.clone_artifact_events.create_index([("created_at", -1)])
    await _db.clone_artifact_events.create_index([("event_name", 1), ("created_at", -1)])
    # Billing / payments / credits indexes
    await _db.subscription_plans.create_index("plan_id", unique=True)
    await _db.credit_grants.create_index("grant_id", unique=True)
    await _db.credit_grants.create_index("user_id", unique=True)
    await _db.credit_grants.create_index("email", unique=True)
    await _db.credit_grants.create_index("device_id", sparse=True)
    await _db.credit_grants.create_index([("ip_address", 1), ("created_at", -1)])
    await _db.credit_events.create_index([("user_id", 1), ("created_at", -1)])
    await _db.credit_events.create_index([("created_at", -1)])
    await _db.fraud_signals.create_index([("created_at", -1)])
    await _db.fraud_signals.create_index([("device_id", 1), ("created_at", -1)])
    await _db.fraud_signals.create_index([("ip_address", 1), ("created_at", -1)])
    await _db.fraud_cooldowns.create_index("expires_at")
    await _db.fraud_cooldowns.create_index("device_id", sparse=True)
    await _db.fraud_cooldowns.create_index("ip_address", sparse=True)
    await _db.payment_orders.create_index("order_id", unique=True)
    await _db.payment_orders.create_index([("user_id", 1), ("created_at", -1)])
    await _db.payment_orders.create_index([("status", 1), ("created_at", -1)])
    await _db.webhook_logs.create_index([("received_at", -1)])
    await _db.webhook_logs.create_index([("order_id", 1), ("received_at", -1)])
    await _db.payment_audit_log.create_index([("created_at", -1)])
    await _db.webhook_dedup.create_index("dedup_key", unique=True)
    await _db.webhook_dedup.create_index([("created_at", -1)])
    await _db.payment_refunds.create_index("refund_id", unique=True)
    await _db.payment_refunds.create_index([("order_id", 1), ("created_at", -1)])
    await _db.payment_refunds.create_index([("user_id", 1), ("created_at", -1)])
    await _db.admin_alerts.create_index([("created_at", -1)])
    await _db.admin_alerts.create_index([("resolved", 1), ("severity", -1), ("created_at", -1)])
    await _db.admin_alerts.create_index([("kind", 1), ("created_at", -1)])
    await _db.email_otp_codes.create_index([("user_id", 1), ("created_at", -1)])
    await _db.email_otp_codes.create_index("expires_at")
    # Start delayed-delivery scheduler in the background
    try:
        import asyncio as _asyncio
        _asyncio.create_task(delayed_messages._scheduler_loop())
    except Exception as e:
        logger.warning("delayed_messages scheduler failed to start: %s", e)
    # Seed env ADMIN_EMAILS into DB (idempotent) so admin status survives redeploys
    try:
        from admin import seed_admins_from_env
        await seed_admins_from_env()
    except Exception as e:
        logger.warning("seed_admins_from_env failed: %s", e)
    # Seed billing plans on every boot — plans are code, not user data
    try:
        await ensure_plans_seeded()
    except Exception as e:
        logger.warning("ensure_plans_seeded failed: %s", e)
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
