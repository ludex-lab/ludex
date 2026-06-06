"""
Phase 0 Tests — Core Communication System

이 테스트로 검증하는 것:
1. Bus: pub/sub 데이터 흐름, 토픽 라우팅, 히스토리
2. Signals: 이벤트 알림, 우선순위, 와일드카드
3. Config: 3-layer 설정, 변경 시그널, 스냅샷
4. Block: 라이프사이클 (attach/detach), 포트 연결
5. Organism: 블록 조립, 포트 자동 매칭, 항상성
6. Membrane: 개체 경계, 접근 제어
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
from ludex.core.vitals import VitalSigns, HomeostasisController, Regulation
from ludex.core.membrane import Membrane, EntityIdentity


# ============================================================
# 1. Bus Tests
# ============================================================

def test_bus_pubsub():
    """Bus: 기본 발행/구독"""
    bus = Bus()
    received = []

    bus.subscribe("test.topic", lambda msg: received.append(msg.data))
    bus.publish("test.topic", {"hello": "world"})

    assert len(received) == 1
    assert received[0] == {"hello": "world"}
    print("  [PASS] Bus pub/sub")

def test_bus_multiple_subscribers():
    """Bus: 여러 구독자"""
    bus = Bus()
    results = []

    bus.subscribe("data", lambda msg: results.append(f"A:{msg.data}"))
    bus.subscribe("data", lambda msg: results.append(f"B:{msg.data}"))
    bus.publish("data", 42)

    assert len(results) == 2
    assert "A:42" in results
    assert "B:42" in results
    print("  [PASS] Bus multiple subscribers")

def test_bus_wildcard():
    """Bus: 와일드카드 구독 (모든 토픽 수신)"""
    bus = Bus()
    all_msgs = []

    bus.subscribe("*", lambda msg: all_msgs.append(msg.topic))
    bus.publish("topic.a", 1)
    bus.publish("topic.b", 2)

    assert all_msgs == ["topic.a", "topic.b"]
    print("  [PASS] Bus wildcard")

def test_bus_seq_numbers():
    """Bus: 메시지 순서 보장 (seq 번호, OC 패턴)"""
    bus = Bus()
    bus.publish("a", 1)
    bus.publish("b", 2)
    bus.publish("c", 3)

    history = bus.get_history()
    assert [m.seq for m in history] == [1, 2, 3]
    print("  [PASS] Bus seq numbers")

def test_bus_unsubscribe():
    """Bus: 구독 해제"""
    bus = Bus()
    received = []

    sub = bus.subscribe("test", lambda msg: received.append(1))
    bus.publish("test", None)
    bus.unsubscribe(sub)
    bus.publish("test", None)

    assert len(received) == 1  # 두번째는 안 받음
    print("  [PASS] Bus unsubscribe")


# ============================================================
# 2. Signals Tests
# ============================================================

def test_signals_basic():
    """Signals: 기본 이벤트 발행/수신"""
    signals = Signals()
    events = []

    signals.on("turn.started", lambda **kw: events.append(kw))
    signals.emit("turn.started", turn=1, model="exaone")

    assert len(events) == 1
    assert events[0]["turn"] == 1
    print("  [PASS] Signals basic")

def test_signals_priority():
    """Signals: 우선순위 기반 실행 (OC 패턴)"""
    signals = Signals()
    order = []

    signals.on("test", lambda **kw: order.append("C"), priority=10)
    signals.on("test", lambda **kw: order.append("A"), priority=0)
    signals.on("test", lambda **kw: order.append("B"), priority=5)
    signals.emit("test")

    assert order == ["A", "B", "C"]
    print("  [PASS] Signals priority ordering")

def test_signals_wildcard():
    """Signals: 와일드카드"""
    signals = Signals()
    all_events = []

    signals.on("*", lambda event, **kw: all_events.append(event))
    signals.emit("turn.started")
    signals.emit("turn.ended")

    assert all_events == ["turn.started", "turn.ended"]
    print("  [PASS] Signals wildcard")


# ============================================================
# 3. Config Tests
# ============================================================

def test_config_layers():
    """Config: 3-layer 설정 (defaults → project → session)"""
    config = Config(defaults={"model": "default", "temp": 0.7})
    config.set("model", "project-model", layer="project")
    config.set("model", "session-model", layer="session")

    assert config.get("model") == "session-model"  # session 우선
    assert config.get("temp") == 0.7               # defaults 폴백
    print("  [PASS] Config 3-layer")

def test_config_change_signal():
    """Config: 변경 시 signal 발행"""
    signals = Signals()
    config = Config(defaults={"model": "old"})
    config.bind_signals(signals)

    changes = []
    signals.on("config.changed", lambda **kw: changes.append(kw))

    config.set("model", "new")

    assert len(changes) == 1
    assert changes[0]["key"] == "model"
    assert changes[0]["old"] == "old"
    assert changes[0]["new"] == "new"
    print("  [PASS] Config change signal")

def test_config_snapshot():
    """Config: 불변 스냅샷 (CC frozen dataclass 패턴)"""
    config = Config(defaults={"a": 1})
    config.set("b", 2, layer="session")

    snap = config.snapshot()
    config.set("a", 999, layer="session")

    assert snap["a"] == 1  # 스냅샷은 변경 안 됨
    assert snap["b"] == 2
    print("  [PASS] Config snapshot immutability")


# ============================================================
# 4. Block + Port Tests
# ============================================================

class MockProvider(Block):
    name = "provider"
    provides = [Port("llm_call", description="Call LLM API")]
    requires = []

    def handle_llm_call(self, prompt: str = "", **kwargs) -> str:
        return f"Response to: {prompt}"

class MockEngine(Block):
    name = "engine"
    provides = [Port("submit", description="Submit a turn")]
    requires = [Port("llm_call", description="Need LLM")]

    def handle_submit(self, prompt: str) -> dict:
        response = self.call_port("llm_call", prompt=prompt)
        self._publish("turn.result", {"prompt": prompt, "response": response})
        return {"prompt": prompt, "response": response}

class MockTracker(Block):
    name = "tracking"
    provides = []
    requires = []

    def __init__(self):
        super().__init__()
        self.recorded = []

    def on_attach(self):
        self._listen("turn.started", self._on_turn)

    def _on_turn(self, **kwargs):
        self.recorded.append(kwargs)


def test_block_lifecycle():
    """Block: attach/detach 라이프사이클"""
    bus, signals, config = Bus(), Signals(), Config()
    block = MockProvider()

    assert not block.is_attached
    block.attach(bus, signals, config)
    assert block.is_attached

    block.detach()
    assert not block.is_attached
    print("  [PASS] Block lifecycle")

def test_block_port_handler():
    """Block: provides 포트 핸들러"""
    provider = MockProvider()
    handler = provider.get_handler("llm_call")
    assert handler is not None
    assert handler("Hello") == "Response to: Hello"
    print("  [PASS] Block port handler")


# ============================================================
# 5. Organism Tests
# ============================================================

def test_organism_assembly():
    """Organism: 블록 조립 + 포트 자동 매칭"""
    org = Organism(
        name="test-agent",
        blocks=[MockProvider(), MockEngine()],
        config={"model": "test"},
    )

    assert len(org.blocks) == 2
    assert "provider" in org.blocks
    assert "engine" in org.blocks
    print("  [PASS] Organism assembly")

def test_organism_port_wiring():
    """Organism: Engine의 llm_call 포트가 Provider에 자동 연결"""
    org = Organism(
        name="test",
        blocks=[MockProvider(), MockEngine()],
    )

    engine = org.get_block("engine")
    result = engine.handle_submit("What is 2+2?")

    assert result["response"] == "Response to: What is 2+2?"
    print("  [PASS] Organism port wiring")

def test_organism_attach_detach():
    """Organism: 런타임 블록 추가/제거"""
    org = Organism(name="test", blocks=[MockProvider()])
    assert len(org.blocks) == 1

    tracker = MockTracker()
    org.attach(tracker)
    assert len(org.blocks) == 2

    org.detach("tracking")
    assert len(org.blocks) == 1
    print("  [PASS] Organism attach/detach")

def test_organism_bus_communication():
    """Organism: 블록 간 Bus 통신"""
    org = Organism(
        name="test",
        blocks=[MockProvider(), MockEngine()],
    )

    bus_msgs = []
    org.bus.subscribe("turn.result", lambda msg: bus_msgs.append(msg.data))

    engine = org.get_block("engine")
    engine.handle_submit("Test prompt")

    assert len(bus_msgs) == 1
    assert bus_msgs[0]["prompt"] == "Test prompt"
    print("  [PASS] Organism bus communication")

def test_organism_status():
    """Organism: 상태 요약"""
    org = Organism(
        name="poker-agent",
        blocks=[MockProvider(), MockEngine(), MockTracker()],
        config={"model": "exaone3.5:7.8b"},
    )

    status = org.status()
    assert status["name"] == "poker-agent"
    assert status["block_count"] == 3
    assert status["species"] == "game_agent"  # engine + provider + tracking = game_agent
    print("  [PASS] Organism status")


# ============================================================
# 6. Homeostasis Tests
# ============================================================

def test_homeostasis_negative_feedback():
    """Homeostasis: 에러율 높으면 모델 전환 권고"""
    controller = HomeostasisController()
    vitals = VitalSigns(error_rate=0.15)  # setpoint 0.05의 3배

    regs = controller.check(vitals)
    actions = [r.action for r in regs]
    assert "switch_model" in actions
    print("  [PASS] Homeostasis negative feedback")

def test_homeostasis_circuit_breaker():
    """Homeostasis: 연속 실패 → 서킷 브레이커"""
    controller = HomeostasisController()
    vitals = VitalSigns(consecutive_failures=5)

    regs = controller.check(vitals)
    actions = [r.action for r in regs]
    assert "open_circuit_breaker" in actions
    print("  [PASS] Homeostasis circuit breaker")

def test_homeostasis_positive_feedback():
    """Homeostasis: 회복 → 서킷 브레이커 해제"""
    controller = HomeostasisController()
    vitals = VitalSigns(consecutive_failures=0, circuit_breaker_open=True)

    regs = controller.check(vitals)
    actions = [r.action for r in regs]
    assert "close_circuit_breaker" in actions
    print("  [PASS] Homeostasis positive feedback")


# ============================================================
# 7. Membrane Tests
# ============================================================

def test_membrane_access_control():
    """Membrane: 허용/차단"""
    identity = EntityIdentity(entity_id="entity-1", name="agent-1")
    membrane = Membrane(identity, policy="selective")

    # 허용 안 된 peer
    ok, reason = membrane.validate_incoming("stranger", "submit")
    assert not ok

    # peer 허용
    membrane.allow_peer("friend")
    membrane.expose("submit")
    ok, reason = membrane.validate_incoming("friend", "submit")
    assert ok

    # 숨긴 포트
    membrane.hide("submit")
    ok, reason = membrane.validate_incoming("friend", "submit")
    assert not ok
    print("  [PASS] Membrane access control")

def test_membrane_wrap_message():
    """Membrane: 메시지 서명"""
    identity = EntityIdentity(entity_id="entity-1", name="agent-1")
    membrane = Membrane(identity)

    wrapped = membrane.wrap_outgoing("entity-2", "submit", {"data": "hello"})
    assert wrapped.source == "entity-1"
    assert wrapped.target == "entity-2"
    assert len(wrapped.signature) == 16
    print("  [PASS] Membrane message wrapping")


# ============================================================
# Run all tests
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Ludex Phase 0 — Core Communication System Tests")
    print("=" * 60)

    print("\n[Bus]")
    test_bus_pubsub()
    test_bus_multiple_subscribers()
    test_bus_wildcard()
    test_bus_seq_numbers()
    test_bus_unsubscribe()

    print("\n[Signals]")
    test_signals_basic()
    test_signals_priority()
    test_signals_wildcard()

    print("\n[Config]")
    test_config_layers()
    test_config_change_signal()
    test_config_snapshot()

    print("\n[Block + Port]")
    test_block_lifecycle()
    test_block_port_handler()

    print("\n[Organism]")
    test_organism_assembly()
    test_organism_port_wiring()
    test_organism_attach_detach()
    test_organism_bus_communication()
    test_organism_status()

    print("\n[Homeostasis]")
    test_homeostasis_negative_feedback()
    test_homeostasis_circuit_breaker()
    test_homeostasis_positive_feedback()

    print("\n[Membrane]")
    test_membrane_access_control()
    test_membrane_wrap_message()

    print("\n" + "=" * 60)
    print("All Phase 0 tests passed!")
    print("=" * 60)
