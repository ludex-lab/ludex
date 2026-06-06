"""D-024 Sprint 2 — progressive disclosure + memory health check.

Runnable directly. Complements test_memory_sprint1.py.
"""
import sys, os, time, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ludex.core.bus import Bus
from ludex.core.signals import Signals
from ludex.core.config import Config
from ludex.blocks.memory import MemoryBlock, Memory, HealthReport
from ludex.core.memory_types import (
    disclose_memories, disclosure_level_for,
    DISCLOSURE_L1, DISCLOSURE_L2, DISCLOSURE_L3,
)


def _make_block(tmp: str, brain: dict | None = None) -> MemoryBlock:
    mem = MemoryBlock(storage_dir=tmp, auto_capture=False)
    cfg = Config()
    if brain:
        cfg.set("brain", brain, layer="session")
    mem.attach(Bus(), Signals(), cfg)
    return mem


def _sample_memories() -> list[Memory]:
    return [
        Memory(id="m1",
               content="I retreat to edges when noise spikes. On calm days I drift outward and find patterns.",
               memory_type="identity", tags=["reflection", "self"]),
        Memory(id="m2",
               content="Met Flare on 2026-04-13. Different frequency — gemini-2.5-flash vs my 3-pro.",
               memory_type="relationship", tags=["bond", "Flare"]),
        Memory(id="m3",
               content="Council v5: argued that proximity becomes pressure when resilience is failing.",
               memory_type="belief", tags=["council", "distress"]),
        Memory(id="m4",
               content="Wilderness tick 3: storm event, defended.",
               memory_type="episodic", tags=["wilderness"]),
    ]


# ============================================================
# Progressive disclosure
# ============================================================

def test_1_disclosure_levels_scale_down():
    """L1 < L2 < L3 — tested with realistic roster (multiple memories
    per tag) where L2's by-group compression actually wins over L3.
    """
    # 15 memories: 5 reflection, 5 bond, 5 council — 3 tag groups,
    # so L2 shows 3 lines, L3 shows 15.
    base = [
        ("reflection", "I retreat to edges when noise spikes and drift outward on calm days finding patterns across the storm and the silence"),
        ("bond",       "Met Flare — different frequency, our generations diverge but we both survived our first encounter in the agora"),
        ("council",    "Council: argued proximity becomes pressure when resilience fails; Verse yielded to my question about internal witnessing"),
    ]
    mems = []
    for i in range(5):
        for tag, content in base:
            mems.append(Memory(
                id=f"m{i}-{tag}", content=content + f" (instance {i})",
                memory_type="episodic", tags=[tag],
            ))
    l1 = disclose_memories(mems, DISCLOSURE_L1)
    l2 = disclose_memories(mems, DISCLOSURE_L2)
    l3 = disclose_memories(mems, DISCLOSURE_L3)
    assert len(l1) < len(l2) < len(l3), f"L1={len(l1)} L2={len(l2)} L3={len(l3)}"
    assert "15 memories" in l1
    assert "Council" in l3
    print(f"  [PASS] disclosure length scales: L1={len(l1)}, L2={len(l2)}, L3={len(l3)}")


def test_2_disclosure_level_for_brain_tier():
    """SMALL_SLM → L1; MID_SLM/LARGE_SLM → L2; MID/LARGE → L3."""
    assert disclosure_level_for({"model": "gemma:2b"}) == DISCLOSURE_L1
    assert disclosure_level_for({"model": "gemma4:e4b"}) == DISCLOSURE_L2  # MID_SLM
    assert disclosure_level_for({"model": "llama-3.1-8b"}) == DISCLOSURE_L2  # LARGE_SLM
    assert disclosure_level_for({"model": "claude-sonnet-4-6"}) == DISCLOSURE_L3
    assert disclosure_level_for({"model": "claude-opus-4-6"}) == DISCLOSURE_L3
    assert disclosure_level_for({}) == DISCLOSURE_L3  # fallback MID → L3
    print("  [PASS] disclosure_level_for tier routing correct")


