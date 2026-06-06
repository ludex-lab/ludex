"""Tests for ludex.core.stage — pure compute_stage tested with synthetic
scalar inputs. audit_creature (the I/O wrapper) is exercised via the
live cohort in tools/cohort_stage.py output, not unit-tested here."""

from __future__ import annotations

from ludex.core.stage import compute_stage


def _kw(**overrides):
    base = dict(
        age_days=10.0,
        session_count=1,
        memory_count=0,
        field_count=0,
        bond_count=0,
        last_session_days_ago=1.0,
        field_distill_total=0,
    )
    base.update(overrides)
    return base


def test_nascent_when_no_activity():
    r = compute_stage(**_kw(session_count=1, memory_count=0))
    assert r.name == "nascent"


def test_growing_after_first_match():
    r = compute_stage(**_kw(session_count=2, memory_count=10))
    assert r.name == "growing"


def test_active_when_multiple_fields():
    r = compute_stage(**_kw(session_count=3, memory_count=20, field_count=2))
    assert r.name == "active"


def test_active_when_first_bond():
    r = compute_stage(**_kw(session_count=2, bond_count=1))
    assert r.name == "active"


def test_veteran_by_session_count():
    r = compute_stage(**_kw(session_count=30))
    assert r.name == "veteran"


def test_veteran_by_memory_count():
    r = compute_stage(**_kw(session_count=5, memory_count=100))
    assert r.name == "veteran"


def test_veteran_by_field_depth():
    # 3 fields × 5 distill each → veteran
    r = compute_stage(**_kw(session_count=10, field_count=3, field_distill_total=15))
    assert r.name == "veteran"


def test_veteran_field_count_alone_not_enough():
    # 3 fields but only 2 distill total → not veteran by field-depth route
    r = compute_stage(**_kw(session_count=3, field_count=3, field_distill_total=2))
    # 3 fields >= 2 + bond_count 0 + session 3 < 5 — should be 'active' via field_count >= 2
    assert r.name == "active"


def test_flag_no_matches_yet_after_grace():
    r = compute_stage(**_kw(age_days=5.0, session_count=1, memory_count=0))
    assert r.name == "nascent"
    assert "no_matches_yet" in r.flags


def test_flag_no_matches_yet_skipped_in_grace():
    r = compute_stage(**_kw(age_days=1.0, session_count=1, memory_count=0))
    assert r.name == "nascent"
    assert "no_matches_yet" not in r.flags


def test_flag_stale_30d():
    r = compute_stage(**_kw(session_count=10, memory_count=20, last_session_days_ago=45.0))
    assert "stale_30d" in r.flags


def test_flag_no_stale_when_recent():
    r = compute_stage(**_kw(session_count=10, memory_count=20, last_session_days_ago=2.0))
    assert "stale_30d" not in r.flags


def test_flag_memory_outlier_high_for_active():
    # 5 sessions, 80 memories → 16/session, > 10 threshold
    r = compute_stage(**_kw(session_count=6, memory_count=80, bond_count=1))
    assert r.name == "active"
    assert "memory_outlier_high" in r.flags


def test_flag_distill_dead():
    # sessions and fields but no distill output
    r = compute_stage(**_kw(session_count=10, memory_count=20, field_count=2, field_distill_total=0))
    assert "distill_dead" in r.flags


def test_signals_round_trip():
    r = compute_stage(**_kw(session_count=15, memory_count=50, field_count=2, field_distill_total=8))
    assert r.signals["session_count"] == 15
    assert r.signals["memory_count"] == 50
    assert r.signals["field_count"] == 2
    assert r.signals["field_distill_total"] == 8


# -------------------------------------------------------------------
# audit_creature hiatus visibility (2026-05-13)
# -------------------------------------------------------------------

def _write_minimal_creature(parent, name="TestCreature"):
    """Minimal creature dir for audit_creature integration tests:
    a ludex.yaml with born_at + session_count + an empty memory dir.
    Avoids the canonical-host guard by leaving habitat.origin empty."""
    import time
    import yaml
    from pathlib import Path
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "memory").mkdir(exist_ok=True)
    (d / "bonds").mkdir(exist_ok=True)
    cfg = {
        "name": name,
        "brain": {"provider": "ollama", "model": "qwen3:8b"},
        "organs": {"engine": {"enabled": True, "required": True}},
        "habitat": {"mode": "local", "home_dir": str(d), "persistent": True},
        "born_at": time.time() - 30 * 86400,
        "session_count": 5,
    }
    (d / "ludex.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False),
                                   encoding="utf-8")
    return d


def _write_hiatus_marker(creature_dir, start: str, end: str):
    """Write a HIATUS.md frontmatter marker for the test."""
    (creature_dir / "HIATUS.md").write_text(
        f"---\nhiatus_start: {start}\nhiatus_end: {end}\n"
        f"reason: test\ndeclared_by: testharness\n---\n\n"
        "dormancy body prose.\n",
        encoding="utf-8",
    )


def test_audit_creature_no_hiatus_no_flag(tmp_path):
    from ludex.core.stage import audit_creature
    d = _write_minimal_creature(tmp_path)
    r = audit_creature(d)
    assert not any(f.startswith("hiatus") for f in r.flags)


def test_audit_creature_mid_hiatus_surfaces_dormant_flag(tmp_path):
    """Today (run-time) falls inside a 2000→2099 window."""
    from ludex.core.stage import audit_creature
    d = _write_minimal_creature(tmp_path)
    _write_hiatus_marker(d, "2000-01-01", "2099-01-01")
    r = audit_creature(d)
    flag = next((f for f in r.flags if f.startswith("hiatus:")), None)
    assert flag == "hiatus:2000-01-01→2099-01-01"
    # Wake-pending flag must not co-fire mid-window.
    assert not any(f.startswith("hiatus_wake_pending") for f in r.flags)


def test_audit_creature_post_wake_surfaces_wake_pending_flag(tmp_path):
    """Window is entirely in the past — wake has fired but marker not consumed."""
    from ludex.core.stage import audit_creature
    d = _write_minimal_creature(tmp_path)
    _write_hiatus_marker(d, "2000-01-01", "2000-01-02")
    r = audit_creature(d)
    flag = next(
        (f for f in r.flags if f.startswith("hiatus_wake_pending")), None
    )
    assert flag == "hiatus_wake_pending:2000-01-02"
    assert not any(f.startswith("hiatus:") for f in r.flags)


def test_audit_creature_pre_window_no_flag(tmp_path):
    """Window entirely in the future — pre-hiatus, no surface yet."""
    from ludex.core.stage import audit_creature
    d = _write_minimal_creature(tmp_path)
    _write_hiatus_marker(d, "2099-01-01", "2099-12-31")
    r = audit_creature(d)
    assert not any(f.startswith("hiatus") for f in r.flags)
