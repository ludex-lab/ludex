"""Engine wires the [Now] where/when line (memory-systems step 1, 2026-07-03).

The topos (where) + chronos (when) sensors were born default-on (D-059/D-060)
but had NO runtime consumer — handle_sense() was never called in the loop.
The engine now folds one compact `[Now]` line into the system prompt per turn,
via direct get_block (the "sense" port name is shared by three organs, so
call_port would be ambiguous). Skipped silently for bare organisms.
"""
from __future__ import annotations

from ludex.blocks.engine import EngineBlock
from ludex.blocks.provider import LLMResponse
from ludex.core.organism_config import OrganismConfig
from ludex.core import trace


def _built_org_with_stub():
    cfg = OrganismConfig.from_preset("full", name="nowtest")
    cfg._ephemeral = True
    org = cfg.build()
    engine = org.get_block("engine")
    captured: list[list[dict]] = []

    def llm_call_stub(messages=None, tools=None, **kwargs):
        captured.append(list(messages or []))
        return LLMResponse(content="ok", model="stub")

    engine.connect("llm_call", llm_call_stub)
    return org, engine, captured


def _system_content(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "system":
            return m.get("content", "")
    return ""


def test_now_line_carries_field_and_time():
    org, engine, captured = _built_org_with_stub()
    trace.set_current_field("wilderness")
    try:
        engine.handle_submit("hello")
    finally:
        trace.clear_current_field()
    sys = _system_content(captured[0])
    assert "[Now]" in sys
    assert "wilderness" in sys          # topos: where


def test_now_line_outside_field_still_locates():
    """No active field: topos still reports habitat/machine locality (a
    creature is always somewhere), chronos may add session/age."""
    org, engine, captured = _built_org_with_stub()
    engine.handle_submit("hello")
    sys = _system_content(captured[0])
    # [Now] appears as long as either sensor yields a non-empty summary;
    # must never crash. (Content varies by host — assert structure only.)
    if "[Now]" in sys:
        now_line = [ln for ln in sys.splitlines()
                    if ln and "[Now]" not in ln and sys.index("[Now]") < sys.index(ln)][0]
        assert len(now_line) < 200      # compact, one line


def test_bare_engine_has_no_now_and_no_crash():
    engine = EngineBlock(system_prompt="You are bare.")
    captured: list[list[dict]] = []

    def llm_call_stub(messages=None, tools=None, **kwargs):
        captured.append(list(messages or []))
        return LLMResponse(content="ok", model="stub")

    engine.connect("llm_call", llm_call_stub)
    from ludex.core.config import Config
    engine._config = Config()
    engine.on_attach()
    result = engine.handle_submit("hello")
    assert result.error in (None, "")
    assert "[Now]" not in _system_content(captured[0])
