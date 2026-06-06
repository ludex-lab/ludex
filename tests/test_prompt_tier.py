"""Tests for prompt tier translator (D-043 Phase A)."""
from __future__ import annotations

from ludex.core.prompt_tier import (
    Tier, tier_of,
    parse_structured, render_segments,
    translate, translate_for, build_tiered,
    RuleBasedTranslator, LLMTranslator,
    TAG_ELABORATION, TAG_CONSTRAINT_NEGATIVE, TAG_TASK, TAG_ESSENTIAL,
)


# ------------------------------------------------------------
# tier_of
# ------------------------------------------------------------

def test_tier_of_maps_known_brains():
    assert tier_of({"model": "claude-opus-4-6"}) == Tier.LARGE
    assert tier_of({"model": "claude-sonnet-4-6"}) == Tier.MID
    assert tier_of({"model": "claude-haiku-4-5"}) == Tier.MID
    assert tier_of({"model": "gemini-2.5-flash"}) == Tier.MID
    assert tier_of({"model": "gemini-3-pro-preview"}) == Tier.LARGE
    assert tier_of({"model": "gemma4:e4b"}) == Tier.MID_SLM


def test_tier_of_defaults_mid_on_unknown():
    assert tier_of(None) == Tier.MID
    assert tier_of({}) == Tier.MID
    assert tier_of({"model": "some-unknown-frankenbrain"}) == Tier.MID


# ------------------------------------------------------------
# parse_structured
# ------------------------------------------------------------

def test_parse_structured_recognizes_single_line_tags():
    p = "[essential] You are a creature.\n[task] Do the thing.\n[length] 3 sentences."
    segs = parse_structured(p)
    tags = [s.tag for s in segs]
    assert tags == ["essential", "task", "length"]
    assert segs[0].content == "You are a creature."


def test_parse_structured_recognizes_multi_line_content():
    p = "[elaboration] Line one.\nLine two.\n[length] Short."
    segs = parse_structured(p)
    assert segs[0].tag == "elaboration"
    assert "Line one." in segs[0].content
    assert "Line two." in segs[0].content
    assert segs[1].tag == "length"


def test_parse_structured_handles_flat_prompt():
    p = "This is a flat prompt with no markers at all."
    segs = parse_structured(p)
    assert len(segs) == 1
    assert segs[0].tag == ""
    assert "flat prompt" in segs[0].content


def test_parse_structured_passes_unknown_tags_through():
    p = "[essential] E.\n[custom-tag] Whatever.\n[task] T."
    segs = parse_structured(p)
    tags = [s.tag for s in segs]
    assert "custom-tag" in tags


# ------------------------------------------------------------
# render_segments round trip
# ------------------------------------------------------------

def test_render_segments_preserves_markers():
    p = "[essential] E.\n[task] T."
    segs = parse_structured(p)
    rendered = render_segments(segs)
    assert "[essential]" in rendered and "[task]" in rendered


# ------------------------------------------------------------
# Translation — rule-based per tier
# ------------------------------------------------------------

def test_translate_large_passes_through():
    p = "[essential] E.\n[task] T.\n[elaboration] Long stuff here that would be dropped at lower tiers."
    r = translate(p, Tier.LARGE)
    assert "elaboration" in r.prompt
    assert r.transformations == []
    assert r.source_length == len(p)


def test_translate_mid_passes_through():
    p = "[task] T.\n[elaboration] Some stuff."
    r = translate(p, Tier.MID)
    assert "elaboration" in r.prompt
    assert r.transformations == []


def test_translate_mid_slm_drops_elaboration():
    p = "[task] Do X.\n[elaboration] Lots of extra words here."
    r = translate(p, Tier.MID_SLM)
    assert "elaboration" not in r.prompt
    assert "Do X" in r.prompt
    assert any("dropped" in t and "elaborations" in t for t in r.transformations)


