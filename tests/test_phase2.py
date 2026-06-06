"""
Phase 2 Tests — Engine + Tracking Blocks

이 테스트로 검증하는 것:
1. Engine: 턴 실행, 토큰 예산, 턴 제한, 컨텍스트 압축
2. Tracking: 자동 기록, 보고서, 비용 추적
3. 전체 유기체: Provider + Resilience + Engine + Tracking 통합
4. Vital Signs: Engine이 생체 신호 업데이트
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism import Organism
from ludex.core.block import Block
from ludex.core.port import Port
from ludex.blocks.provider import LLMResponse, LLMError
from ludex.blocks.engine import EngineBlock, TurnResult
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.resilience import ResilienceBlock


# Mock Provider (same as Phase 1)
class MockProviderBlock(Block):
    name = "provider"
    provides = [Port("llm_call"), Port("health_check"), Port("list_models")]
    requires = []

    def __init__(self, fail_count=0, fail_type="connection"):
        super().__init__()
        self._fail_count = fail_count
        self._fail_type = fail_type
        self._call_count = 0

    def handle_llm_call(self, prompt="", system="", tools=None, messages=None, **kwargs):
        self._call_count += 1
        if self._call_count <= self._fail_count:
            return LLMError(error_type=self._fail_type, message=f"Mock failure {self._call_count}", retryable=True)
        # Extract last user message from messages if available
        display_prompt = prompt
        if messages:
            user_msgs = [m for m in messages if m.get("role") == "user"]
            if user_msgs:
                display_prompt = user_msgs[-1]["content"]
        response = LLMResponse(
            content=f"Response to: {display_prompt}", model=self._cfg("model", "mock"),
            tokens_in=max(len(display_prompt.split()) * 2, 10), tokens_out=10, latency_ms=50.0,
        )
        self._publish("llm.response", {
            "model": self._cfg("model", "mock"), "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out, "latency_ms": response.latency_ms,
        })
        return response

    def handle_health_check(self):
        return {"status": "healthy"}

    def handle_list_models(self):
        return ["mock-model"]


# ============================================================
# 1. Engine Tests
# ============================================================

def test_engine_basic_turn():
    """Engine: 기본 턴 실행"""
    org = Organism(
        name="engine-test",
        blocks=[MockProviderBlock(), EngineBlock()],
        config={"model": "mock"},
    )
    engine = org.get_block("engine")
    result = engine.handle_submit("What is 2+2?")

    assert isinstance(result, TurnResult)
    assert result.turn_number == 1
    assert result.stop_reason == "completed"
    assert "What is 2+2?" in result.response
    print("  [PASS] Engine basic turn")

def test_engine_multiple_turns():
    """Engine: 여러 턴 실행"""
    org = Organism(
        name="multi-turn",
        blocks=[MockProviderBlock(), EngineBlock(max_turns=10)],
        config={"model": "mock"},
    )
    engine = org.get_block("engine")

    for i in range(5):
        result = engine.handle_submit(f"Turn {i+1}")
        assert result.turn_number == i + 1

    assert engine.turn_count == 5
    assert engine.tokens_used > 0
    print("  [PASS] Engine multiple turns")

def test_engine_max_turns():
    """Engine: 턴 제한 도달"""
    org = Organism(
        name="max-turns",
        blocks=[MockProviderBlock(), EngineBlock(max_turns=3)],
        config={"model": "mock"},
    )
    engine = org.get_block("engine")

    for i in range(3):
        engine.handle_submit(f"Turn {i+1}")

    result = engine.handle_submit("Overflow turn")
    assert result.stop_reason == "max_turns"
    print("  [PASS] Engine max turns")

def test_engine_token_budget():
    """Engine: 토큰 예산 초과"""
    org = Organism(
        name="budget-test",
        blocks=[MockProviderBlock(), EngineBlock(token_budget=30)],
        config={"model": "mock"},
    )
    engine = org.get_block("engine")

    # 몇 턴 돌리면 토큰 예산 소진
    for i in range(20):
        result = engine.handle_submit(f"This is a long prompt for turn {i+1} with many words to consume tokens")
        if result.stop_reason == "max_budget":
            break

    assert result.stop_reason == "max_budget"
    print("  [PASS] Engine token budget")

def test_engine_compact():
    """Engine: 컨텍스트 압축"""
    org = Organism(
        name="compact-test",
        blocks=[MockProviderBlock(), EngineBlock(compact_after=5, token_budget=100000)],
        config={"model": "mock"},
    )
    engine = org.get_block("engine")

    # 메시지 쌓기
    for i in range(8):
        engine.handle_submit(f"Message {i+1}")

    # 수동 압축
    removed = engine.handle_compact()
    assert removed > 0

    history = engine.handle_get_history()
    assert len(history) < 16  # 8 user + 8 assistant = 16 before compact
    print("  [PASS] Engine compact")

def test_engine_error_handling():
    """Engine: LLM 에러 시 턴 결과"""
    org = Organism(
        name="error-test",
        blocks=[MockProviderBlock(fail_count=1), EngineBlock()],
        config={"model": "mock"},
    )
    engine = org.get_block("engine")

    result = engine.handle_submit("Will fail")
    assert result.stop_reason == "error"
    assert result.error is not None
    print("  [PASS] Engine error handling")

def test_engine_system_prompt():
    """Engine: 시스템 프롬프트"""
    org = Organism(
        name="system-test",
        blocks=[MockProviderBlock(), EngineBlock(system_prompt="You are a chess player.")],
        config={"model": "mock"},
    )
    engine = org.get_block("engine")

    history = engine.handle_get_history()
    assert any(m["role"] == "system" for m in history)
    print("  [PASS] Engine system prompt")


# ============================================================
# 2. Tracking Tests
# ============================================================

def test_tracking_auto_record():
    """Tracking: 자동 이벤트 기록"""
    org = Organism(
        name="tracking-test",
        blocks=[MockProviderBlock(), EngineBlock(), TrackingBlock()],
        config={"model": "mock"},
    )
    engine = org.get_block("engine")
    tracking = org.get_block("tracking")

    engine.handle_submit("Test prompt")

    entries = tracking.handle_get_entries()
    events = [e["event"] for e in entries]
    assert "turn.started" in events
    assert "turn.ended" in events
    print("  [PASS] Tracking auto record")

def test_tracking_report():
    """Tracking: 보고서 생성"""
    org = Organism(
        name="report-test",
        blocks=[MockProviderBlock(), EngineBlock(), TrackingBlock(experiment_name="exp-test")],
        config={"model": "mock"},
    )
    engine = org.get_block("engine")
    tracking = org.get_block("tracking")

    for i in range(3):
        engine.handle_submit(f"Turn {i+1}")

    report = tracking.handle_get_report()
    assert report["total_turns"] == 3
    assert report["total_tokens"] > 0
    assert report["experiment"] == "exp-test"
    assert report["error_rate"] == 0.0
    assert "mock" in report["models_used"]
    print("  [PASS] Tracking report")

def test_tracking_cost():
    """Tracking: 비용 추적"""
    org = Organism(
        name="cost-test",
        blocks=[MockProviderBlock(), EngineBlock(), TrackingBlock()],
        config={"model": "mock", "cost_per_1k_input": 0.01, "cost_per_1k_output": 0.03},
    )
    engine = org.get_block("engine")
    tracking = org.get_block("tracking")

    engine.handle_submit("Test cost tracking")

    cost = tracking.handle_get_cost()
    assert cost["total_cost"] >= 0
    assert cost["currency"] == "USD"
    print("  [PASS] Tracking cost")

def test_tracking_error_counting():
    """Tracking: 에러 카운팅"""
    org = Organism(
        name="error-track",
        blocks=[MockProviderBlock(fail_count=2), EngineBlock(), TrackingBlock()],
        config={"model": "mock"},
    )
    engine = org.get_block("engine")
    tracking = org.get_block("tracking")

    engine.handle_submit("Will fail")
    engine.handle_submit("Will also fail, provider still failing")

    report = tracking.handle_get_report()
    assert report["total_errors"] >= 1
    print("  [PASS] Tracking error counting")


# ============================================================
# 3. Full Organism Integration
# ============================================================

def test_full_organism():
    """전체 유기체: Provider + Resilience + Engine + Tracking"""
    org = Organism(
        name="full-organism",
        blocks=[
            MockProviderBlock(fail_count=1),
            ResilienceBlock(max_retries=3, initial_delay_ms=1, max_delay_ms=5),
            EngineBlock(max_turns=10, system_prompt="You are a test agent."),
            TrackingBlock(experiment_name="full-test"),
        ],
        config={"model": "mock"},
    )

    engine = org.get_block("engine")
    tracking = org.get_block("tracking")

    # 3턴 실행 (첫 턴은 Provider가 1번 실패 → Resilience가 재시도 → 성공)
    for i in range(3):
        result = engine.handle_submit(f"Full test turn {i+1}")
        assert result.stop_reason == "completed"

    report = tracking.handle_get_report()
    assert report["total_turns"] == 3
    assert report["error_rate"] == 0.0  # Resilience가 에러를 흡수

    status = org.status()
    assert status["block_count"] == 4
    assert status["state"] == "alive"
    print("  [PASS] Full organism integration")


def test_organism_vitals_update():
    """Vital Signs: Engine이 생체 신호 업데이트"""
    org = Organism(
        name="vitals-test",
        blocks=[MockProviderBlock(), EngineBlock(token_budget=5000)],
        config={"model": "mock"},
    )
    engine = org.get_block("engine")

    engine.handle_submit("Test vitals")

    vitals = org.measure_vitals()
    assert vitals.total_turns == 1
    assert vitals.tokens_per_turn > 0
    assert vitals.context_utilization >= 0
    assert vitals.error_rate == 0.0
    print("  [PASS] Organism vitals update")


# ============================================================
# Run all tests
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Ludex Phase 2 — Engine + Tracking Tests")
    print("=" * 60)

    print("\n[Engine]")
    test_engine_basic_turn()
    test_engine_multiple_turns()
    test_engine_max_turns()
    test_engine_token_budget()
    test_engine_compact()
    test_engine_error_handling()
    test_engine_system_prompt()

    print("\n[Tracking]")
    test_tracking_auto_record()
    test_tracking_report()
    test_tracking_cost()
    test_tracking_error_counting()

    print("\n[Full Organism]")
    test_full_organism()
    test_organism_vitals_update()

    print("\n" + "=" * 60)
    print("All Phase 2 tests passed!")
    print("=" * 60)
