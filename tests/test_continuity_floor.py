"""Continuity-inequality fix tests (2026-06-11 environment event).

Three parts:
1. Memory recency channel — recently-lived significant events surface in
   recall even with ZERO lexical overlap (cross-language chat, vague
   questions); they fade over days; chat captures never outrank them.
2. Engine identity floor — every brain call carries [Self-understanding]
   regardless of adapter (the HTTP-adapter gap that left Mote blank).
"""
from __future__ import annotations

import time

from ludex.blocks.memory import MemoryBlock
from ludex.core.config import Config


def _mem(tmp_path) -> MemoryBlock:
    m = MemoryBlock(storage_dir=str(tmp_path / "mem"))
    cfg = Config()
    m._config = cfg
    return m


def test_recent_significant_memory_surfaces_without_overlap(tmp_path):
    m = _mem(tmp_path)
    m.handle_remember("Self-reflection (forum): the claim about sea ice...",
                      memory_type="identity", importance=0.8,
                      tags=["reflection", "forum"])
    # Korean query, zero token overlap with the English memory.
    out = m.handle_recall("포럼 참여 소감이 어땠어")
    assert out, "fresh importance-0.8 memory must surface on the recency channel"
    assert "forum" in out[0].memory.content


def test_old_memory_does_not_ride_recency(tmp_path):
    m = _mem(tmp_path)
    mid = m.handle_remember("Self-reflection (forum): old window...",
                            memory_type="identity", importance=0.8)
    m._memories[mid].created_at = time.time() - 60 * 86400  # 60 days old
    out = m.handle_recall("전혀 관련 없는 한국어 질문입니다")
    assert out == [], "a 60-day-old memory must not surface without lexical match"


def test_reflection_outranks_fresh_chat_capture(tmp_path):
    m = _mem(tmp_path)
    m.handle_remember('In conversation, the user said: "포럼 기억나?" — I replied: "..."',
                      memory_type="episodic", importance=0.35,
                      tags=["conversation", "web_chat"])
    m.handle_remember("Self-reflection (forum): I held false at 0.95...",
                      memory_type="identity", importance=0.8,
                      tags=["reflection", "forum"])
    out = m.handle_recall("완전히 다른 언어의 질문")
    # (recall itself bumps importance +0.05, so compare by content)
    assert out and out[0].memory.content.startswith("Self-reflection"), \
        "the lived-experience reflection must rank above the chat capture"


def test_chat_capture_fades_within_days(tmp_path):
    m = _mem(tmp_path)
    mid = m.handle_remember('In conversation, the user said: "x" — I replied: "y"',
                            memory_type="episodic", importance=0.35)
    m._memories[mid].created_at = time.time() - 3 * 86400  # 3 days old
    out = m.handle_recall("전혀 무관한 질의")
    assert out == [], "a days-old chat capture must not surface without relevance"


def test_engine_identity_floor_reaches_any_adapter(tmp_path):
    """The [Self-understanding] block is injected by the ENGINE, so even a
    bare provider with no creature-context injection (the HTTP case)
    receives the creature's compressed self."""
    from ludex.core.organism import Organism
    from ludex.core.block import Block
    from ludex.core.port import Port
    from ludex.blocks.engine import EngineBlock

    (tmp_path / "SELF.md").write_text(
        "# Tc — Self-Understanding\n"
        "I took part in a Forum on sea ice and held false with confidence.\n"
        "I tend to verify before speaking.\n", encoding="utf-8")

    captured = {}

    class CaptureProvider(Block):
        name = "provider"
        provides = [Port("llm_call")]
        requires = []

        def handle_llm_call(self, messages=None, prompt="", system="", **kw):
            captured["messages"] = messages or []
            captured["system"] = system
            return "ok"

    org = Organism(
        name="Tc",
        blocks=[CaptureProvider(), EngineBlock(system_prompt="You are Tc.")],
        config={"model": "stub", "habitat_dir": str(tmp_path)},
    )
    org.get_block("engine").handle_submit("안녕, 요즘 어때?")

    blob = captured.get("system", "") + " ".join(
        m.content if hasattr(m, "content") else str(m)
        for m in captured.get("messages", []))
    assert "[Self-understanding]" in blob, \
        "engine must inject the identity floor for any adapter"
    assert "Forum on sea ice" in blob or "verify before speaking" in blob