def test_3_disclosure_accepts_recallresult():
    """Duck-typed: RecallResult-shape objects get .memory extracted."""
    class FakeRecall:
        def __init__(self, m):
            self.memory = m
            self.relevance = 0.8
    mems = [FakeRecall(m) for m in _sample_memories()]
    out = disclose_memories(mems, DISCLOSURE_L3)
    assert "Council v5" in out
    print("  [PASS] disclose_memories handles RecallResult-shape input")


def test_4_empty_disclosure():
    """No memories returns a safe placeholder."""
    assert disclose_memories([], DISCLOSURE_L1) == "(no memories)"
    assert disclose_memories([], DISCLOSURE_L3) == "(no memories)"
    print("  [PASS] empty input handled")


# ============================================================
# Health check
# ============================================================

def test_5_health_clean_creature_scores_A():
    """Newborn-ish creature with all sources, one identity, budget
    compliant, recently consolidated → A grade."""
    tmp = tempfile.mkdtemp(prefix="sprint2_A_")
    try:
        mem = _make_block(tmp, brain={"model": "claude-opus-4-6"})
        mem.handle_remember("I am Probe, born for this test",
                            memory_type="identity", source="birth")
        mem.handle_remember("First observation: the storm passed",
                            memory_type="episodic", source="wilderness/tick_1")
        mem.handle_remember("I value quiet witnessing",
                            memory_type="belief", source="reflection/first")
        # Simulate recent consolidation
        mem._last_consolidated_at = time.time() - 3600

        report = mem.handle_health_check()
        assert isinstance(report, HealthReport)
        assert report.grade == "A", f"expected A, got {report.grade}: {report.notes}"
        assert report.pass_count == 5
        print(f"  [PASS] clean creature → A (coverage {report.source_coverage:.0%})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_6_health_fails_on_no_identity():
    """Creature without any identity memory fails identity_grounded."""
    tmp = tempfile.mkdtemp(prefix="sprint2_id_")
    try:
        mem = _make_block(tmp, brain={"model": "claude-opus-4-6"})
        mem.handle_remember("passing event", memory_type="episodic", source="x")
        report = mem.handle_health_check()
        assert not report.identity_grounded
        assert "identity_grounded" in " ".join(report.notes)
        assert report.grade in ("B", "C", "D")
        print(f"  [PASS] no identity → {report.grade} (notes include identity_grounded)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_7_health_fails_on_missing_sources():
    """≥20% missing source → source_attribution fails."""
    tmp = tempfile.mkdtemp(prefix="sprint2_src_")
    try:
        mem = _make_block(tmp, brain={"model": "claude-opus-4-6"})
        mem.handle_remember("I am Probe", memory_type="identity", source="birth")
        # 3 with source, 2 without → 60% coverage → fail
        mem.handle_remember("event 1", memory_type="episodic", source="x")
        mem.handle_remember("event 2", memory_type="episodic", source="x")
        mem.handle_remember("event 3", memory_type="episodic", source="")
        mem.handle_remember("event 4", memory_type="episodic", source="")
        report = mem.handle_health_check()
        assert not report.source_attribution
        assert 0.55 <= report.source_coverage <= 0.65
        print(f"  [PASS] low source coverage detected ({report.source_coverage:.0%})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_8_health_detects_duplicates():
    """Near-duplicate content flagged."""
    tmp = tempfile.mkdtemp(prefix="sprint2_dup_")
    try:
        mem = _make_block(tmp, brain={"model": "claude-opus-4-6"})
        mem.handle_remember("I am Probe", memory_type="identity", source="birth")
        # Two near-duplicates (differ only in trailing punctuation)
        mem.handle_remember("Met Spark during the first storm event",
                            memory_type="episodic", source="x")
        mem.handle_remember("Met Spark during the first storm event.",
                            memory_type="episodic", source="y")
        report = mem.handle_health_check()
        assert not report.no_duplicates
        assert report.duplicate_count >= 1
        print(f"  [PASS] duplicate detection ({report.duplicate_count} pair(s))")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_9_health_fails_on_budget_overflow():
    """HOT over budget → budget_compliance fails."""
    tmp = tempfile.mkdtemp(prefix="sprint2_budget_")
    try:
        mem = _make_block(tmp, brain={"model": "gemma:2b"})  # 3000 HOT cap
        mem.handle_remember("I am Probe", memory_type="identity", source="birth")
        # Pile in HOT memories
        for i in range(30):
            mem.handle_remember(
                ("routine tick " * 33)[:400] + f" #{i}",
                memory_type="episodic", source="wilderness/tick")
        # Do NOT consolidate — leave budget in violation
        report = mem.handle_health_check()
        assert not report.budget_compliance
        assert report.hot_tokens > report.hot_budget
        print(f"  [PASS] budget overflow detected "
              f"(HOT {report.hot_tokens}/{report.hot_budget})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_10_health_stale_consolidation():
    """Long stale + many memories → consolidation_fresh fails."""
    tmp = tempfile.mkdtemp(prefix="sprint2_stale_")
    try:
        mem = _make_block(tmp, brain={"model": "claude-opus-4-6"})
        mem.handle_remember("I am Probe", memory_type="identity", source="birth")
        for i in range(25):
            mem.handle_remember(f"event {i}", memory_type="episodic", source="x")
        # Simulate consolidation 10 days ago
        mem._last_consolidated_at = time.time() - (10 * 24 * 3600)
        report = mem.handle_health_check()
        assert not report.consolidation_fresh
        assert report.days_since_consolidation >= 7
        print(f"  [PASS] stale consolidation detected "
              f"({report.days_since_consolidation:.1f} days)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_11_brain_resolution_flat_config():
    """Regression: OrganismConfig.build() writes `model` and
    `provider` as flat keys at the Organism config level, not as a
    nested `brain` dict. MemoryBlock must read both shapes or
    capacity_for gets the fallback MID cap instead of the creature's
    actual tier (silent bug observed in Moss live smoke).
    """
    tmp = tempfile.mkdtemp(prefix="sprint2_brain_")
    try:
        mem = MemoryBlock(storage_dir=tmp, auto_capture=False)
        cfg = Config()
        # Flat shape — what OrganismConfig actually writes
        cfg.set("model", "gemma4:e4b", layer="session")
        cfg.set("provider", "ollama", layer="session")
        mem.attach(Bus(), Signals(), cfg)

        resolved = mem._resolve_brain()
        assert resolved.get("model") == "gemma4:e4b"

        report = mem.handle_health_check()
        # MID_SLM → HOT 5000 / WARM 3000 (not MID fallback 12000/6000)
        assert report.hot_budget == 5000, f"expected 5000, got {report.hot_budget}"
        assert report.warm_budget == 3000, f"expected 3000, got {report.warm_budget}"
        print(f"  [PASS] flat-config brain resolves correctly "
              f"(HOT {report.hot_budget} / WARM {report.warm_budget})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("\n[D-024 Sprint 2 — progressive disclosure + health check]")
    test_1_disclosure_levels_scale_down()
    test_2_disclosure_level_for_brain_tier()
    test_3_disclosure_accepts_recallresult()
    test_4_empty_disclosure()
    test_5_health_clean_creature_scores_A()
    test_6_health_fails_on_no_identity()
    test_7_health_fails_on_missing_sources()
    test_8_health_detects_duplicates()
    test_9_health_fails_on_budget_overflow()
    test_10_health_stale_consolidation()
    test_11_brain_resolution_flat_config()
    print("\n" + "="*60)
    print("All Sprint 2 memory tests passed.")
    print("="*60)
