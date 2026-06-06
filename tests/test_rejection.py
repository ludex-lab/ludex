"""D-076 — RejectionError + FieldRunner gate tests."""
from __future__ import annotations

import logging
import pytest

from ludex.core.rejection import (
    RejectionError,
    brain_field_class_mismatch,
    required_capability_missing,
    field_required_organ_missing,
)


# ============================================================
# Constructor / format tests
# ============================================================

def test_rejection_error_is_exception():
    err = brain_field_class_mismatch("narrative", "structured")
    assert isinstance(err, Exception)
    assert isinstance(err, RejectionError)
    assert err.pattern_id == "D-073"
    assert err.layer == "field-runner"


def test_rejection_str_format_includes_all_fields():
    err = brain_field_class_mismatch("narrative", "structured")
    s = str(err)
    assert "[D-073]" in s
    assert "narrative" in s
    assert "structured" in s
    assert "suggestion:" in s
    assert "design-decisions-log.md" in s


def test_required_capability_missing_format():
    err = required_capability_missing(
        "json_emit", ["narrative"], "academy/stacker"
    )
    assert err.pattern_id == "D-072"
    assert "json_emit" in err.reason
    assert "academy/stacker" in err.reason
    assert "narrative" in err.reason


def test_required_organ_missing_format():
    err = field_required_organ_missing("memory", "TestBeing", "TestField")
    assert err.pattern_id == "D-076"
    assert err.layer == "config-load"
    assert "memory" in err.reason
    assert "TestBeing" in err.reason


def test_brain_field_class_extra_reason_appended():
    err = brain_field_class_mismatch(
        "narrative", "structured", reason_extra="latency cliff",
    )
    assert "latency cliff" in str(err)


# ============================================================
# FieldRunner gate tests
# ============================================================

class _StubField:
    """Minimal Field-like object for gate testing.

    Provides empty `get_prompts()` so FieldRunner.run can complete
    past the gates without trying to actually execute prompts. The
    gates set `result.notes` / raise `RejectionError` before the
    prompt loop is reached, so tests can assert on `result.notes`
    without needing a real engine.
    """

    def __init__(
        self,
        name: str = "stub",
        version: str = "v0",
        field_class: str | None = None,
        requires_capabilities: list[str] | None = None,
        requires_organs: list[str] | None = None,
    ):
        self.name = name
        self.version = version
        if field_class is not None:
            self.field_class = field_class
        self.requires_capabilities = requires_capabilities or []
        self.requires_organs = requires_organs or []

    def get_prompts(self):
        return []

    def score_overall(self, turns):
        return {"avg": 0.0, "n": 0}


class _StubOrganismConfig:
    def __init__(self, brain_capabilities: list[str] | None = None):
        self.brain_capabilities = brain_capabilities or []


class _StubOrganism:
    def __init__(self, brain_capabilities: list[str] | None = None):
        self.config = _StubOrganismConfig(brain_capabilities)


def _import_field_runner():
    from ludex.fields.base import FieldRunner
    return FieldRunner


# -- D-073 brain × field-class promotion ----------------------

def test_d073_warn_mode_does_not_raise(caplog):
    FieldRunner = _import_field_runner()
    runner = FieldRunner(strict_compatibility=False)
    field = _StubField(name="structured_test", field_class="structured")
    with caplog.at_level(logging.WARNING):
        result = runner.run(
            engine=None,
            creature_name="TestBeing",
            field=field,
            brain_provider="gemini_cli",
            brain_model="gemini-2.5-flash",
        )
    assert result is not None
    assert "class_mismatch" in (result.notes or "")
    assert any("field_class gate" in r.message for r in caplog.records)


