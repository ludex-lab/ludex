"""
End-to-End Integration Tests — 전체 유기체 생존 테스트

단위 테스트가 아닌, 실제 시나리오를 시뮬레이션하는 통합 테스트.
유기체가 살아서 작동하는지, 블록 간 통신이 원활한지,
스트레스 상황에서 항상성이 유지되는지를 검증.

시나리오:
1. Healthy Life — 정상 운영 (다턴 대화 + 도구 사용 + 훅)
2. Stress and Recovery — 에러 발생 + 재시도 + 서킷 브레이커 + 회복
3. Aging and Compaction — 오래 살면서 메모리 압축
4. Organ Transplant — 런타임 블록 추가/제거
5. Vital Signs Monitoring — 생체 신호 일관성
"""

import sys
import os
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism import Organism
from ludex.core.block import Block
from ludex.core.port import Port
from ludex.blocks.provider import LLMResponse, LLMError
from ludex.blocks.engine import EngineBlock
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.resilience import ResilienceBlock
from ludex.blocks.registry import RegistryBlock, ToolDefinition
from ludex.blocks.hooks import HooksBlock, HookDefinition


# ============================================================
# Configurable Mock Provider
# ============================================================

class ScenarioProvider(Block):
    """시나리오별 동작을 제어할 수 있는 Provider"""
    name = "provider"
    provides = [Port("llm_call"), Port("health_check"), Port("list_models")]
    requires = []

    def __init__(self):
        super().__init__()
        self.call_count = 0
        self.fail_schedule: list[str] = []  # ["ok", "fail", "ok", "fail:rate_limit", ...]
        self.responses: list[str] = []       # 커스텀 응답 리스트

    def handle_llm_call(self, prompt="", system="", tools=None, **kwargs):
        self.call_count += 1
        idx = self.call_count - 1

        # 실패 스케줄 체크
        if idx < len(self.fail_schedule):
            action = self.fail_schedule[idx]
            if action.startswith("fail"):
                fail_type = action.split(":")[1] if ":" in action else "connection"
                return LLMError(
                    error_type=fail_type,
                    message=f"Scheduled failure #{self.call_count}: {fail_type}",
                    retryable=fail_type != "auth",
                )

        # 커스텀 응답
        response_text = "I'll bet 50."
        if idx < len(self.responses):
            response_text = self.responses[idx]

        response = LLMResponse(
            content=response_text,
            model=self._cfg("model", "scenario-mock"),
            tokens_in=len(prompt.split()) * 2,
            tokens_out=len(response_text.split()) * 2,
            latency_ms=50.0 + (idx * 10),  # 점진적으로 느려짐
        )
        self._publish("llm.response", {
            "model": response.model,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "latency_ms": response.latency_ms,
        })
        return response

    def handle_health_check(self):
        return {"status": "healthy", "provider": "scenario-mock"}

    def handle_list_models(self):
        return ["scenario-mock"]


# ============================================================
# Scenario 1: Healthy Life — 정상 다턴 운영
# ============================================================

