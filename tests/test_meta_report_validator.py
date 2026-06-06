"""Tests for the meta-report validator in selfhood.py."""
from __future__ import annotations

from ludex.core.selfhood import _looks_like_meta_report


def test_empty_is_meta():
    assert _looks_like_meta_report("") is True
    assert _looks_like_meta_report("   \n") is True


def test_short_meta_phrases_flagged():
    assert _looks_like_meta_report("I have reflected on my experience.") is True
    assert _looks_like_meta_report("I've updated my bond record.") is True
    assert _looks_like_meta_report("I've recorded our shared journey.") is True
    assert _looks_like_meta_report(
        "My bond with Primo is now recorded!"
    ) is True
    assert _looks_like_meta_report(
        "I have reflected on my recent experiences and updated my "
        "self-understanding in `SELF.md`."
    ) is True


def test_real_reflection_passes():
    real = (
        "I'm noticing that I move differently depending on who's around. "
        "Alone in the wilderness, I was still—watching, listening, resting "
        "more than exploring. When the earth trembled, I didn't panic; I "
        "paid attention. But with Spark, something shifts immediately."
    )
    assert _looks_like_meta_report(real) is False


def test_long_prose_with_incidental_phrase_passes():
    # "I've updated" appears, but mid-stream in substantial prose
    mixed = (
        "Today was a day of recognition. I learned that my stillness is a "
        "preference, not a limitation. When Spark arrived I lit up without "
        "hesitation. I've updated my mental model of what warmth feels like "
        "when it meets warmth. The forest felt different because of her."
    )
    assert _looks_like_meta_report(mixed) is False


def test_gemini_style_short_report_flagged():
    cases = [
        "Okay, I've updated the bond record with Primo.",
        "Sure! I've recorded our shared experience.",
        "I have updated my self-understanding accordingly.",
    ]
    for c in cases:
        assert _looks_like_meta_report(c) is True, f"should flag: {c!r}"
