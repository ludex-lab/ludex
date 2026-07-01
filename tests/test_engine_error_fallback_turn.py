"""Engine-level guard: an adapter error-fallback ('[Error: ...]') arrives as a
*successful* LLMResponse (adapters return failures as content, not exceptions —
provider.handle_llm_call). The engine must surface it as a FAILED TurnResult
(error set, response blanked) so vitals (error_rate), field completion, and
downstream consumers — including the LxM bridge's exit_code — stop counting a
timed-out brain call as a success. See EngineBlock.handle_submit."""
from __future__ import annotations

from ludex.core.organism import Organism
from ludex.core.block import Block
from ludex.core.port import Port
from ludex.blocks.engine import EngineBlock


class _FakeProvider(Block):
    """Returns a fixed string from llm_call (the engine coerces str ->
    LLMResponse). Also serves an empty `recall` so handle_submit's optional
    memory port is satisfied without a MemoryBlock."""
    name = "provider"
    provides = [Port("llm_call"), Port("recall")]
    requires = []

    def __init__(self, content: str):
        super().__init__()
        self._content = content

    def handle_llm_call(self, prompt: str = "", **kwargs) -> str:
        return self._content

    def handle_recall(self, query: str = "", **kwargs) -> str:
        return ""


def _engine_returning(content: str) -> EngineBlock:
    org = Organism(
        name="t",
        blocks=[_FakeProvider(content), EngineBlock(system_prompt="You are a test creature.")],
    )
    return org.get_block("engine")


def test_error_fallback_turn_is_marked_failed():
    eng = _engine_returning("[Error: Claude CLI timed out]")
    r = eng.handle_submit("hello")
    assert r.error, "error-fallback must populate TurnResult.error"
    assert r.response == "", "error-fallback response must be blanked, not leaked downstream"
    assert r.stop_reason == "error"


def test_lowercase_and_whitespace_error_fallback_still_caught():
    eng = _engine_returning("  \n[error: gemini cli timed out]")
    r = eng.handle_submit("hello")
    assert r.error
    assert r.stop_reason == "error"


def test_normal_turn_is_unaffected():
    eng = _engine_returning("All good — I explored the ridge and rested.")
    r = eng.handle_submit("hello")
    assert not r.error, "a healthy turn must not be flagged"
    assert r.response == "All good — I explored the ridge and rested."
    assert r.stop_reason == "completed"
