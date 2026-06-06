"""Bond staleness — caretaker observability + creature-internal substrate.

Q1 framing: observation, not bug. We make absence visible without
auto-resolving.
Q2 framing: mixed layers. Caretaker reads bond mtime; creature gets a
self-noticing memory. Same source of truth (bond mtime).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from ludex.blocks.memory import MemoryBlock
from ludex.core.bond_staleness import (
    DEFAULT_STALE_THRESHOLD_DAYS,
    list_bonds,
    maybe_record_bond_staleness,
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


# --- list_bonds / stale_bonds ---


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


# --- maybe_record_bond_staleness ---


def test_records_one_memory_per_stale_bond(tmp_path):
    _make_bond(tmp_path, "Flint", age_days=8.0)
    mem = MemoryBlock(storage_dir=str(tmp_path / "memory"))
    written = maybe_record_bond_staleness(mem, tmp_path, ["Flint"])
    assert written == ["Flint"]
    # Memory exists with the right shape
    matches = [m for m in mem._memories.values() if "bond_stale:Flint" in (m.tags or [])]
    assert len(matches) == 1
    m = matches[0]
    assert m.memory_type == "episodic"
    assert "Flint" in m.content
    assert "bond_staleness" in m.tags
    assert m.source.startswith("bond_staleness/Flint")


def test_idempotent_within_same_stale_period(tmp_path):
    """Re-pulse during the same stale period must not re-write."""
    _make_bond(tmp_path, "Flint", age_days=8.0)
    mem = MemoryBlock(storage_dir=str(tmp_path / "memory"))
    first = maybe_record_bond_staleness(mem, tmp_path, ["Flint"])
    second = maybe_record_bond_staleness(mem, tmp_path, ["Flint"])
    assert first == ["Flint"]
    assert second == []
    matches = [m for m in mem._memories.values() if "bond_stale:Flint" in (m.tags or [])]
    assert len(matches) == 1


def test_re_engagement_resets_eligibility(tmp_path):
    """When the bond is updated (mtime advances past the alert), the
    next stale period gets a fresh alert."""
    bond = _make_bond(tmp_path, "Flint", age_days=8.0)
    mem = MemoryBlock(storage_dir=str(tmp_path / "memory"))
    first = maybe_record_bond_staleness(mem, tmp_path, ["Flint"])
    assert first == ["Flint"]

    # Re-engage — bond mtime jumps to "now"
    os.utime(bond, None)

    # Now backdate again to simulate going stale a second time
    new_stale_age = 9.0
    backdate = time.time() - new_stale_age * 86400.0
    os.utime(bond, (backdate, backdate))

    second = maybe_record_bond_staleness(mem, tmp_path, ["Flint"])
    # Second alert allowed because no existing alert is created_at > new bond mtime
    assert second == ["Flint"]
    # And we have two alerts on disk
    matches = [m for m in mem._memories.values() if "bond_stale:Flint" in (m.tags or [])]
    assert len(matches) == 2


def test_handles_missing_bond_file_gracefully(tmp_path):
    """A name passed in stale_names but no bond file → silently skipped."""
    (tmp_path / "bonds").mkdir()  # empty bonds dir
    mem = MemoryBlock(storage_dir=str(tmp_path / "memory"))
    written = maybe_record_bond_staleness(mem, tmp_path, ["Ghost"])
    assert written == []


def test_no_op_when_no_bonds_dir(tmp_path):
    mem = MemoryBlock(storage_dir=str(tmp_path / "memory"))
    written = maybe_record_bond_staleness(mem, tmp_path, ["Anyone"])
    assert written == []


def test_no_op_when_memory_block_is_none(tmp_path):
    _make_bond(tmp_path, "Flint", age_days=8.0)
    written = maybe_record_bond_staleness(None, tmp_path, ["Flint"])
    assert written == []


# --- heartbeat integration ---


def test_heartbeat_writes_bond_staleness_memory(tmp_path, monkeypatch):
    """End-to-end: pulse_creature on a creature with stale bonds writes
    one memory per stale bond, via maybe_record_bond_staleness."""
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

    result = pulse_creature(creature, dry_run=False)
    assert result.get("bond_staleness_recorded") == ["Flint"]


def test_heartbeat_dry_run_does_not_record(tmp_path, monkeypatch):
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
    assert "bond_staleness_recorded" not in result
