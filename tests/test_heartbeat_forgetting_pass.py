"""D-071 pillar 4c — heartbeat fires run_forgetting_pass.

Closes the optional last piece: pillar 4b's method now has an
automatic caller. Cheap, idempotent, no-LLM. Tests:
- pulse_creature does NOT fire forgetting on dry-run
- pulse_creature fires when over target (provider/model dispatch)
- pulse_creature is a no-op when under target (no key in result)
- forgetting_error captured if the call raises
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml

from ludex.core.heartbeat import pulse_creature


def _write_creature(parent: Path, name: str, *, sessions: int = 5,
                    memories: int = 0, brain_provider: str = "ollama",
                    brain_model: str = "qwen3:8b") -> Path:
    """Build a minimal creature with N memories that look forgettable
    (old, low importance, recall_count 0)."""
    creature_dir = parent / name
    (creature_dir / "memory").mkdir(parents=True)
    (creature_dir / "bonds").mkdir(parents=True)
    cfg = {
        "name": name,
        "brain": {"provider": brain_provider, "model": brain_model},
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
        "born_at": time.time() - 30 * 86400,  # 30 days old
        "session_count": sessions,
    }
    (creature_dir / "ludex.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8"
    )
    if memories:
        # Old-low-importance entries — high forget score
        old_ts = time.time() - 60 * 86400  # 60 days old
        with (creature_dir / "memory" / "memories.jsonl").open("w", encoding="utf-8") as f:
            for i in range(memories):
                f.write(json.dumps({
                    "id": f"mem_{i:05d}",
                    "content": f"old wilderness tick {i}",
                    "memory_type": "episodic",
                    "tags": ["wilderness"],
                    "importance": 0.3,
                    "created_at": old_ts + i,
                    "status": "active",
                    "recall_count": 0,
                }) + "\n")
    return creature_dir


def test_dry_run_does_not_fire_forgetting(tmp_path, monkeypatch):
    """Forgetting pass mutates state — dry-run must skip it."""
    monkeypatch.setenv("LUDEX_HABITAT_ORIGIN_PATH", str(tmp_path / "no_origin"))
    creature = _write_creature(tmp_path, "Subject", memories=10)
    result = pulse_creature(creature, dry_run=True)
    assert "forgetting_pass" not in result
    assert "forgetting_error" not in result


def test_under_target_is_silent(tmp_path, monkeypatch):
    """When recall surface is well under disk target (slm 2000), the
    forgetting pass returns 0 forgotten and no key shows up in the
    result dict."""
    monkeypatch.setenv("LUDEX_HABITAT_ORIGIN_PATH", str(tmp_path / "no_origin"))
    creature = _write_creature(tmp_path, "Small", memories=20)
    result = pulse_creature(creature, dry_run=False)
    assert "forgetting_pass" not in result, result.get("forgetting_pass")
    assert "forgetting_error" not in result


def test_over_target_fires_forgetting(tmp_path, monkeypatch):
    """When recall surface exceeds target, the forgetting_pass key
    appears with forgotten count + remaining."""
    monkeypatch.setenv("LUDEX_HABITAT_ORIGIN_PATH", str(tmp_path / "no_origin"))
    # Use a small forced target by patching get_disk_target to return 5
    # for slm so we can trigger the trim with only ~10 memories.
    from ludex.core import consolidation as _con

    original = _con.get_disk_target
    monkeypatch.setattr(
        _con, "get_disk_target",
        lambda provider, model="": 5 if provider == "ollama" else original(provider, model),
    )
    creature = _write_creature(tmp_path, "Big", memories=15)
    result = pulse_creature(creature, dry_run=False)
    fp = result.get("forgetting_pass")
    assert fp is not None, f"expected forgetting_pass, got {result}"
    assert fp["target"] == 5
    assert fp["forgotten"] >= 10
    assert fp["recallable_after"] <= 5


def test_forgetting_error_captured(tmp_path, monkeypatch):
    """If run_forgetting_pass raises, the error is captured under
    forgetting_error and the heartbeat continues."""
    monkeypatch.setenv("LUDEX_HABITAT_ORIGIN_PATH", str(tmp_path / "no_origin"))
    creature = _write_creature(tmp_path, "Boom", memories=5)

    from ludex.blocks.memory import MemoryBlock

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic forget failure")

    monkeypatch.setattr(MemoryBlock, "run_forgetting_pass", boom)
    result = pulse_creature(creature, dry_run=False)
    assert "forgetting_error" in result
    assert "synthetic" in result["forgetting_error"]
    # Heartbeat completed
    assert "outcome" in result
