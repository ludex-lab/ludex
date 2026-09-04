"""Heartbeat substrate_status label tests.

Verifies the caretaker-declared brain.substrate_status lifecycle label
(substrate_change_policy: live / cost-watch / wind-down / retiring /
dormant / retired) is surfaced in the pulse result, and absent when
undeclared. Most labels are display-only, but dormant/retired must
short-circuit the pulse BEFORE OrganismConfig.load/build — building is
not side-effect-free (an ollama brain's FC-wiring probe loads the whole
model into RAM; retired Moss's gemma4 load collapsed the habitat's GUI
session on 2026-08-24 02:03).
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from ludex.core.heartbeat import pulse_creature


def _write_creature(parent: Path, name: str = "Tc",
                    substrate_status: str | None = None) -> Path:
    creature_dir = parent / name
    (creature_dir / "memory").mkdir(parents=True)
    (creature_dir / "bonds").mkdir(parents=True)
    brain = {"provider": "gemini_cli", "model": "gemini-2.5-flash"}
    if substrate_status:
        brain["substrate_status"] = substrate_status
    cfg = {
        "name": name,
        "brain": brain,
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


def test_substrate_status_surfaced_in_result(tmp_path):
    creature_dir = _write_creature(tmp_path, substrate_status="wind-down")
    result = pulse_creature(creature_dir, dry_run=True)
    assert result.get("substrate_status") == "wind-down"


def test_substrate_status_absent_when_undeclared(tmp_path):
    creature_dir = _write_creature(tmp_path)
    result = pulse_creature(creature_dir, dry_run=True)
    assert "substrate_status" not in result


def _forbid_load(monkeypatch):
    import ludex.core.organism_config as oc

    def _boom(*args, **kwargs):
        raise AssertionError(
            "dormant/retired creature must not be loaded/built")

    monkeypatch.setattr(oc.OrganismConfig, "load", _boom)


def test_dormant_skips_before_build(tmp_path, monkeypatch):
    creature_dir = _write_creature(tmp_path, substrate_status="dormant")
    _forbid_load(monkeypatch)
    result = pulse_creature(creature_dir, dry_run=True)
    assert result.get("skip") is True
    assert result.get("substrate_status") == "dormant"
    assert result.get("reason") == "substrate:dormant"


def test_retired_skips_before_build(tmp_path, monkeypatch):
    creature_dir = _write_creature(tmp_path, substrate_status="retired")
    _forbid_load(monkeypatch)
    result = pulse_creature(creature_dir, dry_run=True)
    assert result.get("skip") is True
    assert result.get("substrate_status") == "retired"


def test_retired_label_is_case_insensitive(tmp_path, monkeypatch):
    creature_dir = _write_creature(tmp_path, substrate_status="Retired")
    _forbid_load(monkeypatch)
    result = pulse_creature(creature_dir, dry_run=True)
    assert result.get("skip") is True
    assert result.get("substrate_status") == "retired"
