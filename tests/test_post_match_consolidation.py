"""Tests for ludex.core.post_match_consolidation (Phase 1).

Synthetic 3-creature Avalon fixture under tests/fixtures/m_fixture_avalon/.
Exercises: pair extraction arithmetic, phrase template, dry-run vs commit
semantics, pre-match verification, and index idempotency.

Live-write tests (--commit path) use temporary creature dirs under
pytest's tmp_path rather than modifying the real creatures/ tree.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ludex.core import game_adapters
from ludex.core.game_adapters.avalon import (
    extract_pair_summary,
    phrase_for,
)
from ludex.core.post_match_consolidation import (
    VerificationError,
    consolidate_match,
    consolidate_matches_root,
    is_processed,
    load_index,
    mark_processed,
    verify_match,
    write_index,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "m_fixture_avalon"


def _load_fixture():
    log = json.loads((FIXTURE_DIR / "log.json").read_text(encoding="utf-8"))
    state = json.loads((FIXTURE_DIR / "state.json").read_text(encoding="utf-8"))
    return log, state


# --- Avalon adapter: pair extraction arithmetic ------------------------------


def test_pair_summary_roles():
    log, state = _load_fixture()
    s = extract_pair_summary(log, state, "m_fixture", "alpha", "bravo")
    assert s.my_id == "alpha"
    assert s.other_id == "bravo"
    assert s.my_role == "good"
    assert s.their_role == "evil"
    assert s.game == "avalon"
    assert s.match_id == "m_fixture"


def test_pair_summary_shared_team_and_sabotage():
    """alpha + bravo both on Quest 1; bravo sabotaged it."""
    log, state = _load_fixture()
    s = extract_pair_summary(log, state, "m_fixture", "alpha", "bravo")
    assert s.metrics["shared_quest_teams"] == 1
    assert s.metrics["sabotages_on_shared_team"] == 1


def test_pair_summary_nominations():
    """alpha nominated bravo (Q1); charlie nominated alpha (Q2)."""
    log, state = _load_fixture()
    ab = extract_pair_summary(log, state, "m_fixture", "alpha", "bravo")
    assert ab.metrics["i_nominated_them"] == 1  # alpha → bravo on Q1
    assert ab.metrics["nominated_me"] == 0      # bravo never proposed

    ca = extract_pair_summary(log, state, "m_fixture", "charlie", "alpha")
    assert ca.metrics["i_nominated_them"] == 1  # charlie → alpha on Q2
    assert ca.metrics["nominated_me"] == 0      # alpha → bravo not me


def test_pair_summary_votes():
    """Q1 alpha/bravo/charlie all approve. Q2 alpha+charlie approve, bravo rejects."""
    log, state = _load_fixture()
    ab = extract_pair_summary(log, state, "m_fixture", "alpha", "bravo")
    assert ab.metrics["votes_agreed"] == 1       # Q1 both approve
    assert ab.metrics["votes_disagreed"] == 1    # Q2 alpha approve, bravo reject

    ac = extract_pair_summary(log, state, "m_fixture", "alpha", "charlie")
    assert ac.metrics["votes_agreed"] == 2       # both Q1 + Q2 approve
    assert ac.metrics["votes_disagreed"] == 0


def test_pair_summary_no_shared_team_when_not_both_present():
    """alpha and charlie shared only Q2; no sabotage there."""
    log, state = _load_fixture()
    ac = extract_pair_summary(log, state, "m_fixture", "alpha", "charlie")
    assert ac.metrics["shared_quest_teams"] == 1
    assert ac.metrics["sabotages_on_shared_team"] == 0


# --- Phrase template ---------------------------------------------------------


def test_phrase_includes_roles_and_shared():
    log, state = _load_fixture()
    s = extract_pair_summary(log, state, "m_fixture", "alpha", "bravo")
    phrase = phrase_for(s)
    assert "m_fixture" in phrase
    assert "I was Good" in phrase
    assert "you were Evil" in phrase
    assert "shared" in phrase.lower()
    assert "sabotage" in phrase.lower()


def test_phrase_no_shared_team_omitted():
    """When shared_quest_teams == 0, the shared-team clause is omitted."""
    # Construct synthetic summary with zero shared teams
    from ludex.core.game_adapters import PairSummary
    s = PairSummary(
        game="avalon", match_id="t", my_id="a", other_id="b",
        my_role="good", their_role="evil",
        metrics={
            "shared_quest_teams": 0, "sabotages_on_shared_team": 0,
            "nominated_me": 0, "i_nominated_them": 0,
            "votes_agreed": 3, "votes_disagreed": 1,
        },
    )
    p = phrase_for(s)
    assert "shared" not in p.lower()
    assert "votes agreed 3" in p


# --- Game adapter registry ---------------------------------------------------


def test_avalon_adapter_registered():
    adapter = game_adapters.get("avalon")
    assert hasattr(adapter, "extract_pair_summary")
    assert hasattr(adapter, "phrase_for")


def test_unknown_game_raises_with_listing():
    with pytest.raises(KeyError, match="Registered games"):
        game_adapters.get("bogus_game")


# --- Pre-match verification --------------------------------------------------


def test_verify_match_passes_with_subset():
    """Log agents ⊆ target creatures — OK."""
    verify_match("m", {"alpha", "bravo"}, {"alpha", "bravo", "charlie"})


def test_verify_match_passes_with_exact_match():
    verify_match("m", {"alpha", "bravo"}, {"alpha", "bravo"})


def test_verify_match_fails_when_log_has_unknown():
    """Log references a creature we don't have → raise."""
    with pytest.raises(VerificationError, match="unknown_agent"):
        verify_match("m", {"alpha", "unknown_agent"}, {"alpha", "bravo"})


