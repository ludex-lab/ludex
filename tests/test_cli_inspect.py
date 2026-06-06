"""Tests for `python -m ludex inspect` and `... list` (FORGE wedge #2).

Construct a minimal creature directory in tmp_path with a known
ludex.yaml + memory state, then run the CLI commands and assert key
strings appear in stdout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ludex.cli import main


@pytest.fixture
def isolated_host_origin(tmp_path, monkeypatch):
    """Point the host habitat-origin marker at a controlled tmp file so
    the canonical-host guard status in `inspect` output is deterministic."""
    origin_file = tmp_path / "habitat_origin"
    origin_file.write_text("test-host", encoding="utf-8")
    monkeypatch.setenv("LUDEX_HABITAT_ORIGIN_PATH", str(origin_file))
    monkeypatch.delenv("LUDEX_HABITAT_ORIGIN", raising=False)
    return origin_file


def _write_creature(parent: Path, name: str, *, origin: str, sessions: int,
                    memories: int, born_at: float = 1_777_000_000.0) -> Path:
    creature_dir = parent / name
    (creature_dir / "memory").mkdir(parents=True)
    (creature_dir / "bonds").mkdir(parents=True)

    cfg = {
        "name": name,
        "brain": {"provider": "ollama", "model": "qwen3.5:4b"},
        "organs": {
            "engine": {"enabled": True, "required": True},
            "resilience": {"enabled": True, "required": True},
            "memory": {"enabled": True},
            "physis": {"enabled": True},
        },
        "habitat": {
            "mode": "local",
            "home_dir": ".",
            "persistent": True,
            "origin": origin,
            "machine_id": "test-machine",
        },
        "born_at": born_at,
        "session_count": sessions,
    }
    (creature_dir / "ludex.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8"
    )

    mem_file = creature_dir / "memory" / "memories.jsonl"
    with mem_file.open("w", encoding="utf-8") as f:
        for i in range(memories):
            f.write(json.dumps({
                "id": f"mem_{i:04d}",
                "content": f"memory {i}",
                "memory_type": "episodic",
                "tags": ["test"],
                "status": "active",
                "created_at": born_at + i * 60.0,
            }) + "\n")
    return creature_dir


def test_inspect_local_creature(tmp_path, capsys, isolated_host_origin, monkeypatch):
    """A creature whose origin matches the host marker → guard OK."""
    creature = _write_creature(tmp_path, "Local", origin="test-host", sessions=10, memories=20)
    monkeypatch.chdir(tmp_path)

    rc = main(["inspect", str(creature)])
    assert rc == 0

    out = capsys.readouterr().out
    assert "=== Local ===" in out
    assert "stage:" in out
    assert "ollama / qwen3.5:4b" in out
    assert "active memories:     20" in out
    assert "canonical-host guard: ✓ OK" in out


def test_inspect_foreign_host_creature(tmp_path, capsys, isolated_host_origin, monkeypatch):
    """A creature whose origin doesn't match host → MISMATCH + foreign_host flag."""
    creature = _write_creature(tmp_path, "Foreign", origin="other-host", sessions=10, memories=0)
    monkeypatch.chdir(tmp_path)

    rc = main(["inspect", str(creature)])
    assert rc == 0

    out = capsys.readouterr().out
    assert "=== Foreign ===" in out
    assert "foreign_host:other-host" in out
    assert "canonical-host guard: ✗ MISMATCH" in out


def test_inspect_resolves_creature_under_creatures_dir(tmp_path, capsys, isolated_host_origin, monkeypatch):
    """`ludex inspect Hearth` should resolve to ./creatures/Hearth/."""
    cdir = tmp_path / "creatures"
    cdir.mkdir()
    _write_creature(cdir, "Hearth", origin="test-host", sessions=5, memories=10)
    monkeypatch.chdir(tmp_path)

    rc = main(["inspect", "Hearth"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== Hearth ===" in out


def test_inspect_missing_creature_returns_2(tmp_path, capsys, isolated_host_origin, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = main(["inspect", "NonExistent"])
    assert rc == 2


def test_list_shows_cohort(tmp_path, capsys, isolated_host_origin, monkeypatch):
    cdir = tmp_path / "creatures"
    cdir.mkdir()
    _write_creature(cdir, "Alpha", origin="test-host", sessions=10, memories=20)
    _write_creature(cdir, "Beta", origin="other-host", sessions=5, memories=0)
    monkeypatch.chdir(tmp_path)

    rc = main(["list", "--dir", str(cdir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Alpha" in out
    assert "Beta" in out
    assert "stage distribution:" in out
    assert "foreign_host:other-host" in out


def test_list_empty_dir_returns_zero(tmp_path, capsys, isolated_host_origin, monkeypatch):
    cdir = tmp_path / "creatures"
    cdir.mkdir()
    monkeypatch.chdir(tmp_path)

    rc = main(["list", "--dir", str(cdir)])
    assert rc == 0
    assert "no creatures" in capsys.readouterr().out


def test_cohort_alias_matches_list(tmp_path, capsys, isolated_host_origin, monkeypatch):
    """`ludex cohort` is an alias for `list` post-tools-consolidation."""
    cdir = tmp_path / "creatures"
    cdir.mkdir()
    _write_creature(cdir, "Alpha", origin="test-host", sessions=10, memories=20)
    monkeypatch.chdir(tmp_path)

    rc = main(["cohort", "--dir", str(cdir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Alpha" in out
    assert "stage distribution:" in out


def test_audit_runs_on_creature(tmp_path, capsys, isolated_host_origin, monkeypatch):
    """`ludex audit Name` resolves under ./creatures/ and prints the
    standard memory_audit report."""
    cdir = tmp_path / "creatures"
    cdir.mkdir()
    _write_creature(cdir, "Subject", origin="test-host", sessions=10, memories=15)
    monkeypatch.chdir(tmp_path)

    rc = main(["audit", "Subject"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== memory audit: Subject ===" in out
    assert "total: 15 entries" in out
    assert "recall surface:" in out
    assert "top 20 tags:" in out


def test_audit_show_forgotten_flag(tmp_path, capsys, isolated_host_origin, monkeypatch):
    """`ludex audit --show-forgotten Name` prints the forgotten-memory
    diagnostic view."""
    cdir = tmp_path / "creatures"
    cdir.mkdir()
    _write_creature(cdir, "Subject", origin="test-host", sessions=5, memories=5)
    monkeypatch.chdir(tmp_path)

    rc = main(["audit", "Subject", "--show-forgotten"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "forgotten memories: Subject" in out


def test_audit_missing_creature_returns_2(tmp_path, capsys, isolated_host_origin, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = main(["audit", "NoSuchCreature"])
    assert rc == 2
