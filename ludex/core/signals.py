# Mirrored in public repo: ai-creature-field-study/ludex/core/signals.py — sync on change.
"""
Signals — 신경 시스템 (이벤트 알림)

Bus와 달리 데이터 전달이 아니라 "무슨 일이 일어났다"는 경량 알림.
"턴이 시작됐다", "에러가 발생했다" 같은 라이프사이클 이벤트.

Reference:
- OC: src/plugins/hooks.ts (20+ hook types, priority ordering)
- CC: costHook.py, history.py (simple callback pattern)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class SignalHandler:
    """시그널 핸들러. 우선순위 기반 실행."""
    event: str
    handler: Callable
    priority: int = 0  # 낮을수록 먼저 실행
    name: str = ""


class Signals:
    """
    블록 간 이벤트 알림을 관리하는 신경 시스템.

    Bus = 데이터 흐름 (혈액). 큰 페이로드.
    Signals = 이벤트 알림 (신경 자극). 가벼운 신호.
    """

    def __init__(self):
        self._handlers: dict[str, list[SignalHandler]] = defaultdict(list)

    def on(self, event: str, handler: Callable, priority: int = 0, name: str = "") -> SignalHandler:
        """이벤트 리스너 등록. 우선순위 낮을수록 먼저 실행."""
        sh = SignalHandler(event=event, handler=handler, priority=priority, name=name)
        self._handlers[event].append(sh)
        self._handlers[event].sort(key=lambda h: h.priority)
        return sh

    def off(self, handler: SignalHandler) -> None:
        """이벤트 리스너 해제"""
        handlers = self._handlers.get(handler.event, [])
        if handler in handlers:
            handlers.remove(handler)

    def emit(self, event: str, **kwargs: Any) -> int:
        """
        이벤트 발생 알림. 등록된 모든 핸들러를 우선순위 순으로 실행.
        반환값: 실행된 핸들러 수.
        """
        count = 0
        for sh in self._handlers.get(event, []):
            try:
                sh.handler(**kwargs)
                count += 1
            except Exception as e:
                logger.error(f"Signal handler error [{sh.name}] on '{event}': {e}")

        # 와일드카드 ("*"는 모든 이벤트 수신)
        for sh in self._handlers.get("*", []):
            try:
                sh.handler(event=event, **kwargs)
                count += 1
            except Exception as e:
                logger.error(f"Signal wildcard handler error [{sh.name}]: {e}")

        return count

    def listeners(self, event: str | None = None) -> list[SignalHandler]:
        """등록된 리스너 목록 조회"""
        if event is None:
            return [h for handlers in self._handlers.values() for h in handlers]
        return list(self._handlers.get(event, []))

    def clear(self) -> None:
        """모든 리스너 해제"""
        self._handlers.clear()
