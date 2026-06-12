"""Tier-scaled injection budget tests (2026-06-12, whitepaper §0 P5/§6).

The scaffolding-dilemma fix: the engine's per-call injection (identity
floor + recalled memory) is guaranteed to every brain, but its SIZE is
fitted to the brain — payload shrinks as the brain shrinks. Three rules
under test:
1. The budget table shrinks monotonically down the tiers.
2. The floor never degrades to zero content — every tier's recall keeps
   real memory text (the 06-11 continuity fix must hold for SLMs too).
3. New-model tier patterns: gpt-5.5 is LARGE, *-nano is MID_SLM.
"""
from __future__ import annotations

from ludex.core.prompt_tier import (
    INJECTION_BUDGET, Tier, injection_budget, tier_of,
)


TIER_ORDER = [Tier.LARGE, Tier.MID, Tier.LARGE_SLM, Tier.MID_SLM, Tier.SMALL_SLM]


def test_budget_covers_all_tiers():
    assert set(INJECTION_BUDGET) == set(Tier)


def test_budget_shrinks_monotonically():
    for key in ("recall_n", "recall_chars", "self_chars"):
        vals = [INJECTION_BUDGET[t][key] for t in TIER_ORDER]
        assert vals == sorted(vals, reverse=True), \
            f"{key} must not grow as the brain shrinks: {vals}"


def test_budget_floor_never_zero():
    for t in TIER_ORDER:
        b = INJECTION_BUDGET[t]
        assert b["recall_n"] >= 2 and b["recall_chars"] >= 100 \
            and b["self_chars"] >= 100, \
            f"{t}: the continuity floor must keep real content"


def test_unknown_brain_gets_mid_budget():
    assert injection_budget({"model": "mystery-9000"}) == INJECTION_BUDGET[Tier.MID]
    assert injection_budget(None) == INJECTION_BUDGET[Tier.MID]


def test_new_model_tier_patterns():
    assert tier_of({"model": "gpt-5.5"}) == Tier.LARGE
    assert tier_of({"model": "gpt-5.4-nano"}) == Tier.MID_SLM
    assert tier_of({"model": "claude-opus-4-8"}) == Tier.LARGE
    assert tier_of({"model": "claude-haiku-4-5"}) == Tier.MID


def test_self_compressed_respects_max_chars(tmp_path):
    from ludex.core.selfhood import load_self_compressed
    (tmp_path / "SELF.md").write_text(
        "# Self\n" + "\n".join(f"- pattern number {i}: I notice that I "
                               f"tend toward steadiness under pressure" for i in range(20)),
        encoding="utf-8")
    full = load_self_compressed(str(tmp_path), max_chars=600)
    slim = load_self_compressed(str(tmp_path), max_chars=150)
    assert len(slim) < len(full)
    # "[Self] " prefix + budget + "..." suffix
    assert len(slim) <= len("[Self] ") + 150 + 3
    assert slim.startswith("[Self] pattern number 0")


class _FakeMemory:
    def __init__(self, content, importance=0.8, tags=None):
        self.content = content
        self.importance = importance
        self.tags = tags or ["reflection", "forum"]


class _FakeRecall:
    def __init__(self, content):
        self.memory = _FakeMemory(content)
        self.relevance = 1.0


def _formatter():
    from ludex.blocks.engine import EngineBlock
    return EngineBlock()._format_memory_context


def test_formatter_caps_chars_and_meta():
    fmt = _formatter()
    raw = [_FakeRecall("x" * 1000)]
    rich = fmt(raw, max_chars=400, include_meta=True)
    slim = fmt(raw, max_chars=120, include_meta=False)
    assert "importance=" in rich and "[reflection" in rich
    assert "importance=" not in slim and "[reflection" not in slim
    assert len(slim) < len(rich)
    # content survives at every budget — never an empty aggregate
    assert slim.startswith("- xxx")


def test_formatter_keeps_content_at_smallest_budget():
    fmt = _formatter()
    raw = [_FakeRecall("Self-reflection (forum): I held false at 0.95")]
    out = fmt(raw, max_chars=INJECTION_BUDGET[Tier.SMALL_SLM]["recall_chars"],
              include_meta=False)
    assert "Self-reflection (forum)" in out