def test_d073_strict_mode_raises():
    FieldRunner = _import_field_runner()
    runner = FieldRunner(strict_compatibility=True)
    field = _StubField(name="structured_test", field_class="structured")
    with pytest.raises(RejectionError) as exc:
        runner.run(
            engine=None,
            creature_name="TestBeing",
            field=field,
            brain_provider="gemini_cli",
            brain_model="gemini-2.5-flash",
        )
    assert exc.value.pattern_id == "D-073"
    assert "narrative" in exc.value.reason
    assert "structured" in exc.value.reason


def test_d073_compatible_pair_no_warn(caplog):
    FieldRunner = _import_field_runner()
    runner = FieldRunner(strict_compatibility=False)
    field = _StubField(name="harmonious_test", field_class="hybrid")
    with caplog.at_level(logging.WARNING):
        result = runner.run(
            engine=None,
            creature_name="TestBeing",
            field=field,
            brain_provider="gemini_cli",
            brain_model="gemini-2.5-flash",
        )
    assert "class_mismatch" not in (result.notes or "")


# -- D-072 required-capability ---------------------------------

def test_required_capability_missing_warn(caplog):
    FieldRunner = _import_field_runner()
    runner = FieldRunner(strict_compatibility=False)
    field = _StubField(
        name="requires_json", requires_capabilities=["json_emit"],
    )
    org = _StubOrganism(brain_capabilities=["narrative"])
    with caplog.at_level(logging.WARNING):
        result = runner.run(
            engine=None,
            creature_name="TestBeing",
            field=field,
            brain_provider="ollama",
            brain_model="some_model",
            organism=org,
        )
    assert "capability_missing" in (result.notes or "")


def test_required_capability_missing_strict_raises():
    FieldRunner = _import_field_runner()
    runner = FieldRunner(strict_compatibility=True)
    field = _StubField(
        name="requires_json", requires_capabilities=["json_emit"],
    )
    org = _StubOrganism(brain_capabilities=["narrative"])
    with pytest.raises(RejectionError) as exc:
        runner.run(
            engine=None,
            creature_name="TestBeing",
            field=field,
            brain_provider="ollama",
            brain_model="some_model",
            organism=org,
        )
    assert exc.value.pattern_id == "D-072"
    assert "json_emit" in exc.value.reason


def test_required_capability_satisfied_passes():
    FieldRunner = _import_field_runner()
    runner = FieldRunner(strict_compatibility=True)
    field = _StubField(
        name="requires_json", requires_capabilities=["json_emit"],
    )
    org = _StubOrganism(brain_capabilities=["json_emit", "narrative"])
    result = runner.run(
        engine=None,
        creature_name="TestBeing",
        field=field,
        brain_provider="ollama",
        brain_model="some_model",
        organism=org,
    )
    assert "capability_missing" not in (result.notes or "")


# -- D-076 required-organ --------------------------------------

def test_required_organ_missing_warn(caplog):
    FieldRunner = _import_field_runner()
    runner = FieldRunner(strict_compatibility=False)
    field = _StubField(
        name="requires_memory", requires_organs=["memory", "emotion"],
    )
    with caplog.at_level(logging.WARNING):
        result = runner.run(
            engine=None,
            creature_name="TestBeing",
            field=field,
            brain_provider="ollama",
            brain_model="some_model",
            organs_enabled=["engine"],
        )
    assert "organ_missing" in (result.notes or "")


def test_required_organ_missing_strict_raises():
    FieldRunner = _import_field_runner()
    runner = FieldRunner(strict_compatibility=True)
    field = _StubField(
        name="requires_memory", requires_organs=["memory"],
    )
    with pytest.raises(RejectionError) as exc:
        runner.run(
            engine=None,
            creature_name="TestBeing",
            field=field,
            brain_provider="ollama",
            brain_model="some_model",
            organs_enabled=["engine"],
        )
    assert exc.value.pattern_id == "D-076"
    assert "memory" in exc.value.reason


