"""Unit tests for `_looks_like_distill_error` — the distill error-pattern
detector that catches subprocess-error strings that pass the bare length
threshold (motivated by physis_smoke_031, `[Error: 'claude' not found...]`)."""

from ludex.blocks.physis import _looks_like_distill_error


def test_empty_text_is_not_error():
    assert _looks_like_distill_error("") is False
    assert _looks_like_distill_error(None) is False  # type: ignore[arg-type]


def test_smoke_031_path_glitch_caught():
    text = "[Error: 'claude' not found from background-bash subprocess; PATH excludes nvm shim]"
    assert _looks_like_distill_error(text) is True


def test_not_found_pattern_caught():
    text = "command not found: claude"
    assert _looks_like_distill_error(text) is True


def test_is_installed_pattern_caught():
    text = "Error: Is claude installed? Try `nvm use 20` first."
    assert _looks_like_distill_error(text) is True


def test_legitimate_distill_with_error_in_body_not_flagged():
    """A long legitimate distillation that mentions the word 'Error:' in
    body narrative (not in the head) should NOT be flagged."""
    head = (
        "# World model — `lxm/avalon`\n\n"
        "## What I learn from this match\n\n"
        "Quest 1: I voted approve on a 2-person team and the quest succeeded. "
        "This is consistent with my prior hypothesis that early-round trust "
        "should be extended by default. Three additional plays would let me "
        "narrow the confidence interval here.\n\n"
        "## Open questions\n\n"
    )
    body_with_error = head + "Error: I should test what happens when I reject early.\n"
    assert len(body_with_error) > 200
    assert _looks_like_distill_error(body_with_error) is False


def test_short_legitimate_distill_not_flagged():
    text = (
        "# World model — `lxm/trustgame`\n\n"
        "Cooperate by default in early rounds; the iterated structure rewards "
        "trust-building over short-term defection."
    )
    assert _looks_like_distill_error(text) is False


def test_case_insensitive_match():
    assert _looks_like_distill_error("ERROR: command not found") is True
    assert _looks_like_distill_error("error: claude binary missing") is True
