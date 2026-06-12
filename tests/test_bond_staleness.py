"""Bond staleness — caretaker observability.

Q1 framing: observation, not bug. We make absence visible without
auto-resolving.

D-024 / F1 (2026-06-12): the creature-layer writer
(maybe_record_bond_staleness) was removed — staleness memories were
observability written into the experience store. The creature now
receives staleness via the heartbeat reflect trigger string
("heartbeat:stale_bonds=[...]"); the caretaker layer (detection below)
is unchanged and bond mtime stays the single source of truth.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ludex.core.bond_staleness import (
    DEFAULT_STALE_THRESHOLD_DAYS,
    list_bonds,
    stale_bonds,
)


def _make_bond(creature_dir: Path, name: str, *, age_days: float) -> Path:
    bonds_dir = creature_dir / "bonds"
    bonds_dir.mkdir(parents=True, exist_ok=True)
    p = bonds_dir / f"{name}.md"
    p.write_text(f"# Bond with {name}\n", encoding="utf-8")
    backdate = time.time() - age_days * 86400.0
    os.utime(p, (backdate, backdate))
    return p


# --- list_bonds / stale_bonds (caretaker layer, unchanged) ---


def test_list_bonds_returns_age_days(tmp_path):
    _make_bond(tmp_path, "Alpha", age_days=2.0)
    _make_bond(tmp_path, "Beta", age_days=10.0)
    rows = list_bonds(tmp_path)
    names = {n for n, _ in rows}
    assert names == {"Alpha", "Beta"}
    # Sorted oldest-first
    assert rows[0][0] == "Beta"
    assert rows[0][1] >= 10.0
    assert rows[1][1] >= 2.0


def test_list_bonds_empty_when_no_bonds_dir(tmp_path):
    assert list_bonds(tmp_path) == []


def test_stale_bonds_filters_below_threshold(tmp_path):
    _make_bond(tmp_path, "Recent", age_days=2.0)
    _make_bond(tmp_path, "Stale", age_days=10.0)
    rows = stale_bonds(tmp_path, threshold_days=DEFAULT_STALE_THRESHOLD_DAYS)
    names = {n for n, _ in rows}
    assert names == {"Stale"}


def test_stale_bonds_custom_threshold(tmp_path):
    _make_bond(tmp_path, "Recent", age_days=2.0)
    _make_bond(tmp_path, "Older", age_days=5.0)
    # Custom threshold below "Older" age
    rows = stale_bonds(tmp_path, threshold_days=3.0)
    names = {n for n, _ in rows}
    assert names == {"Older"}


# --- heartbeat integration: detection still drives the reflect trigger,
# --- but NO memory is written (D-024/F1) ---


def test_heartbeat_detects_staleness_without_memory_write(tmp_path, monkeypatch):
    monkeypatch.setenv("LUDEX_HABITAT_ORIGIN_PATH", str(tmp_path / "no_origin"))
    import yaml
    from ludex.core.heartbeat import pulse_creature

    creature = tmp_path / "Subject"
    (creature / "memory").mkdir(parents=True)
    cfg = {
        "name": "Subject",
        "brain": {"provider": "ollama", "model": "qwen3:8b"},
        "organs": {
            "engine": {"enabled": True, "required": True},
            "resilience": {"enabled": True, "required": True},
            "memory": {"enabled": True},
        },
        "habitat": {"mode": "local", "home_dir": str(creature),
                    "persistent": True, "origin": ""},
        "born_at": time.time() - 10 * 86400,
        "session_count": 5,
    }
    (creature / "ludex.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    _make_bond(creature, "Flint", age_days=10.0)

    result = pulse_creature(creature, dry_run=True)
    # Detection unchanged — drives the reflect trigger
    assert result.get("stale_bonds") == ["Flint"]
    # Writer removed — no staleness memory key, in any mode
    assert "bond_staleness_recorded" not in result
    mems_file = creature / "memory" / "memories.jsonl"
    assert (not mems_file.exists()) or "bond_stale" not in mems_file.read_text()
