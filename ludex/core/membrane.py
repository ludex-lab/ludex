"""
Membrane — 세포막 (개체 경계 제어)

개체의 내부/외부를 구분하고 출입을 제어.
내부: Bus/Signals 직접 사용 (신뢰 기반)
외부: Membrane을 통해서만 통신 (검증 필수)

Reference:
- CC: permissions.py ToolPermissionContext (deny list), deferred_init.py (trust gate)
- OC: security/audit.ts (3-severity), channels/allowlists/ (merge logic)
"""

from __future__ import annotations

import time
import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class EntityIdentity:
    """개체의 고유 정체성"""
    entity_id: str
    name: str
    species: str = ""           # e.g., "game_agent", "measurement"
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class WrappedMessage:
    """Membrane을 통해 외부로 나가는 서명된 메시지"""
    source: str                 # 발신 entity_id
    target: str                 # 수신 entity_id
    port_name: str
    payload: Any
    signature: str
    timestamp: float = field(default_factory=time.time)


class Membrane:
    """
    개체의 세포막. 내부/외부 통신을 구분하고 보안 적용.

    - expose(): 내부 포트를 외부에 노출
    - hide(): 포트를 외부에서 숨김
    - allow_peer(): 특정 외부 개체 허용
    - validate_incoming(): 외부 요청 검증
    """

    def __init__(self, identity: EntityIdentity, policy: str = "selective"):
        self.identity = identity
        self.policy = policy            # "open", "selective", "closed"
        self._allowed_peers: set[str] = set()
        self._exposed_ports: set[str] = set()
        self._denied_ports: set[str] = set()
        self._secret: str = hashlib.sha256(identity.entity_id.encode()).hexdigest()[:16]

    def expose(self, port_name: str) -> None:
        """내부 포트를 외부에 노출"""
        self._exposed_ports.add(port_name)
        self._denied_ports.discard(port_name)

    def hide(self, port_name: str) -> None:
        """포트를 외부에서 숨김"""
        self._exposed_ports.discard(port_name)
        self._denied_ports.add(port_name)

    def allow_peer(self, peer_entity_id: str) -> None:
        """특정 외부 개체의 접근 허용"""
        self._allowed_peers.add(peer_entity_id)

    def deny_peer(self, peer_entity_id: str) -> None:
        """특정 외부 개체 차단"""
        self._allowed_peers.discard(peer_entity_id)

    def validate_incoming(self, source_entity_id: str, port_name: str) -> tuple[bool, str]:
        """
        외부에서 들어오는 요청 검증.
        반환: (허용 여부, 사유)
        """
        if self.policy == "closed":
            return False, "Membrane policy is closed"

        if self.policy == "selective":
            if source_entity_id not in self._allowed_peers:
                return False, f"Peer '{source_entity_id}' not in allowlist"

        if port_name in self._denied_ports:
            return False, f"Port '{port_name}' is denied"

        if self._exposed_ports and port_name not in self._exposed_ports:
            return False, f"Port '{port_name}' is not exposed"

        return True, "OK"

    def wrap_outgoing(self, target_entity_id: str, port_name: str, payload: Any) -> WrappedMessage:
        """외부로 나가는 데이터에 개체 식별 + 서명 추가"""
        sig_data = f"{self.identity.entity_id}:{target_entity_id}:{port_name}:{self._secret}"
        signature = hashlib.sha256(sig_data.encode()).hexdigest()[:16]

        return WrappedMessage(
            source=self.identity.entity_id,
            target=target_entity_id,
            port_name=port_name,
            payload=payload,
            signature=signature,
        )

    @property
    def exposed_ports(self) -> set[str]:
        return set(self._exposed_ports)

    @property
    def allowed_peers(self) -> set[str]:
        return set(self._allowed_peers)

    def __repr__(self) -> str:
        return f"Membrane({self.identity.name!r}, policy={self.policy!r}, exposed={len(self._exposed_ports)}, peers={len(self._allowed_peers)})"
