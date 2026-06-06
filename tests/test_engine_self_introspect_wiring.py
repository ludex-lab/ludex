"""Engine wires self_introspect (D-071 option-b) into system prompt.

Per the 2026-05-01 stage discussion: every brain call should be
grounded in *who the creature is right now* by observable signals.
The engine consumes the optional `self_introspect` port and folds the
text under `[Self]` into the system prompt, alongside `[Recalled
Memory]`. Skipped silently when the port isn't wired or returns "".
"""

from __future__ import annotations

import pytest

from ludex.blocks.engine import EngineBlock
from ludex.blocks.provider import LLMResponse


def _stub_engine_with_ports(self_text: str = "", recall_text: str = "") -> tuple[EngineBlock, list[list[dict]]]:
    """Build an EngineBlock with stubbed llm_call / self_introspect /
    recall ports, returning the engine and a captured-messages list
    (mutated by llm_call stub)."""
    engine = EngineBlock(system_prompt="You are a test creature.")
    captured: list[list[dict]] = []

    def llm_call_stub(messages=None, tools=None, **kwargs):
        captured.append(list(messages or []))
        return LLMResponse(content="ok", model="stub")

    engine.connect("llm_call", llm_call_stub)

    if self_text:
        engine.connect("self_introspect", lambda: self_text)
    if recall_text:
        engine.connect("recall", lambda q: recall_text)

    # Wire the bare minimum the block needs to operate.
    from ludex.core.config import Config
    engine._config = Config()
    engine.on_attach()
    return engine, captured


def _system_content(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "system":
            return m.get("content", "")
    return ""


def test_self_introspect_folds_into_system_prompt():
    engine, captured = _stub_engine_with_ports(
        self_text="You are Hearth. You're at the `active` stage — 12.0 days old, 8 sessions."
    )
    engine.handle_submit("hello")
    sys = _system_content(captured[0])
    assert "[Self]" in sys
    assert "Hearth" in sys
    assert "active" in sys
    # Original system prompt still present
    assert "test creature" in sys


def test_self_introspect_empty_string_is_no_op():
    engine, captured = _stub_engine_with_ports(self_text="")
    engine.handle_submit("hello")
    sys = _system_content(captured[0])
    assert "[Self]" not in sys
    assert "test creature" in sys


def test_self_introspect_unconnected_port_is_no_op():
    """If no block provides self_introspect, the port resolves to None
    and the engine skips injection cleanly."""
    engine, captured = _stub_engine_with_ports()  # no self_text
    engine.handle_submit("hello")
    sys = _system_content(captured[0])
    assert "[Self]" not in sys


def test_self_introspect_works_alongside_recall():
    engine, captured = _stub_engine_with_ports(
        self_text="You are X. stage active.",
        recall_text="prior memory snippet",
    )
    engine.handle_submit("hello")
    sys = _system_content(captured[0])
    assert "[Self]" in sys
    assert "[Recalled Memory]" in sys
    # Self should appear before recalled memory (ordering choice — stage
    # is identity-grounding, recall is per-query; identity sets context
    # for the recall to land in).
    assert sys.index("[Self]") < sys.index("[Recalled Memory]")


def test_self_introspect_handler_exception_is_swallowed():
    engine = EngineBlock(system_prompt="You are stable.")
    captured: list[list[dict]] = []

    def llm_call_stub(messages=None, tools=None, **kwargs):
        captured.append(list(messages or []))
        return LLMResponse(content="ok", model="stub")

    def boom():
        raise RuntimeError("self_introspect blew up")

    engine.connect("llm_call", llm_call_stub)
    engine.connect("self_introspect", boom)
    from ludex.core.config import Config
    engine._config = Config()
    engine.on_attach()

    result = engine.handle_submit("hello")
    assert result.error in (None, ""), f"unexpected error: {result.error}"
    sys = _system_content(captured[0])
    assert "stable" in sys
    assert "[Self]" not in sys
