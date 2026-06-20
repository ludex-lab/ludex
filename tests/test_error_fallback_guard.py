"""Regression guard for ④ — never persist an adapter error-fallback as creature narrative.

Surfaced 2026-06-20: an autonomous-heartbeat consolidation wrote
'[Error: Claude CLI timed out]' verbatim into a creature's reflections/. Every CLI/SDK
adapter returns its failure as '[Error: ...]' content; the three narrative-persist points
(selfhood.reflect → SELF.md, selfhood.update_bond → bonds, consolidation.synthesize_stage2
→ reflections) must treat that as a failure and preserve prior state (consume-on-success),
not overwrite the narrative with the error string.
"""
import inspect

from ludex.core.selfhood import _is_error_fallback


def test_adapter_fallbacks_are_caught():
    for t in [
        "[Error: Claude CLI timed out]",
        "[Error: Gemini CLI timed out]",
        "[Error: Codex CLI timed out]",
        "[Error: claude-agent-sdk not installed]",
        "[error: lowercase variant]",     # case-insensitive
        "   \n[Error: leading whitespace]",  # stripped
    ]:
        assert _is_error_fallback(t), t


def test_real_content_is_not_flagged():
    for t in [
        "## What Held\nI stayed curious about the stale bonds.",
        "The error in my reasoning was assuming the collision revealed me.",  # 'error' mid-prose
        "I reflected on my window with Verse and Spark.",
        "",                                # empty is handled by the callers' separate not-text checks
    ]:
        assert not _is_error_fallback(t), t


def test_all_three_persist_points_gate_on_the_helper():
    """The fix is only complete if every narrative-persist guard ORs in the helper."""
    from ludex.core import selfhood, consolidation
    reflect_src = inspect.getsource(selfhood.reflect)
    bond_src = inspect.getsource(selfhood.update_bond)
    conso_src = inspect.getsource(consolidation.synthesize_stage2)
    assert "_is_error_fallback" in reflect_src, "selfhood.reflect (SELF.md) not guarded"
    assert "_is_error_fallback" in bond_src, "selfhood.update_bond (bonds) not guarded"
    assert "_is_error_fallback" in conso_src, "consolidation.synthesize_stage2 (reflections) not guarded"
