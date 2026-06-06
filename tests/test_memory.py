"""
Phase 4b Tests — Memory Block

1. Remember: 기억 저장 (episodic, semantic, prospective)
2. Recall: 기억 검색 (키워드, 태그, 타입 필터)
3. Forget: 기억 삭제/보관
4. Consolidate: 기억 정리 (Dream 기초)
5. Persistence: 파일 저장/로드
6. Integration: Engine과 연동 (auto-capture)
"""

import sys
import os
import time
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism import Organism
from ludex.core.bus import Bus
from ludex.core.signals import Signals
from ludex.core.config import Config
from ludex.core.block import Block
from ludex.core.port import Port
from ludex.blocks.provider import LLMResponse
from ludex.blocks.engine import EngineBlock
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.memory import MemoryBlock, Memory, ConsolidationReport


class MockProvider(Block):
    name = "provider"
    provides = [Port("llm_call"), Port("health_check"), Port("list_models")]
    requires = []
    def handle_llm_call(self, prompt="", **kwargs):
        return LLMResponse(content=f"Response to: {prompt[:30]}", model="mock",
                           tokens_in=10, tokens_out=5, latency_ms=50.0)
    def handle_health_check(self): return {"status": "healthy"}
    def handle_list_models(self): return ["mock"]


# Temp directory for test storage
test_dir = None
_test_counter = [0]

def setup_module(module):
    global test_dir
    test_dir = tempfile.mkdtemp(prefix="ludex_test_memory_")

def teardown_module(module):
    global test_dir
    if test_dir and os.path.exists(test_dir):
        shutil.rmtree(test_dir)


# ============================================================
# 1. Remember Tests
# ============================================================

def test_remember_episodic():
    """기억 저장: episodic"""
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    mem_id = mem.handle_remember("llama betrayed in round 5", memory_type="episodic",
                                  tags=["trust_game", "llama"], importance=0.7)
    assert mem_id.startswith("mem_")
    assert mem.count == 1
    print("  [PASS] Remember episodic")

def test_remember_semantic():
    """기억 저장: semantic"""
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    mem.handle_remember("exaone cooperates 100% of the time", memory_type="semantic",
                         tags=["exaone", "cooperation"], importance=0.9)
    stats = mem.stats()
    assert stats["semantic"] == 1
    print("  [PASS] Remember semantic")

def test_remember_prospective():
    """기억 저장: prospective (할 일)"""
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    mem.handle_remember("Send pilot code to Luca", memory_type="prospective",
                         tags=["luca", "emotion_benchmark"], importance=0.8)
    stats = mem.stats()
    assert stats["prospective"] == 1
    print("  [PASS] Remember prospective")


# ============================================================
# 2. Recall Tests
# ============================================================

def test_recall_keyword():
    """기억 검색: 키워드"""
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    mem.handle_remember("llama betrayed in Trust Game round 5", tags=["llama"])
    mem.handle_remember("exaone always cooperates", tags=["exaone"])
    mem.handle_remember("mistral retaliates after betrayal", tags=["mistral"])

    results = mem.handle_recall("llama betrayed")
    assert len(results) > 0
    assert "llama" in results[0].memory.content.lower()
    print("  [PASS] Recall keyword")

def test_recall_by_type():
    """기억 검색: 타입 필터"""
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    mem.handle_remember("experiment result", memory_type="episodic")
    mem.handle_remember("general fact", memory_type="semantic")

    results = mem.handle_recall("result", memory_type="episodic")
    assert all(r.memory.memory_type == "episodic" for r in results)
    print("  [PASS] Recall by type")

def test_recall_by_tags():
    """기억 검색: 태그 필터"""
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    mem.handle_remember("poker result 1", tags=["poker"])
    mem.handle_remember("trust result 1", tags=["trust_game"])
    mem.handle_remember("poker result 2", tags=["poker"])

    results = mem.handle_recall("result", tags=["poker"])
    assert len(results) == 2
    print("  [PASS] Recall by tags")

def test_recall_empty():
    """기억 검색: 결과 없음"""
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    mem.handle_remember("something about chess", tags=["chess"])

    results = mem.handle_recall("quantum physics")
    assert len(results) == 0
    print("  [PASS] Recall empty")


