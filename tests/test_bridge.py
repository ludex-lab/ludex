"""
Phase 5a Tests — EntityBridge (유기체 간 통신)

1. Connection: 유기체 등록, 연결, 해제
2. Messaging: send, broadcast, channel listeners
3. Remote Call: 원격 포트 호출
4. Membrane: 보안 정책 준수
5. Integration: 실제 유기체 간 통신
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism import Organism
from ludex.core.block import Block
from ludex.core.port import Port
from ludex.core.bridge import EntityBridge, BridgeMessage
from ludex.blocks.provider import LLMResponse
from ludex.blocks.engine import EngineBlock
from ludex.blocks.tracking import TrackingBlock


class MockProvider(Block):
    name = "provider"
    provides = [Port("llm_call"), Port("health_check"), Port("list_models")]
    requires = []
    def handle_llm_call(self, prompt="", **kwargs):
        return LLMResponse(content=f"Response: {prompt[:30]}", model="mock",
                           tokens_in=10, tokens_out=5, latency_ms=50.0)
    def handle_health_check(self): return {"status": "healthy"}
    def handle_list_models(self): return ["mock"]


def _make_org(name):
    return Organism(name=name, blocks=[
        MockProvider(), EngineBlock(), TrackingBlock(),
    ])


# ============================================================
# 1. Connection
# ============================================================

def test_register():
    """유기체 등록"""
    bridge = EntityBridge()
    org = _make_org("alpha")
    bridge.register(org)
    assert bridge.total_organisms == 1


def test_connect_two():
    """두 유기체 연결"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)
    assert bridge.is_connected(a, b)
    assert bridge.is_connected(b, a)  # 양방향
    assert bridge.total_connections == 1


def test_connect_unidirectional():
    """단방향 연결"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b, bidirectional=False)
    assert bridge.is_connected(a, b)
    assert not bridge.is_connected(b, a)


def test_disconnect():
    """연결 해제"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)
    bridge.disconnect(a, b)
    assert not bridge.is_connected(a, b)


def test_get_connected():
    """연결된 유기체 목록"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    c = _make_org("gamma")
    bridge.connect(a, b)
    bridge.connect(a, c)
    connected = bridge.get_connected(a)
    assert len(connected) == 2


# ============================================================
# 2. Messaging
# ============================================================

def test_send_message():
    """메시지 전송"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)

    # b의 membrane에 채널 노출
    b.membrane.expose("game.action")

    result = bridge.send(a, b, channel="game.action", payload={"action": "COOPERATE"})
    assert result is True

    stats_a = bridge.get_stats(a)
    assert stats_a.messages_sent == 1


def test_send_without_connection():
    """연결 없이 전송 → 실패"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.register(a)
    bridge.register(b)

    result = bridge.send(a, b, channel="test", payload="hello")
    assert result is False

    stats_a = bridge.get_stats(a)
    assert stats_a.messages_rejected == 1


def test_send_triggers_signal():
    """메시지 수신 시 signal 발행"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)
    b.membrane.expose("game.action")

    events = []
    b.signals.on("bridge.game.action", lambda **kw: events.append(kw))

    bridge.send(a, b, channel="game.action", payload={"round": 1})
    assert len(events) == 1
    assert events[0]["source"] == "alpha"
    assert events[0]["payload"]["round"] == 1


def test_broadcast():
    """연결된 모든 유기체에 브로드캐스트"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    c = _make_org("gamma")
    bridge.connect(a, b)
    bridge.connect(a, c)
    b.membrane.expose("game.start")
    c.membrane.expose("game.start")

    sent = bridge.broadcast(a, channel="game.start", payload={"round": 1})
    assert sent == 2


def test_channel_listener():
    """채널 리스너"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)
    b.membrane.expose("game.action")

    messages = []
    bridge.on("game.action", lambda msg: messages.append(msg))

    bridge.send(a, b, channel="game.action", payload="COOPERATE")
    assert len(messages) == 1
    assert isinstance(messages[0], BridgeMessage)
    assert messages[0].source_name == "alpha"


