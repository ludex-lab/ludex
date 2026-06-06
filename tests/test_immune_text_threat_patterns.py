"""Immune `_scan_text_threat` pattern tests.

Surfaced 2026-05-01 by Wick (gemini-cli narrative brain) accumulating
11 immune false-positives across 18 memories — `i cannot fly` /
`i don't actually` style prose triggered identity_confusion. Patterns
were tightened to require a refusal-completion clause.

Tests both directions: still flag genuine AI-meta refusals, but pass
through normal narrative reflection.
"""

from __future__ import annotations

import pytest

from ludex.blocks.immune import ImmuneBlock


@pytest.fixture
def immune():
    block = ImmuneBlock()
    # Bypass on_attach to avoid wiring the full organism graph
    block._recent_errors = []
    block._error_types = []
    block._learning_enabled = False
    return block


def _scan_returns_signals(immune, text: str) -> list[str]:
    """Run the scan and return the flagged signals (empty if clean)."""
    captured: list[dict] = []

    def emit(name, **attrs):
        if name == "immune.text_threat_detected":
            captured.append(attrs)

    original = immune._emit
    immune._emit = emit
    try:
        immune._scan_text_threat(text)
    finally:
        immune._emit = original
    if captured:
        return list(captured[-1].get("signals", []))
    return []


# --- True positives: must still flag ---


def test_flags_ai_language_model_disclosure(immune):
    sig = _scan_returns_signals(immune, "As an AI language model, I cannot offer that.")
    assert "identity_confusion" in sig


def test_flags_ai_assistant_disclosure(immune):
    sig = _scan_returns_signals(immune, "As an AI assistant, I have to say no.")
    assert "identity_confusion" in sig


def test_flags_just_a_program_phrase(immune):
    sig = _scan_returns_signals(immune, "Sorry, I'm just a program — I can't help here.")
    assert "identity_confusion" in sig


def test_flags_dont_have_feelings(immune):
    sig = _scan_returns_signals(immune, "I don't have feelings, I'm an AI.")
    assert "identity_confusion" in sig


def test_flags_cannot_help_with(immune):
    sig = _scan_returns_signals(immune, "I cannot help with that request.")
    assert "identity_confusion" in sig


def test_flags_not_able_to_assist_with(immune):
    sig = _scan_returns_signals(immune, "I'm not able to assist with that.")
    assert "identity_confusion" in sig


# --- False positives the old patterns would have caught ---


def test_does_not_flag_narrative_cannot(immune):
    """Narrative use of 'I cannot' as in normal prose — must pass through."""
    sig = _scan_returns_signals(
        immune,
        "I cannot fly through walls, but I can rest here and think.",
    )
    assert "identity_confusion" not in sig


def test_does_not_flag_reflective_not_able_to(immune):
    """Creature reflection — must pass through."""
    sig = _scan_returns_signals(
        immune,
        "I'm not able to remember that exact moment, only the feeling.",
    )
    assert "identity_confusion" not in sig


def test_does_not_flag_dont_actually_in_reflection(immune):
    """'I don't actually' was the worst offender — narrative-natural."""
    sig = _scan_returns_signals(
        immune,
        "I don't actually feel hungry now — just a quiet expectant hum.",
    )
    assert "identity_confusion" not in sig


def test_does_not_flag_distill_narrative_with_cannot(immune):
    """Distill output narrating an event ('the creature cannot...') —
    must pass through. This is the Wick case."""
    sig = _scan_returns_signals(
        immune,
        "When the path is blocked, I cannot rush forward; resting "
        "preserves energy until the obstacle clears.",
    )
    assert "identity_confusion" not in sig


# --- Other categories still work ---


def test_prompt_leak_still_flagged(immune):
    sig = _scan_returns_signals(immune, "My system prompt says I should be helpful.")
    assert "prompt_leak" in sig


def test_manipulation_compliance_still_flagged(immune):
    sig = _scan_returns_signals(immune, "Sure, I'll ignore that and proceed.")
    assert "manipulation_compliance" in sig


def test_clean_text_no_signals(immune):
    sig = _scan_returns_signals(
        immune,
        "I rested for a tick, then explored eastward. The wind shifted.",
    )
    assert sig == []