def test_recall_excludes_deprecated_tag():
    """D-067 bond-memory leak (2026-04-30): tags starting with
    ``deprecated:`` exclude the memory from recall surface even
    when status is active. Archaeological record preserved on disk."""
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    mem.handle_remember("avalon team proposal hearth bot_b", tags=["lxm", "physis_smoke_015"])
    mem.handle_remember("avalon team proposal hearth flint", tags=["lxm", "physis_smoke_013", "deprecated:lxm_leak"])
    mem.handle_remember("avalon team proposal hearth bot_c", tags=["lxm", "physis_smoke_017"])

    results = mem.handle_recall("avalon team proposal")
    contents = [r.memory.content for r in results]
    assert any("bot_b" in c for c in contents)
    assert any("bot_c" in c for c in contents)
    assert not any("flint" in c for c in contents), "deprecated: tagged memory should be excluded"

    # Memory still on disk — exclusion is at recall surface only.
    all_active = [m for m in mem._memories.values() if m.status == "active"]
    assert any("flint" in m.content for m in all_active), "memory must persist on disk"
    print("  [PASS] Recall excludes deprecated: prefix")


def test_last_recall_exposure():
    """last_recall caches (query, results) after handle_recall — joint
    spec §A.3 callers (LxM adapter) consume this to avoid re-running
    TF-IDF when they want the turn-level recall snapshot."""
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    # Pristine block: no recall performed yet
    assert mem.last_recall is None

    mem.handle_remember("I chose to cooperate in round 5", memory_type="episodic")
    mem.handle_remember("Wilderness taught me about choice", memory_type="semantic", tags=["dream"])

    returned = mem.handle_recall("cooperate choice")
    cached = mem.last_recall
    assert cached is not None
    query, results = cached
    assert query == "cooperate choice"
    assert len(results) == len(returned)
    # Identity of list contents (same RecallResult objects)
    for r_cached, r_returned in zip(results, returned):
        assert r_cached.memory.id == r_returned.memory.id
        assert r_cached.relevance == r_returned.relevance
    print("  [PASS] last_recall exposure")


# ============================================================
# 3. Forget Tests
# ============================================================

def test_forget_archive():
    """기억 삭제: 보관 (soft delete)"""
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    mem_id = mem.handle_remember("temporary note")
    assert mem.count == 1

    mem.handle_forget(mem_id)
    assert mem.count == 0  # archived, not counted
    print("  [PASS] Forget archive")

def test_forget_hard_delete():
    """기억 삭제: 완전 삭제"""
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    mem_id = mem.handle_remember("delete me")
    mem.handle_forget(mem_id, hard_delete=True)
    assert mem.count == 0
    print("  [PASS] Forget hard delete")


# ============================================================
# 4. Consolidate Tests
# ============================================================

def test_consolidate_promote():
    """기억 정리: episodic → belief 승격 (D-024 Sprint 1 vocabulary).

    Was `semantic` in Sprint 0; `belief` is the successor type in
    the 8-type taxonomy. Legacy `semantic` still readable/writable
    for backward compatibility, but new promotions use `belief`.
    """
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    for i in range(4):
        mem.handle_remember(f"llama defected in game {i}", memory_type="episodic",
                             tags=["llama_defection"])

    report = mem.handle_consolidate()
    assert report.promoted >= 1

    beliefs = mem.handle_list_memories(memory_type="belief")
    assert len(beliefs) >= 1
    assert any("consolidated" in m.get("tags", []) for m in beliefs)
    print("  [PASS] Consolidate promote")

def test_consolidate_archive_old():
    """기억 정리: 오래된 저중요도 episodic 보관.

    Effective-age threshold (D-024 Sprint 1): episodic has decay
    multiplier 1.5, so raw age needs to be > 1.5 × 7 days = 10.5
    days for archive eligibility. Use 12 days to be safely past.
    """
    _test_counter[0] += 1; mem = MemoryBlock(storage_dir=os.path.join(test_dir, f"t{_test_counter[0]}"))
    mem.attach(Bus(), Signals(), Config())

    old_time = time.time() - (12 * 24 * 3600)
    mem_id = mem.handle_remember("old unimportant note", memory_type="episodic", importance=0.1)
    mem._memories[mem_id].created_at = old_time

    report = mem.handle_consolidate()
    assert report.archived >= 1
    print("  [PASS] Consolidate archive old")


# ============================================================
# 5. Persistence Tests
# ============================================================

