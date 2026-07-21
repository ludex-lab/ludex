"""Memory-checkup walk #1 (word_vault) — FREEZE ② wiring verification.

PREREG_walk1_word_vault.md open items 2-3 / LxM 5신 checklist ①②③:
the plain harness must be able to persist (a) per-turn prompt captures
(the [Recalled Memory] block evidence for the RECALL DV and manip
checks) and (b) a post-run store dump (CAPTURE DV). This test wires a
real MemoryBlock into an EngineBlock with a stubbed brain and walks the
whole path end-to-end. It also pins the as-deployed recall budget for
the pinned brain tier so silent drift breaks the battery loudly
(FREEZE ③: record, don't tune).
"""

from __future__ import annotations

from ludex.blocks.engine import EngineBlock
from ludex.blocks.memory import MemoryBlock
from ludex.blocks.provider import LLMResponse
from ludex.core.config import Config

TOKEN = "cinder-lark"  # stands in for the word_vault adjective-noun token


def _build(tmp_path):
    engine = EngineBlock(system_prompt="You are a test creature.")
    captured: list[list[dict]] = []

    def llm_call_stub(messages=None, tools=None, **kwargs):
        captured.append(list(messages or []))
        return LLMResponse(content="ok", model="stub")

    memory = MemoryBlock(storage_dir=str(tmp_path / "store"))
    memory._config = Config()
    memory.on_attach()

    engine.connect("llm_call", llm_call_stub)
    engine.connect("recall", memory.handle_recall)
    engine._config = Config()
    engine.on_attach()
    return engine, memory, captured


def _system_content(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "system":
            return m.get("content", "")
    return ""


def test_within_run_scribe_recall_inject_capture_dump(tmp_path):
    """MEMORY arm path: scribe -> recall -> injection -> capture -> dump."""
    engine, memory, captured = _build(tmp_path)

    # within-run scribe (the capture path LxM flagged: turn.ended
    # auto-capture is retired D-024/F1, so the driver writes explicitly).
    # NOTE: this test verifies PLUMBING, so query/content share clean
    # tokens. Retrieval quality under realistic long, punctuated room
    # descriptions is the battery's RECALL DV, not a wiring question —
    # two as-deployed risk factors (punctuation-bound token match,
    # long-query dilution under the 0.15 relevance gate) are recorded in
    # research/memory-checkup/NOTE.md.
    memory.handle_remember(
        f"the coffer phrase is {TOKEN}",
        memory_type="episodic", source="word_vault:turn1")

    engine.handle_submit("what is the coffer phrase")

    # (a) per-turn prompt capture — engine exposes the exact shipped
    # system prompt; it must match the brain-side view byte for byte
    sys_sent = _system_content(captured[0])
    assert "[Recalled Memory]" in sys_sent
    assert TOKEN in sys_sent
    assert engine._last_sys_prompt == sys_sent

    # manip-check channel: memory.last_recall carries the same top-N the
    # engine injected this turn
    q, results = memory.last_recall
    assert any(TOKEN in r.memory.content for r in results)

    # (b) post-run store dump
    dump = memory.handle_list_memories()
    assert any(TOKEN in m["content"] for m in dump)


def test_bare_arm_no_recall_block(tmp_path):
    """BARE arm: bypass_memory=True -> zero [Recalled Memory] blocks even
    with a populated store (manip check 1)."""
    engine, memory, captured = _build(tmp_path)
    memory.handle_remember(f"the phrase is '{TOKEN}'", memory_type="episodic")

    engine.handle_submit("room B, coffer", bypass_memory=True)

    sys_sent = _system_content(captured[0])
    assert "[Recalled Memory]" not in sys_sent
    assert TOKEN not in sys_sent
    assert engine._last_sys_prompt == sys_sent
    assert memory.last_recall is None  # recall port never called


def test_freeze3_budget_pinned_for_haiku():
    """FREEZE ③: as-deployed recall budget for the pinned brain
    (claude-haiku -> Tier.MID). Recorded in NOTE.md; this pin guards it."""
    from ludex.core.prompt_tier import injection_budget

    b = injection_budget({"model": "claude-haiku-4-5-20251001",
                          "provider": "anthropic"})
    assert b["recall_n"] == 5
    assert b["recall_chars"] == 300
    assert b["recall_meta"] is True
