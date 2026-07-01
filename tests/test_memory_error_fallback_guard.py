"""Storage-boundary guard: adapter error-fallbacks must never be persisted as
memories (regression for the 2026-06-13/18 contamination — timed-out brain
calls written as '<name> @<field>: [Error: ... CLI timed out]' episodic/identity
memories via the LxM turn-capture path). See MemoryBlock.handle_remember and
selfhood._is_error_fallback."""
from __future__ import annotations

from ludex.blocks.memory import MemoryBlock


def _mem(tmp_path) -> MemoryBlock:
    return MemoryBlock(storage_dir=str(tmp_path / "mem"))


def test_error_fallback_is_not_stored(tmp_path):
    m = _mem(tmp_path)

    # Raw adapter fallback (the whole response is the error).
    assert m.handle_remember("[Error: Claude CLI timed out]") == ""

    # LxM turn-capture shape: '<name> @<field>: <response>'.
    assert m.handle_remember(
        "aria @blockworld_v2: [Error: Gemini CLI timed out]",
        memory_type="episodic", source="lxm/blockworld_v2/turn",
    ) == ""

    # Reflection-capture shape with a colon inside the prefix.
    assert m.handle_remember(
        "Self-reflection (heartbeat:stale_bonds=['nova']): [Error: Gemini CLI timed out]",
        memory_type="identity", source="reflection/heartbeat",
    ) == ""

    # Multi-line Gemini key error (DOTALL must match across the newline).
    multiline = (
        "flare @reflect: [Error: When using Gemini API, you must specify the "
        "GEMINI_API_KEY environment variable.\n"
        "Update your environment and try again (no reload needed if using .env)!]"
    )
    assert m.handle_remember(multiline) == ""

    assert len(m._memories) == 0, "no error-fallback may reach the store"


def test_normal_memory_still_stored(tmp_path):
    m = _mem(tmp_path)

    mid = m.handle_remember(
        "I shared a wilderness with Primo and felt steadier afterward.",
        memory_type="episodic",
    )
    assert mid
    assert len(m._memories) == 1

    # A memory that merely mentions an error mid-sentence is NOT rejected —
    # the guard requires a trailing '[Error: ...]' adapter signature.
    mid2 = m.handle_remember(
        "I learned that handling an error calmly keeps me grounded."
    )
    assert mid2
    assert len(m._memories) == 2
