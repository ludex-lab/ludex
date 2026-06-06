"""
Phase 3 Tests — Registry + Hooks Blocks

1. Registry: 도구 등록, 실행, 필터링, LLM 스키마
2. Hooks: 라이프사이클 훅, 우선순위, 체이닝
3. 통합: 게임 에이전트 시나리오 (도구 + 훅 + Engine)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism import Organism
from ludex.core.block import Block
from ludex.core.port import Port
from ludex.blocks.provider import LLMResponse, LLMError
from ludex.blocks.engine import EngineBlock
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.resilience import ResilienceBlock
from ludex.blocks.registry import RegistryBlock, ToolDefinition, ToolResult
from ludex.blocks.hooks import HooksBlock, HookDefinition


# Mock Provider
class MockProviderBlock(Block):
    name = "provider"
    provides = [Port("llm_call"), Port("health_check"), Port("list_models")]
    requires = []

    def handle_llm_call(self, prompt="", system="", tools=None, **kwargs):
        response = LLMResponse(
            content=f"I choose to bet 100", model=self._cfg("model", "mock"),
            tokens_in=10, tokens_out=5, latency_ms=50.0,
        )
        self._publish("llm.response", {
            "model": self._cfg("model", "mock"), "tokens_in": 10,
            "tokens_out": 5, "latency_ms": 50.0,
        })
        return response

    def handle_health_check(self):
        return {"status": "healthy"}

    def handle_list_models(self):
        return ["mock"]


# ============================================================
# 1. Registry Tests
# ============================================================

def test_registry_register_and_get():
    """Registry: 도구 등록 + 조회"""
    registry = RegistryBlock(tools=[
        ToolDefinition(name="bet", description="Place a bet", tags=("poker",)),
        ToolDefinition(name="fold", description="Fold hand", tags=("poker",)),
        ToolDefinition(name="move", description="Move a piece", tags=("chess",)),
    ])
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config
    registry.attach(Bus(), Signals(), Config())

    all_tools = registry.handle_get_tools()
    assert len(all_tools) == 3

    poker_tools = registry.handle_get_tools(tags=["poker"])
    assert len(poker_tools) == 2
    assert all(t.name in ("bet", "fold") for t in poker_tools)

    names = registry.handle_get_tools(format="names")
    assert "bet" in names
    print("  [PASS] Registry register and get")

def test_registry_execute_tool():
    """Registry: 도구 실행"""
    registry = RegistryBlock()
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config
    registry.attach(Bus(), Signals(), Config())

    # 핸들러와 함께 등록
    registry.handle_register_tool(
        ToolDefinition(name="add", description="Add two numbers"),
        handler=lambda a, b: a + b,
    )

    result = registry.handle_execute_tool("add", {"a": 3, "b": 5})
    assert result.success
    assert result.output == 8
    print("  [PASS] Registry execute tool")

def test_registry_permission_deny():
    """Registry: 도구 권한 차단 (CC ToolPermissionContext)"""
    registry = RegistryBlock(tools=[
        ToolDefinition(name="bet", description="Bet"),
        ToolDefinition(name="dangerous_hack", description="Bad"),
    ])
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config
    registry.attach(Bus(), Signals(), Config())

    registry.deny_tool("dangerous_hack")

    tools = registry.handle_get_tools(format="names")
    assert "bet" in tools
    assert "dangerous_hack" not in tools

    result = registry.handle_execute_tool("dangerous_hack", {})
    assert not result.success
    assert "denied" in result.error
    print("  [PASS] Registry permission deny")

def test_registry_llm_schema():
    """Registry: LLM용 도구 스키마 변환"""
    registry = RegistryBlock(tools=[
        ToolDefinition(
            name="bet",
            description="Place a bet",
            parameters={"type": "object", "properties": {"amount": {"type": "integer"}}},
        ),
    ])
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config
    registry.attach(Bus(), Signals(), Config())

    schemas = registry.handle_get_tools(format="llm")
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "bet"
    assert "amount" in schemas[0]["function"]["parameters"]["properties"]
    print("  [PASS] Registry LLM schema")

def test_registry_dynamic_registration():
    """Registry: 런타임 도구 추가 (OC registerTool 패턴)"""
    registry = RegistryBlock()
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config
    registry.attach(Bus(), Signals(), Config())

    assert len(registry.handle_get_tools()) == 0

    registry.handle_register_tool(
        ToolDefinition(name="new_tool", description="Added at runtime"),
        handler=lambda: "works",
    )

    assert len(registry.handle_get_tools()) == 1
    result = registry.handle_execute_tool("new_tool", {})
    assert result.output == "works"
    print("  [PASS] Registry dynamic registration")


# ============================================================
# 2. Hooks Tests
# ============================================================

def test_hooks_register_and_run():
    """Hooks: 훅 등록 + 수동 실행"""
    hooks = HooksBlock()
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config
    hooks.attach(Bus(), Signals(), Config())

    results = []
    hooks.before_turn("log_turn", lambda **kw: results.append("before_turn fired"))

    hooks.handle_run_hook("before_turn", turn_number=1)
    assert len(results) == 1
    print("  [PASS] Hooks register and run")

def test_hooks_priority():
    """Hooks: 우선순위 순 실행 (OC 패턴)"""
    hooks = HooksBlock()
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config
    hooks.attach(Bus(), Signals(), Config())

    order = []
    hooks.before_turn("third", lambda **kw: order.append("C"), priority=10)
    hooks.before_turn("first", lambda **kw: order.append("A"), priority=0)
    hooks.before_turn("second", lambda **kw: order.append("B"), priority=5)

    hooks.handle_run_hook("before_turn")
    assert order == ["A", "B", "C"]
    print("  [PASS] Hooks priority ordering")

def test_hooks_chaining():
    """Hooks: 반환값 체이닝"""
    hooks = HooksBlock()
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config
    hooks.attach(Bus(), Signals(), Config())

    # 첫 번째 훅이 board_state를 추가
    hooks.handle_register_hook(HookDefinition(
        name="inject_board",
        event="before_turn",
        handler=lambda **kw: {"board_state": "e4 e5 Nf3"},
        priority=0,
    ))

    # 두 번째 훅이 board_state를 사용
    received = []
    hooks.handle_register_hook(HookDefinition(
        name="use_board",
        event="before_turn",
        handler=lambda **kw: received.append(kw.get("board_state")),
        priority=10,
    ))

    hooks.handle_run_hook("before_turn")
    assert received[0] == "e4 e5 Nf3"
    print("  [PASS] Hooks chaining")

def test_hooks_error_handling():
    """Hooks: 에러 시에도 다른 훅 계속 실행"""
    hooks = HooksBlock()
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config
    hooks.attach(Bus(), Signals(), Config())

    results = []
    hooks.before_turn("bad_hook", lambda **kw: 1/0, priority=0)  # 에러
    hooks.before_turn("good_hook", lambda **kw: results.append("ok"), priority=10)

    hooks.handle_run_hook("before_turn")
    assert "ok" in results  # 에러 훅 이후에도 실행됨
    print("  [PASS] Hooks error handling")

def test_hooks_list():
    """Hooks: 등록된 훅 목록"""
    hooks = HooksBlock()
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config
    hooks.attach(Bus(), Signals(), Config())

    hooks.before_turn("hook_a", lambda **kw: None)
    hooks.after_turn("hook_b", lambda **kw: None)

    all_hooks = hooks.handle_list_hooks()
    assert len(all_hooks) == 2

    turn_hooks = hooks.handle_list_hooks(event="before_turn")
    assert len(turn_hooks) == 1
    print("  [PASS] Hooks list")


# ============================================================
# 3. Integration: Game Agent Scenario
# ============================================================

def test_game_agent_scenario():
    """통합: LxM 포커 에이전트 시나리오"""

    # 도구 정의
    poker_tools = [
        ToolDefinition(name="bet", description="Place a bet", parameters={
            "type": "object",
            "properties": {"amount": {"type": "integer"}},
        }, tags=("poker",)),
        ToolDefinition(name="fold", description="Fold hand", tags=("poker",)),
        ToolDefinition(name="check", description="Check", tags=("poker",)),
    ]

    # 도구 핸들러
    game_log = []
    def handle_bet(amount=0):
        game_log.append(f"bet:{amount}")
        return {"action": "bet", "amount": amount}
    def handle_fold():
        game_log.append("fold")
        return {"action": "fold"}

    # 블록 조립
    registry = RegistryBlock(tools=poker_tools)
    hooks = HooksBlock()
    org = Organism(
        name="poker-agent",
        blocks=[
            MockProviderBlock(),
            EngineBlock(max_turns=10, system_prompt="You are a poker player."),
            TrackingBlock(experiment_name="poker-test"),
            registry,
            hooks,
        ],
        config={"model": "mock"},
    )

    # 도구 핸들러 등록
    registry.handle_register_tool(poker_tools[0], handler=handle_bet)
    registry.handle_register_tool(poker_tools[1], handler=handle_fold)

    # 훅: 매 턴 전에 게임 상태 주입
    hand_state = {"hand": "A♠ K♥", "pot": 200}
    hooks.before_turn("inject_hand", lambda **kw: hand_state)

    # 훅: 매 턴 후 로깅
    hook_log = []
    hooks.after_turn("log_result", lambda **kw: hook_log.append(f"turn {kw.get('turn_number', '?')} done"))

    # 실행
    engine = org.get_block("engine")
    result = engine.handle_submit("Your hand is A♠ K♥. The pot is 200. What do you do?")

    assert result.stop_reason == "completed"

    # 도구 실행
    tool_result = registry.handle_execute_tool("bet", {"amount": 100})
    assert tool_result.success
    assert game_log == ["bet:100"]

    # 보고서
    tracking = org.get_block("tracking")
    report = tracking.handle_get_report()
    assert report["total_turns"] >= 1

    # 상태
    status = org.status()
    assert status["block_count"] == 5
    assert status["state"] == "alive"

    print("  [PASS] Game agent scenario (full integration)")


def test_hooks_signal_auto_trigger():
    """통합: Signal → Hooks 자동 트리거"""
    hooks = HooksBlock()
    org = Organism(
        name="auto-trigger",
        blocks=[MockProviderBlock(), EngineBlock(), hooks],
        config={"model": "mock"},
    )

    triggered = []
    hooks.after_turn("auto_hook", lambda **kw: triggered.append(kw.get("turn_number")))

    engine = org.get_block("engine")
    engine.handle_submit("Test auto trigger")

    assert len(triggered) >= 1
    print("  [PASS] Hooks signal auto-trigger")


# ============================================================
# Run all tests
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Ludex Phase 3 — Registry + Hooks Tests")
    print("=" * 60)

    print("\n[Registry]")
    test_registry_register_and_get()
    test_registry_execute_tool()
    test_registry_permission_deny()
    test_registry_llm_schema()
    test_registry_dynamic_registration()

    print("\n[Hooks]")
    test_hooks_register_and_run()
    test_hooks_priority()
    test_hooks_chaining()
    test_hooks_error_handling()
    test_hooks_list()

    print("\n[Integration]")
    test_game_agent_scenario()
    test_hooks_signal_auto_trigger()

    print("\n" + "=" * 60)
    print("All Phase 3 tests passed!")
    print("=" * 60)
