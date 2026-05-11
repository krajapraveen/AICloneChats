from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal
from datetime import datetime, timezone
import uuid


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


# ---------- USERS ----------
class User(BaseModel):
    user_id: str
    email: str
    name: str = ""
    picture: str = ""
    auth_provider: Literal["email", "google"] = "email"
    created_at: str


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)
    name: str = ""


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class GoogleSessionRequest(BaseModel):
    session_id: str


# REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
class GoogleCallbackRequest(BaseModel):
    code: str
    redirect_uri: str


# ---------- CLONES ----------
PERSONALITY_DEFAULT = {
    "tone": "direct",
    "humor_level": 5,
    "directness": 6,
    "warmth": 6,
    "energy": 6,
    "reply_length": "short",  # short | medium | detailed
    "emoji_usage": "low",  # none | low | medium | high
    "catchphrases": [],
    "common_words": [],
    "avoid_words": [],
}


class CloneCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    bio: str = Field(default="", max_length=400)
    avatar_url: str = ""
    default_language: str = "en"
    visibility: Literal["public", "private", "unlisted"] = "public"
    allowed_topics: List[str] = []
    blocked_topics: List[str] = []
    personality: dict = Field(default_factory=lambda: dict(PERSONALITY_DEFAULT))


class CloneUpdate(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    visibility: Optional[Literal["public", "private", "unlisted"]] = None
    status: Optional[Literal["draft", "ready", "paused"]] = None
    allowed_topics: Optional[List[str]] = None
    blocked_topics: Optional[List[str]] = None
    personality: Optional[dict] = None


class Clone(BaseModel):
    clone_id: str
    user_id: str
    slug: str
    display_name: str
    bio: str = ""
    avatar_url: str = ""
    default_language: str = "en"
    visibility: str = "public"
    status: str = "ready"
    allowed_topics: List[str] = []
    blocked_topics: List[str] = []
    personality: dict
    created_at: str
    updated_at: str


# ---------- MEMORIES ----------
class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    memory_type: Literal["profile", "factual", "preference", "relationship", "style"] = "factual"
    importance: float = Field(default=0.7, ge=0, le=1)
    visibility: Literal["public", "private", "owner_only"] = "public"
    can_use_for_reply: bool = True


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    memory_type: Optional[str] = None
    importance: Optional[float] = None
    visibility: Optional[str] = None
    can_use_for_reply: Optional[bool] = None


class Memory(BaseModel):
    memory_id: str
    clone_id: str
    user_id: str
    content: str
    memory_type: str
    importance: float
    visibility: str
    can_use_for_reply: bool
    created_at: str


# ---------- CHAT ----------
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    visitor_id: Optional[str] = None
    visitor_name: Optional[str] = None
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    used_memories: List[str] = []


class Message(BaseModel):
    message_id: str
    conversation_id: str
    clone_id: str
    sender: str  # visitor | clone
    text: str
    created_at: str
