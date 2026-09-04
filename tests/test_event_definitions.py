"""The gate and the lived-event definition differ in BOTH directions.

should_consolidate decides when a creature reflects. It drops three span kinds
and counts the five sense kinds, which fire on every heartbeat — so on a sensing
creature it counts the clock, which its own comment says it avoids. Measured
2026-08-06: 20 of the 28 events pushing Wisp toward his threshold were sensory.

The asymmetry is easy to get wrong in the direction I first got it wrong:
deriving the lived set as GATE_EXCLUDED | SENSE_KINDS looks tidy and silently
adds translation_applied, which M1's frozen pre-registration counts as a lived
event. These tests pin both directions so neither definition drifts into the
other.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ludex.cadence.event_definitions import (          # noqa: E402
    GATE_EXCLUDED, GATE_ONLY, LIVED_EXCLUDED, LIVED_ONLY, SENSE_KINDS,
    counterfactual,
)
from ludex.core.consolidation import _NON_EVENT_KINDS  # noqa: E402


def test_gate_set_is_imported_not_copied():
    """A second copy would drift from the decision it mirrors."""
    assert GATE_EXCLUDED == frozenset(_NON_EVENT_KINDS)


def test_the_gate_counts_every_sense_kind():
    """The premise of the whole cadence-face question."""
    assert not (SENSE_KINDS & GATE_EXCLUDED)


def test_lived_set_is_not_a_superset_of_the_gate_set():
    """translation_applied is a lived event to M1 and invisible to the gate."""
    assert GATE_ONLY == {"translation_applied"}
    assert not LIVED_EXCLUDED >= GATE_EXCLUDED


def test_lived_set_drops_the_senses_and_the_weight_check():
    assert LIVED_ONLY == SENSE_KINDS | {"weight_check"}


def test_missing_ledger_returns_none_rather_than_zero():
    """Ray-habitat spans are not tracked here (.gitignore drops *.jsonl and only
    Mac creatures were force-added), so a sweep over 'both habitats' must report
    absence. A zero would read as a creature with no events."""
    assert counterfactual(Path("creatures") / "__no_such_creature__") is None


def test_a_real_creature_reads_both_counts():
    r = counterfactual(Path("creatures") / "Lyra")
    if r is None:                      # not every checkout carries the ledger
        return
    assert r["gate_events"] >= r["lived_events"] >= 0
    assert r["excluded_by_lived_definition"] == r["gate_events"] - r["lived_events"]
