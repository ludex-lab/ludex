"""D-071 pillar 4b — `run_forgetting_pass()` end-to-end test.

Closes the forgetting axis: substrate (recall_count, BRAIN_DISK_TARGETS,
compute_forget_score) is now wired into an actual caller that prunes
the recall surface to a budget. Tests cover:

- Under-target no-op (returns 0 forgotten)
- Over-target forgets exactly recallable_before - target
- Highest-score (1-imp) × age × 1/(1+rc) candidates forgotten first
- Protected entries skipped unconditionally
- Already-forgotten entries skipped (idempotency)
- Provider/model dispatch via BRAIN_DISK_TARGETS
- Explicit target overrides provider/model dispatch
- `deprecated:` tagged entries don't count toward recallable budget
- `memory.forgot` span emits per forgotten entry
"""

from __future__ import annotations

import time

import pytest

from ludex.blocks.memory import MemoryBlock


@pytest.fixture
def mb(tmp_path):
    return MemoryBlock(storage_dir=str(tmp_path / "memory"))


def _bulk_remember(mb: MemoryBlock, n: int, *, prefix: str = "ep", importance: float = 0.5,
                   memory_type: str = "episodic", age_days: float = 0.0,
                   recall_count: int = 0) -> list[str]:
    """Insert n memories, optionally backdating created_at and bumping
    recall_count. Returns the IDs."""
    ids: list[str] = []
    now = time.time()
    for i in range(n):
        mid = mb.handle_remember(
            f"{prefix}_{i:03d} content body",
            memory_type=memory_type,
            importance=importance,
        )
        m = mb._memories[mid]
        m.created_at = now - age_days * 86400.0
        m.recall_count = recall_count
        ids.append(mid)
    return ids


def test_under_target_is_noop(mb):
    _bulk_remember(mb, 5)
    report = mb.run_forgetting_pass(target=10)
    assert report["forgotten_now"] == 0
    assert report["forgotten_ids"] == []
    assert report["recallable_after"] == 5


def test_over_target_forgets_excess(mb):
    _bulk_remember(mb, 20, age_days=10.0)
    report = mb.run_forgetting_pass(target=12)
    assert report["forgotten_now"] == 8
    assert report["recallable_after"] == 12
    # all forgotten entries marked
    for mid in report["forgotten_ids"]:
        assert mb._memories[mid].forgotten is True
        assert mb._memories[mid].forgotten_reason == "score_threshold"


def test_idempotent_on_second_call(mb):
    _bulk_remember(mb, 15, age_days=10.0)
    first = mb.run_forgetting_pass(target=10)
    assert first["forgotten_now"] == 5
    second = mb.run_forgetting_pass(target=10)
    assert second["forgotten_now"] == 0
    assert second["recallable_after"] == 10


def test_protected_entries_skipped(mb):
    """Protected memories are never forgotten — even if they would
    otherwise score highest. Forgetting pass picks unprotected
    candidates instead, even when that means undershooting target."""
    # 5 protected (high age, low importance — would normally rank top)
    protected_ids = _bulk_remember(mb, 5, prefix="prot", age_days=100.0, importance=0.1)
    for mid in protected_ids:
        mb._memories[mid].protected = True
    # 10 unprotected (lower scoring — recent, important)
    _bulk_remember(mb, 10, prefix="unprot", age_days=1.0, importance=0.9)

    report = mb.run_forgetting_pass(target=8)
    # Total recallable = 15, target = 8 → forget 7
    # but only 10 are forgettable, so all 7 come from unprot pool
    assert report["forgotten_now"] == 7
    for mid in protected_ids:
        assert mb._memories[mid].forgotten is False