def test_healthy_life():
    """
    정상적인 유기체 수명: 생성 → 5턴 대화 → 도구 사용 → 보고서 → 종료
    모든 블록이 유기적으로 작동하는지 검증.
    """
    print("\n  --- Scenario 1: Healthy Life ---")

    # 도구 정의
    bet_tool = ToolDefinition(name="bet", description="Place a bet",
        parameters={"type": "object", "properties": {"amount": {"type": "integer"}}},
        tags=("poker",))
    fold_tool = ToolDefinition(name="fold", description="Fold", tags=("poker",))

    game_actions = []

    # 유기체 조립
    provider = ScenarioProvider()
    provider.responses = [
        "I'll bet 50 chips.",
        "I'll check.",
        "I'll raise to 100.",
        "I'll fold this hand.",
        "I'll go all in!",
    ]

    hooks = HooksBlock()
    registry = RegistryBlock(tools=[bet_tool, fold_tool])

    org = Organism(
        name="healthy-poker-agent",
        blocks=[
            provider,
            ResilienceBlock(max_retries=2, initial_delay_ms=1, max_delay_ms=5),
            EngineBlock(max_turns=10, token_budget=5000, system_prompt="You are a poker player."),
            TrackingBlock(experiment_name="e2e-healthy"),
            registry,
            hooks,
        ],
        config={"model": "scenario-mock"},
    )

    # 훅: 매 턴 전에 게임 상태 주입
    turn_states = []
    hooks.before_turn("track_turns", lambda **kw: turn_states.append(kw.get("turn_number", 0)))

    # 도구 핸들러 등록
    registry.handle_register_tool(bet_tool, handler=lambda amount=0: game_actions.append(f"bet:{amount}"))
    registry.handle_register_tool(fold_tool, handler=lambda: game_actions.append("fold"))

    engine = org.get_block("engine")
    tracking = org.get_block("tracking")

    # 5턴 실행
    for i in range(5):
        result = engine.handle_submit(f"Round {i+1}: Your hand is strong. Pot is {100 + i*50}.")
        assert result.stop_reason == "completed", f"Turn {i+1} failed: {result.error}"

    # 도구 실행
    registry.handle_execute_tool("bet", {"amount": 50})
    registry.handle_execute_tool("fold")

    # 검증
    assert engine.turn_count == 5, f"Expected 5 turns, got {engine.turn_count}"
    assert engine.tokens_used > 0, "Tokens should be consumed"
    assert len(game_actions) == 2, f"Expected 2 actions, got {game_actions}"
    assert len(turn_states) >= 5, f"Hooks should fire 5+ times, got {len(turn_states)}"

    # 보고서
    report = tracking.handle_get_report()
    assert report["total_turns"] == 5
    assert report["error_rate"] == 0.0
    assert report["total_tokens"] > 0

    # 생체 신호
    vitals = org.measure_vitals()
    assert vitals.total_turns == 5
    assert vitals.error_rate == 0.0
    assert vitals.tokens_per_turn > 0
    assert vitals.temporal.age_seconds >= 0
    assert len(vitals.temporal.turn_timestamps) == 5

    # 상태
    status = org.status()
    assert status["state"] == "alive"
    assert status["block_count"] == 6

    print(f"    Turns: {report['total_turns']}, Tokens: {report['total_tokens']}, "
          f"Errors: {report['total_errors']}, Latency: {report['avg_latency_ms']:.0f}ms")
    print("  [PASS] Healthy life — all systems nominal")


# ============================================================
# Scenario 2: Stress and Recovery — 에러 + 복구
# ============================================================

def test_stress_and_recovery():
    """
    스트레스 시나리오: 에러 발생 → Resilience 재시도 → 성공 → 정상 복귀
    면역 시스템(Resilience)이 에러를 흡수하는지 검증.
    """
    print("\n  --- Scenario 2: Stress and Recovery ---")

    provider = ScenarioProvider()
    # 첫 2번 실패, 3번째 성공, 4번째 실패, 5번째 성공, 나머지 성공
    provider.fail_schedule = ["fail:connection", "fail:timeout", "ok", "fail:rate_limit", "ok", "ok", "ok", "ok"]

    org = Organism(
        name="stressed-agent",
        blocks=[
            provider,
            ResilienceBlock(max_retries=3, initial_delay_ms=1, max_delay_ms=5),
            EngineBlock(max_turns=5, token_budget=10000),
            TrackingBlock(experiment_name="e2e-stress"),
        ],
        config={"model": "scenario-mock"},
    )

    engine = org.get_block("engine")
    tracking = org.get_block("tracking")

    # 3턴 실행 (내부적으로 재시도 발생)
    results = []
    for i in range(3):
        result = engine.handle_submit(f"Stress test turn {i+1}")
        results.append(result)

    # 검증: Resilience가 에러를 흡수해서 Engine에는 성공으로 보임
    completed = [r for r in results if r.stop_reason == "completed"]
    assert len(completed) == 3, f"All 3 turns should complete (Resilience absorbs errors), got {len(completed)}"

    # Provider는 재시도 때문에 더 많이 호출됨
    assert provider.call_count > 3, f"Provider should be called >3 times due to retries, got {provider.call_count}"

    # Tracking에 retry 이벤트 기록
    entries = tracking.handle_get_entries()
    retry_events = [e for e in entries if e["event"] == "retry.attempted"]
    assert len(retry_events) > 0, "Retry events should be recorded"

    report = tracking.handle_get_report()
    print(f"    Turns: {report['total_turns']}, Provider calls: {provider.call_count}, "
          f"Retries recorded: {len(retry_events)}")
    print("  [PASS] Stress and recovery — immune system absorbed errors")


