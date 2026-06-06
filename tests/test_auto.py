"""Tests for ludex.blocks.auto (D-058 Phase A).

Exercises AutoBlock.handle_sense() with and without sibling organs,
plus the sensory_consolidation gate's integration with Auto.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ludex.blocks.auto import AutoBlock, InteroceptionReading
from ludex.core import sensory_consolidation as sc


# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------

@dataclass
class _FakeHealth:
    grade: str = "A"


@dataclass
class _FakeVitals:
    valence: float = 0.0
    arousal: float = 0.0
    dominant_emotion: str = ""


class _FakeEmotion:
    def __init__(self, valence=0.0, arousal=0.0, dominant=""):
        self._current = _FakeVitals(
            valence=valence, arousal=arousal, dominant_emotion=dominant
        )


class _FakeMemory:
    def __init__(self, grade: str = "A"):
        self._grade = grade
        self.writes: list[dict] = []

    def handle_health_check(self):
        return _FakeHealth(grade=self._grade)

    def handle_remember(self, content, memory_type="episodic",
                        tags=None, importance=0.5, source="",
                        metadata=None):
        self.writes.append({
            "content": content, "memory_type": memory_type,
            "tags": list(tags or []), "importance": importance,
            "source": source, "metadata": dict(metadata or {}),
        })
        return f"mem_{len(self.writes):04d}"


class _FakeHumoral:
    def __init__(self, stress: float = 0.0):
        self._stress = stress

    def handle_get_humoral_state(self):
        return {"stress": self._stress}


class _FakeEngine:
    def __init__(self, budget: int = 100_000, used: int = 0):
        self._token_budget = budget
        self._tokens_used = used


class _FakeOrganism:
    def __init__(self, name: str = "T", **blocks):
        self.name = name
        self._blocks = dict(blocks)

    def get_block(self, name: str):
        return self._blocks.get(name)


def _auto_attached(organism) -> AutoBlock:
    """Build an AutoBlock and attach it to a fake organism."""
    block = AutoBlock()
    block._organism = organism
    block.on_attach()
    return block


@pytest.fixture(autouse=True)
def _clear_tracker():
    sc._LAST_SENSING.clear()
    yield
    sc._LAST_SENSING.clear()


# ----------------------------------------------------------------------
# AutoBlock: basic reading shape
# ----------------------------------------------------------------------

def test_auto_returns_interoception_reading_type():
    org = _FakeOrganism()
    auto = _auto_attached(org)
    r = auto.handle_sense()
    assert isinstance(r, InteroceptionReading)


def test_auto_empty_organism_returns_neutral_defaults():
    """With no sibling organs, reading is all neutral."""
    org = _FakeOrganism()
    auto = _auto_attached(org)
    r = auto.handle_sense()
    assert r.affective_valence == 0.0
    assert r.affective_arousal == 0.0
    assert r.dominant_emotion == ""
    assert r.memory_grade == "A"
    assert r.memory_pressure == 0.0
    assert r.stress_level == 0.0
    assert r.token_headroom_ratio == 1.0


def test_auto_reads_emotion():
    org = _FakeOrganism(
        emotion=_FakeEmotion(valence=0.5, arousal=0.3, dominant="calm")
    )
    auto = _auto_attached(org)
    r = auto.handle_sense()
    assert r.affective_valence == pytest.approx(0.5)
    assert r.affective_arousal == pytest.approx(0.3)
    assert r.dominant_emotion == "calm"


def test_auto_reads_memory_health():
    org = _FakeOrganism(memory=_FakeMemory(grade="C"))
    auto = _auto_attached(org)
    r = auto.handle_sense()
    assert r.memory_grade == "C"
    assert r.memory_pressure == pytest.approx(0.5)


def test_auto_reads_humoral_stress():
    org = _FakeOrganism(humoral_immune=_FakeHumoral(stress=0.7))
    auto = _auto_attached(org)
    r = auto.handle_sense()
    assert r.stress_level == pytest.approx(0.7)


def test_auto_reads_engine_token_headroom():
    org = _FakeOrganism(engine=_FakeEngine(budget=100_000, used=70_000))
    auto = _auto_attached(org)
    r = auto.handle_sense()
    assert r.token_headroom_ratio == pytest.approx(0.3)


def test_auto_reading_source_signals_populated():
    org = _FakeOrganism(
        emotion=_FakeEmotion(valence=0.5),
        memory=_FakeMemory(grade="B"),
    )
    auto = _auto_attached(org)
    r = auto.handle_sense()
    assert "emotion" in r.source_signals
    assert "memory" in r.source_signals
    assert r.source_signals["memory"]["grade"] == "B"


# ----------------------------------------------------------------------
# summary() prose
# ----------------------------------------------------------------------

def test_auto_summary_neutral_baseline():
    """A fully-empty organism yields a neutral tone/arousal summary
    with no alerting fields. Summary is terse but not empty."""
    org = _FakeOrganism()
    auto = _auto_attached(org)
    s = auto.handle_sense().summary()
    assert "neutral" in s
    assert "settled" in s  # 0 arousal → settled
    # No alert fields surface
    assert "memory=" not in s
    assert "stress=" not in s
    assert "tokens_low=" not in s


def test_auto_summary_positive_tone():
    org = _FakeOrganism(emotion=_FakeEmotion(valence=0.5, arousal=0.2, dominant="curious"))
    auto = _auto_attached(org)
    s = auto.handle_sense().summary()
    assert "positive" in s
    assert "settled" in s
    assert "curious" in s


def test_auto_summary_flags_memory_distress():
    org = _FakeOrganism(memory=_FakeMemory(grade="D"))
    auto = _auto_attached(org)
    s = auto.handle_sense().summary()
    assert "memory=D" in s


def test_auto_summary_flags_high_stress():
    org = _FakeOrganism(humoral_immune=_FakeHumoral(stress=0.8))
    auto = _auto_attached(org)
    s = auto.handle_sense().summary()
    assert "stress=" in s


def test_auto_summary_flags_low_tokens():
    org = _FakeOrganism(engine=_FakeEngine(budget=100_000, used=90_000))
    auto = _auto_attached(org)
    s = auto.handle_sense().summary()
    assert "tokens_low=" in s


# ----------------------------------------------------------------------
# Defensive: broken organ should not raise
# ----------------------------------------------------------------------

class _BrokenMemory:
    def handle_health_check(self):
        raise RuntimeError("memory offline")


def test_auto_handles_broken_memory_gracefully():
    org = _FakeOrganism(memory=_BrokenMemory())
    auto = _auto_attached(org)
    r = auto.handle_sense()  # should not raise
    assert r.memory_grade == "A"  # neutral default on failure
    assert r.memory_pressure == 0.0


# ----------------------------------------------------------------------
# Gate integration: sensory_consolidation prefers Auto when present
# ----------------------------------------------------------------------

def test_gate_uses_auto_for_memory_pressure_when_present():
    """Auto reports pressure 0.5 → gate should suppress fallback-write."""
    org = _FakeOrganism(memory=_FakeMemory(grade="C"))
    # Wire Auto to the organism
    auto = _auto_attached(org)
    org._blocks["auto"] = auto

    sc._mark_sensed(org, sc.SensoryEvent(
        source_kind="opsis", content="x", field_name="f",
        timestamp=500.0,
    ))
    # Low-salience event 10 minutes later — fallback window
    event = sc.SensoryEvent(
        source_kind="opsis", content="a short caption",
        field_name="f", timestamp=500.0 + 10 * 60,
    )
    decision = sc.evaluate_gate(org, event)
    # pressure 0.5 should suppress fallback-write
    if "gate:fallback-write" in decision.reasons:
        pytest.fail("fallback-write should be suppressed when Auto reports pressure 0.5")


def test_gate_falls_back_to_inline_memory_when_auto_absent():
    """Without Auto, gate still reads memory health directly."""
    org = _FakeOrganism(memory=_FakeMemory(grade="C"))
    # Auto not in blocks
    sc._mark_sensed(org, sc.SensoryEvent(
        source_kind="opsis", content="x", field_name="f",
        timestamp=500.0,
    ))
    event = sc.SensoryEvent(
        source_kind="opsis", content="a short caption",
        field_name="f", timestamp=500.0 + 10 * 60,
    )
    decision = sc.evaluate_gate(org, event)
    # Should still see pressure from the direct memory read
    assert any("pressure=0.50" in r for r in decision.reasons)


def test_gate_without_auto_or_memory():
    """No auto, no memory → pressure 0.0 (fallback-write allowed)."""
    org = _FakeOrganism()
    sc._mark_sensed(org, sc.SensoryEvent(
        source_kind="opsis", content="x", field_name="f",
        timestamp=500.0,
    ))
    event = sc.SensoryEvent(
        source_kind="opsis", content="a short caption",
        field_name="f", timestamp=500.0 + 10 * 60,
    )
    decision = sc.evaluate_gate(org, event)
    assert any("pressure=0.00" in r for r in decision.reasons)
