"""Field class taxonomy + brain×field compatibility gate.

Companion to brain_class — together they form the coarse routing
substrate that catches the Wick × Stacker class of mistake at field
engagement time.
"""

from __future__ import annotations

import logging

import pytest

from ludex.core.brain_class import (
    classify_brain,
    NARRATIVE as B_NAR, STRUCTURED as B_STR, HYBRID as B_HYB,
)
from ludex.core.field_class import (
    classify_field, is_compatible, mismatch_reason,
    NARRATIVE, STRUCTURED, HYBRID, UNKNOWN, FIELD_CLASSES,
)


# --- classify_field ---


def test_direct_lookup_wilderness():
    assert classify_field("wilderness") == NARRATIVE


def test_direct_lookup_stacker():
    assert classify_field("stacker") == STRUCTURED


def test_direct_lookup_avalon():
    assert classify_field("avalon") == STRUCTURED


def test_namespaced_academy_stacker_resolves_to_leaf():
    """`academy/stacker` should pick up stacker = structured even
    though academy = hybrid. Leaf wins."""
    assert classify_field("academy/stacker") == STRUCTURED


def test_namespaced_lxm_avalon_resolves_to_leaf():
    assert classify_field("lxm/avalon") == STRUCTURED


def test_namespaced_unknown_leaf_returns_unknown():
    assert classify_field("custom/unrecognized") == UNKNOWN


def test_empty_field_name_is_unknown():
    assert classify_field("") == UNKNOWN


def test_none_input_is_unknown():
    assert classify_field(None) == UNKNOWN


# --- declarative override on Field subclass ---


def test_field_instance_attribute_wins_over_registry():
    """A Field subclass declaring `field_class = "narrative"` overrides
    the registry's classification for its name."""
    from ludex.fields.base import Field

    class _CustomField(Field):
        name = "stacker"  # registry says structured
        field_class = NARRATIVE  # declarative override

    assert classify_field(_CustomField()) == NARRATIVE


def test_field_instance_without_attribute_falls_back_to_registry():
    """An instance with empty field_class still resolves via name lookup."""
    from ludex.fields.base import Field

    class _DefaultField(Field):
        name = "wilderness"
        # field_class inherits "" from base

    assert classify_field(_DefaultField()) == NARRATIVE


def test_field_instance_with_unknown_name_is_unknown():
    from ludex.fields.base import Field

    class _Mystery(Field):
        name = "completely_made_up"

    assert classify_field(_Mystery()) == UNKNOWN


def test_field_instance_namespaced_leaf_resolves():
    from ludex.fields.base import Field

    class _Nested(Field):
        name = "lxm/avalon"

    assert classify_field(_Nested()) == STRUCTURED


def test_classes_constant_complete():
    assert set(FIELD_CLASSES) == {NARRATIVE, STRUCTURED, HYBRID, UNKNOWN}


# --- compatibility matrix ---


def test_narrative_brain_on_structured_field_is_incompatible():
    """The Wick × Stacker case — only blocked pair."""
    assert is_compatible(B_NAR, STRUCTURED) is False


def test_narrative_brain_on_narrative_field_is_compatible():
    assert is_compatible(B_NAR, NARRATIVE) is True


def test_narrative_brain_on_hybrid_field_is_compatible():
    assert is_compatible(B_NAR, HYBRID) is True


def test_structured_brain_on_any_field_is_compatible():
    """Function-call brains can do narrative; overkill but works."""
    assert is_compatible(B_STR, NARRATIVE) is True
    assert is_compatible(B_STR, STRUCTURED) is True
    assert is_compatible(B_STR, HYBRID) is True


def test_hybrid_brain_on_any_field_is_compatible():
    assert is_compatible(B_HYB, NARRATIVE) is True
    assert is_compatible(B_HYB, STRUCTURED) is True
    assert is_compatible(B_HYB, HYBRID) is True


def test_unknown_brain_or_field_abstains():
    """Unknowns don't trigger a warning — caretaker can investigate."""
    assert is_compatible("unknown", STRUCTURED) is True
    assert is_compatible(B_NAR, "unknown") is True


# --- mismatch_reason ---


def test_mismatch_reason_for_narrative_structured_is_descriptive():
    msg = mismatch_reason(B_NAR, STRUCTURED)
    assert "narrative" in msg
    assert "structured" in msg
    # Should mention the operational consequence
    assert "timeout" in msg or "parse" in msg


def test_mismatch_reason_returns_empty_when_compatible():
    assert mismatch_reason(B_NAR, NARRATIVE) == ""
    assert mismatch_reason(B_HYB, STRUCTURED) == ""


# --- end-to-end: real Wick example ---


def test_wick_on_stacker_is_caught():
    """Runtime composition: classify Wick's brain + classify the
    Stacker field, and confirm the gate flags it."""
    bclass = classify_brain("gemini_cli", "gemini-3.1-pro-preview")
    fclass = classify_field("academy/stacker")
    assert bclass == B_NAR
    assert fclass == STRUCTURED
    assert is_compatible(bclass, fclass) is False


def test_hearth_on_wilderness_is_compatible():
    bclass = classify_brain("claude_cli", "claude-haiku-4-5")
    fclass = classify_field("wilderness")
    assert bclass == B_STR
    assert fclass == NARRATIVE
    assert is_compatible(bclass, fclass) is True


def test_anvil_on_stacker_is_compatible():
    """Hybrid brain (gpt-5.5/opus) on structured field — fine."""
    bclass = classify_brain("codex_cli", "gpt-5.5")
    fclass = classify_field("stacker")
    assert bclass == B_HYB
    assert fclass == STRUCTURED
    assert is_compatible(bclass, fclass) is True


# --- gate logs warning ---


def test_field_runner_warns_on_class_mismatch(caplog):
    """The FieldRunner.run gate logs a warning + sets result.notes
    when brain×field is incompatible. Uses minimal stubs to avoid
    needing a real organism."""
    from ludex.fields.base import Field, FieldRunner

    class _StubField(Field):
        name = "stacker"
        version = "0.1"

        def get_prompts(self):
            return []  # no turns; we only care about the entry gate

    class _StubEngine:
        def _cfg(self, k, default=""):
            return default

    runner = FieldRunner()
    with caplog.at_level(logging.WARNING):
        result = runner.run(
            _StubEngine(),
            creature_name="Wick",
            field=_StubField(),
            brain_provider="gemini_cli",
            brain_model="gemini-3.1-pro-preview",
        )

    # Warning logged
    warning_records = [r for r in caplog.records if "field_class gate" in r.message]
    assert warning_records, "expected a field_class gate warning"

    # Result notes carry the mismatch tag
    assert "class_mismatch" in result.notes
    assert "narrative" in result.notes
    assert "structured" in result.notes


def test_field_runner_silent_on_compatible_pair(caplog):
    """Compatible pairs (Hearth × Wilderness) emit no warning."""
    from ludex.fields.base import Field, FieldRunner

    class _StubField(Field):
        name = "wilderness"
        version = "0.1"

        def get_prompts(self):
            return []

    class _StubEngine:
        def _cfg(self, k, default=""):
            return default

    runner = FieldRunner()
    with caplog.at_level(logging.WARNING):
        result = runner.run(
            _StubEngine(),
            creature_name="Hearth",
            field=_StubField(),
            brain_provider="claude_cli",
            brain_model="claude-haiku-4-5",
        )

    warning_records = [r for r in caplog.records if "field_class gate" in r.message]
    assert not warning_records
    assert "class_mismatch" not in (result.notes or "")
