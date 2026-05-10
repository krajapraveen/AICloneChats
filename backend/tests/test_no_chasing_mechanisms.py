"""
Constitutional CI test.

Asserts that thesis-aligned modules (delayed_messages, clone_artifacts) contain
NO chasing mechanisms — no reminders, no notifications, no nudges, no streaks,
no reactivation, no win-back, no engagement-triggered re-entry.

If this test ever fails, the discussion happens BEFORE the code ships, not after.
That is the drift protection encoded as architecture.

The test scans:
  - the modules' source files
  - the modules' frontend pages
  - the modules' admin endpoints

It does NOT scan:
  - this test file itself (intentional mention of forbidden terms)
  - the safety_filter (which legitimately uses "report" for moderation)
  - voice/avatar/translation modules (different theses, different rules)
"""
from __future__ import annotations
import os
import re
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files that MUST not contain chasing terms
GUARDED_FILES = [
    "delayed_messages.py",
    "clone_artifacts.py",
]

GUARDED_FRONTEND = [
    "../frontend/src/pages/DelayedChat.jsx",
    "../frontend/src/pages/AdminDelayedMessages.jsx",
    "../frontend/src/components/ConversationArtifactsPanel.jsx",
    "../frontend/src/pages/ConversationMemory.jsx",
]

# Forbidden terms — anything that manufactures user return rather than trusting it.
# Matched as whole words, case-insensitive, with light morphology tolerance.
FORBIDDEN = [
    r"\breminder(s|ed|ing)?\b",
    r"\bremind\b",
    r"\bnudge(s|d|ing)?\b",
    r"\bnotify\b",
    r"\bnotification(s)?\b",
    r"\breactivation\b",
    r"\bwinback\b",
    r"\bwin[-_ ]back\b",
    r"\bdigest\b",
    r"\bstreak(s)?\b",
    r"\bengagement[_ ]?email(s)?\b",
    r"\bpush[_ ]?notification(s)?\b",
    r"\bstale[_ ]?room[_ ]?digest\b",
    r"\bdon't[ ]forget\b",
    r"\bdo not forget\b",
    r"\bcome[ ]back\b",
    r"\bstay[ ]active\b",
    r"\bkeep your streak\b",
]

COMPILED = [re.compile(pat, re.IGNORECASE) for pat in FORBIDDEN]


def _scan_file(path: str) -> list[tuple[str, int, str]]:
    """Returns list of (term, line_no, line_text) violations."""
    if not os.path.exists(path):
        return []
    violations: list[tuple[str, int, str]] = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            stripped = line.strip()
            # Allow lines that explicitly call out the philosophy (e.g.,
            # "no reminders, no notifications, no scheduler"). We detect
            # these by looking for "no " or "NOT " or "do NOT" or "no not"
            # immediately preceding the term within the line.
            if any(marker in stripped.lower() for marker in [
                "no reminder", "no notification", "no nudge", "no digest",
                "no streak", "no reactivation", "no winback", "no win-back",
                "no chasing", "no scheduler watches", "no notifications",
                "do not invent", "do not add nudges", "no schedule",
                "scheduling, no reminders",
                "thesis: ",  # documentation lines
            ]):
                continue
            for term_re, term_pat in zip(COMPILED, FORBIDDEN):
                if term_re.search(line):
                    violations.append((term_pat, line_no, stripped))
                    break
    return violations


def test_delayed_messages_no_chasing_mechanisms():
    target = os.path.join(REPO, "delayed_messages.py")
    violations = _scan_file(target)
    assert not violations, f"delayed_messages.py contains chasing terms: {violations}"


def test_clone_artifacts_no_chasing_mechanisms():
    target = os.path.join(REPO, "clone_artifacts.py")
    violations = _scan_file(target)
    assert not violations, f"clone_artifacts.py contains chasing terms: {violations}"


def test_frontend_thesis_pages_no_chasing_copy():
    for rel in GUARDED_FRONTEND:
        target = os.path.join(REPO, rel)
        violations = _scan_file(target)
        assert not violations, f"{rel} contains chasing terms: {violations}"


def test_no_reminder_routes_in_delayed_or_artifacts():
    """Sanity check at the route level — no /reminders, /notify, /digest endpoints
    have crept in to the thesis-aligned modules."""
    for fname in GUARDED_FILES:
        target = os.path.join(REPO, fname)
        if not os.path.exists(target):
            continue
        with open(target, encoding="utf-8") as f:
            content = f.read()
        for forbidden_route in [
            '"/reminders"', "'/reminders'",
            '"/notify"', "'/notify'",
            '"/digest"', "'/digest'",
            '"/dispatch"', "'/dispatch'",
            '"/winback"', "'/winback'",
            '"/streak"', "'/streak'",
        ]:
            assert forbidden_route not in content, f"{fname} declares forbidden route {forbidden_route}"


def test_constitution_includes_thesis_aligned_files_only():
    """Meta-test: ensures the guarded list itself doesn't quietly shrink over time.
    If anyone removes a thesis-aligned file from this guard list, this test fails."""
    expected_min = {"delayed_messages.py", "clone_artifacts.py"}
    assert expected_min.issubset(set(GUARDED_FILES)), (
        "Constitutional guard list has shrunk. If you intentionally removed a "
        "thesis-aligned module, that requires explicit founder approval and a "
        "documented rationale in PRD.md."
    )