# ============================================================
# Scenario 3: Aging and Compaction — 장수 + 메모리 관리
# ============================================================

def test_aging_and_compaction():
    """
    장수 시나리오: 많은 턴을 돌리면서 컨텍스트 압축이 작동하는지 검증.
    토큰 예산 소진 + 자동 압축 테스트.
    """
    print("\n  --- Scenario 3: Aging and Compaction ---")

    provider = ScenarioProvider()

    org = Organism(
        name="aging-agent",
        blocks=[
            provider,
            EngineBlock(max_turns=30, token_budget=100000, compact_after=8),
            TrackingBlock(experiment_name="e2e-aging"),
        ],
        config={"model": "scenario-mock"},
    )

    engine = org.get_block("engine")
    tracking = org.get_block("tracking")

    # 15턴 실행 (compact_after=8이니까 자동 압축 발생)
    for i in range(15):
        result = engine.handle_submit(f"This is turn {i+1} with a moderately long prompt to consume tokens")
        if result.stop_reason != "completed":
            break

    # 검증
    assert engine.turn_count == 15

    # 히스토리가 압축됐는지 확인 (15턴 * 2(user+assistant) = 30 메시지인데 압축 후 더 적음)
    history = engine.handle_get_history()
    assert len(history) < 30, f"History should be compacted, got {len(history)} messages"

    # Tracking에 compaction 이벤트
    entries = tracking.handle_get_entries()
    compact_events = [e for e in entries if e["event"] == "context.compacted"]
    assert len(compact_events) > 0, "Compaction should have occurred"

    # 생체 신호: 나이 확인
    vitals = org.measure_vitals()
    assert vitals.temporal.age_seconds >= 0
    assert vitals.total_turns == 15

    report = tracking.handle_get_report()
    print(f"    Turns: {report['total_turns']}, History size: {len(history)}, "
          f"Compactions: {len(compact_events)}, Tokens used: {engine.tokens_used}")
    print("  [PASS] Aging and compaction — organism managed its memory")


# ============================================================
# Scenario 4: Organ Transplant — 런타임 블록 교체
# ============================================================

def test_organ_transplant():
    """
    장기 이식: 유기체가 살아있는 상태에서 블록을 추가/제거.
    graceful degradation 검증.
    """
    print("\n  --- Scenario 4: Organ Transplant ---")

    provider = ScenarioProvider()

    # 최소 구성으로 시작
    org = Organism(
        name="transplant-agent",
        blocks=[provider, EngineBlock(max_turns=20)],
        config={"model": "scenario-mock"},
    )

    engine = org.get_block("engine")

    # Phase 1: Tracking 없이 실행
    result = engine.handle_submit("Turn without tracking")
    assert result.stop_reason == "completed"
    assert org.status()["block_count"] == 2

    # Phase 2: Tracking 이식
    tracking = TrackingBlock(experiment_name="transplant-test")
    org.attach(tracking)
    assert org.status()["block_count"] == 3

    result = engine.handle_submit("Turn with tracking")
    assert result.stop_reason == "completed"

    report = tracking.handle_get_report()
    assert report["total_turns"] >= 1  # 이식 후 턴만 기록됨

    # Phase 3: Hooks 이식
    hooks = HooksBlock()
    org.attach(hooks)
    hook_fired = []
    hooks.after_turn("transplant_hook", lambda **kw: hook_fired.append(True))

    result = engine.handle_submit("Turn with hooks")
    assert result.stop_reason == "completed"
    assert len(hook_fired) >= 1

    # Phase 4: Tracking 제거
    org.detach("tracking")
    assert org.status()["block_count"] == 3  # provider, engine, hooks

    result = engine.handle_submit("Turn after tracking removed")
    assert result.stop_reason == "completed"  # 문제없이 작동

    print(f"    Blocks: {list(org.blocks.keys())}")
    print("  [PASS] Organ transplant — attach/detach during runtime")


# ============================================================
# Scenario 5: Vital Signs Consistency — 생체 신호 일관성
# ============================================================

