"""D-085 param 4 tests — dual-thread recall (raw + consolidated narrative)."""
from __future__ import annotations

from ludex.blocks.memory import MemoryBlock
from ludex.core.config import Config

_REFLECTION = """---
window_from: 2026-04-13
window_to: 2026-06-02
consolidated_on: 2026-06-02
consolidated_on_ts: 1780000000.0
synthesizer: test (stub)
distiller: mechanical/deterministic (no LLM)
events: 100
---

## What Happened

A storm season; I learned to wait before the wind named itself.

## Identity Shifts

My pause before answering has settled from habit into identity.
"""


def _block(tmp_path, with_reflection: bool = True) -> MemoryBlock:
    mem = MemoryBlock(storage_dir=str(tmp_path / "mem"))
    cfg = Config()
    cfg.set("habitat_dir", str(tmp_path))
    mem._config = cfg
    if with_reflection:
        refl = tmp_path / "reflections"
        refl.mkdir()
        (refl / "2026-06.md").write_text(_REFLECTION, encoding="utf-8")
    mem.handle_remember("storm came and I waited it out", memory_type="episodic")
    mem.handle_remember("met a new creature near the ridge", memory_type="episodic")
    return mem


def test_dual_returns_both_threads(tmp_path):
    mem = _block(tmp_path)
    out = mem.handle_recall_dual("storm waited identity")
    assert out["raw"], "raw thread missing"
    assert out["consolidated"], "consolidated thread missing"
    top = out["consolidated"][0]
    assert top["file"] == "2026-06.md"
    assert "score" in top and top["score"] > 0


def test_no_reflections_degrades_to_ordinary_recall(tmp_path):
    mem = _block(tmp_path, with_reflection=False)
    out = mem.handle_recall_dual("storm")
    assert out["raw"]
    assert out["consolidated"] == []


def test_token_cap_never_starves_either_branch(tmp_path):
    mem = _block(tmp_path)
    # Tiny cap: still must keep at least one hit per non-empty branch.
    out = mem.handle_recall_dual("storm waited identity", token_cap=1)
    assert len(out["raw"]) >= 1
    assert len(out["consolidated"]) >= 1


def test_recency_bonus_surfaces_latest_on_zero_overlap(tmp_path):
    mem = _block(tmp_path)
    out = mem.handle_recall_dual("zzz qqq xxx")  # no term overlap at all
    assert out["consolidated"], "latest retrospective must still participate"


def test_unattached_block_is_safe(tmp_path):
    mem = MemoryBlock(storage_dir=str(tmp_path / "mem"))
    mem.handle_remember("a memory", memory_type="episodic")
    out = mem.handle_recall_dual("memory")
    assert out["consolidated"] == []
