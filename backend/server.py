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

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
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
    logger.info("Startup complete: indexes ensured")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
