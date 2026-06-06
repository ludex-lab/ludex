"""Heartbeat model-currency integration tests (D-051 phase 2).

Verifies that pulse_creature, given a pre-resolved available_models map:
- skips the check entirely when available_models is None (default)
- classifies a stale brain as drifted, records a model_currency span,
  and triggers a reflect on the *first* detection only (idempotent)
- excludes the ollama source from recording
- leaves CURRENT brains untouched (no span, no reflect)
- dry_run reports the would-be drift without writing
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from ludex.core.heartbeat import pulse_creature
from ludex.core.store import LudexStore


def _write_creature(parent: Path, provider: str, model: str,
                    name: str = "Tc") -> Path:
    creature_dir = parent / name
    (creature_dir / "memory").mkdir(parents=True)
    (creature_dir / "bonds").mkdir(parents=True)
    cfg = {
        "name": name,
        "brain": {"provider": provider, "model": model},
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


def _no_reflect(monkeypatch):
    """Stub selfhood.reflect; return (calls, trigger-capture)."""
    state = {"calls": 0, "trigger": None}

    def fake_reflect(organism, trigger="manual", engine=None,
                     hiatus_marker=None):
        state["calls"] += 1
        state["trigger"] = trigger
        return "currency reflection text"

    import ludex.core.selfhood as selfhood_mod
    monkeypatch.setattr(selfhood_mod, "reflect", fake_reflect)
    return state


def test_no_available_models_skips_check(tmp_path):
    creature_dir = _write_creature(tmp_path, "claude_cli", "claude-opus-4-6")
    r = pulse_creature(creature_dir, dry_run=True)
    assert "model_currency" not in r
    assert "model_currency_changed" not in r.get("reflect_reasons", [])


def test_superseded_records_span_and_triggers_reflect(tmp_path, monkeypatch):
    creature_dir = _write_creature(tmp_path, "claude_cli", "claude-opus-4-6")
    state = _no_reflect(monkeypatch)
    avail = {"anthropic": ["claude-opus-4-8", "claude-sonnet-4-6"]}

    r = pulse_creature(creature_dir, dry_run=False, available_models=avail)

    assert r["model_currency"]["status"] == "DEPRECATED"  # 4-6 gone, 4-8 lives
    assert r["model_currency"]["successor"] == "claude-opus-4-8"
    assert r["model_currency"]["recorded"] is True
    assert "model_currency_changed" in r["reflect_reasons"]
    assert state["calls"] == 1
    assert "model_currency_changed" in state["trigger"]

    spans = LudexStore(str(creature_dir)).spans(kind="model_currency")
    assert len(spans) == 1
    assert spans[-1]["attributes"]["status"] == "DEPRECATED"


def test_drift_is_idempotent_across_pulses(tmp_path, monkeypatch):
    creature_dir = _write_creature(tmp_path, "claude_cli", "claude-opus-4-6")
    state = _no_reflect(monkeypatch)
    avail = {"anthropic": ["claude-opus-4-8"]}

    r1 = pulse_creature(creature_dir, dry_run=False, available_models=avail)
    assert r1["model_currency"]["recorded"] is True
    assert state["calls"] == 1

    # Same standing fact next pulse → no new span, no new reflect trigger.
    r2 = pulse_creature(creature_dir, dry_run=False, available_models=avail)
    assert r2["model_currency"]["recorded"] is False
    assert "model_currency_changed" not in r2["reflect_reasons"]
    assert state["calls"] == 1
    assert len(LudexStore(str(creature_dir)).spans(kind="model_currency")) == 1


def test_current_model_no_record_no_reflect(tmp_path, monkeypatch):
    creature_dir = _write_creature(tmp_path, "claude_cli", "claude-opus-4-8")
    state = _no_reflect(monkeypatch)
    avail = {"anthropic": ["claude-opus-4-8"]}

    r = pulse_creature(creature_dir, dry_run=False, available_models=avail)
    assert r["model_currency"]["status"] == "CURRENT"
    assert "recorded" not in r["model_currency"]
    assert "model_currency_changed" not in r.get("reflect_reasons", [])
    assert state["calls"] == 0
    assert LudexStore(str(creature_dir)).spans(kind="model_currency") == []


def test_ollama_source_not_recorded(tmp_path):
    creature_dir = _write_creature(tmp_path, "ollama", "gemma:7b")
    # Pretend the local pull list lacks the model — must NOT be flagged.
    avail = {"ollama": ["qwen3:8b"]}
    r = pulse_creature(creature_dir, dry_run=False, available_models=avail)
    # source for ollama is "ollama" → recording branch skipped.
    assert "recorded" not in r.get("model_currency", {})
    assert "model_currency_changed" not in r.get("reflect_reasons", [])
    assert LudexStore(str(creature_dir)).spans(kind="model_currency") == []


def test_dry_run_reports_but_does_not_write(tmp_path):
    creature_dir = _write_creature(tmp_path, "claude_cli", "claude-opus-4-6")
    avail = {"anthropic": ["claude-opus-4-8"]}
    r = pulse_creature(creature_dir, dry_run=True, available_models=avail)
    assert r["model_currency"]["status"] == "DEPRECATED"
    assert r["model_currency"]["recorded"] is True  # would record
    assert "model_currency_changed" in r["reflect_reasons"]
    # But nothing was actually written.
    assert LudexStore(str(creature_dir)).spans(kind="model_currency") == []