def test_required_organ_satisfied_passes():
    FieldRunner = _import_field_runner()
    runner = FieldRunner(strict_compatibility=True)
    field = _StubField(
        name="requires_memory", requires_organs=["memory"],
    )
    result = runner.run(
        engine=None,
        creature_name="TestBeing",
        field=field,
        brain_provider="ollama",
        brain_model="some_model",
        organs_enabled=["engine", "memory", "emotion"],
    )
    assert "organ_missing" not in (result.notes or "")


# -- backward compatibility -----------------------------------

def test_field_runner_default_is_warn_mode(monkeypatch):
    """Default constructor must remain warn-mode so existing
    experiment scripts do not start raising unexpectedly. Override
    any ambient env var that might enable strict mode in CI."""
    monkeypatch.delenv("LUDEX_STRICT_COMPATIBILITY", raising=False)
    FieldRunner = _import_field_runner()
    runner = FieldRunner()
    assert runner.strict_compatibility is False


# -- env var ---------------------------------------------------

def test_strict_compatibility_from_env_truthy(monkeypatch):
    monkeypatch.setenv("LUDEX_STRICT_COMPATIBILITY", "1")
    FieldRunner = _import_field_runner()
    assert FieldRunner().strict_compatibility is True

    monkeypatch.setenv("LUDEX_STRICT_COMPATIBILITY", "true")
    assert FieldRunner().strict_compatibility is True

    monkeypatch.setenv("LUDEX_STRICT_COMPATIBILITY", "YES")
    assert FieldRunner().strict_compatibility is True


def test_strict_compatibility_from_env_falsy(monkeypatch):
    monkeypatch.setenv("LUDEX_STRICT_COMPATIBILITY", "0")
    FieldRunner = _import_field_runner()
    assert FieldRunner().strict_compatibility is False

    monkeypatch.setenv("LUDEX_STRICT_COMPATIBILITY", "false")
    assert FieldRunner().strict_compatibility is False

    monkeypatch.setenv("LUDEX_STRICT_COMPATIBILITY", "")
    assert FieldRunner().strict_compatibility is False


def test_strict_compatibility_explicit_overrides_env(monkeypatch):
    """Explicit constructor argument wins over env var."""
    monkeypatch.setenv("LUDEX_STRICT_COMPATIBILITY", "1")
    FieldRunner = _import_field_runner()
    runner = FieldRunner(strict_compatibility=False)
    assert runner.strict_compatibility is False


# -- real-field declarations (D-076 second-evidence) -----------

def test_council_declares_required_organs(monkeypatch):
    """Council class should declare requires_organs so the gate
    fires usefully on real Council runs."""
    monkeypatch.delenv("LUDEX_STRICT_COMPATIBILITY", raising=False)
    from ludex.fields.council import Council
    assert hasattr(Council, "requires_organs")
    assert "engine" in Council.requires_organs
    assert "memory" in Council.requires_organs


def test_academy_declares_required_organs(monkeypatch):
    monkeypatch.delenv("LUDEX_STRICT_COMPATIBILITY", raising=False)
    from ludex.fields.academy import Academy
    assert hasattr(Academy, "requires_organs")
    assert "engine" in Academy.requires_organs
    assert "memory" in Academy.requires_organs


def test_wilderness_declares_required_organs(monkeypatch):
    """Wilderness has its own .run() (not via FieldRunner) so the
    gate does not fire against it directly, but the declaration is
    expected for tooling consistency."""
    monkeypatch.delenv("LUDEX_STRICT_COMPATIBILITY", raising=False)
    from ludex.fields.wilderness import Wilderness
    assert hasattr(Wilderness, "requires_organs")
    for organ in ("engine", "emotion", "immune"):
        assert organ in Wilderness.requires_organs


def test_stacker_declares_required_capabilities(monkeypatch):
    monkeypatch.delenv("LUDEX_STRICT_COMPATIBILITY", raising=False)
    from ludex.fields.stacker.engine import StackerEngine
    assert hasattr(StackerEngine, "requires_capabilities")
    assert "json_emit" in StackerEngine.requires_capabilities
