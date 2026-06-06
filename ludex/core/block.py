"""
Block — 모든 장기 블록의 기본 클래스

각 블록은:
- provides: 제공하는 포트 (다른 블록이 호출 가능)
- requires: 필요한 포트 (다른 블록에서 충족)
- attach/detach 라이프사이클
- Bus/Signals/Config 자동 주입

Reference:
- CC: execution_registry.py (MirroredTool/MirroredCommand wrapper)
- OC: plugins/api-builder.ts (plugin register pattern)
"""

from __future__ import annotations

import time
import logging
from typing import Any, Optional, Callable, TYPE_CHECKING

from ludex.core.port import Port

if TYPE_CHECKING:
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config

logger = logging.getLogger(__name__)


class Block:
    """
    모든 장기 블록의 기본 클래스.
    서브클래스는 provides/requires를 선언하고 on_attach/on_detach를 오버라이드.
    """

    # 서브클래스에서 오버라이드
    name: str = "unnamed"
    provides: list[Port] = []
    requires: list[Port] = []

    def __init__(self):
        self._bus: Optional[Bus] = None
        self._signals: Optional[Signals] = None
        self._config: Optional[Config] = None
        self._organism: Any = None
        self._connections: dict[str, Callable] = {}
        self._attached: bool = False
        self._attached_at: Optional[float] = None
        self._signal_handlers: list = []  # detach 시 정리용

    # --- Lifecycle ---

    def attach(self, bus: Bus, signals: Signals, config: Config, organism: Any = None) -> None:
        """유기체에 부착될 때 호출. Bus/Signals/Config/Organism 주입."""
        self._bus = bus
        self._signals = signals
        self._config = config
        self._organism = organism
        self._attached = True
        self._attached_at = time.time()
        self.on_attach()
        logger.debug(f"Block '{self.name}' attached")

    def detach(self) -> None:
        """유기체에서 분리될 때 호출."""
        self.on_detach()
        # 등록한 signal handlers 정리
        for sh in self._signal_handlers:
            if self._signals:
                self._signals.off(sh)
        self._signal_handlers.clear()
        self._bus = None
        self._signals = None
        self._config = None
        self._organism = None
        self._connections.clear()
        self._attached = False
        logger.debug(f"Block '{self.name}' detached")

    @property
    def is_attached(self) -> bool:
        return self._attached

    # --- Override Points ---

    def on_attach(self) -> None:
        """블록이 유기체에 부착된 후. Signal 구독, 초기화 등."""
        pass

    def on_detach(self) -> None:
        """블록이 유기체에서 분리되기 전. 정리 작업."""
        pass

    # --- Port Resolution ---

    def connect(self, port_name: str, handler: Callable) -> None:
        """requires 포트를 다른 블록의 provides 핸들러와 연결"""
        self._connections[port_name] = handler

    def call_port(self, port_name: str, *args: Any, **kwargs: Any) -> Any:
        """연결된 포트를 호출. 연결 안 되면 기본 동작."""
        if port_name in self._connections:
            return self._connections[port_name](*args, **kwargs)
        return self._default_for_port(port_name, *args, **kwargs)

    def _default_for_port(self, port_name: str, *args: Any, **kwargs: Any) -> Any:
        """포트 미연결 시 기본 동작. 서브클래스에서 오버라이드."""
        # required 포트가 연결 안 됐으면 에러
        for port in self.requires:
            if port.name == port_name and port.required:
                raise RuntimeError(
                    f"Block '{self.name}': required port '{port_name}' not connected"
                )
        # optional 포트는 None 반환
        return None

    def get_handler(self, port_name: str) -> Optional[Callable]:
        """provides 포트의 핸들러를 반환. Organism이 포트 매칭 시 사용."""
        method_name = f"handle_{port_name}"
        if hasattr(self, method_name):
            return getattr(self, method_name)
        return None

    # --- Helpers ---

    def _listen(self, event: str, handler: Callable, priority: int = 0) -> None:
        """Signal 리스너 등록 (detach 시 자동 정리)"""
        if self._signals:
            sh = self._signals.on(event, handler, priority=priority, name=self.name)
            self._signal_handlers.append(sh)

    def _publish(self, topic: str, data: Any) -> None:
        """Bus에 데이터 발행"""
        if self._bus:
            self._bus.publish(topic, data, source=self.name)

    def _emit(self, event: str, **kwargs: Any) -> None:
        """Signal 이벤트 발행"""
        if self._signals:
            self._signals.emit(event, **kwargs)

    def _cfg(self, key: str, default: Any = None) -> Any:
        """Config 조회 단축"""
        if self._config:
            return self._config.get(key, default)
        return default

    # --- Benchmark Point ---

    def _timed(self, label: str):
        """성능 측정 데코레이터용 컨텍스트 (나중에 Rust 전환 판단 자료)"""
        return _TimedContext(label, self.name, self._bus)

    def __repr__(self) -> str:
        status = "attached" if self._attached else "detached"
        return f"<{self.__class__.__name__} '{self.name}' [{status}]>"


class _TimedContext:
    """벤치마크 포인트. with 구문으로 사용."""
    def __init__(self, label: str, block_name: str, bus: Optional[Bus]):
        self.label = label
        self.block_name = block_name
        self.bus = bus
        self.start = 0.0

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *args):
        elapsed_ms = (time.perf_counter() - self.start) * 1000
        if self.bus:
            self.bus.publish("benchmark", {
                "block": self.block_name,
                "label": self.label,
                "elapsed_ms": round(elapsed_ms, 2),
            }, source="benchmark")
