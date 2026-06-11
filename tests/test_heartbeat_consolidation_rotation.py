"""D-085 heartbeat wiring tests — consolidation rotation (max one per run)."""
from __future__ import annotations

from pathlib import Path

import ludex.core.consolidation as consolidation_mod
from ludex.core.heartbeat import _consolidation_rotation


class _Decision:
    def __init__(self, fire: bool, last: float | None = None,
                 events: int = 40, days: float = 20.0):
        self.fire = fire
        self.last_consolidated_on = last
        self.new_events = events
        self.days_elapsed = days


def _stub(monkeypatch, decisions: dict[str, _Decision], ran: list[str]):
    monkeypatch.setattr(
        consolidation_mod, "should_consolidate",
        lambda d, **kw: decisions[Path(d).name])
    monkeypatch.setattr(
        consolidation_mod, "consolidate",
        lambda d, dry_run=False, **kw: (
            ran.append(Path(d).name) or
            {"name": Path(d).name, "outcome": "would_consolidate" if dry_run
             else "consolidated", "reflection_file": "reflections/x.md",
             "new_events": 40}))


def test_picks_only_longest_starved(tmp_path, monkeypatch):
    ran: list[str] = []
    _stub(monkeypatch, {
        "A": _Decision(True, last=2000.0),
        "B": _Decision(True, last=1000.0),   # older → wins among consolidated
        "C": _Decision(False),
    }, ran)
    pulsed = [(tmp_path / n, {"outcome": "healthy"}) for n in ("A", "B", "C")]
    out = _consolidation_rotation(pulsed)
    assert ran == ["B"]
    assert out["outcome"] == "consolidated"
    assert pulsed[1][1]["consolidation"]["name"] == "B"
    assert pulsed[1][1]["consolidation_queue"] == 2


def test_never_consolidated_beats_oldest(tmp_path, monkeypatch):
    ran: list[str] = []
    _stub(monkeypatch, {
        "A": _Decision(True, last=1000.0),
        "B": _Decision(True, last=None),     # never consolidated → first
    }, ran)
    pulsed = [(tmp_path / n, {"outcome": "healthy"}) for n in ("A", "B")]
    _consolidation_rotation(pulsed)
    assert ran == ["B"]


def test_skips_resting_hiatus_and_dormant(tmp_path, monkeypatch):
    ran: list[str] = []
    _stub(monkeypatch, {"A": _Decision(True), "B": _Decision(True),
                        "C": _Decision(True), "D": _Decision(True)}, ran)
    pulsed = [
        (tmp_path / "A", {"outcome": "resting"}),
        (tmp_path / "B", {"outcome": "in_hiatus"}),
        (tmp_path / "C", {"outcome": "healthy", "substrate_status": "dormant"}),
        (tmp_path / "D", {"outcome": "healthy"}),
    ]
    _consolidation_rotation(pulsed)
    assert ran == ["D"]


def test_no_candidates_returns_none(tmp_path, monkeypatch):
    ran: list[str] = []
    _stub(monkeypatch, {"A": _Decision(False)}, ran)
    out = _consolidation_rotation([(tmp_path / "A", {"outcome": "healthy"})])
    assert out is None
    assert ran == []


def test_dry_run_passthrough(tmp_path, monkeypatch):
    ran: list[str] = []
    _stub(monkeypatch, {"A": _Decision(True)}, ran)
    pulsed = [(tmp_path / "A", {"outcome": "healthy"})]
    out = _consolidation_rotation(pulsed, dry_run=True)
    assert out["outcome"] == "would_consolidate"
