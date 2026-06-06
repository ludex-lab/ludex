"""D-071 pillar 4a — recall_count tracking + BRAIN_DISK_TARGETS +
compute_forget_score. Substrate for pillar 4b's forgetting pass.

Tests:
- recall_count default 0, roundtrip serialization
- handle_recall bumps recall_count for surfaced memories
- handle_recall bumps importance per design (+0.05 capped at 0.95)
- compute_forget_score formula (1 - importance) × age_days × 1/(1 + recall_count)
- BRAIN_DISK_TARGETS lookup via get_disk_target dispatches frontier/mid/slm
"""

from __future__ import annotations

import pytest

from ludex.blocks.memory import Memory, MemoryBlock
from ludex.core.consolidation import (
    BRAIN_DISK_TARGETS,
    compute_forget_score,
    get_disk_target,
)


@pytest.fixture
def mb(tmp_path):
    return MemoryBlock(storage_dir=str(tmp_path / "memory"))


# --- schema ---


def test_memory_default_recall_count_zero():
    m = Memory(id="m", content="x", memory_type="episodic")
    assert m.recall_count == 0


def test_memory_roundtrip_recall_count():
    m = Memory(id="m", content="x", memory_type="episodic", recall_count=7)
    d = m.to_dict()
    assert d["recall_count"] == 7
    m2 = Memory.from_dict(d)
    assert m2.recall_count == 7


def test_legacy_data_without_recall_count_loads_zero():
    m = Memory.from_dict({"id": "m", "content": "x", "memory_type": "episodic"})
    assert m.recall_count == 0


# --- recall_count bump on handle_recall ---


def test_handle_recall_bumps_recall_count_on_surfaced(mb):
    a = mb.handle_remember("trust strategy quest one", memory_type="episodic")
    b = mb.handle_remember("unrelated bond reflection", memory_type="episodic")
    initial_a = mb._memories[a].recall_count
    initial_b = mb._memories[b].recall_count
    results = mb.handle_recall("trust strategy")
    surfaced_ids = {r.memory.id for r in results}
    assert a in surfaced_ids
    assert mb._memories[a].recall_count == initial_a + 1
    if b not in surfaced_ids:
        assert mb._memories[b].recall_count == initial_b


def test_handle_recall_bumps_importance_capped_at_095(mb):
    mid = mb.handle_remember("trust strategy quest", memory_type="episodic", importance=0.5)
    # Single recall: 0.5 + 0.05 = 0.55
    mb.handle_recall("trust strategy")
    assert abs(mb._memories[mid].importance - 0.55) < 1e-9


def test_handle_recall_importance_bump_caps_at_095(mb):
    mid = mb.handle_remember("trust strategy quest", memory_type="episodic", importance=0.93)
    # First bump: 0.93 + 0.05 = 0.98 → capped to 0.95
    mb.handle_recall("trust strategy")
    assert mb._memories[mid].importance == 0.95
    # Subsequent recalls don't push above cap
    mb.handle_recall("trust strategy")
    assert mb._memories[mid].importance == 0.95


def test_handle_recall_does_not_bump_unrelated_memories(mb):
    related = mb.handle_remember("trust strategy quest", memory_type="episodic")
    unrelated = mb.handle_remember("rainbow umbrella picnic", memory_type="episodic")
    initial_unrelated = mb._memories[unrelated].importance
    mb.handle_recall("trust strategy")
    assert mb._memories[unrelated].recall_count == 0
    assert mb._memories[unrelated].importance == initial_unrelated


# --- compute_forget_score ---


def test_forget_score_basic_formula():
    # (1 - 0.5) * 10 * 1/(1+0) = 5.0
    s = compute_forget_score(importance=0.5, age_days=10.0, recall_count=0)
    assert abs(s - 5.0) < 1e-9


def test_forget_score_recall_count_reduces_score():
    s_unread = compute_forget_score(importance=0.5, age_days=10.0, recall_count=0)
    s_read = compute_forget_score(importance=0.5, age_days=10.0, recall_count=4)
    # 1/(1+4) = 0.2 → score should be 1/5 of unread
    assert s_read < s_unread
    assert abs(s_read - s_unread / 5) < 1e-9


def test_forget_score_importance_reduces_score():
    s_low = compute_forget_score(importance=0.1, age_days=10.0, recall_count=0)
    s_high = compute_forget_score(importance=0.9, age_days=10.0, recall_count=0)
    assert s_low > s_high


def test_forget_score_age_zero_returns_zero():
    # Brand-new memory: nothing to forget
    s = compute_forget_score(importance=0.0, age_days=0.0, recall_count=0)
    assert s == 0.0


def test_forget_score_clamps_negative_inputs():
    # Defensive: should not crash or return negative
    s = compute_forget_score(importance=-0.5, age_days=-10.0, recall_count=-1)
    assert s == 0.0  # negatives clamped → all zero terms compose to 0


def test_forget_score_clamps_importance_above_one():
    # importance > 1.0 still gives non-negative (clamped to 1.0 → factor 0)
    s = compute_forget_score(importance=1.5, age_days=10.0, recall_count=0)
    assert s == 0.0


# --- get_disk_target dispatch ---


def test_get_disk_target_frontier_for_opus():
    assert get_disk_target("claude_cli", "claude-opus-4-7") == BRAIN_DISK_TARGETS["frontier"]


def test_get_disk_target_frontier_for_sonnet_4_6():
    assert get_disk_target("claude_cli", "claude-sonnet-4-6") == BRAIN_DISK_TARGETS["frontier"]


def test_get_disk_target_frontier_for_gpt55():
    assert get_disk_target("codex_cli", "gpt-5.5") == BRAIN_DISK_TARGETS["frontier"]


def test_get_disk_target_mid_for_haiku():
    assert get_disk_target("claude_cli", "claude-haiku-4-5") == BRAIN_DISK_TARGETS["mid"]


def test_get_disk_target_mid_for_sonnet_4_5():
    assert get_disk_target("claude_cli", "claude-sonnet-4-5") == BRAIN_DISK_TARGETS["mid"]


def test_get_disk_target_slm_for_ollama():
    assert get_disk_target("ollama", "qwen3.5:4b") == BRAIN_DISK_TARGETS["slm"]
    assert get_disk_target("ollama", "exaone3.5:7.8b") == BRAIN_DISK_TARGETS["slm"]


def test_get_disk_target_default_for_unknown():
    assert get_disk_target("unknown", "unknown") == BRAIN_DISK_TARGETS["default"]
