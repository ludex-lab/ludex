"""
Phase 1 Tests — Provider + Resilience Blocks

이 테스트로 검증하는 것:
1. Provider: LLM 호출 인터페이스, 에러 분류, health check
2. Resilience: 재시도, 서킷 브레이커, 폴백 모델
3. Wrapping Chain: Engine → Resilience → Provider 자동 매칭
4. Organism Lifecycle: 상태 머신
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.bus import Bus
from ludex.core.signals import Signals
from ludex.core.config import Config
from ludex.core.port import Port
from ludex.core.block import Block
from ludex.core.organism import Organism
from ludex.blocks.provider import ProviderBlock, LLMResponse, LLMError
from ludex.blocks.resilience import ResilienceBlock


# ============================================================
# Mock Provider for testing (no real API calls)
# ============================================================

class MockProviderBlock(Block):
    """테스트용 Provider. 실제 API 호출 없이 동작."""
    name = "provider"
    provides = [
        Port("llm_call"),
        Port("health_check"),
        Port("list_models"),
    ]
    requires = []

    def __init__(self, fail_count: int = 0, fail_type: str = "connection"):
        super().__init__()
        self._fail_count = fail_count
        self._fail_type = fail_type
        self._call_count = 0

    def handle_llm_call(self, prompt: str = "", system: str = "", tools: list | None = None, **kwargs):
        self._call_count += 1
        if self._call_count <= self._fail_count:
            return LLMError(
                error_type=self._fail_type,
                message=f"Mock failure {self._call_count}",
                retryable=self._fail_type != "auth",
            )
        response = LLMResponse(
            content=f"Response to: {prompt}",
            model=self._cfg("model", "mock-model"),
            tokens_in=len(prompt.split()) * 2,
            tokens_out=10,
            latency_ms=100.0,
        )
        self._publish("llm.response", {
            "model": self._cfg("model", "mock-model"),
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "latency_ms": response.latency_ms,
        })
        return response

    def handle_health_check(self):
        return {"status": "healthy", "provider": "mock"}

    def handle_list_models(self):
        return ["mock-model-a", "mock-model-b"]


# ============================================================
# 1. Provider Tests
# ============================================================

def test_provider_interface():
    """Provider: 기본 인터페이스"""
    provider = MockProviderBlock()
    bus, signals, config = Bus(), Signals(), Config({"model": "test"})
    provider.attach(bus, signals, config)

    result = provider.handle_llm_call("Hello world")
    assert isinstance(result, LLMResponse)
    assert "Hello world" in result.content
    print("  [PASS] Provider interface")

def test_provider_error_classification():
    """Provider: 에러 분류"""
    provider = MockProviderBlock(fail_count=1, fail_type="rate_limit")
    bus, signals, config = Bus(), Signals(), Config()
    provider.attach(bus, signals, config)

    result = provider.handle_llm_call("test")
    assert isinstance(result, LLMError)
    assert result.error_type == "rate_limit"
    assert result.retryable == True
    print("  [PASS] Provider error classification")

def test_provider_auth_not_retryable():
    """Provider: auth 에러는 재시도 불가"""
    provider = MockProviderBlock(fail_count=1, fail_type="auth")
    bus, signals, config = Bus(), Signals(), Config()
    provider.attach(bus, signals, config)

    result = provider.handle_llm_call("test")
    assert isinstance(result, LLMError)
    assert result.retryable == False
    print("  [PASS] Provider auth not retryable")

def test_provider_health_check():
    """Provider: 건강 체크"""
    provider = MockProviderBlock()
    bus, signals, config = Bus(), Signals(), Config()
    provider.attach(bus, signals, config)

    health = provider.handle_health_check()
    assert health["status"] == "healthy"
    print("  [PASS] Provider health check")


# ============================================================
# 2. Resilience Tests
# ============================================================

def test_resilience_retry_success():
    """Resilience: 2번 실패 후 3번째 성공"""
    provider = MockProviderBlock(fail_count=2)
    resilience = ResilienceBlock(max_retries=3, initial_delay_ms=1, max_delay_ms=10)

    org = Organism(
        name="retry-test",
        blocks=[provider, resilience],
        config={"model": "test"},
    )

    # Engine 역할로 직접 resilience의 llm_call 호출
    result = resilience.handle_llm_call("Hello")
    assert isinstance(result, LLMResponse)
    assert "Hello" in result.content
    assert provider._call_count == 3  # 2 failures + 1 success
    print("  [PASS] Resilience retry success")

def test_resilience_all_retries_fail():
    """Resilience: 모든 재시도 실패"""
    provider = MockProviderBlock(fail_count=10)
    resilience = ResilienceBlock(max_retries=3, initial_delay_ms=1, max_delay_ms=10)

    org = Organism(
        name="fail-test",
        blocks=[provider, resilience],
        config={"model": "test"},
    )

    result = resilience.handle_llm_call("Hello")
    assert isinstance(result, LLMError)
    assert provider._call_count == 3
    print("  [PASS] Resilience all retries fail")

def test_resilience_auth_no_retry():
    """Resilience: auth 에러는 재시도 없이 즉시 반환"""
    provider = MockProviderBlock(fail_count=10, fail_type="auth")
    resilience = ResilienceBlock(max_retries=3, initial_delay_ms=1)

    org = Organism(
        name="auth-test",
        blocks=[provider, resilience],
        config={"model": "test"},
    )

    result = resilience.handle_llm_call("Hello")
    assert isinstance(result, LLMError)
    assert result.error_type == "auth"
    assert provider._call_count == 1  # 재시도 없이 1번만 호출
    print("  [PASS] Resilience auth no retry")

def test_resilience_circuit_breaker():
    """Resilience: 서킷 브레이커 작동"""
    provider = MockProviderBlock(fail_count=100)
    resilience = ResilienceBlock(
        max_retries=2,
        initial_delay_ms=1,
        max_delay_ms=5,
        circuit_breaker_threshold=3,
    )

    org = Organism(
        name="circuit-test",
        blocks=[provider, resilience],
        config={"model": "test"},
    )

    # 첫 번째 호출: 2번 재시도 후 실패 (consecutive=2)
    resilience.handle_llm_call("test1")
    # 두 번째 호출: 또 2번 재시도 (consecutive=4, threshold 3 초과 → circuit open)
    resilience.handle_llm_call("test2")

    # 서킷 열린 후 → 즉시 거절
    result = resilience.handle_llm_call("test3")
    assert isinstance(result, LLMError)
    assert result.error_type == "circuit_breaker"
    print("  [PASS] Resilience circuit breaker")


# ============================================================
# 3. Wrapping Chain Tests
# ============================================================

class MockEngine(Block):
    name = "engine"
    provides = [Port("submit")]
    requires = [Port("llm_call")]

    def handle_submit(self, prompt: str) -> dict:
        result = self.call_port("llm_call", prompt=prompt)
        return {"result": result}


def test_wrapping_chain():
    """Wrapping Chain: Engine → Resilience → Provider"""
    provider = MockProviderBlock(fail_count=1)  # 1번 실패 후 성공
    resilience = ResilienceBlock(max_retries=3, initial_delay_ms=1, max_delay_ms=5)
    engine = MockEngine()

    org = Organism(
        name="chain-test",
        blocks=[provider, resilience, engine],
        config={"model": "test"},
    )

    # Engine이 llm_call 호출 → Resilience를 거쳐 → Provider에 도달
    result = engine.handle_submit("Chain test")
    assert isinstance(result["result"], LLMResponse)
    assert "Chain test" in result["result"].content
    # Provider가 2번 호출됨 (1 fail + 1 success, Resilience가 재시도)
    assert provider._call_count == 2
    print("  [PASS] Wrapping chain: Engine -> Resilience -> Provider")


def test_wrapping_chain_without_resilience():
    """Engine → Provider 직접 연결 (Resilience 없이)"""
    provider = MockProviderBlock()
    engine = MockEngine()

    org = Organism(
        name="direct-test",
        blocks=[provider, engine],
        config={"model": "test"},
    )

    result = engine.handle_submit("Direct test")
    assert isinstance(result["result"], LLMResponse)
    print("  [PASS] Direct chain: Engine -> Provider (no resilience)")


# ============================================================
# 4. Organism Lifecycle Tests
# ============================================================

def test_organism_lifecycle_state():
    """Organism: 생명 주기 상태"""
    org = Organism(name="lifecycle-test", blocks=[MockProviderBlock()])
    status = org.status()
    assert status["state"] == "alive"
    print("  [PASS] Organism lifecycle state")

def test_organism_bus_events():
    """Organism: 블록 간 Bus 이벤트 전파"""
    provider = MockProviderBlock()
    resilience = ResilienceBlock(max_retries=1, initial_delay_ms=1)

    bus_events = []
    org = Organism(
        name="events-test",
        blocks=[provider, resilience],
        config={"model": "test"},
    )
    org.bus.subscribe("llm.response", lambda msg: bus_events.append(msg.data))

    resilience.handle_llm_call("Event test")

    assert len(bus_events) == 1
    assert bus_events[0]["model"] == "test"
    print("  [PASS] Organism bus events")


# ============================================================
# Run all tests
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Ludex Phase 1 — Provider + Resilience Tests")
    print("=" * 60)

    print("\n[Provider]")
    test_provider_interface()
    test_provider_error_classification()
    test_provider_auth_not_retryable()
    test_provider_health_check()

    print("\n[Resilience]")
    test_resilience_retry_success()
    test_resilience_all_retries_fail()
    test_resilience_auth_no_retry()
    test_resilience_circuit_breaker()

    print("\n[Wrapping Chain]")
    test_wrapping_chain()
    test_wrapping_chain_without_resilience()

    print("\n[Organism Lifecycle]")
    test_organism_lifecycle_state()
    test_organism_bus_events()

    print("\n" + "=" * 60)
    print("All Phase 1 tests passed!")
    print("=" * 60)
