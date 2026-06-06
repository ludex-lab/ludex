"""
Organism — 블록 조립기

모든 블록을 하나의 유기체로 조립하는 컨테이너.
포트 자동 매칭, Bus/Signals/Config 공유, Homeostasis 모니터링.

Reference:
- CC: bootstrap_graph.py (7-stage bootstrap), runtime.py bootstrap_session()
- OC: gateway/server.impl.ts (startup orchestration), plugins/loader.ts (4-stage loading)
"""

from __future__ import annotations

import uuid
import time
import logging
from typing import Any, Optional

from ludex.core.bus import Bus
from ludex.core.signals import Signals
from ludex.core.config import Config
from ludex.core.block import Block
from ludex.core.port import Port, PortConnection
from ludex.core.vitals import VitalSigns, TimeAwareness, HomeostasisController
from ludex.core.membrane import Membrane, EntityIdentity

logger = logging.getLogger(__name__)


class LifecycleState:
    """유기체 생명 주기 상태"""
    INITIALIZING = "initializing"
    ALIVE = "alive"
    STRESSED = "stressed"
    RECOVERING = "recovering"
    DYING = "dying"
    DEAD = "dead"


class Organism:
    """
    블록들을 하나의 유기체로 조립.

    organism = Organism(
        name="poker-agent",
        blocks=[ProviderBlock(...), EngineBlock(...), TrackingBlock(...)],
    )
    """

    def __init__(
        self,
        blocks: list[Block] | None = None,
        name: str = "",
        config: dict[str, Any] | None = None,
        homeostasis: bool = True,
        membrane_policy: str = "selective",
    ):
        self.entity_id = uuid.uuid4().hex
        self.name = name or f"organism-{self.entity_id[:8]}"
        self.created_at = time.time()

        # Core systems
        self.bus = Bus()
        self.signals = Signals()
        self.config = Config(defaults=config or {})
        self.config.bind_signals(self.signals)

        # Identity & boundary
        self.identity = EntityIdentity(
            entity_id=self.entity_id,
            name=self.name,
            species=self._infer_species(blocks or []),
        )
        self.membrane = Membrane(self.identity, policy=membrane_policy)

        # Lifecycle
        self._state = LifecycleState.INITIALIZING

        # Block registry
        self._blocks: dict[str, Block] = {}

        # Homeostasis
        self._homeostasis_enabled = homeostasis
        self._homeostasis = HomeostasisController() if homeostasis else None
        self._vitals_history: list[VitalSigns] = []

        # Time tracking
        self._turn_timestamps: list[float] = []
        self._last_activity: float = self.created_at
        self._idle_threshold: float = 300.0  # 5분 유휴 = idle 판정

        # 턴 시각 기록용 시그널 구독
        self.signals.on("turn.ended", self._record_turn_time, name="organism_time")

        # Bus에서 vitals 데이터 수집 (Config 남용 수정 — Cody 피드백)
        self._latest_vitals_data: dict = {}
        self.bus.subscribe("vitals.update", self._on_vitals_update, subscriber="organism")

        # Attach initial blocks
        if blocks:
            for block in blocks:
                self.attach(block)

        # Wire ports after all blocks attached
        if blocks:
            self._wire_ports()

        self._state = LifecycleState.ALIVE
        logger.info(f"Organism '{self.name}' created with {len(self._blocks)} blocks")

    # --- Block Management ---

    def attach(self, block: Block) -> None:
        """블록을 유기체에 부착 (장기 이식)"""
        if block.name in self._blocks:
            raise ValueError(f"Block '{block.name}' already attached")

        self._blocks[block.name] = block
        block.attach(self.bus, self.signals, self.config, organism=self)
        self.signals.emit("block.attached", block_name=block.name)
        logger.debug(f"Attached block '{block.name}' to '{self.name}'")

    def detach(self, block_name: str) -> Optional[Block]:
        """블록을 유기체에서 분리 (장기 제거)"""
        block = self._blocks.pop(block_name, None)
        if block:
            block.detach()
            self.signals.emit("block.detached", block_name=block_name)
            logger.debug(f"Detached block '{block_name}' from '{self.name}'")
        return block

    def get_block(self, name: str) -> Optional[Block]:
        """이름으로 블록 조회"""
        return self._blocks.get(name)

    @property
    def blocks(self) -> dict[str, Block]:
        """부착된 모든 블록"""
        return dict(self._blocks)

    # --- Port Wiring ---

    def _wire_ports(self) -> None:
        """
        모든 블록의 requires/provides 포트를 자동 매칭.

        래핑 체인 지원: 같은 포트를 provides + requires 하는 블록이 있으면
        래핑 체인을 형성한다.
        예: Resilience provides "llm_call" + requires "llm_call"
            → Engine → Resilience.llm_call → Provider.llm_call
        """
        connections = []

        # 1단계: 래핑 체인 구성
        # 같은 포트를 provides + requires 하는 블록 찾기 (= wrapper)
        wrappers: dict[str, list[str]] = {}  # port_name -> [wrapper block names]
        for name, block in self._blocks.items():
            provides_names = {p.name for p in block.provides}
            requires_names = {p.name for p in block.requires}
            overlap = provides_names & requires_names
            for port_name in overlap:
                wrappers.setdefault(port_name, []).append(name)

        # 2단계: 래핑 체인의 inner (원본 제공자)에 먼저 연결
        for port_name, wrapper_names in wrappers.items():
            for wrapper_name in wrapper_names:
                wrapper = self._blocks[wrapper_name]
                # wrapper의 requires를 원본 제공자에 연결
                inner_handler = self._find_provider(port_name, exclude=wrapper_name, exclude_wrappers=wrapper_names)
                if inner_handler:
                    wrapper.connect(port_name, inner_handler)
                    connections.append(PortConnection(
                        provider_block=inner_handler.__self__.name if hasattr(inner_handler, '__self__') else "unknown",
                        consumer_block=wrapper_name,
                        port_name=port_name,
                    ))

        # 3단계: 일반 소비자 연결 (wrapper가 있으면 wrapper에, 없으면 원본에)
        for consumer_name, consumer in self._blocks.items():
            for req_port in consumer.requires:
                # 이미 연결된 wrapper는 건너뜀
                if consumer_name in wrappers.get(req_port.name, []):
                    continue

                # wrapper가 있으면 wrapper에 연결, 없으면 원본에
                handler = None
                if req_port.name in wrappers:
                    # wrapper의 provides 핸들러 사용
                    for wrapper_name in wrappers[req_port.name]:
                        wrapper = self._blocks[wrapper_name]
                        handler = wrapper.get_handler(req_port.name)
                        if handler:
                            break

                if not handler:
                    handler = self._find_provider(req_port.name, exclude=consumer_name)

                if handler:
                    consumer.connect(req_port.name, handler)
                    connections.append(PortConnection(
                        provider_block=handler.__self__.name if hasattr(handler, '__self__') else "unknown",
                        consumer_block=consumer_name,
                        port_name=req_port.name,
                    ))
                    logger.debug(f"Wired port '{req_port.name}': -> {consumer_name}")
                elif req_port.required:
                    logger.warning(
                        f"Block '{consumer_name}' requires port '{req_port.name}' "
                        f"but no provider found (will use default)"
                    )

        if connections:
            logger.info(f"Wired {len(connections)} port connections")

    def _find_provider(self, port_name: str, exclude: str = "", exclude_wrappers: list[str] | None = None) -> Any:
        """특정 포트를 provides하는 블록의 핸들러를 찾는다"""
        excludes = {exclude}
        if exclude_wrappers:
            excludes.update(exclude_wrappers)

        for name, block in self._blocks.items():
            if name in excludes:
                continue
            for prov_port in block.provides:
                if prov_port.name == port_name:
                    handler = block.get_handler(port_name)
                    if handler:
                        return handler
        return None

    def rewire(self) -> None:
        """포트를 다시 매칭 (블록 추가/제거 후)"""
        # 기존 연결 초기화
        for block in self._blocks.values():
            block._connections.clear()
        self._wire_ports()

    # --- Vitals Collection (from Bus, not Config) ---

    def _on_vitals_update(self, msg):
        """Bus에서 vitals 데이터 수집"""
        self._latest_vitals_data.update(msg.data)

    # --- Time Tracking ---

    def _record_turn_time(self, **kwargs):
        """턴 완료 시 시각 기록"""
        now = time.time()
        self._turn_timestamps.append(now)
        self._last_activity = now
        # 최근 20개만 유지
        if len(self._turn_timestamps) > 20:
            self._turn_timestamps = self._turn_timestamps[-20:]

    def _build_time_awareness(self) -> TimeAwareness:
        """현재 시간 인식 구축"""
        now = time.time()
        age = now - self.created_at
        idle = now - self._last_activity
        is_idle = idle > self._idle_threshold

        # 평균 턴 간격 계산 (심박 주기)
        avg_interval = 0.0
        if len(self._turn_timestamps) >= 2:
            intervals = [
                self._turn_timestamps[i] - self._turn_timestamps[i-1]
                for i in range(1, len(self._turn_timestamps))
            ]
            avg_interval = sum(intervals) / len(intervals)

        return TimeAwareness(
            created_at=self.created_at,
            age_seconds=round(age, 1),
            last_activity_at=self._last_activity,
            idle_seconds=round(idle, 1),
            is_idle=is_idle,
            turn_timestamps=tuple(self._turn_timestamps[-20:]),
            avg_turn_interval=round(avg_interval, 3),
        )

    # --- Vital Signs ---

    def measure_vitals(self) -> VitalSigns:
        """현재 생체 신호 측정"""
        # 데이터 소스: Bus에서 수집한 vitals (Config 남용 수정)
        vd = self._latest_vitals_data
        budget = self.config.get("token_budget", 0)
        tokens_used = vd.get("tokens_used", 0)

        vitals = VitalSigns(
            temporal=self._build_time_awareness(),
            total_turns=vd.get("total_turns", 0),
            tokens_per_turn=vd.get("tokens_per_turn", 0.0),
            token_budget_remaining=budget - tokens_used,
            context_utilization=vd.get("context_utilization", 0.0),
            error_rate=vd.get("error_rate", 0.0),
            latency_ms=vd.get("latency_ms", 0.0),
            consecutive_failures=self.config.get("_consecutive_failures", 0),  # Resilience still uses Config for circuit breaker state
            circuit_breaker_open=self.config.get("_circuit_breaker_open", False),
            memory_entries=self.config.get("_memory_entries", 0),  # Memory block updates this
            memory_recall_hit_rate=self.config.get("_memory_hit_rate", 0.0),
        )
        self._vitals_history.append(vitals)
        return vitals

    def check_homeostasis(self) -> list:
        """항상성 체크 + 자동 조절"""
        if not self._homeostasis:
            return []
        vitals = self.measure_vitals()
        regulations = self._homeostasis.check(vitals)
        for reg in regulations:
            self.signals.emit("homeostasis.regulation", regulation=reg)
            logger.info(f"Homeostasis [{reg.type}]: {reg.action} — {reg.reason}")
        return regulations

    # --- Info ---

    def status(self) -> dict[str, Any]:
        """유기체 상태 요약"""
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "state": self._state,
            "species": self.identity.species,
            "blocks": list(self._blocks.keys()),
            "block_count": len(self._blocks),
            "config_keys": self.config.keys(),
            "membrane_policy": self.membrane.policy,
            "uptime_seconds": round(time.time() - self.created_at, 1),
            "vitals_history_count": len(self._vitals_history),
        }

    def _infer_species(self, blocks: list[Block]) -> str:
        """블록 구성에서 종(species) 추론"""
        names = {b.name for b in blocks}
        if "engine" in names and "provider" in names and "tracking" in names:
            return "game_agent"
        if "engine" in names and "provider" in names and "registry" in names:
            return "measurement"
        if "provider" in names and "resilience" in names:
            return "minimal"
        return "custom"

    def __repr__(self) -> str:
        return f"<Organism '{self.name}' [{len(self._blocks)} blocks]>"

    def __del__(self):
        # 정리: 모든 블록 detach
        for name in list(self._blocks.keys()):
            try:
                self.detach(name)
            except Exception:
                pass
