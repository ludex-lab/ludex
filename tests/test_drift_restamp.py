"""The drift re-stamp must adjudicate a mismatch, not assume the worst.

Version equality alone cannot separate a mid-battery upgrade from one that
landed afterwards, and it quietly assumes the re-stamp runs promptly. Physics E2
showed the failure: haiku read 2.1.220 at the gate and 2.1.221 a day later, and
on equality alone that quarantines sixteen runs which had in fact finished
fifteen hours before the upgrade.

So the rule is: mismatch triggers a window check, and whichever way it resolves,
the decision carries the basis it was made on. Absence of window evidence
quarantines — not knowing is not the same as knowing it was clean.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ludex.core import canary as C                          # noqa: E402

HOUR = 3600.0
LAST_RUN = 1_000_000.0


def gates(version="1.0.0"):
    return {"agy": {"provider": "agy_cli", "cli_version": version}}


def test_matching_versions_are_clean_and_uninterrogated(monkeypatch):
    monkeypatch.setattr(C, "_cli_version", lambda p: "1.0.0")
    monkeypatch.setattr(C, "_cli_install_time",
                        lambda p: (_ for _ in ()).throw(
                            AssertionError("must not probe install time on a match")))
    got = C.restamp(gates(), last_run_at=LAST_RUN)
    assert got["clean"] and got["drift"]["agy"]["match"]
    assert not got["quarantined_lineages"]


def test_upgrade_after_the_last_run_is_not_a_quarantine(monkeypatch):
    monkeypatch.setattr(C, "_cli_version", lambda p: "1.0.1")
    monkeypatch.setattr(C, "_cli_install_time", lambda p: LAST_RUN + 15.5 * HOUR)
    got = C.restamp(gates(), last_run_at=LAST_RUN)
    assert got["clean"]
    assert got["drift"]["agy"]["window"] == "after"
    assert got["drift"]["agy"]["gap_hours"] == 15.5
    assert "postdates" in got["quarantine_basis"]["agy"]


def test_upgrade_inside_the_battery_window_quarantines(monkeypatch):
    monkeypatch.setattr(C, "_cli_version", lambda p: "1.0.1")
    monkeypatch.setattr(C, "_cli_install_time", lambda p: LAST_RUN - HOUR)
    got = C.restamp(gates(), last_run_at=LAST_RUN)
    assert not got["clean"]
    assert got["quarantined_lineages"] == ["agy"]
    assert got["drift"]["agy"]["window"] == "during-or-before"


def test_missing_window_evidence_quarantines_rather_than_assuming_clean(monkeypatch):
    monkeypatch.setattr(C, "_cli_version", lambda p: "1.0.1")
    monkeypatch.setattr(C, "_cli_install_time", lambda p: None)
    got = C.restamp(gates(), last_run_at=LAST_RUN)
    assert not got["clean"]
    assert got["drift"]["agy"]["window"] == "unknown"
    assert "not evidence" in got["quarantine_basis"]["agy"]


def test_every_quarantine_decision_states_its_basis(monkeypatch):
    """A bare number invites being read as a version match that never happened."""
    monkeypatch.setattr(C, "_cli_version", lambda p: "1.0.1")
    for install, expected_clean in ((LAST_RUN + HOUR, True),
                                    (LAST_RUN - HOUR, False),
                                    (None, False)):
        monkeypatch.setattr(C, "_cli_install_time", lambda p, i=install: i)
        got = C.restamp(gates(), last_run_at=LAST_RUN)
        assert got["clean"] is expected_clean
        assert got["quarantine_basis"]["agy"], "decision recorded without a basis"


def test_lineages_are_adjudicated_independently(monkeypatch):
    two = {"agy": {"provider": "agy_cli", "cli_version": "1.0.0"},
           "haiku": {"provider": "claude_cli", "cli_version": "2.0.0"}}
    monkeypatch.setattr(C, "_cli_version",
                        lambda p: "1.0.0" if p == "agy_cli" else "2.0.1")
    monkeypatch.setattr(C, "_cli_install_time", lambda p: LAST_RUN - HOUR)
    got = C.restamp(two, last_run_at=LAST_RUN)
    assert got["drift"]["agy"]["match"] and "agy" not in got["quarantined_lineages"]
    assert got["quarantined_lineages"] == ["haiku"]
