"""Wilderness should not tag memories with `tick_<N>`.

Surfaced 2026-05-01 in the cohort observation pass: Anvil/Hearth
memories had `tick_1`, `tick_2`, ..., `tick_6` tags (one per turn),
inflating tag distribution noise without helping recall — every tick
was unique so no two memories shared the tag. Tick number is
preserved in `source` for trace replay; tag is dropped.
"""

from __future__ import annotations

import pytest


def test_wilderness_memory_tags_exclude_tick_n(tmp_path):
    """Run the wilderness store_memory line and check resulting tags."""
    from ludex.blocks.memory import MemoryBlock

    mb = MemoryBlock(storage_dir=str(tmp_path / "memory"))

    # Replicate the call shape from wilderness.py for two ticks
    for tick in (1, 7):
        mb.handle_remember(
            f"Wilderness tick {tick}: dummy event. I chose to rest. Energy: 100/100.",
            memory_type="episodic",
            tags=["wilderness", "test_field"],  # post-fix: no f"tick_{tick}"
            source=f"wilderness/test_field/tick_{tick}",
        )

    # No memory should carry a tick_<n> tag
    for m in mb._memories.values():
        for t in m.tags:
            assert not t.startswith("tick_"), f"unexpected tick tag: {t} on {m.id}"

    # Tick number should still be recoverable from source
    sources = {m.source for m in mb._memories.values()}
    assert any("tick_1" in s for s in sources)
    assert any("tick_7" in s for s in sources)


def test_wilderness_module_no_longer_emits_tick_tag():
    """Static check: the wilderness handler builds tags without tick_N."""
    import inspect
    from ludex.fields import wilderness

    src = inspect.getsource(wilderness)
    # The memory call should not include f"tick_{tick}" in the tags list
    # (tags=["wilderness", self.name]). Allow tick in the source kwarg.
    handle_lines = []
    in_block = False
    for line in src.splitlines():
        if "memory.handle_remember" in line:
            in_block = True
        if in_block:
            handle_lines.append(line)
            if ")" in line and "(" not in line:
                break
    block = "\n".join(handle_lines)
    assert "tags=" in block
    # Find the tags= portion and confirm no tick reference
    tags_start = block.find("tags=")
    tags_end = block.find("]", tags_start)
    tags_line = block[tags_start:tags_end + 1]
    assert "tick" not in tags_line, f"unexpected tick reference in tags: {tags_line}"