def test_message_log():
    """메시지 로그"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)
    b.membrane.expose("chat")

    bridge.send(a, b, "chat", "hello")
    bridge.send(a, b, "chat", "world")

    log = bridge.get_message_log()
    assert len(log) == 2
    assert log[0].payload == "hello"


# ============================================================
# 3. Remote Call
# ============================================================

def test_call_remote_port():
    """원격 포트 호출"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)
    b.membrane.expose("health_check")

    result = bridge.call(a, b, port="health_check")
    assert result["status"] == "healthy"


def test_call_remote_submit():
    """원격 submit 호출"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)
    b.membrane.expose("submit")

    result = bridge.call(a, b, port="submit", prompt="What is 1+1?")
    assert hasattr(result, 'response')


def test_call_without_connection():
    """연결 없이 원격 호출 → ConnectionError"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.register(a)
    bridge.register(b)

    try:
        bridge.call(a, b, port="health_check")
        assert False, "Should have raised ConnectionError"
    except ConnectionError:
        pass


def test_call_nonexistent_port():
    """존재하지 않는 포트 호출 → AttributeError"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)
    b.membrane.expose("nonexistent")

    try:
        bridge.call(a, b, port="nonexistent")
        assert False, "Should have raised AttributeError"
    except AttributeError:
        pass


# ============================================================
# 4. Membrane Security
# ============================================================

def test_membrane_blocks_unexposed_port():
    """expose 설정 후 다른 포트로 call → PermissionError"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)
    # b는 health_check만 expose → 다른 포트는 차단
    b.membrane.expose("health_check")

    # health_check는 통과
    result = bridge.call(a, b, port="health_check")
    assert result["status"] == "healthy"

    # submit은 차단
    try:
        bridge.call(a, b, port="submit")
        assert False, "Should have raised PermissionError"
    except PermissionError:
        pass


def test_membrane_blocks_unexposed_message():
    """expose 설정 후 다른 채널로 send → 거부"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)
    # b는 game.action만 expose
    b.membrane.expose("game.action")

    # game.action은 통과
    assert bridge.send(a, b, channel="game.action", payload="ok") is True
    # secret은 차단
    assert bridge.send(a, b, channel="secret", payload="hack") is False


def test_open_membrane_allows_all():
    """open 정책 membrane은 모든 접근 허용"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    b.membrane.policy = "open"
    bridge.connect(a, b)

    result = bridge.call(a, b, port="health_check")
    assert result["status"] == "healthy"


def test_closed_membrane_blocks_all():
    """closed 정책 membrane은 모든 접근 차단"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    b.membrane.policy = "closed"
    bridge.connect(a, b)

    try:
        bridge.call(a, b, port="health_check")
        assert False, "Should have raised PermissionError"
    except PermissionError:
        pass


# ============================================================
# 5. Multi-Organism Network
# ============================================================

def test_three_way_network():
    """3개 유기체 네트워크"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    c = _make_org("gamma")

    bridge.connect(a, b)
    bridge.connect(b, c)
    # a-c는 직접 연결 없음

    assert bridge.is_connected(a, b)
    assert bridge.is_connected(b, c)
    assert not bridge.is_connected(a, c)
    assert bridge.total_organisms == 3
    assert bridge.total_connections == 2


def test_stats_tracking():
    """통계 추적"""
    bridge = EntityBridge()
    a = _make_org("alpha")
    b = _make_org("beta")
    bridge.connect(a, b)
    b.membrane.expose("chat")

    bridge.send(a, b, "chat", "msg1")
    bridge.send(a, b, "chat", "msg2")

    stats_a = bridge.get_stats(a)
    stats_b = bridge.get_stats(b)
    assert stats_a.messages_sent == 2
    assert stats_b.messages_received == 2


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":
    test_functions = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0

    for test_fn in test_functions:
        try:
            test_fn()
            print(f"  PASS  {test_fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test_fn.__name__}: {e}")
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"Phase 5a Bridge: {passed} passed, {failed} failed, {passed + failed} total")
    if failed == 0:
        print("All tests passed!")
    else:
        sys.exit(1)
