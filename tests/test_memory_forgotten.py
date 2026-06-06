"""D-071 pillar 3 — `forgotten: bool` retrieval-failure substrate.

Covers Memory schema, handle_mark_forgotten contract, list_forgotten,
recall filter (default exclude + show_forgotten=True override),
roundtrip serialization, and the protected-takes-precedence rule.
"""

from __future__ import annotations

import pytest

from ludex.blocks.memory import Memory, MemoryBlock


@pytest.fixture
def mb(tmp_path):
    return MemoryBlock(storage_dir=str(tmp_path / "memory"))


# --- schema ---


def test_memory_default_forgotten_false():
    m = Memory(id="m", content="x", memory_type="episodic")
    assert m.forgotten is False
    assert m.forgotten_at == 0.0
    assert m.forgotten_reason == ""


def test_memory_to_from_dict_roundtrip_forgotten():
    m = Memory(
        id="m1",
        content="x",
        memory_type="episodic",
        forgotten=True,
        forgotten_at=1_777_500_000.0,
        forgotten_reason="tier_pruned",
    )
    d = m.to_dict()
    assert d["forgotten"] is True
    assert d["forgotten_at"] == 1_777_500_000.0
    assert d["forgotten_reason"] == "tier_pruned"
    m2 = Memory.from_dict(d)
    assert m2.forgotten is True
    assert m2.forgotten_at == 1_777_500_000.0
    assert m2.forgotten_reason == "tier_pruned"


def test_legacy_data_without_forgotten_loads_false():
    """Pre-pillar-3 JSONL has no `forgotten`/`forgotten_at`/`forgotten_reason`
    keys — must load with sane defaults."""
    legacy = {
        "id": "m_old",
        "content": "old entry",
        "memory_type": "episodic",
    }
    m = Memory.from_dict(legacy)
    assert m.forgotten is False
    assert m.forgotten_at == 0.0
    assert m.forgotten_reason == ""


# --- handle_mark_forgotten ---


def test_mark_forgotten_sets_fields(mb):
    mid = mb.handle_remember("ep", memory_type="episodic")
    assert mb.handle_mark_forgotten(mid, "tier_pruned") is True
    m = mb._memories[mid]
    assert m.forgotten is True
    assert m.forgotten_at > 0.0
    assert m.forgotten_reason == "tier_pruned"


def test_mark_forgotten_idempotent(mb):
    mid = mb.handle_remember("ep", memory_type="episodic")
    assert mb.handle_mark_forgotten(mid, "score_threshold") is True
    first_at = mb._memories[mid].forgotten_at
    # second call should not change forgotten_at (idempotent)
    assert mb.handle_mark_forgotten(mid, "different_reason") is True
    assert mb._memories[mid].forgotten_at == first_at
    assert mb._memories[mid].forgotten_reason == "score_threshold"  # original preserved


def test_mark_forgotten_refuses_protected(mb):
    mid = mb.handle_remember("identity entry", memory_type="identity")
    assert mb._memories[mid].protected is True
    assert mb.handle_mark_forgotten(mid, "manual") is False
    assert mb._memories[mid].forgotten is False


def test_mark_forgotten_unknown_id_returns_false(mb):
    assert mb.handle_mark_forgotten("nope", "manual") is False


def test_mark_forgotten_empty_reason_defaults_to_unspecified(mb):
    mid = mb.handle_remember("ep", memory_type="episodic")
    assert mb.handle_mark_forgotten(mid, "") is True
    assert mb._memories[mid].forgotten_reason == "unspecified"


# --- list_forgotten ---


def test_list_forgotten_returns_only_forgotten_sorted_by_recency(mb):
    a = mb.handle_remember("a", memory_type="episodic")
    b = mb.handle_remember("b", memory_type="episodic")
    c = mb.handle_remember("c", memory_type="episodic")
    mb.handle_mark_forgotten(a, "reason_a")
    mb.handle_mark_forgotten(c, "reason_c")  # forgotten more recently than a
    out = mb.list_forgotten()
    assert [m.id for m in out] == [c, a]  # newest first
    assert b not in [m.id for m in out]


def test_list_forgotten_empty(mb):
    mb.handle_remember("ep", memory_type="episodic")
    assert mb.list_forgotten() == []


# --- recall filter ---


def test_recall_excludes_forgotten_by_default(mb):
    a = mb.handle_remember("trust strategy quest", memory_type="episodic")
    b = mb.handle_remember("trust strategy quest", memory_type="episodic")
    mb.handle_mark_forgotten(b, "manual")
    results = mb.handle_recall("trust strategy")
    ids = [r.memory.id for r in results]
    assert a in ids
    assert b not in ids


def test_recall_show_forgotten_includes_them(mb):
    a = mb.handle_remember("trust strategy quest one", memory_type="episodic")
    b = mb.handle_remember("trust strategy quest two", memory_type="episodic")
    mb.handle_mark_forgotten(b, "manual")
    results = mb.handle_recall("trust strategy", show_forgotten=True)
    ids = [r.memory.id for r in results]
    assert a in ids
    assert b in ids


def test_is_forgettable_independent_of_forgotten():
    """is_forgettable is about whether the forget pass *can* forget;
    distinct from whether the memory is *already* forgotten. A
    forgotten memory still reports is_forgettable=True so a re-pass
    can re-evaluate; only protected/non-active blocks the pass."""
    m = Memory(id="m", content="x", memory_type="episodic", forgotten=True)
    assert m.is_forgettable is True

    m2 = Memory(id="m2", content="x", memory_type="identity", protected=True, forgotten=False)
    assert m2.is_forgettable is False
