"""Unit tests for tools/memory_audit.py — read-only audit of a creature's
memory store. Helpers are pure functions over a parsed list of dicts."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# tools/ isn't on sys.path by default; tests usually live in tests/
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from memory_audit import (  # noqa: E402
    age_histogram,
    deprecated_summary,
    forgotten_summary,
    name_frequency,
    recall_surface_size,
    session_tag_frequency,
    status_distribution,
    tag_distribution,
)

NOW = 1_777_500_000.0  # arbitrary fixed reference for age tests


def _mem(**kw) -> dict:
    base = {
        "id": "mem_x",
        "content": "",
        "tags": [],
        "status": "active",
        "created_at": NOW,
    }
    base.update(kw)
    return base


def test_status_distribution_counts():
    mems = [_mem(status="active"), _mem(status="active"), _mem(status="archived")]
    d = status_distribution(mems)
    assert d == Counter({"active": 2, "archived": 1})


def test_tag_distribution_counts_each_tag_once_per_memory():
    mems = [
        _mem(tags=["a", "b"]),
        _mem(tags=["a"]),
        _mem(tags=["a", "b", "c"]),
    ]
    d = tag_distribution(mems)
    assert d["a"] == 3 and d["b"] == 2 and d["c"] == 1


def test_deprecated_summary_filters_by_prefix():
    mems = [
        _mem(id="m1", tags=["wilderness"]),
        _mem(id="m2", tags=["lxm", "deprecated:lxm_leak"]),
        _mem(id="m3", tags=["deprecated:bad_recall"]),
    ]
    n, flagged = deprecated_summary(mems)
    assert n == 2
    assert {m["id"] for m in flagged} == {"m2", "m3"}


def test_recall_surface_excludes_deprecated_and_inactive():
    mems = [
        _mem(status="active", tags=["lxm"]),
        _mem(status="active", tags=["lxm", "deprecated:bad"]),
        _mem(status="archived", tags=["lxm"]),
    ]
    recallable, total = recall_surface_size(mems)
    assert recallable == 1
    assert total == 3


def test_age_histogram_buckets_by_week():
    day = 86400.0
    mems = [
        _mem(created_at=NOW - 3 * day),       # <1w
        _mem(created_at=NOW - 10 * day),      # 1-2w
        _mem(created_at=NOW - 20 * day),      # 2-4w
        _mem(created_at=NOW - 60 * day),      # 1-3mo
        _mem(created_at=NOW - 200 * day),     # >3mo
        _mem(created_at=None),                # unknown
    ]
    hist = age_histogram(mems, now=NOW)
    assert hist["<1w"] == 1
    assert hist["1-2w"] == 1
    assert hist["2-4w"] == 1
    assert hist["1-3mo"] == 1
    assert hist[">3mo"] == 1
    assert hist["unknown"] == 1


def test_session_tag_frequency_filters_by_prefix():
    mems = [
        _mem(tags=["physis_smoke_001"]),
        _mem(tags=["physis_smoke_001", "lxm"]),
        _mem(tags=["physis_smoke_002"]),
        _mem(tags=["wilderness"]),  # no prefix match
    ]
    c = session_tag_frequency(mems, "physis_smoke_")
    assert c["physis_smoke_001"] == 2
    assert c["physis_smoke_002"] == 1
    assert "wilderness" not in c


def test_name_frequency_with_known_names():
    mems = [
        _mem(content="Today Flint visited and we discussed trust. Flint had thoughts."),
        _mem(content="Anvil mentioned the design review."),
    ]
    c = name_frequency(mems, known_names={"Flint", "Anvil", "Loom"})
    assert c["Flint"] == 2
    assert c["Anvil"] == 1
    assert "Loom" not in c  # not mentioned


def test_forgotten_summary_groups_reasons():
    mems = [
        _mem(forgotten=True, forgotten_reason="tier_pruned"),
        _mem(forgotten=True, forgotten_reason="tier_pruned"),
        _mem(forgotten=True, forgotten_reason="score_threshold"),
        _mem(forgotten=False),  # not forgotten
        _mem(),  # legacy missing key
    ]
    n, reasons = forgotten_summary(mems)
    assert n == 3
    assert reasons["tier_pruned"] == 2
    assert reasons["score_threshold"] == 1


def test_recall_surface_size_excludes_forgotten():
    mems = [
        _mem(status="active"),                    # in
        _mem(status="active", forgotten=True),    # out
        _mem(status="active", tags=["deprecated:x"]),  # out
    ]
    recallable, total = recall_surface_size(mems)
    assert recallable == 1
    assert total == 3


def test_name_frequency_auto_detect_filters_stop_words():
    mems = [_mem(content="The Brain noticed Trust. Trust is hard. Quill spoke up.")]
    c = name_frequency(mems, known_names=None)
    # Stop words filtered; only Quill survives.
    assert "Quill" in c
    assert "The" not in c
    assert "Trust" not in c  # in COMMON_WORDS
    assert "Brain" not in c  # in COMMON_WORDS
