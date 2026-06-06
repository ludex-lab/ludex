"""D-024 Sprint 1 — 8-type vocabulary + decay + token budgets + Phase 2 signal.

Runnable directly (not pytest). Matches the runnable test convention
in tests/test_core.py, tests/test_memory.py, etc.
"""
import sys, os, time, tempfile, shutil, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ludex.core.bus import Bus
from ludex.core.signals import Signals
from ludex.core.config import Config
from ludex.blocks.memory import MemoryBlock, Memory
from ludex.core.memory_types import (
    estimate_tokens, decay_multiplier, effective_age, capacity_for,
    tier_for_type, MEMORY_TYPES,
)


def _make_block(tmp: str, brain: dict | None = None) -> MemoryBlock:
    mem = MemoryBlock(storage_dir=tmp, auto_capture=False)
    cfg = Config()
    if brain:
        cfg.set("brain", brain, layer="session")
    mem.attach(Bus(), Signals(), cfg)
    return mem


def test_1_identity_survives_beyond_episodic_threshold():
    """Identity memory outlives same-age episodic under consolidation."""
    tmp = tempfile.mkdtemp(prefix="sprint1_")
    try:
        mem = _make_block(tmp, brain={"model": "claude-opus-4-6"})
        # 60 days old — well past episodic's 10.5-day threshold (1.5×7),
        # well inside identity's 70-day threshold (10×7)
        old = time.time() - (60 * 24 * 3600)

        id_ep = mem.handle_remember("passing event", memory_type="episodic",
                                    importance=0.1)
        id_id = mem.handle_remember("I am Moss, born of an early dream",
                                    memory_type="identity", importance=0.1)
        mem._memories[id_ep].created_at = old
        mem._memories[id_id].created_at = old

        mem.handle_consolidate()
        assert mem._memories[id_ep].status == "archived", "episodic should archive"
        assert mem._memories[id_id].status == "active", "identity must survive"
        print("  [PASS] identity survives while episodic archives")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_2_token_count_heuristic():
    """estimate_tokens roughly 4 chars/token + 20% overhead."""
    content = "hello world " * 250  # 3000 chars
    tok = estimate_tokens(content)
    # Expected: (3000//4) * 1.2 = 900
    assert 800 < tok < 1000, f"expected ~900, got {tok}"

    mem = _make_block(tempfile.mkdtemp(prefix="sprint1_tok_"))
    mem_id = mem.handle_remember(content, memory_type="episodic")
    stored = mem._memories[mem_id]
    assert stored.token_count == tok, f"token_count stored mismatch: {stored.token_count} vs {tok}"
    print(f"  [PASS] token_count heuristic ({tok} for 3000 chars)")


