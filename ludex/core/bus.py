"""
Bus — 혈관 시스템 (데이터 흐름)

Pub/Sub 패턴으로 블록 간 데이터를 전달한다.
Provider가 LLM 응답을 발행하면 Engine, Tracking이 각자 구독해서 처리.

Reference:
- OC: src/shared/listeners.ts (global listener Set + seq numbering)
- CC: query_engine.py stream_submit_message() (generator streaming)
"""

from __future__ import annotations

import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BusMessage:
    """Bus를 통해 전달되는 메시지"""
    topic: str
    data: Any
    timestamp: float
    seq: int
    source: str = ""  # 발신 블록 이름


@dataclass
class Subscription:
    """구독 핸들. unsubscribe 시 사용."""
    topic: str
    handler: Callable
    subscriber: str = ""


class Bus:
    """
    블록 간 데이터 흐름을 관리하는 혈관 시스템.

    - Pub/Sub 토픽 기반 라우팅
    - 이벤트 순서 보장 (seq 번호, OC 패턴)
    - 동기 전달 (같은 Organism 내부)
    """

    def __init__(self):
        self._subscribers: dict[str, list[Subscription]] = defaultdict(list)
        self._seq: int = 0
        self._history: list[BusMessage] = []
        self._history_limit: int = 1000

    def subscribe(self, topic: str, handler: Callable, subscriber: str = "") -> Subscription:
        """특정 토픽의 데이터를 구독"""
        sub = Subscription(topic=topic, handler=handler, subscriber=subscriber)
        self._subscribers[topic].append(sub)
        return sub

    def unsubscribe(self, subscription: Subscription) -> None:
        """구독 해제 (블록 제거 시)"""
        subs = self._subscribers.get(subscription.topic, [])
        if subscription in subs:
            subs.remove(subscription)

    def publish(self, topic: str, data: Any, source: str = "") -> BusMessage:
        """토픽에 데이터를 발행 (모든 구독자에게 전달)"""
        self._seq += 1
        msg = BusMessage(
            topic=topic,
            data=data,
            timestamp=time.time(),
            seq=self._seq,
            source=source,
        )

        # 히스토리 기록 (감사 로그)
        self._history.append(msg)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]

        # 구독자에게 전달
        for sub in self._subscribers.get(topic, []):
            try:
                sub.handler(msg)
            except Exception as e:
                logger.error(f"Bus handler error [{sub.subscriber}] on '{topic}': {e}")

        # 와일드카드 구독자 ("*" 토픽은 모든 메시지 수신)
        for sub in self._subscribers.get("*", []):
            try:
                sub.handler(msg)
            except Exception as e:
                logger.error(f"Bus wildcard handler error [{sub.subscriber}]: {e}")

        return msg

    def get_history(self, topic: str | None = None, limit: int = 50) -> list[BusMessage]:
        """히스토리 조회 (디버깅/감사용)"""
        if topic is None:
            return self._history[-limit:]
        return [m for m in self._history if m.topic == topic][-limit:]

    def clear(self) -> None:
        """모든 구독 해제 + 히스토리 초기화"""
        self._subscribers.clear()
        self._history.clear()
        self._seq = 0
