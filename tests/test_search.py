"""Tests for ludex.search FTS5 index + indexer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ludex.search.indexer import (
    SCANNERS,
    _slug,
    reindex,
    scan_design_log,
    scan_joint_spec,
    scan_journal,
    scan_sub_system,
)
from ludex.search.store import SearchStore


# --- Slug helper -------------------------------------------------------------

def test_slug_basic():
    assert _slug("D-044: Narrative identity") == "d-044-narrative-identity"
    assert _slug("§C.4.1 M3-full result") == "c-4-1-m3-full-result"
    assert _slug("   hello   world   ") == "hello-world"


def test_slug_unicode_stripped():
    # Unicode non-word chars collapse to hyphens
    assert _slug("바람 wind bond") == "wind-bond"


# --- Store round-trip --------------------------------------------------------

def test_store_insert_and_search(tmp_path):
    db = tmp_path / "test.db"
    rows = [
        {
            "source": "test", "path": "a.md", "anchor": "one",
            "title": "First document", "lineno": 1,
            "body": "the quick brown fox jumps over the lazy dog",
        },
        {
            "source": "test", "path": "b.md", "anchor": "two",
            "title": "Second document", "lineno": 1,
            "body": "never jump over a sleeping bond partner",
        },
    ]
    with SearchStore(db) as store:
        stats = store.bulk_replace(rows)
        assert stats.rows_inserted == 2
        # FTS5 default tokenizer does not stem — use prefix to match
        # both "jumps" and "jump".
        hits = store.search("jump*")
        assert len(hits) == 2


def test_store_search_phrase(tmp_path):
    db = tmp_path / "test.db"
    rows = [
        {"source": "test", "path": "a.md", "anchor": "one",
         "title": "T1", "lineno": 1,
         "body": "register context fitness is the metric we test"},
        {"source": "test", "path": "b.md", "anchor": "two",
         "title": "T2", "lineno": 1,
         "body": "the register has a context separately from fitness"},
    ]
    with SearchStore(db) as store:
        store.bulk_replace(rows)
        exact = store.search('"register context fitness"')
        assert len(exact) == 1
        assert exact[0].path == "a.md"
        either = store.search("register fitness")
        # AND default on FTS5 MATCH with multiple terms
        assert len(either) >= 1


def test_store_replaces_on_conflict(tmp_path):
    db = tmp_path / "test.db"
    row = {
        "source": "test", "path": "a.md", "anchor": "one",
        "title": "Old title", "lineno": 1, "body": "old body",
    }
    with SearchStore(db) as store:
        store.bulk_replace([row])
        row_updated = {**row, "title": "New title", "body": "new body"}
        store.bulk_replace([row_updated])
        hits = store.search("new")
        assert len(hits) == 1
        assert hits[0].title == "New title"
        old = store.search("old")
        assert len(old) == 0


def test_store_delete_source(tmp_path):
    db = tmp_path / "test.db"
    a = {"source": "s_a", "path": "x.md", "anchor": "1", "title": "A",
         "lineno": 1, "body": "alpha content"}
    b = {"source": "s_b", "path": "y.md", "anchor": "1", "title": "B",
         "lineno": 1, "body": "beta content"}
    with SearchStore(db) as store:
        store.bulk_replace([a, b])
        assert store.list_sources() == {"s_a": 1, "s_b": 1}
        # Re-insert s_a only, with delete_source — s_b untouched
        store.bulk_replace([a], delete_source="s_a")
        assert store.list_sources() == {"s_a": 1, "s_b": 1}


def test_store_filter_by_source(tmp_path):
    db = tmp_path / "test.db"
    rows = [
        {"source": "apple", "path": "a.md", "anchor": "1", "title": "A",
         "lineno": 1, "body": "same term"},
        {"source": "banana", "path": "b.md", "anchor": "1", "title": "B",
         "lineno": 1, "body": "same term"},
    ]
    with SearchStore(db) as store:
        store.bulk_replace(rows)
        a_only = store.search("same", source="apple")
        assert len(a_only) == 1
        assert a_only[0].path == "a.md"


# --- Indexer scanners against a synthetic mini-repo -------------------------

@pytest.fixture
def mini_repo(tmp_path):
    root = tmp_path / "minirepo"
    (root / "docs").mkdir(parents=True)
    (root / "journal").mkdir()

    (root / "docs" / "design-decisions-log.md").write_text(
        "# Design Decisions Log\n\n"
        "## D-001: First decision\n\n"
        "This is the first decision body.\n\n"
        "## D-002: Second decision\n\n"
        "Bodies for the second decision go here.\n",
        encoding="utf-8",
    )
    (root / "docs" / "joint_session_spec_v0.1.md").write_text(
        "# Joint Spec\n\n"
        "## §A. Protocol\n\n"
        "Envelope rules live here.\n\n"
        "### §A.1 Adapter interface\n\n"
        "Adapter details.\n\n"
        "## §B. Hypotheses\n\n"
        "Hypothesis list.\n",
        encoding="utf-8",
    )
    (root / "docs" / "sub-system-doc.md").write_text(
        "# Sub-system design\n\nSpec body for a specific sub-system.\n",
        encoding="utf-8",
    )
    (root / "journal" / "2026-04-21-sample.md").write_text(
        "# 2026-04-21 sample entry\n\nSample journal content.\n",
        encoding="utf-8",
    )
    return root


def test_scan_design_log(mini_repo):
    rows = scan_design_log(mini_repo)
    assert len(rows) == 2
    assert {r["anchor"] for r in rows} == {"d-001-first-decision",
                                            "d-002-second-decision"}
    assert rows[0]["source"] == "design_log"
    assert "First decision" in rows[0]["title"]


def test_scan_joint_spec_top_and_sub(mini_repo):
    rows = scan_joint_spec(mini_repo)
    # Matches ## §A., ### §A.1, ## §B. — three rows
    assert len(rows) == 3
    titles = [r["title"] for r in rows]
    assert any("A. Protocol" in t for t in titles)
    assert any("A.1 Adapter interface" in t for t in titles)
    assert any("B. Hypotheses" in t for t in titles)


def test_scan_journal(mini_repo):
    rows = scan_journal(mini_repo)
    assert len(rows) == 1
    assert rows[0]["source"] == "journal"
    assert "sample" in rows[0]["anchor"]


def test_scan_sub_system_excludes_special_files(mini_repo):
    rows = scan_sub_system(mini_repo)
    paths = [r["path"] for r in rows]
    assert "docs/sub-system-doc.md" in paths
    assert not any("design-decisions-log" in p for p in paths)
    assert not any("joint_session_spec" in p for p in paths)


# --- Reindex end-to-end on the mini repo ------------------------------------

def test_reindex_end_to_end(mini_repo, tmp_path):
    db = tmp_path / "idx.db"
    stats = reindex(db_path=db, repo_root=mini_repo)
    # 2 design log + 3 joint spec + 1 journal + 1 sub_system + 0 review = 7
    assert stats.rows_inserted == 7

    with SearchStore(db) as store:
        # Query spanning sources
        hits = store.search("decision")
        assert len(hits) >= 2
        all_sources = store.list_sources()
        assert set(all_sources.keys()) == {
            "design_log", "joint_spec", "journal", "sub_system"
        }


def test_reindex_is_idempotent(mini_repo, tmp_path):
    db = tmp_path / "idx.db"
    s1 = reindex(db_path=db, repo_root=mini_repo)
    s2 = reindex(db_path=db, repo_root=mini_repo)
    assert s1.rows_inserted == s2.rows_inserted
    with SearchStore(db) as store:
        counts = store.list_sources()
    assert counts["design_log"] == 2
    assert counts["joint_spec"] == 3


# --- Scanners list is the canonical registry --------------------------------

def test_all_scanners_accessible_via_registry():
    expected = {"design_log", "joint_spec", "journal", "sub_system", "review"}
    assert set(SCANNERS.keys()) == expected
