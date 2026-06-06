"""Tests for stage option (b) — creature-facing self-introspection.

Two layers:
- `creature_self_context(report, name)` — pure stringifier of a
  StageReport into prose suitable for the creature's own context.
- `AutoBlock.handle_self_introspect()` — port that wires it up via
  the organism's habitat home_dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ludex.core.stage import StageReport, creature_self_context


def _report(name: str, *, age=10.0, sessions=10, mem=20, fields=2, bonds=1, last=2.0, dt=10, flags=None) -> StageReport:
    return StageReport(
        name=name,
        signals={
            "age_days": age,
            "session_count": sessions,
            "memory_count": mem,
            "field_count": fields,
            "bond_count": bonds,
            "last_session_days_ago": last,
            "field_distill_total": dt,
        },
        flags=list(flags or []),
    )


def test_self_context_plain_active():
    text = creature_self_context(_report("active"), name="Hearth")
    assert "You are Hearth." in text
    assert "`active`" in text
    assert "10.0 days" in text
    assert "10 sessions" in text
    assert "20 active memories" in text
    assert "Notes:" not in text  # no flags


def test_self_context_no_name_falls_back():
    text = creature_self_context(_report("nascent"))
    assert "About yourself:" in text


def test_self_context_includes_flag_notes():
    text = creature_self_context(_report("active", flags=["distill_dead"]), name="Wick")
    assert "Notes:" in text
    assert "distill pipeline" in text


def test_self_context_foreign_host_special_framing():
    text = creature_self_context(
        _report("veteran", mem=0, fields=0, flags=["foreign_host:Mac-habitat"]),
        name="Verse",
    )
    assert "canonical state" in text
    assert "Mac-habitat" in text
    # Should NOT claim local memories
    assert "0 active memories" not in text


def test_self_context_singular_bond():
    text = creature_self_context(_report("active", bonds=1), name="X")
    assert "1 bond," in text  # singular


def test_self_context_plural_bonds():
    text = creature_self_context(_report("active", bonds=3), name="X")
    assert "3 bonds," in text  # plural


def test_self_context_recent_activity_phrase():
    text = creature_self_context(_report("active", last=0.5), name="X")
    assert "active recently" in text


def test_self_context_distant_activity_phrase():
    text = creature_self_context(_report("active", last=5.0), name="X")
    assert "5.0 days ago" in text


def test_self_context_young_creature_uses_hours():
    # < 1 day old
    text = creature_self_context(_report("nascent", age=0.5), name="Newborn")
    assert "hours old" in text


# --- AutoBlock port ---


def test_auto_self_introspect_returns_empty_when_no_home_dir(tmp_path):
    """An organism without a persistent home_dir → empty string (don't
    fabricate a stage from nothing)."""
    from ludex.core.organism_config import OrganismConfig

    cfg = OrganismConfig.from_preset(preset="minimal", name="Tmp")
    cfg.habitat.home_dir = ""  # explicit
    org = cfg.build()
    auto = org.get_block("auto") if hasattr(org, "get_block") else None
    if auto is None:
        # Some organism shapes don't expose get_block; skip gracefully
        pytest.skip("organism doesn't expose get_block")
    assert auto.handle_self_introspect() == ""


def test_auto_self_introspect_reads_creature_dir(tmp_path, monkeypatch):
    """When the organism's home_dir contains a ludex.yaml, the port
    returns a non-empty self-context string."""
    import yaml
    from ludex.core.organism_config import OrganismConfig

    # Disable canonical-host guard for this isolated test
    monkeypatch.setenv("LUDEX_HABITAT_ORIGIN_PATH", str(tmp_path / "no_origin"))
    monkeypatch.delenv("LUDEX_HABITAT_ORIGIN", raising=False)

    creature_dir = tmp_path / "Tester"
    (creature_dir / "memory").mkdir(parents=True)
    cfg = {
        "name": "Tester",
        "brain": {"provider": "ollama", "model": "qwen3.5:4b"},
        "organs": {"engine": {"enabled": True, "required": True},
                   "resilience": {"enabled": True, "required": True},
                   "auto": {"enabled": True}},
        "habitat": {
            "mode": "local",
            "home_dir": str(creature_dir),
            "persistent": True,
            "origin": "",
        },
        "born_at": 1_777_000_000.0,
        "session_count": 5,
    }
    (creature_dir / "ludex.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    config = OrganismConfig.load(str(creature_dir))
    org = config.build()
    auto = org.get_block("auto") if hasattr(org, "get_block") else None
    if auto is None:
        pytest.skip("organism doesn't expose get_block")

    text = auto.handle_self_introspect()
    assert "Tester" in text
    assert "stage" in text or "active" in text or "growing" in text or "nascent" in text or "veteran" in text