def test_vital_signs_consistency():
    """
    생체 신호 일관성: 여러 턴에 걸쳐 Vital Signs가 정확하게 업데이트되는지.
    Homeostasis 체크가 올바른 조절을 내리는지.
    """
    print("\n  --- Scenario 5: Vital Signs Consistency ---")

    provider = ScenarioProvider()

    org = Organism(
        name="vitals-agent",
        blocks=[
            provider,
            ResilienceBlock(max_retries=2, initial_delay_ms=1, max_delay_ms=5),
            EngineBlock(max_turns=20, token_budget=5000),
            TrackingBlock(experiment_name="e2e-vitals"),
        ],
        config={"model": "scenario-mock"},
    )

    engine = org.get_block("engine")

    # 5턴 실행하면서 매번 vitals 측정
    vitals_history = []
    for i in range(5):
        engine.handle_submit(f"Vitals check turn {i+1}")
        v = org.measure_vitals()
        vitals_history.append(v)

    # 검증: 턴 수 증가
    turns = [v.total_turns for v in vitals_history]
    assert turns == [1, 2, 3, 4, 5], f"Turns should increment: {turns}"

    # 검증: 토큰 소비 증가
    tokens = [v.tokens_per_turn for v in vitals_history]
    assert all(t > 0 for t in tokens), f"All tokens_per_turn should be > 0: {tokens}"

    # 검증: 에러율 0
    errors = [v.error_rate for v in vitals_history]
    assert all(e == 0.0 for e in errors), f"Error rate should be 0: {errors}"

    # 검증: 시간 진행
    ages = [v.temporal.age_seconds for v in vitals_history]
    assert ages[-1] >= ages[0], "Age should increase"

    # 검증: 심박 주기 (턴 간격)
    last_vitals = vitals_history[-1]
    assert len(last_vitals.temporal.turn_timestamps) == 5

    # Homeostasis 체크 — 정상이니까 조절 없어야 함
    regulations = org.check_homeostasis()
    assert len(regulations) == 0, f"No regulations expected for healthy organism, got {len(regulations)}"

    print(f"    Vitals snapshots: {len(vitals_history)}, "
          f"Last age: {last_vitals.temporal.age_seconds:.1f}s, "
          f"Avg tokens/turn: {last_vitals.tokens_per_turn:.0f}")
    print("  [PASS] Vital signs consistency — all readings coherent")


# ============================================================
# Scenario 6: Bus/Signal Flow — 전체 통신 흐름
# ============================================================

def test_communication_flow():
    """
    전체 통신 흐름: Bus와 Signals를 통해 모든 블록이 연결되는지.
    하나의 턴에서 발생하는 모든 이벤트를 추적.
    """
    print("\n  --- Scenario 6: Communication Flow ---")

    provider = ScenarioProvider()

    org = Organism(
        name="flow-agent",
        blocks=[
            provider,
            EngineBlock(max_turns=5),
            TrackingBlock(experiment_name="e2e-flow"),
            HooksBlock(),
        ],
        config={"model": "scenario-mock"},
    )

    # 모든 Bus 메시지 수집
    bus_topics = []
    org.bus.subscribe("*", lambda msg: bus_topics.append(msg.topic))

    # 모든 Signal 이벤트 수집
    signal_events = []
    org.signals.on("*", lambda event, **kw: signal_events.append(event))

    # 1턴 실행
    engine = org.get_block("engine")
    engine.handle_submit("Communication flow test")

    # 검증: Bus에 데이터가 흘렀나
    assert "llm.response" in bus_topics, f"llm.response should be on bus: {bus_topics}"
    assert "turn.result" in bus_topics, f"turn.result should be on bus: {bus_topics}"

    # 검증: Signals가 발행됐나
    assert "turn.started" in signal_events, f"turn.started signal expected: {signal_events}"
    assert "turn.ended" in signal_events, f"turn.ended signal expected: {signal_events}"
    # Note: llm.responded is emitted by real ProviderBlock, not ScenarioProvider mock

    print(f"    Bus topics: {list(set(bus_topics))}")
    print(f"    Signal events: {list(set(signal_events))}")
    print("  [PASS] Communication flow — all channels active")


# ============================================================
# Run all scenarios
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Ludex End-to-End Integration Tests")
    print("=" * 60)

    test_healthy_life()
    test_stress_and_recovery()
    test_aging_and_compaction()
    test_organ_transplant()
    test_vital_signs_consistency()
    test_communication_flow()

    print("\n" + "=" * 60)
    print("All E2E scenarios passed! The organism is alive.")
    print("=" * 60)
