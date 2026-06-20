"""Heartbeat hiatus integration tests.

Verifies that pulse_creature:
- ignores pre-window hiatus markers (declared but not yet active)
- surfaces post-window hiatus markers in the result dict
- forces a reflect trigger when post-window
- consumes the marker only on successful reflect (text truthy)
- preserves the marker when reflect returns empty (re-fire next pulse)
- noops with dry_run (no consume even when post-window)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml

from ludex.core.heartbeat import pulse_creature


def _write_creature(parent: Path, name: str = "TestCreature") -> Path:
    creature_dir = parent / name
    (creature_dir / "memory").mkdir(parents=True)
    (creature_dir / "bonds").mkdir(parents=True)
    cfg = {
        "name": name,
        "brain": {"provider": "ollama", "model": "qwen3:8b"},
        "organs": {
            "engine": {"enabled": True, "required": True},
            "resilience": {"enabled": True, "required": True},
            "memory": {"enabled": True},
        },
        "habitat": {
            "mode": "local",
            "home_dir": str(creature_dir),
            "persistent": True,
            "origin": "",
        },
        "born_at": time.time() - 30 * 86400,
        "session_count": 5,
    }
    (creature_dir / "ludex.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8"
    )
    return creature_dir


def _write_hiatus(creature_dir: Path, start: str, end: str) -> Path:
    p = creature_dir / "HIATUS.md"
    p.write_text(
        f"---\n"
        f"hiatus_start: {start}\n"
        f"hiatus_end: {end}\n"
        f"reason: caretaker_traveled\n"
        f"declared_by: JJ\n"
        f"---\n\n"
        f"You were dormant {start} → {end}. The cohort was effectively asleep.\n",
        encoding="utf-8",
    )
    return p


def test_no_hiatus_marker_noop(tmp_path):
    creature_dir = _write_creature(tmp_path)
    r = pulse_creature(creature_dir, dry_run=True)
    assert "hiatus" not in r
    assert "post_hiatus" not in r.get("reflect_reasons", [])


def test_pre_window_hiatus_noop(tmp_path):
    creature_dir = _write_creature(tmp_path)
    _write_hiatus(creature_dir, "2099-01-01", "2099-12-31")
    r = pulse_creature(creature_dir, dry_run=True)
    assert "hiatus" not in r
    assert "post_hiatus" not in r.get("reflect_reasons", [])
    # File still present, not consumed.
    assert (creature_dir / "HIATUS.md").exists()
    assert not (creature_dir / "HIATUS.consumed.md").exists()


def test_post_window_hiatus_surfaces_in_result(tmp_path):
    creature_dir = _write_creature(tmp_path)
    _write_hiatus(creature_dir, "2000-01-01", "2000-01-02")
    r = pulse_creature(creature_dir, dry_run=True)
    assert "hiatus" in r
    assert r["hiatus"]["window"] == "2000-01-01 → 2000-01-02"
    assert r["should_reflect"] is True
    assert "post_hiatus" in r["reflect_reasons"]


def test_post_window_dry_run_does_not_consume(tmp_path):
    creature_dir = _write_creature(tmp_path)
    _write_hiatus(creature_dir, "2000-01-01", "2000-01-02")
    pulse_creature(creature_dir, dry_run=True)
    # Dry-run never calls reflect; marker must remain active.
    assert (creature_dir / "HIATUS.md").exists()
    assert not (creature_dir / "HIATUS.consumed.md").exists()


def test_post_window_live_consume_on_reflect_success(tmp_path, monkeypatch):
    creature_dir = _write_creature(tmp_path)
    _write_hiatus(creature_dir, "2000-01-01", "2000-01-02")

    captured = {}

    def fake_reflect(organism, trigger="manual", engine=None,
                     hiatus_marker=None):
        captured["trigger"] = trigger
        captured["hiatus_marker"] = hiatus_marker
        return "I notice I was dormant for one day; the world resumed."

    import ludex.core.selfhood as selfhood_mod
    monkeypatch.setattr(selfhood_mod, "reflect", fake_reflect)

    r = pulse_creature(creature_dir, dry_run=False)

    # reflect was called with the marker
    assert captured["hiatus_marker"] is not None
    assert "post_hiatus" in captured["trigger"]

    # marker was consumed
    assert not (creature_dir / "HIATUS.md").exists()
    assert (creature_dir / "HIATUS.consumed.md").exists()
    assert r.get("hiatus_consumed") == "HIATUS.consumed.md"


def test_post_window_live_preserve_on_reflect_failure(tmp_path, monkeypatch):
    creature_dir = _write_creature(tmp_path)
    _write_hiatus(creature_dir, "2000-01-01", "2000-01-02")

    def fake_reflect(organism, trigger="manual", engine=None,
                     hiatus_marker=None):
        return ""  # brain unavailable / quota — empty result

    import ludex.core.selfhood as selfhood_mod
    monkeypatch.setattr(selfhood_mod, "reflect", fake_reflect)

    pulse_creature(creature_dir, dry_run=False)

    # Reflect returned empty → marker preserved for next pulse retry.
    assert (creature_dir / "HIATUS.md").exists()
    assert not (creature_dir / "HIATUS.consumed.md").exists()


def test_post_window_subsequent_pulse_after_consume_is_noop(
    tmp_path, monkeypatch
):
    creature_dir = _write_creature(tmp_path)
    _write_hiatus(creature_dir, "2000-01-01", "2000-01-02")

    call_count = {"n": 0}

    def fake_reflect(organism, trigger="manual", engine=None,
                     hiatus_marker=None):
        call_count["n"] += 1
        return "reflection text"

    import ludex.core.selfhood as selfhood_mod
    monkeypatch.setattr(selfhood_mod, "reflect", fake_reflect)

    # First pulse: consumes the marker.
    r1 = pulse_creature(creature_dir, dry_run=False)
    assert "hiatus" in r1
    assert r1.get("hiatus_consumed") == "HIATUS.consumed.md"

    # Second pulse: no active marker, hiatus path inert.
    r2 = pulse_creature(creature_dir, dry_run=False)
    assert "hiatus" not in r2
    assert "post_hiatus" not in r2.get("reflect_reasons", [])


# -------------------------------------------------------------------
# Mid-hiatus short-circuit (2026-05-13 — protects longitudinal-research
# measurement on first post-wake reflection from accidental
# mid-hiatus contamination)
# -------------------------------------------------------------------

def test_mid_hiatus_pulse_short_circuits(tmp_path, monkeypatch):
    """A pulse during the declared hiatus window must noop: outcome
    is "in_hiatus", reflect is never called, no bond-staleness memory
    write fires. Window straddles today (2000-01-01 → 2099-01-01)
    to keep the test deterministic across calendar drift within the
    next ~70 years."""
    creature_dir = _write_creature(tmp_path)
    _write_hiatus(creature_dir, "2000-01-01", "2099-01-01")

    reflect_calls = {"n": 0}

    def fake_reflect(organism, trigger="manual", engine=None,
                     hiatus_marker=None):
        reflect_calls["n"] += 1
        return "should not be called"

    import ludex.core.selfhood as selfhood_mod
    monkeypatch.setattr(selfhood_mod, "reflect", fake_reflect)

    r = pulse_creature(creature_dir, dry_run=False)

    assert r["outcome"] == "in_hiatus"
    assert "in_hiatus" in r
    assert r["in_hiatus"]["window"] == "2000-01-01 → 2099-01-01"
    # Critical: no reflect during mid-hiatus pulse.
    assert reflect_calls["n"] == 0
    # Marker stays in place — wake-up condition has not fired yet.
    assert (creature_dir / "HIATUS.md").exists()
    assert not (creature_dir / "HIATUS.consumed.md").exists()
    # Stale-bond memory write must not have happened either.
    assert "bond_staleness_recorded" not in r


def test_reflect_empty_distinct_outcome_from_maintenance_ran(tmp_path, monkeypatch):
    """When reflect runs cleanly (no exception) but returns empty
    text — e.g. the engine fell through silently, or ollama timed
    out — the outcome must be 'reflect_empty', not 'maintenance_ran'.
    Real instance: Moss reflect on 2026-05-14 took 184s through
    ollama and returned an empty string; the SELF.md was not updated
    but the previous reporting labeled it 'maintenance_ran', hiding
    the no-update fact from caretaker tooling."""
    creature_dir = _write_creature(tmp_path)
    # Trigger reflect via a post-window hiatus marker (a kept metabolism-only
    # trigger; stale-bonds no longer auto-triggers as of 2026-06-20).
    _write_hiatus(creature_dir, "2000-01-01", "2001-01-01")

    def empty_reflect(organism, trigger="manual", engine=None,
                      hiatus_marker=None):
        return ""  # engine clean-return-no-text case

    import ludex.core.selfhood as selfhood_mod
    monkeypatch.setattr(selfhood_mod, "reflect", empty_reflect)

    r = pulse_creature(creature_dir, dry_run=False)
    assert r["reflected"] is False
    assert r["reflect_len"] == 0
    assert r["outcome"] == "reflect_empty"


def test_reflect_with_text_keeps_maintenance_ran_outcome(tmp_path, monkeypatch):
    """Sanity: when reflect returns truthy text, outcome stays
    'maintenance_ran' — the empty-text distinction does not regress
    the productive path."""
    creature_dir = _write_creature(tmp_path)
    # Trigger reflect via a post-window hiatus marker (a kept metabolism-only
    # trigger; stale-bonds no longer auto-triggers as of 2026-06-20).
    _write_hiatus(creature_dir, "2000-01-01", "2001-01-01")

    def good_reflect(organism, trigger="manual", engine=None,
                     hiatus_marker=None):
        return "some reflection text"

    import ludex.core.selfhood as selfhood_mod
    monkeypatch.setattr(selfhood_mod, "reflect", good_reflect)

    r = pulse_creature(creature_dir, dry_run=False)
    assert r["reflected"] is True
    assert r["reflect_len"] > 0
    assert r["outcome"] == "maintenance_ran"


def test_metabolism_only_drops_churn_triggers(tmp_path, monkeypatch):
    """Metabolism-only (2026-06-20): a stale bond no longer AUTO-triggers reflect on the
    autonomous heartbeat — that per-pulse churn moved to the deliberate caretaker pass.
    The staleness is still DETECTED and recorded (the signal survives); it just doesn't
    fire a brain call. (Same applies to low health_grade.)"""
    creature_dir = _write_creature(tmp_path)
    bond_path = creature_dir / "bonds" / "someone.md"
    bond_path.write_text("hi\n", encoding="utf-8")
    import os
    old = time.time() - 60 * 86400
    os.utime(bond_path, (old, old))   # stale bond — formerly a reflect trigger

    called = {"reflect": False}

    def spy_reflect(organism, trigger="manual", engine=None, hiatus_marker=None):
        called["reflect"] = True
        return "x"

    import ludex.core.selfhood as selfhood_mod
    monkeypatch.setattr(selfhood_mod, "reflect", spy_reflect)

    r = pulse_creature(creature_dir, dry_run=False)
    assert r.get("stale_bonds")                 # detection intact
    assert called["reflect"] is False           # but no autonomous reflect fired
    assert "stale_bonds" not in (r.get("reflect_reasons") or [])


def test_mid_hiatus_pulse_skips_health_and_consolidation(tmp_path, monkeypatch):
    """The short-circuit must be early enough that health_check,
    consolidation, and forgetting-pass downstream side effects
    do not run during mid-hiatus. The result dict therefore does
    not carry those keys when the creature is dormant."""
    creature_dir = _write_creature(tmp_path)
    _write_hiatus(creature_dir, "2000-01-01", "2099-01-01")

    def fake_reflect(organism, trigger="manual", engine=None,
                     hiatus_marker=None):
        return "should not be called"

    import ludex.core.selfhood as selfhood_mod
    monkeypatch.setattr(selfhood_mod, "reflect", fake_reflect)

    r = pulse_creature(creature_dir, dry_run=False)

    # Confirm none of the post-fatigue / pre-reflect work executed.
    assert "health_grade" not in r
    assert "consolidated" not in r
    assert "forgetting_pass" not in r
    assert "stale_bonds" not in r
    assert "should_reflect" not in r