def test_score_based_ordering_prefers_high_score(mb):
    """compute_forget_score = (1-imp) × age × 1/(1+rc). Old-low-imp-cold
    entries should be forgotten before young-high-imp-hot ones."""
    # Create three groups
    cold = _bulk_remember(mb, 5, prefix="cold", age_days=100.0,
                          importance=0.1, recall_count=0)  # high score
    hot = _bulk_remember(mb, 5, prefix="hot", age_days=1.0,
                         importance=0.9, recall_count=10)  # low score
    mid_ids = _bulk_remember(mb, 5, prefix="mid", age_days=10.0,
                             importance=0.5, recall_count=2)  # medium

    # Total = 15, target = 10 → forget 5 — should be the cold ones.
    report = mb.run_forgetting_pass(target=10)
    assert report["forgotten_now"] == 5
    for mid in cold:
        assert mb._memories[mid].forgotten is True, f"cold {mid} should be forgotten"
    for mid in hot:
        assert mb._memories[mid].forgotten is False, f"hot {mid} should remain"
    for mid in mid_ids:
        assert mb._memories[mid].forgotten is False, f"mid {mid} should remain"


def test_provider_model_dispatch_uses_disk_target(mb):
    """Without explicit target, provider+model resolve via
    get_disk_target. Ollama → slm tier → 2000."""
    # Insert just enough to be over slm (2000) — too expensive to simulate
    # at scale; test the dispatch by checking the report's `target` field.
    _bulk_remember(mb, 5)
    report = mb.run_forgetting_pass(provider="ollama", model="qwen3:8b")
    assert report["target"] == 2000


def test_explicit_target_overrides_provider(mb):
    _bulk_remember(mb, 5)
    report = mb.run_forgetting_pass(target=42, provider="ollama")
    assert report["target"] == 42


def test_deprecated_tagged_excluded_from_recallable(mb):
    """`deprecated:` tagged memories aren't part of recall surface —
    they shouldn't count toward the budget. Pass should leave them
    alone if everything else is under budget."""
    # 5 normal (below target 10)
    _bulk_remember(mb, 5)
    # 10 deprecated-tagged (would push total to 15, but recall surface is 5)
    dep_ids = _bulk_remember(mb, 10, prefix="dep")
    for mid in dep_ids:
        mb._memories[mid].tags = ["deprecated:lxm_leak"]

    report = mb.run_forgetting_pass(target=10)
    # recallable_before = 5 (not 15) → no-op
    assert report["forgotten_now"] == 0
    for mid in dep_ids:
        assert mb._memories[mid].forgotten is False


def test_already_forgotten_skipped(mb):
    """Entries with forgotten=True aren't candidates — skipping them
    keeps the pass idempotent and prevents reason-stomping."""
    ids = _bulk_remember(mb, 10, age_days=10.0)
    # Pre-mark 3 as forgotten with a different reason
    for mid in ids[:3]:
        mb.handle_mark_forgotten(mid, "manual")

    # recallable_before = 7, target = 5 → forget 2 from the unforgotten pool
    report = mb.run_forgetting_pass(target=5)
    assert report["forgotten_now"] == 2
    # First three keep their manual reason
    for mid in ids[:3]:
        assert mb._memories[mid].forgotten_reason == "manual"


def test_emits_memory_forgot_span_per_entry(mb):
    """Each forgotten entry emits one `memory.forgot` span via
    handle_mark_forgotten — pillar 4b's pass relies on that path."""
    spans: list[dict] = []

    def capture(name, **attrs):
        if name == "memory.forgot":
            spans.append(attrs)

    # Patch _emit at the instance level
    original = mb._emit
    mb._emit = capture  # type: ignore

    try:
        _bulk_remember(mb, 8, age_days=20.0)
        report = mb.run_forgetting_pass(target=5)
        assert report["forgotten_now"] == 3
        assert len(spans) == 3
        for s in spans:
            assert s["forgotten_reason"] == "score_threshold"
    finally:
        mb._emit = original  # type: ignore


def test_custom_reason_propagates(mb):
    _bulk_remember(mb, 8, age_days=20.0)
    report = mb.run_forgetting_pass("disk_threshold", target=5)
    assert report["forgotten_now"] == 3
    for mid in report["forgotten_ids"]:
        assert mb._memories[mid].forgotten_reason == "disk_threshold"


def test_report_shape(mb):
    _bulk_remember(mb, 8, age_days=20.0)
    report = mb.run_forgetting_pass(target=5)
    assert set(report.keys()) == {
        "total_before", "forgettable", "target",
        "forgotten_now", "forgotten_ids", "recallable_after",
    }
    assert report["total_before"] == 8
    assert report["forgettable"] == 8
    assert report["target"] == 5