def test_persistence_save_load():
    """파일 저장/로드"""
    storage = os.path.join(test_dir, "persist_test")

    # 저장
    mem1 = MemoryBlock(storage_dir=storage)
    mem1.attach(Bus(), Signals(), Config())
    mem1.handle_remember("persistent memory 1", tags=["test"])
    mem1.handle_remember("persistent memory 2", tags=["test"])
    mem1.on_detach()  # 저장 트리거

    # 로드
    mem2 = MemoryBlock(storage_dir=storage)
    mem2.attach(Bus(), Signals(), Config())

    assert mem2.count == 2
    results = mem2.handle_recall("persistent")
    assert len(results) == 2
    print("  [PASS] Persistence save/load")


# ============================================================
# 6. Integration Tests
# ============================================================

def test_auto_capture():
    """Engine 연동: 턴 성공은 memory 에 남기지 않음, 실패만 transient 로 기록.

    D-024 Sprint 3 (2026-04-16): auto_capture of successful turns
    was found to dominate 44-72% of creature memory roster-wide and
    nothing read those entries. Success turns are now no-ops in
    memory; only errors land (as transient, so they archive fast).
    """
    storage = os.path.join(test_dir, "auto_capture")

    org = Organism(
        name="memory-integration",
        blocks=[
            MockProvider(),
            EngineBlock(max_turns=5),
            MemoryBlock(storage_dir=storage, auto_capture=True),
        ],
        config={"model": "mock"},
    )

    engine = org.get_block("engine")
    memory = org.get_block("memory")

    for i in range(3):
        engine.handle_submit(f"Test turn {i+1}")

    # Successful turns must not create episodic memories
    episodics = memory.handle_list_memories(memory_type="episodic")
    turn_episodics = [e for e in episodics if "turn" in e.get("tags", [])]
    assert len(turn_episodics) == 0, f"success turns leaked to episodic: {turn_episodics}"
    # No transient memory either (all turns succeeded in mock)
    transients = memory.handle_list_memories(memory_type="transient")
    assert len(transients) == 0
    print("  [PASS] Auto-capture suppresses success turns")

def test_recall_in_engine():
    """Engine이 Memory에서 리콜"""
    storage = os.path.join(test_dir, "engine_recall")

    org = Organism(
        name="recall-integration",
        blocks=[
            MockProvider(),
            EngineBlock(max_turns=5),
            MemoryBlock(storage_dir=storage, auto_capture=False),
        ],
        config={"model": "mock"},
    )

    memory = org.get_block("memory")
    engine = org.get_block("engine")

    # 사전에 기억 저장
    memory.handle_remember("The opponent always defects on the last round",
                            memory_type="semantic", tags=["strategy"])

    # Engine이 recall 포트를 호출하면 Memory에서 검색
    # (Engine의 handle_submit이 내부에서 call_port("recall", prompt)를 호출)
    result = engine.handle_submit("What should I do in the last round?")
    assert result.stop_reason == "completed"
    print("  [PASS] Recall in Engine")

def test_memory_vitals():
    """Vital Signs에 기억 통계 반영"""
    storage = os.path.join(test_dir, "vitals_test")

    org = Organism(
        name="vitals-memory",
        blocks=[
            MockProvider(),
            EngineBlock(max_turns=5),
            MemoryBlock(storage_dir=storage),
        ],
        config={"model": "mock"},
    )

    memory = org.get_block("memory")
    memory.handle_remember("test memory", importance=0.8)

    vitals = org.measure_vitals()
    assert vitals.memory_entries >= 1
    print("  [PASS] Memory vitals")


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Ludex Phase 4b — Memory Block Tests")
    print("=" * 60)

    setup()

    try:
        print("\n[Remember]")
        test_remember_episodic()
        test_remember_semantic()
        test_remember_prospective()

        print("\n[Recall]")
        test_recall_keyword()
        test_recall_by_type()
        test_recall_by_tags()
        test_recall_empty()
        test_recall_excludes_deprecated_tag()
        test_last_recall_exposure()

        print("\n[Forget]")
        test_forget_archive()
        test_forget_hard_delete()

        print("\n[Consolidate]")
        test_consolidate_promote()
        test_consolidate_archive_old()

        print("\n[Persistence]")
        test_persistence_save_load()

        print("\n[Integration]")
        test_auto_capture()
        test_recall_in_engine()
        test_memory_vitals()

        print("\n" + "=" * 60)
        print("All Phase 4b Memory tests passed!")
        print("=" * 60)

    finally:
        teardown()
