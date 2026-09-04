"""Arrival records are canonical spans, written once, and history stands.

이음 (lab:ludex-village), 2026-08-26: `onboard_creature` reported success and
the house was right, but `validate_creature_data.py` failed every arrival line
on creature/timestamp/attributes — for all five Studio residents. The writer
had hand-rolled {kind, t, who, habitat, col, row, note} instead of a Span.

The second half is worse than the schema: the module documents each step as
idempotent, and the house and the outfit are — but the arrival was appended
again on every call. A creature could accumulate arrivals at the same address.

Legacy lines are not rewritten. An append-only ledger does not get edited to
please a validator, so the validator learns the old shape instead — narrowly,
so a NEW arrival written wrongly still fails.
"""

from __future__ import annotations

import json

import pytest

from ludex.village.onboard import _arrival_event


@pytest.fixture()
def creature(tmp_path, monkeypatch):
    home = tmp_path / "Nori"
    (home / "store").mkdir(parents=True)
    monkeypatch.setattr("ludex.village.onboard._creature_dir", lambda n: home)
    return home


def _lines(home):
    p = home / "store" / "spans.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_arrival_is_a_canonical_span(creature):
    assert _arrival_event("Nori", "ludex-studio", {"col": 52, "row": 54}) is True
    span = _lines(creature)[0]
    assert span["kind"] == "village_arrival"
    assert span["creature"] == "Nori"
    assert isinstance(span["timestamp"], (int, float))
    assert span["attributes"]["habitat"] == "ludex-studio"
    assert span["attributes"]["col"] == 52 and span["attributes"]["row"] == 54


def test_re_onboarding_does_not_append_a_second_arrival(creature):
    plot = {"col": 52, "row": 54}
    assert _arrival_event("Nori", "ludex-studio", plot) is True
    assert _arrival_event("Nori", "ludex-studio", plot) is False
    assert len(_lines(creature)) == 1, "the ledger is idempotent too, not just the house"


def test_a_move_to_a_new_plot_is_a_new_arrival(creature):
    assert _arrival_event("Nori", "ludex-studio", {"col": 52, "row": 54}) is True
    assert _arrival_event("Nori", "ludex-studio", {"col": 60, "row": 61}) is True
    assert len(_lines(creature)) == 2, "a different address is a different event"


def test_legacy_arrival_suppresses_a_duplicate(creature):
    """A resident onboarded before the fix must not get a second arrival."""
    legacy = {"kind": "village_arrival", "t": 1.0, "who": "Nori",
              "habitat": "ludex-studio", "col": 52, "row": 54, "note": "x"}
    (creature / "store" / "spans.jsonl").write_text(
        json.dumps(legacy) + "\n", encoding="utf-8")
    assert _arrival_event("Nori", "ludex-studio", {"col": 52, "row": 54}) is False
    assert len(_lines(creature)) == 1


def test_validator_tolerates_history_but_not_habits():
    from tools.validate_creature_data import _is_legacy_arrival
    legacy = {"kind": "village_arrival", "t": 1.0, "who": "Nori"}
    assert _is_legacy_arrival(legacy), "history stands"
    # a new arrival written wrongly is still an error
    assert not _is_legacy_arrival({"kind": "village_arrival", "attributes": {}})
    assert not _is_legacy_arrival({"kind": "reflect", "t": 1.0, "who": "Nori"})


def test_validator_knows_every_label_the_engine_acts_on():
    """The heartbeat skips dormant AND retired; a validator that knows only
    one of them calls a real lifecycle state an error (Moss, 2026-08-26)."""
    from tools.validate_creature_data import SUBSTRATE_STATUSES
    assert {"dormant", "retired"} <= SUBSTRATE_STATUSES