def test_verify_match_case_insensitive():
    """Creature names in configs may be capitalized; log agent_ids lowercased.
    The verifier compares case-insensitively."""
    verify_match("m", {"alpha"}, {"Alpha"})


# --- Index persistence and idempotency ---------------------------------------


def test_index_round_trip(tmp_path):
    creature_dir = tmp_path / "TestCreature"
    creature_dir.mkdir()
    idx = load_index(creature_dir)
    assert idx == {"processed": {}}
    mark_processed(idx, "m_one", pair_count=3)
    write_index(creature_dir, idx)
    re = load_index(creature_dir)
    assert is_processed(re, "m_one")
    assert re["processed"]["m_one"]["pair_count"] == 3
    assert "ts" in re["processed"]["m_one"]


def test_index_missing_file_is_empty(tmp_path):
    creature_dir = tmp_path / "TestCreature"
    creature_dir.mkdir()
    idx = load_index(creature_dir)
    assert not is_processed(idx, "anything")


# --- End-to-end consolidate_match with synthetic creatures -------------------


def _make_synthetic_creature(parent: Path, name: str, *, origin: str = "Test-habitat"):
    """Create a minimal creature dir with ludex.json + empty bonds/."""
    d = parent / name
    d.mkdir()
    (d / "bonds").mkdir()
    (d / "ludex.json").write_text(json.dumps({
        "name": name,
        "brain": {"provider": "ollama", "model": "qwen3.5:4b"},
        "organs": {
            "engine": {"enabled": True, "required": True},
            "resilience": {"enabled": True, "required": True},
            "memory": {"enabled": False},
            "immune": {"enabled": False},
            "humoral_immune": {"enabled": False},
            "emotion": {"enabled": False},
            "tracking": {"enabled": False},
            "hooks": {"enabled": False},
        },
        "habitat": {
            "mode": "local",
            "home_dir": ".",
            "max_storage_mb": 100,
            "allow_network": False,
            "persistent": True,
            "origin": origin,
        },
    }, indent=2), encoding="utf-8")
    # SELF.md empty-born
    (d / "SELF.md").write_text(f"# {name} — Self-Understanding\n\n", encoding="utf-8")
    return d


def _copy_fixture_as(match_dir: Path) -> Path:
    """Copy the fixture into a dir named as a match_id."""
    shutil.copytree(FIXTURE_DIR, match_dir)
    return match_dir


def test_consolidate_match_dry_run_produces_phrases(tmp_path):
    creatures_dir = tmp_path / "creatures"
    creatures_dir.mkdir()
    for name in ("alpha", "bravo", "charlie"):
        _make_synthetic_creature(creatures_dir, name)
    creatures_by_name = {
        d.name.lower(): d for d in creatures_dir.iterdir() if d.is_dir()
    }
    match_dir = _copy_fixture_as(tmp_path / "m_fixture")

    reports = consolidate_match(
        match_dir, creatures_by_name, dry_run=True,
    )
    # 3 creatures × 1 match = 3 reports, all dry_run
    assert len(reports) == 3
    assert all(r.action == "dry_run" for r in reports)
    # Each has 2 pair summaries (others in the match)
    assert all(r.pair_count == 2 for r in reports)
    # No bond files written
    for name in ("alpha", "bravo", "charlie"):
        assert list((creatures_dir / name / "bonds").iterdir()) == []
    # No index written
    for name in ("alpha", "bravo", "charlie"):
        assert not (creatures_dir / name / "consolidation_index.json").exists()


def test_consolidate_match_verification_failure_blocks_all(tmp_path):
    """If the log references an unknown creature, no bonds are written
    and every report row for the match is an error."""
    creatures_dir = tmp_path / "creatures"
    creatures_dir.mkdir()
    # Only create 2 of 3 creatures in log.
    for name in ("alpha", "bravo"):
        _make_synthetic_creature(creatures_dir, name)
    creatures_by_name = {
        d.name.lower(): d for d in creatures_dir.iterdir() if d.is_dir()
    }
    match_dir = _copy_fixture_as(tmp_path / "m_fixture")

    reports = consolidate_match(
        match_dir, creatures_by_name, dry_run=True,
    )
    assert len(reports) == 1
    assert reports[0].action == "error"
    assert "charlie" in reports[0].detail


# --- Batch over matches_root + idempotency -----------------------------------


def test_consolidate_matches_root_idempotent(tmp_path):
    """Run twice in dry-run — second pass should still dry-run (no index
    writes), but with --commit and then re-run, the second pass skips."""
    creatures_dir = tmp_path / "creatures"
    creatures_dir.mkdir()
    for name in ("alpha", "bravo", "charlie"):
        _make_synthetic_creature(creatures_dir, name)
    creatures_by_name = {
        d.name.lower(): d for d in creatures_dir.iterdir() if d.is_dir()
    }
    matches_root = tmp_path / "matches"
    matches_root.mkdir()
    _copy_fixture_as(matches_root / "m_fixture")

    # Dry-run twice — both should produce 3 dry_run rows, no index state
    r1 = consolidate_matches_root(matches_root, creatures_by_name, dry_run=True)
    r2 = consolidate_matches_root(matches_root, creatures_by_name, dry_run=True)
    assert all(r.action == "dry_run" for r in r1 + r2)
