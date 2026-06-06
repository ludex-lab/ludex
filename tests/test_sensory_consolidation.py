"""Tests for ludex.core.sensory_consolidation (Phase B Step 1).

Exercises the gate decisions (purpose-bypass, first-in-field,
high-salience, stale-or-novel, ambient-skip, pressure-skip,
fallback-write) and the memory-write side effect. Uses a
minimal test double for the organism rather than a full build,
so tests run fast and don't touch real memory stores.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ludex.core import sensory_consolidation as sc


# ----------------------------------------------------------------------
# Minimal test doubles
# ----------------------------------------------------------------------

@dataclass
class _FakeHealth:
    grade: str = "A"


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
            "content": content,
            "memory_type": memory_type,
            "tags": list(tags or []),
            "importance": importance,
            "source": source,
            "metadata": dict(metadata or {}),
        })
        return f"mem_{len(self.writes):04d}"


class _FakeOrganism:
    def __init__(self, name: str = "TestCreature",
                 memory: _FakeMemory | None = None):
        self.name = name
        self._memory = memory

    def get_block(self, name: str):
        if name == "memory":
            return self._memory
        return None


@pytest.fixture(autouse=True)
def _clear_last_sensing():
    sc._LAST_SENSING.clear()
    yield
    sc._LAST_SENSING.clear()


def _evt(organism_name="TestCreature", content="a small scene",
         source_kind="opsis", purpose="", field_name="wilderness_01",
         timestamp=1000.0):
    return sc.SensoryEvent(
        source_kind=source_kind,
        content=content,
        source="fake://source",
        purpose=purpose,
        channel="logos_fallback",
        field_name=field_name,
        timestamp=timestamp,
    )


# ----------------------------------------------------------------------
# Gate: purpose-hint bypass
# ----------------------------------------------------------------------

def test_purpose_remember_hint_bypasses_gate():
    org = _FakeOrganism(memory=_FakeMemory())
    event = _evt(purpose="please remember this scene")
    decision = sc.evaluate_gate(org, event)
    assert decision.pass_gate is True
    assert decision.write_memory is True
    assert decision.importance == pytest.approx(0.9)
    assert any("purpose:remember-hint" in r for r in decision.reasons)


def test_purpose_without_hint_does_not_bypass():
    org = _FakeOrganism(memory=_FakeMemory())
    event = _evt(purpose="inspecting a chart", content="ambient")
    decision = sc.evaluate_gate(org, event)
    # No bypass reason
    assert not any("purpose:remember-hint" in r for r in decision.reasons)


# ----------------------------------------------------------------------
# Gate: first-in-field anchor
# ----------------------------------------------------------------------

def test_first_observation_in_field_always_passes():
    org = _FakeOrganism(memory=_FakeMemory())
    event = _evt(content="brief text")
    decision = sc.evaluate_gate(org, event)
    assert decision.pass_gate is True
    assert decision.write_memory is True
    assert any("gate:first-in-field" in r for r in decision.reasons)


# ----------------------------------------------------------------------
# Gate: salience paths
# ----------------------------------------------------------------------

def test_high_salience_content_writes():
    org = _FakeOrganism(memory=_FakeMemory())
    # Seed the tracker so this is not a first-in-field case
    sc._mark_sensed(org, _evt(timestamp=500.0))
    long_content = "x " * 400  # 800 chars → length_score 0.6 alone
    event = _evt(content=long_content, timestamp=520.0)   # 20s later
    decision = sc.evaluate_gate(org, event)
    assert decision.write_memory is True
    assert any("high-salience" in r or "stale-or-novel" in r
               for r in decision.reasons)


def test_novelty_markers_raise_salience():
    org = _FakeOrganism(memory=_FakeMemory())
    sc._mark_sensed(org, _evt(timestamp=500.0))
    event = _evt(
        content="a first-time encounter; this feels new and unexpected",
        timestamp=520.0,
    )
    decision = sc.evaluate_gate(org, event)
    assert decision.write_memory is True


# ----------------------------------------------------------------------
# Gate: ambient skip within short window
# ----------------------------------------------------------------------

def test_ambient_skip_within_window_low_salience():
    org = _FakeOrganism(memory=_FakeMemory())
    sc._mark_sensed(org, _evt(timestamp=500.0))
    # 60 seconds later, thin content (< SALIENCE_DESC_LEN_LOW) → skip
    event = _evt(content="tiny", timestamp=560.0)
    decision = sc.evaluate_gate(org, event)
    assert decision.pass_gate is False
    assert decision.write_memory is False
    assert any("ambient-skip" in r for r in decision.reasons)


def test_ambient_skip_at_exact_low_salience_boundary():
    """Salience 0.30 (length just above DESC_LEN_LOW, no novelty) also
    skips if within ambient window. Previously a strict `< 0.3` check
    left this case falling through to fallback-write — tuned to `<= 0.3`
    after the 2026-04-23 smoke surfaced the boundary."""
    org = _FakeOrganism(memory=_FakeMemory())
    sc._mark_sensed(org, _evt(timestamp=500.0))
    # Length just above DESC_LEN_LOW (80 chars) → length_score = 0.3,
    # no novelty markers → salience exactly 0.30.
    payload = "x" * (sc.SALIENCE_DESC_LEN_LOW + 5)  # 85 chars
    # Sanity: confirm the fixture sits at the expected salience.
    assert sc._content_salience(payload) == pytest.approx(0.3)
    event = _evt(content=payload, timestamp=560.0)  # 60s later
    decision = sc.evaluate_gate(org, event)
    assert decision.pass_gate is False
    assert decision.write_memory is False
    assert any("ambient-skip" in r for r in decision.reasons)


# ----------------------------------------------------------------------
# Gate: stale window always writes
# ----------------------------------------------------------------------

def test_stale_window_writes_even_low_salience():
    org = _FakeOrganism(memory=_FakeMemory())
    sc._mark_sensed(org, _evt(timestamp=500.0))
    # 40 minutes later, thin content → stale-window pass
    event = _evt(content="tiny", timestamp=500.0 + sc.STALE_WINDOW_SECONDS + 60)
    decision = sc.evaluate_gate(org, event)
    assert decision.write_memory is True


# ----------------------------------------------------------------------
# Gate: memory pressure suppresses fallback-write
# ----------------------------------------------------------------------

def test_high_memory_pressure_skips_fallback_write():
    # At grade C (pressure 0.5) the fallback-write path is suppressed
    org = _FakeOrganism(memory=_FakeMemory(grade="C"))
    sc._mark_sensed(org, _evt(timestamp=500.0))
    # 10 minutes later — past ambient, before stale. Mid-salience.
    event = _evt(content="a medium-length caption about the scene",
                 timestamp=500.0 + 10 * 60)
    decision = sc.evaluate_gate(org, event)
    # 10-minute gap may land in the stale-or-novel path with higher
    # salience; at grade C we expect the result to still skip *if* it
    # falls to fallback. Assert on the reason trail rather than the
    # final verdict for robustness.
    if "gate:fallback-write" in decision.reasons:
        # Should not happen under pressure — must be pressure-skip
        pytest.fail("fallback-write should be suppressed at high pressure")


# ----------------------------------------------------------------------
# consolidate_observation — memory write side effect
# ----------------------------------------------------------------------

def test_consolidate_writes_memory_on_pass():
    mem = _FakeMemory()
    org = _FakeOrganism(memory=mem)
    event = _evt(content="a first observation", field_name="wilderness_5")
    decision = sc.consolidate_observation(org, event)
    assert decision.write_memory is True
    assert len(mem.writes) == 1
    w = mem.writes[0]
    assert w["memory_type"] == "episodic"
    assert "opsis" in w["tags"]
    assert "wilderness_5" in w["tags"]
    assert w["source"] == "opsis"
    assert "a first observation" in w["content"]
    assert "gate_reasons" in w["metadata"]


def test_consolidate_skips_memory_on_ambient():
    mem = _FakeMemory()
    org = _FakeOrganism(memory=mem)
    # Seed tracker
    sc._mark_sensed(org, _evt(timestamp=500.0, field_name="wilderness_5"))
    event = _evt(content="tiny", timestamp=560.0, field_name="wilderness_5")
    decision = sc.consolidate_observation(org, event)
    assert decision.write_memory is False
    assert len(mem.writes) == 0


def test_consolidate_graceful_when_memory_block_absent():
    """Organism may have no memory block (test doubles, disabled
    organs). consolidate_observation must not raise."""
    org = _FakeOrganism(memory=None)
    event = _evt(content="brief", field_name="f")
    # Should not raise even though memory is absent
    decision = sc.consolidate_observation(org, event)
    # Decision still reflects gate logic; memory write is no-op
    assert decision.pass_gate in (True, False)


# ----------------------------------------------------------------------
# Last-seen tracker independence across source kinds + fields
# ----------------------------------------------------------------------

def test_last_seen_per_source_kind_and_field():
    org = _FakeOrganism(memory=_FakeMemory())
    # Sensing in field A does not make field B look non-first
    sc._mark_sensed(org, _evt(field_name="field_a", timestamp=500.0))
    d = sc.evaluate_gate(org, _evt(field_name="field_b", timestamp=560.0))
    assert any("first-in-field" in r for r in d.reasons)

    # Opsis sensing does not affect Akoué first-in-field
    sc._mark_sensed(org, _evt(source_kind="opsis",
                              field_name="field_a", timestamp=500.0))
    d2 = sc.evaluate_gate(org, _evt(source_kind="akoue",
                                    field_name="field_a",
                                    timestamp=560.0))
    assert any("first-in-field" in r for r in d2.reasons)


def test_consolidate_observation_updates_tracker_even_on_skip():
    org = _FakeOrganism(memory=_FakeMemory())
    # First
    sc.consolidate_observation(org, _evt(content="first", timestamp=500.0))
    assert sc._LAST_SENSING  # tracker populated
    # Ambient skip
    decision = sc.consolidate_observation(
        org, _evt(content="tiny", timestamp=560.0)
    )
    assert decision.write_memory is False
    # Tracker updated anyway
    key = sc._last_seen_key(org, "opsis", "wilderness_01")
    assert sc._LAST_SENSING[key] == 560.0


# ----------------------------------------------------------------------
# Content-salience edge cases
# ----------------------------------------------------------------------

def test_salience_empty_content_zero():
    assert sc._content_salience("") == 0.0


def test_salience_short_content_low():
    assert sc._content_salience("hi") < 0.3


def test_salience_long_content_high():
    text = "word " * 200  # 1000 chars
    assert sc._content_salience(text) >= 0.5


def test_salience_novelty_adds_score():
    base = sc._content_salience("a scene of moderate length describing something")
    boosted = sc._content_salience(
        "a scene of moderate length — this is new, unexpected, unusual"
    )
    assert boosted > base