def test_translate_mid_slm_caps_negatives_to_1():
    p = (
        "[task] Do X.\n"
        "[constraint-negative] Do NOT do A.\n"
        "[constraint-negative] Do NOT do B.\n"
        "[constraint-negative] Do NOT do C."
    )
    r = translate(p, Tier.MID_SLM)
    assert r.prompt.count("[constraint-negative]") == 1
    assert any("dropped_2_negatives" in t for t in r.transformations)


def test_translate_mid_slm_collapses_task_steps():
    p = (
        "[task] Do this in three parts:\n"
        "1. First part here.\n"
        "2. Second part here.\n"
        "3. Third part here."
    )
    r = translate(p, Tier.MID_SLM)
    # Should keep up to 2 steps
    assert "1." in r.prompt and "2." in r.prompt
    assert "3." not in r.prompt
    assert any("collapsed" in t for t in r.transformations)


def test_translate_small_slm_drops_all_negatives():
    p = (
        "[task] Do X.\n"
        "[constraint-negative] Do NOT do A."
    )
    r = translate(p, Tier.SMALL_SLM)
    assert "[constraint-negative]" not in r.prompt
    assert "Do X" in r.prompt


def test_translate_large_slm_shortens_elaboration():
    long_text = "word " * 200  # ~1000 chars
    p = f"[task] Do X.\n[elaboration] {long_text}"
    r = translate(p, Tier.LARGE_SLM)
    # Should keep elaboration but shortened
    assert "elaboration" in r.prompt
    assert r.target_length < r.source_length
    assert any("shortened" in t for t in r.transformations)


# ------------------------------------------------------------
# TranslationResult shape
# ------------------------------------------------------------

def test_translation_result_metadata_populated():
    p = "[task] T.\n[elaboration] E."
    r = translate(p, Tier.MID_SLM)
    assert r.source_length == len(p)
    assert r.target_length == len(r.prompt)
    assert r.target_tier == Tier.MID_SLM
    assert r.translator == "rule-based"
    assert isinstance(r.transformations, list)


# ------------------------------------------------------------
# translate_for (brain lookup)
# ------------------------------------------------------------

def test_translate_for_with_brain_dict():
    p = "[task] T.\n[elaboration] E."
    r = translate_for(p, {"model": "gemma4:e4b"})
    assert r.target_tier == Tier.MID_SLM
    assert "elaboration" not in r.prompt


def test_translate_for_with_large_brain():
    p = "[task] T.\n[elaboration] E."
    r = translate_for(p, {"model": "claude-opus-4-6"})
    assert r.target_tier == Tier.LARGE
    assert "elaboration" in r.prompt


# ------------------------------------------------------------
# build_tiered — authoring helper
# ------------------------------------------------------------

def test_build_tiered_composes_structured_prompt():
    r = build_tiered(
        essential="You are here.",
        task="Pick one.",
        elaboration="Extra context.",
        constraints_negative=["Do NOT X", "Do NOT Y"],
        length="3 sentences.",
        frame="First person.",
        target=Tier.LARGE,
    )
    assert "[essential]" in r.prompt
    assert "[task]" in r.prompt
    assert "[elaboration]" in r.prompt
    assert r.prompt.count("[constraint-negative]") == 2


def test_build_tiered_for_mid_slm_applies_reductions():
    r = build_tiered(
        task="Pick one.",
        elaboration="Lots of extra context.",
        constraints_negative=["Do NOT X", "Do NOT Y", "Do NOT Z"],
        target=Tier.MID_SLM,
    )
    assert "[elaboration]" not in r.prompt
    assert r.prompt.count("[constraint-negative]") == 1


# ------------------------------------------------------------
# LLMTranslator interface reserved
# ------------------------------------------------------------

def test_llm_translator_raises_not_implemented():
    t = LLMTranslator()
    try:
        t.translate("anything", Tier.MID_SLM)
        raise AssertionError("LLMTranslator should raise")
    except NotImplementedError:
        pass
