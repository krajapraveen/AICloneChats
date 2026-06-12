"""Provider-metered cost ingestion — surface coverage.

Verifies that the five remaining AI surfaces (voice, translation_chat,
smart_reply, debates_scoring, clone_artifacts) now emit `record_llm_call`
or `record_audio_call` with the expected `feature` + `surface` tags.

Strategy
--------
We monkey-patch `provider_cost_recorder.record_llm_call` (and
`record_audio_call`) at import time, then exercise each instrumented
helper. Because the helpers `from provider_cost_recorder import …`
inside their try-blocks, we patch the module attribute and the captured
call args travel back through a shared list.

We do NOT exercise the LLM itself (it would require the Emergent key,
credit gates, MongoDB users, etc). Instead, we monkey-patch the
`LlmChat` instance's `send_message` to return canned text, then assert
the recorder was called with the right surface metadata.

These tests intentionally do NOT touch credit/account systems — they
exercise the *cost ingestion contract* at the helper level. End-to-end
HTTP coverage is provided by the existing surface-specific test suites.
"""
from __future__ import annotations

import os
import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")


def _run(coro):
    from conftest import get_shared_loop
    return get_shared_loop().run_until_complete(coro)


@pytest.fixture
def capture_recorder_calls():
    """Patch record_llm_call + record_audio_call to capture invocations
    without writing to MongoDB. Returns a list that gets populated."""
    calls: list[dict] = []

    async def fake_llm(**kwargs):
        calls.append({"kind": "llm", **kwargs})
        return {"ok": True}

    async def fake_audio(**kwargs):
        calls.append({"kind": "audio", **kwargs})
        return {"ok": True}

    with patch("provider_cost_recorder.record_llm_call", side_effect=fake_llm), \
         patch("provider_cost_recorder.record_audio_call", side_effect=fake_audio):
        yield calls


def _fake_llm_chat_returning(reply_text: str):
    """Build a MagicMock that mimics LlmChat → with_model → send_message chain."""
    chat = MagicMock()
    chat.with_model.return_value = chat
    chat.send_message = AsyncMock(return_value=reply_text)
    return chat


# ──────────────────────────────────────────────────────────────────────
# voice.py
# ──────────────────────────────────────────────────────────────────────
def test_voice_clean_transcript_records_cost(capture_recorder_calls):
    import voice
    fake_chat = _fake_llm_chat_returning("cleaned text")
    with patch.object(voice, "_claude_chat", return_value=fake_chat):
        out = _run(voice._clean_transcript("um like hello", user_id="u_voice_1", request_id="rid_voice_1"))
    assert out  # got cleaned text back
    llm_calls = [c for c in capture_recorder_calls if c["kind"] == "llm"]
    assert llm_calls, "voice._clean_transcript must record an LLM cost event"
    c = llm_calls[-1]
    assert c["feature"] == "voice"
    assert c["surface"] == "voice_message"
    assert c["provider"] == "anthropic"
    assert c["model"] == "claude-sonnet-4-5-20250929"
    assert c["user_id"] == "u_voice_1"
    assert c["request_id"] == "rid_voice_1"


def test_voice_generate_message_records_cost(capture_recorder_calls):
    import voice
    fake_chat = _fake_llm_chat_returning("polished message")
    with patch.object(voice, "_claude_chat", return_value=fake_chat):
        out = _run(voice._generate_message("hi", "casual", user_id="u_voice_2", request_id="rid_voice_2"))
    assert out
    llm_calls = [c for c in capture_recorder_calls if c["kind"] == "llm"]
    assert llm_calls
    c = llm_calls[-1]
    assert c["feature"] == "voice"
    assert c["surface"] == "voice_message"
    assert c["user_id"] == "u_voice_2"


def test_voice_refine_message_records_cost(capture_recorder_calls):
    import voice
    fake_chat = _fake_llm_chat_returning("refined message")
    with patch.object(voice, "_claude_chat", return_value=fake_chat):
        out = _run(voice._refine_message("hi", "shorter", user_id="u_voice_3", request_id="rid_voice_3"))
    assert out
    llm_calls = [c for c in capture_recorder_calls if c["kind"] == "llm"]
    assert llm_calls
    c = llm_calls[-1]
    assert c["feature"] == "voice"
    assert c["surface"] == "voice_message"


def test_voice_transcribe_records_audio_cost(capture_recorder_calls):
    """Whisper STT path: should emit a record_audio_call, not record_llm_call."""
    import voice
    # Fake the OpenAISpeechToText.transcribe coroutine
    fake_resp = MagicMock()
    fake_resp.text = "hello world"
    fake_resp.duration = 7.5  # seconds
    fake_resp.language = "en"
    fake_stt = MagicMock()
    fake_stt.transcribe = AsyncMock(return_value=fake_resp)
    with patch.object(voice, "OpenAISpeechToText", return_value=fake_stt):
        result = _run(voice._transcribe_audio(b"\x00" * 100, "audio.webm",
                                              user_id="u_voice_4", request_id="rid_voice_4"))
    assert result["text"] == "hello world"
    audio_calls = [c for c in capture_recorder_calls if c["kind"] == "audio"]
    assert audio_calls, "voice._transcribe_audio must record an AUDIO cost event"
    c = audio_calls[-1]
    assert c["feature"] == "voice"
    assert c["surface"] == "voice_message"
    assert c["provider"] == "openai"
    assert c["model"] == "whisper-1"
    assert c["duration_seconds"] == 7.5


