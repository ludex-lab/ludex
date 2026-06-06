"""
EntityBridge — 유기체 간 통신 채널

두 Organism의 Membrane을 연결해서 안전한 유기체 간 통신을 제공.
생물학적 비유: 세포 간 접합(gap junction) 또는 시냅스.

통신 방식:
- send(): 한 유기체에서 다른 유기체로 메시지 전송
- call(): 원격 포트 호출 (요청-응답)
- broadcast(): 연결된 모든 유기체에게 전송

보안:
- Membrane의 allowlist/expose 정책 준수
- WrappedMessage로 서명된 통신
- 감사 로그 (audit trail)

Reference:
- OC: gateway/server.impl.ts (WebSocket channels between services)
- CC: runtime.py (tool_use call/response pattern)
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Callable

from ludex.core.membrane import Membrane, WrappedMessage, EntityIdentity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BridgeMessage:
    """Bridge를 통해 전달되는 메시지"""
    source_name: str        # 발신 organism name
    target_name: str        # 수신 organism name
    channel: str            # 메시지 채널 (예: "game.action", "immune.alert")
    payload: Any
    timestamp: float = field(default_factory=time.time)
    reply_to: str | None = None     # 응답인 경우 원본 메시지 ID


@dataclass
class BridgeStats:
    """Bridge 통계"""
    messages_sent: int = 0
    messages_received: int = 0
    messages_rejected: int = 0
    calls_made: int = 0
    calls_failed: int = 0


class EntityBridge:
    """
    유기체 간 통신 채널.

    사용법:
        bridge = EntityBridge()
        bridge.connect(organism_a, organism_b)

        # 한 방향 메시지
        bridge.send(from_org=organism_a, to_org=organism_b,
                    channel="game.action", payload={"action": "COOPERATE"})

        # 원격 포트 호출
        result = bridge.call(from_org=organism_a, to_org=organism_b,
                            port="submit", prompt="Choose your action")

        # 전체 브로드캐스트
        bridge.broadcast(from_org=organism_a, channel="game.start",
                        payload={"round": 1})
    """

    def __init__(self):
        self._connections: dict[str, set[str]] = {}  # entity_id → {connected entity_ids}
        self._organisms: dict[str, Any] = {}          # entity_id → Organism
        self._name_to_id: dict[str, str] = {}         # organism name → entity_id
        self._listeners: dict[str, list[Callable]] = {}  # channel → [callbacks]
        self._stats: dict[str, BridgeStats] = {}      # entity_id → stats
        self._message_log: list[BridgeMessage] = []

    # --- Connection Management ---

    def register(self, organism) -> None:
        """유기체를 bridge에 등록"""
        eid = organism.entity_id
        self._organisms[eid] = organism
        self._name_to_id[organism.name] = eid
        self._connections.setdefault(eid, set())
        self._stats.setdefault(eid, BridgeStats())
        logger.debug(f"Bridge: registered {organism.name} ({eid[:8]})")

    def connect(self, org_a, org_b, bidirectional: bool = True) -> None:
        """두 유기체를 연결 (Membrane allowlist 상호 등록)"""
        self.register(org_a)
        self.register(org_b)

        eid_a = org_a.entity_id
        eid_b = org_b.entity_id

        # Bridge 연결
        self._connections[eid_a].add(eid_b)
        if bidirectional:
            self._connections[eid_b].add(eid_a)

        # Membrane 상호 허용
        org_a.membrane.allow_peer(eid_b)
        if bidirectional:
            org_b.membrane.allow_peer(eid_a)

        logger.info(f"Bridge: connected {org_a.name} <-> {org_b.name}")

    def disconnect(self, org_a, org_b) -> None:
        """두 유기체 연결 해제"""
        eid_a = org_a.entity_id
        eid_b = org_b.entity_id
        self._connections.get(eid_a, set()).discard(eid_b)
        self._connections.get(eid_b, set()).discard(eid_a)
        org_a.membrane.deny_peer(eid_b)
        org_b.membrane.deny_peer(eid_a)

    def is_connected(self, org_a, org_b) -> bool:
        """두 유기체가 연결되어 있는지"""
        return org_b.entity_id in self._connections.get(org_a.entity_id, set())

    # --- Messaging ---

    def send(self, from_org, to_org, channel: str, payload: Any) -> bool:
        """
        한 유기체에서 다른 유기체로 메시지 전송.
        Membrane 검증을 거침.
        """
        from_eid = from_org.entity_id
        to_eid = to_org.entity_id

        # 연결 확인
        if to_eid not in self._connections.get(from_eid, set()):
            logger.warning(f"Bridge: {from_org.name} not connected to {to_org.name}")
            self._stats[from_eid].messages_rejected += 1
            return False

        # Membrane 검증
        allowed, reason = to_org.membrane.validate_incoming(from_eid, channel)
        if not allowed:
            logger.warning(f"Bridge: message rejected by membrane — {reason}")
            self._stats[from_eid].messages_rejected += 1
            return False

        # 메시지 생성 + 전달
        msg = BridgeMessage(
            source_name=from_org.name,
            target_name=to_org.name,
            channel=channel,
            payload=payload,
        )
        self._message_log.append(msg)
        self._stats[from_eid].messages_sent += 1
        self._stats[to_eid].messages_received += 1

        # 수신 organism의 signals에 이벤트 발행
        to_org.signals.emit(f"bridge.{channel}",
                           source=from_org.name,
                           payload=payload)

        # 채널 리스너 호출
        for listener in self._listeners.get(channel, []):
            listener(msg)

        return True

    def call(self, from_org, to_org, port: str, **kwargs) -> Any:
        """
        원격 포트 호출 — 다른 유기체의 Block 포트를 호출.
        Membrane 검증 후 포트 핸들러 실행.
        """
        from_eid = from_org.entity_id
        to_eid = to_org.entity_id

        self._stats[from_eid].calls_made += 1

        # 연결 확인
        if to_eid not in self._connections.get(from_eid, set()):
            self._stats[from_eid].calls_failed += 1
            raise ConnectionError(f"{from_org.name} not connected to {to_org.name}")

        # Membrane 검증
        allowed, reason = to_org.membrane.validate_incoming(from_eid, port)
        if not allowed:
            self._stats[from_eid].calls_failed += 1
            raise PermissionError(f"Call rejected: {reason}")

        # 포트 찾기 + 실행
        for block in to_org.blocks.values():
            handler = block.get_handler(port)
            if handler:
                return handler(**kwargs)

        self._stats[from_eid].calls_failed += 1
        raise AttributeError(f"Port '{port}' not found in {to_org.name}")

    def broadcast(self, from_org, channel: str, payload: Any) -> int:
        """연결된 모든 유기체에게 메시지 전송. 성공 수 반환."""
        from_eid = from_org.entity_id
        connected = self._connections.get(from_eid, set())
        sent = 0
        for to_eid in connected:
            to_org = self._organisms.get(to_eid)
            if to_org and self.send(from_org, to_org, channel, payload):
                sent += 1
        return sent

    # --- Listeners ---

    def on(self, channel: str, callback: Callable) -> None:
        """채널 리스너 등록 (관찰자 패턴)"""
        self._listeners.setdefault(channel, []).append(callback)

    # --- Query ---

    def get_connected(self, organism) -> list:
        """유기체에 연결된 다른 유기체 목록"""
        eid = organism.entity_id
        return [
            self._organisms[peer_eid]
            for peer_eid in self._connections.get(eid, set())
            if peer_eid in self._organisms
        ]

    def get_stats(self, organism) -> BridgeStats:
        """유기체의 bridge 통계"""
        return self._stats.get(organism.entity_id, BridgeStats())

    def get_message_log(self, limit: int = 50) -> list[BridgeMessage]:
        """최근 메시지 로그"""
        return self._message_log[-limit:]

    @property
    def total_organisms(self) -> int:
        return len(self._organisms)

    @property
    def total_connections(self) -> int:
        return sum(len(peers) for peers in self._connections.values()) // 2
