"""Voice → Message multilingual support.

Locks the contract that:
  1. The Whisper STT call no longer pins `language="en"` — it auto-detects.
  2. The /voice/text-input endpoint accepts an optional `target_language`.
  3. The LLM language directive helper picks the right instruction for:
       - "auto" / "" / "mirror"  → mirror input language
       - "en", "hi", "Spanish"…  → force that target
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voice  # noqa: E402


def test_lang_directive_auto_mirrors_input_with_detected_language():
    d = voice._lang_directive("auto", detected_language="hindi")
    assert "same language as the user" in d.lower()
    assert "hindi" in d.lower()


def test_lang_directive_auto_without_detection_still_mirrors():
    d = voice._lang_directive("", detected_language=None)
    assert "same language as the user" in d.lower()


def test_lang_directive_named_target_forces_language():
    d = voice._lang_directive("Hindi", detected_language="english")
    assert "in Hindi" in d
    assert "MUST be in Hindi" in d


def test_lang_directive_code_target_forces_language():
    """BCP-47 / ISO codes work too — the LLM understands `es` as Spanish."""
    d = voice._lang_directive("es", detected_language=None)
    assert "in es" in d
    assert "MUST be in es" in d


def test_lang_directive_sentinels_all_mean_mirror():
    for sentinel in ("", "auto", "mirror", "same", "input", "AUTO", "Mirror"):
        d = voice._lang_directive(sentinel, detected_language="french")
        assert "same language" in d.lower(), f"sentinel {sentinel!r} should mirror"


def test_transcribe_helper_no_longer_hardcodes_english():
    """Whisper auto-detection requires us NOT to pass language='en'. Read
    the source and assert the kwarg is absent."""
    src = Path("/app/backend/voice.py").read_text()
    # The line `language="en",` must not appear inside _transcribe_audio.
    # We look at the function body.
    start = src.index("async def _transcribe_audio")
    end = src.index("return {", start)
    body = src[start:end]
    assert 'language="en"' not in body, "Whisper call must not hardcode English anymore"
    assert "auto-detects" in body or "auto-detect" in body, "function should document auto-detection"


def test_text_input_request_has_target_language_field():
    """The Pydantic model accepts an optional target_language so the UI
    can ask for a specific output language even without recording."""
    m = voice.TextInputRequest(text="hello", target_language="hi")
    assert m.target_language == "hi"
    m2 = voice.TextInputRequest(text="hello")
    assert m2.target_language is None


def test_generate_and_refine_requests_accept_target_language():
    g = voice.GenerateRequest(session_id="s1", tone="concise", target_language="es")
    assert g.target_language == "es"
    r = voice.RefineRequest(message_id="m1", refine_type="shorter", target_language="fr")
    assert r.target_language == "fr"
    ga = voice.GenerateAllRequest(session_id="s2", target_language="ja")
    assert ga.target_language == "ja"