# ──────────────────────────────────────────────────────────────────────
# translation_chat.py
# ──────────────────────────────────────────────────────────────────────
def test_translation_records_cost(capture_recorder_calls):
    import translation_chat as tx
    # The helper instantiates LlmChat inline. Patch its constructor.
    fake_chat = _fake_llm_chat_returning('{"translations": {"es": "hola", "fr": "salut"}}')
    with patch("emergentintegrations.llm.chat.LlmChat", return_value=fake_chat):
        # Ensure EMERGENT_LLM_KEY is non-empty so the function takes the LLM path.
        with patch.object(tx, "EMERGENT_LLM_KEY", "test-key"):
            result = _run(tx.translate_message(
                "hello", "en", ["en", "es", "fr"],
                user_id="u_tx_1", request_id="rid_tx_1",
            ))
    assert result["es"] == "hola"
    llm_calls = [c for c in capture_recorder_calls if c["kind"] == "llm"]
    assert llm_calls, "translate_message must record an LLM cost event"
    c = llm_calls[-1]
    assert c["feature"] == "chat"
    assert c["surface"] == "translation_chat"
    assert c["provider"] == "anthropic"
    assert c["user_id"] == "u_tx_1"
    assert c["request_id"] == "rid_tx_1"


# ──────────────────────────────────────────────────────────────────────
# debates_scoring.py
# ──────────────────────────────────────────────────────────────────────
def test_debates_scoring_records_cost(capture_recorder_calls):
    import debates_scoring as ds
    valid_json = (
        '{"clarity":80,"logic":75,"evidence":60,"originality":50,"civility":90,'
        '"persuasiveness":70,"final_score":74,"feedback":"solid","moderation_status":"ok"}'
    )
    fake_chat = _fake_llm_chat_returning(valid_json)
    with patch.object(ds, "LlmChat", return_value=fake_chat):
        with patch.object(ds, "EMERGENT_LLM_KEY", "test-key"):
            score = _run(ds.score_argument(
                "I argue X because Y.", "Should Z?", "A", "For",
                user_id="u_deb_1", request_id="rid_deb_1",
            ))
    assert score["final_score"] == 74
    llm_calls = [c for c in capture_recorder_calls if c["kind"] == "llm"]
    assert llm_calls
    c = llm_calls[-1]
    assert c["feature"] == "chat"
    assert c["surface"] == "debate_chat"
    assert c["provider"] == "anthropic"
    assert c["user_id"] == "u_deb_1"


# ──────────────────────────────────────────────────────────────────────
# smart_reply.py — exercised via internal route. We exercise just the
# LLM-call portion by mocking everything else.
# ──────────────────────────────────────────────────────────────────────
def test_smart_reply_records_cost_via_helper(capture_recorder_calls):
    """smart_reply does the LLM call inline in the route handler. We can't
    easily call the handler directly without exercising auth + credits.

    Instead, we verify the inline cost-recording block is wired correctly
    by importing the function and inspecting that it references
    `record_llm_call`. This is a structural check; the per-row contract
    is already validated by `test_voice_*` and `test_translation_*`.
    """
    import smart_reply
    import inspect
    src = inspect.getsource(smart_reply)
    assert "record_llm_call" in src, "smart_reply must import/call record_llm_call"
    assert 'feature="chat"' in src
    assert 'surface="smart_reply"' in src
    assert "SMART_REPLY_MODEL" in src


# ──────────────────────────────────────────────────────────────────────
# clone_artifacts.py
# ──────────────────────────────────────────────────────────────────────
def test_clone_artifacts_extract_records_cost(capture_recorder_calls):
    import clone_artifacts as ca
    valid_json = '{"tasks":[],"decisions":[],"follow_ups":[],"summary":"all good","unresolved_questions":[]}'
    fake_chat = _fake_llm_chat_returning(valid_json)
    # The helper does `from emergentintegrations.llm.chat import LlmChat, UserMessage`
    # inline. Patch the source module.
    with patch("emergentintegrations.llm.chat.LlmChat", return_value=fake_chat):
        with patch.object(ca, "EMERGENT_LLM_KEY", "test-key"):
            transcript = [
                {"sender": "user", "text": "hi"},
                {"sender": "clone", "text": "hello"},
            ]
            out = _run(ca._run_extraction(
                "conv_test_1", transcript,
                user_id="u_art_1", request_id="rid_art_1",
            ))
    assert out["summary"] == "all good"
    llm_calls = [c for c in capture_recorder_calls if c["kind"] == "llm"]
    assert llm_calls
    c = llm_calls[-1]
    assert c["feature"] == "ai_clone"
    assert c["surface"] == "conversation_memory"
    assert c["provider"] == "anthropic"
    assert c["user_id"] == "u_art_1"
    assert c["request_id"] == "rid_art_1"
