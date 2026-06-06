"""
Config — 내분비 시스템 (전역 설정)

모든 블록의 행동을 전역적으로 조절하는 호르몬.
설정 변경 시 자동으로 Signals에 config.changed 발행.

Reference:
- OC: src/config/config.ts + sessions/model-overrides.ts (5-layer override)
- CC: context.py (frozen PortContext), QueryEngineConfig (frozen dataclass)
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ludex.core.signals import Signals


class Config:
    """
    전역 설정 관리. 3-layer: defaults → project → session overrides.
    변경 시 연결된 Signals에 'config.changed' 이벤트 발행.
    """

    def __init__(self, defaults: dict[str, Any] | None = None):
        self._defaults: dict[str, Any] = defaults or {}
        self._project: dict[str, Any] = {}
        self._session: dict[str, Any] = {}
        self._signals: Optional[Signals] = None

    def bind_signals(self, signals: Signals) -> None:
        """Signals 시스템과 연결 (Organism이 호출)"""
        self._signals = signals

    def get(self, key: str, default: Any = None) -> Any:
        """설정 값 조회. session → project → defaults 순서로 탐색."""
        if key in self._session:
            return self._session[key]
        if key in self._project:
            return self._project[key]
        return self._defaults.get(key, default)

    def set(self, key: str, value: Any, layer: str = "session") -> None:
        """
        설정 값 변경. 기본적으로 session layer에 기록.
        변경 시 config.changed 시그널 발행.
        """
        old_value = self.get(key)

        if layer == "session":
            self._session[key] = value
        elif layer == "project":
            self._project[key] = value
        elif layer == "defaults":
            self._defaults[key] = value

        if old_value != value and self._signals:
            self._signals.emit("config.changed", key=key, old=old_value, new=value, layer=layer)

    def update(self, values: dict[str, Any], layer: str = "session") -> None:
        """여러 설정을 한번에 변경"""
        for key, value in values.items():
            self.set(key, value, layer=layer)

    def snapshot(self) -> dict[str, Any]:
        """현재 설정의 불변 스냅샷 반환 (실험 재현용, CC frozen dataclass 패턴)"""
        merged = {}
        merged.update(self._defaults)
        merged.update(self._project)
        merged.update(self._session)
        return copy.deepcopy(merged)

    def reset_session(self) -> None:
        """세션 오버라이드 초기화 (새 세션 시작 시)"""
        self._session.clear()
        if self._signals:
            self._signals.emit("config.changed", key="*", old=None, new=None, layer="session_reset")

    def keys(self) -> list[str]:
        """모든 설정 키 목록"""
        all_keys = set()
        all_keys.update(self._defaults.keys())
        all_keys.update(self._project.keys())
        all_keys.update(self._session.keys())
        return sorted(all_keys)

    def to_json(self) -> str:
        """현재 설정을 JSON으로 직렬화"""
        return json.dumps(self.snapshot(), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """딕셔너리에서 Config 생성"""
        return cls(defaults=data)

    def __repr__(self) -> str:
        return f"Config(keys={self.keys()})"