def test_3_hot_budget_enforced_on_small_slm():
    """Fill HOT past SMALL_SLM budget (3000 tokens) → consolidate archives
    oldest-effective-age until under budget."""
    tmp = tempfile.mkdtemp(prefix="sprint1_budget_")
    try:
        mem = _make_block(tmp, brain={"model": "gemma:2b"})  # small_slm
        caps = capacity_for({"model": "gemma:2b"})
        assert caps["hot_tokens"] == 3000

        # Add ~20 episodic memories of ~400 chars each.
        # Each ~120 tokens; 20 × 120 = 2400 — below budget
        # 30 × 120 = 3600 — over budget
        for i in range(30):
            mid = mem.handle_remember(
                content=("lived event " * 33)[:400] + f" #{i}",
                memory_type="episodic",
                importance=0.5,
            )
            # Stagger timestamps so "oldest" is well-defined
            mem._memories[mid].created_at = time.time() - (30 - i) * 60

        before_active = sum(1 for m in mem._memories.values() if m.status == "active")
        report = mem.handle_consolidate()
        after_active = sum(1 for m in mem._memories.values() if m.status == "active")

        # Must have archived something, and total active HOT tokens must
        # now be <= budget
        assert report.archived >= 1, f"expected archive, got {report.archived}"
        hot_tokens = sum(
            (m.token_count or 0) for m in mem._memories.values()
            if m.status == "active" and tier_for_type(m.memory_type) == "hot"
        )
        assert hot_tokens <= 3000, f"HOT tokens {hot_tokens} exceeds budget 3000"
        print(f"  [PASS] HOT budget enforced (archived {before_active - after_active}, "
              f"HOT tokens now {hot_tokens}/3000)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_4_warm_overflow_emits_distillation_candidate():
    """WARM tier over budget → mark-and-signal Phase 2."""
    tmp = tempfile.mkdtemp(prefix="sprint1_phase2_")
    try:
        mem = _make_block(tmp, brain={"model": "gemma:2b"})  # warm cap 2000
        captured = []
        mem._bus.subscribe(
            "memory.distillation_candidate",
            lambda msg: captured.append(msg.data),
            subscriber="test",
        )

        # Fill warm tier with belief memories — 30 × ~120 tokens = 3600 > 2000
        for i in range(30):
            mid = mem.handle_remember(
                content=("held conviction " * 25)[:400] + f" #{i}",
                memory_type="belief",
                importance=0.5,
            )
            mem._memories[mid].created_at = time.time() - (30 - i) * 60

        mem.handle_consolidate()

        # Signal must have fired with a non-trivial count
        assert len(captured) >= 1, "no distillation_candidate signal"
        evt = captured[0]
        assert evt["count"] >= 1
        assert evt["token_total"] >= 100
        assert len(evt["sample_ids"]) <= 5

        # Candidate memories now have new status
        candidates = [
            m for m in mem._memories.values()
            if m.status == "candidate_for_distillation"
        ]
        assert len(candidates) == evt["count"]

        # Surviving active warm memories are under budget
        warm_active_tokens = sum(
            (m.token_count or 0) for m in mem._memories.values()
            if m.status == "active" and tier_for_type(m.memory_type) == "warm"
        )
        assert warm_active_tokens <= 2000, f"warm active {warm_active_tokens} > 2000"
        print(f"  [PASS] Phase 2 emits distillation_candidate "
              f"(count={evt['count']}, warm active tokens={warm_active_tokens}/2000)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_5_legacy_jsonl_loads():
    """Memory records without token_count field load cleanly."""
    tmp = tempfile.mkdtemp(prefix="sprint1_legacy_")
    try:
        # Write an old-format JSONL directly
        path = os.path.join(tmp, "memories.jsonl")
        legacy_record = {
            "id": "mem_0001",
            "content": "an old memory from Sprint 0",
            "memory_type": "episodic",
            "tags": ["legacy"],
            "importance": 0.6,
            "created_at": time.time() - 3600,
            "updated_at": time.time() - 3600,
            "source": "legacy_import",
            "status": "active",
            # note: no token_count, no metadata
        }
        with open(path, "w") as f:
            f.write(json.dumps(legacy_record) + "\n")

        mem = _make_block(tmp)
        recalled = mem.handle_recall("old memory", limit=5)
        assert len(recalled) == 1
        assert recalled[0].memory.id == "mem_0001"
        assert recalled[0].memory.token_count == 0, "legacy token_count defaults to 0"
        print("  [PASS] legacy JSONL loads without token_count")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_6_legacy_types_accepted():
    """Legacy 'semantic' and 'prospective' still accepted; decay at
    intermediate rates; don't break anything."""
    tmp = tempfile.mkdtemp(prefix="sprint1_legacy_types_")
    try:
        mem = _make_block(tmp, brain={"model": "claude-opus-4-6"})
        sem_id = mem.handle_remember("a conviction of old", memory_type="semantic")
        pro_id = mem.handle_remember("remember to refactor this",
                                     memory_type="prospective")
        assert mem._memories[sem_id].memory_type == "semantic"
        assert mem._memories[pro_id].memory_type == "prospective"

        # Decay multipliers per design: semantic=2.0, prospective=1.5
        assert decay_multiplier("semantic") == 2.0
        assert decay_multiplier("prospective") == 1.5

        # Tier buckets
        assert tier_for_type("semantic") == "warm"
        assert tier_for_type("prospective") == "hot"
        print("  [PASS] legacy types (semantic, prospective) work")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_7_identity_never_archived_by_budget():
    """Even if HOT/WARM blows budget, COLD (identity) never touched."""
    tmp = tempfile.mkdtemp(prefix="sprint1_cold_")
    try:
        mem = _make_block(tmp, brain={"model": "gemma:2b"})
        # 50 identity memories, very old, low importance
        for i in range(50):
            mid = mem.handle_remember(
                content="self-fact " + ("I am here " * 30) + f"#{i}",
                memory_type="identity",
                importance=0.1,
            )
            mem._memories[mid].created_at = time.time() - (100 * 24 * 3600)

        mem.handle_consolidate()
        id_active = sum(
            1 for m in mem._memories.values()
            if m.memory_type == "identity" and m.status == "active"
        )
        assert id_active == 50, f"expected all 50 identity active, got {id_active}"
        print("  [PASS] identity memories immune to budget archive")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("\n[D-024 Sprint 1 — memory extensions]")
    test_1_identity_survives_beyond_episodic_threshold()
    test_2_token_count_heuristic()
    test_3_hot_budget_enforced_on_small_slm()
    test_4_warm_overflow_emits_distillation_candidate()
    test_5_legacy_jsonl_loads()
    test_6_legacy_types_accepted()
    test_7_identity_never_archived_by_budget()
    print("\n" + "="*60)
    print("All Sprint 1 memory tests passed.")
    print("="*60)
