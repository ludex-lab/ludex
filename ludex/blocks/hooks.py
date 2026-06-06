"""
Hooks Block — 반사 신경 (라이프사이클 인터셉트)

턴 전후, 도구 실행 전후, 프롬프트 구성 시 끼어들 수 있는 장기.
LxM에서 게임 규칙 주입, 반칙 감지, MTI에서 측정 조건 설정 등에 사용.

Reference:
- OC: src/plugins/hooks.ts (20+ hook types, priority ordering, async)
- OC: src/hooks/bundled/ (session-memory, notification hooks)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)


@dataclass
class HookDefinition:
    """훅 정의"""
    name: str
    event: str                  # 어떤 이벤트에 반응하는가
    handler: Callable           # 핸들러 함수
    priority: int = 0           # 낮을수록 먼저 실행
    description: str = ""
    tags: tuple[str, ...] = ()


# --- Standard Hook Events ---
# OC의 20+ 훅 타입에서 핵심만 추출

HOOK_EVENTS = {
    # 턴 라이프사이클
    "before_turn": "턴 시작 전. 프롬프트를 수정하거나 조건을 주입할 수 있음.",
    "after_turn": "턴 완료 후. 결과를 검증하거나 후처리할 수 있음.",

    # 프롬프트
    "before_prompt": "LLM 호출 직전. 시스템 프롬프트나 컨텍스트를 수정할 수 있음.",

    # 도구
    "before_tool": "도구 실행 직전. 도구 호출을 검증하거나 차단할 수 있음.",
    "after_tool": "도구 실행 직후. 결과를 검증하거나 변환할 수 있음.",

    # 세션
    "session_start": "세션 시작 시. 초기화 작업.",
    "session_end": "세션 종료 시. 정리 작업.",

    # 컨텍스트
    "before_compact": "컨텍스트 압축 전. 보존할 메시지를 지정할 수 있음.",

    # 에러
    "on_error": "에러 발생 시. 에러 처리 또는 로깅.",
}


class HooksBlock(Block):
    """
    라이프사이클 훅 블록.

    provides: register_hook, run_hook, list_hooks
    requires: (없음) — Signals를 통해 이벤트를 감지하고 훅을 실행

    사용 예:
        hooks.handle_register_hook(HookDefinition(
            name="inject_board_state",
            event="before_turn",
            handler=lambda prompt, **kw: f"{prompt}\\n현재 보드: {board_state}",
        ))
    """

    name = "hooks"
    provides = [
        Port("register_hook", description="Register a lifecycle hook"),
        Port("run_hook", description="Manually run hooks for an event"),
        Port("list_hooks", description="List registered hooks"),
    ]
    requires = []

    def __init__(self):
        super().__init__()
        self._hooks: dict[str, list[HookDefinition]] = {}

    def on_attach(self):
        # 표준 이벤트에 대한 Signal 리스너 등록
        self._listen("turn.started", lambda **kw: self._trigger("before_turn", **kw))
        self._listen("turn.ended", lambda **kw: self._trigger("after_turn", **kw))
        self._listen("tool.calling", lambda **kw: self._trigger("before_tool", **kw))
        self._listen("tool.executed", lambda **kw: self._trigger("after_tool", **kw))
        self._listen("context.compacted", lambda **kw: self._trigger("before_compact", **kw))
        self._listen("error.occurred", lambda **kw: self._trigger("on_error", **kw))

    # --- Provides: register_hook ---

    def handle_register_hook(self, hook: HookDefinition) -> bool:
        """훅 등록"""
        if hook.event not in HOOK_EVENTS:
            logger.warning(f"Unknown hook event: {hook.event}. Known: {list(HOOK_EVENTS.keys())}")
            # 허용은 하되 경고 (확장성)

        hooks_list = self._hooks.setdefault(hook.event, [])
        hooks_list.append(hook)
        hooks_list.sort(key=lambda h: h.priority)
        logger.debug(f"Registered hook '{hook.name}' for event '{hook.event}' (priority {hook.priority})")
        return True

    # --- Provides: run_hook ---

    def handle_run_hook(self, event: str, **kwargs) -> list[Any]:
        """
        특정 이벤트의 모든 훅을 실행.
        각 훅의 반환값을 리스트로 반환.
        훅이 값을 반환하면 다음 훅의 입력에 반영 (체이닝).
        """
        return self._trigger(event, **kwargs)

    # --- Provides: list_hooks ---

    def handle_list_hooks(self, event: str | None = None) -> list[dict]:
        """등록된 훅 목록"""
        result = []
        events = [event] if event else list(self._hooks.keys())
        for ev in events:
            for hook in self._hooks.get(ev, []):
                result.append({
                    "name": hook.name,
                    "event": hook.event,
                    "priority": hook.priority,
                    "description": hook.description,
                    "tags": hook.tags,
                })
        return result

    # --- Internal ---

    def _trigger(self, event: str, **kwargs) -> list[Any]:
        """훅 실행 (우선순위 순, OC hook runner 패턴)"""
        hooks = self._hooks.get(event, [])
        if not hooks:
            return []

        results = []
        for hook in hooks:
            try:
                result = hook.handler(**kwargs)
                results.append(result)
                # 체이닝: 훅이 값을 반환하면 다음 훅의 kwargs에 반영
                if result is not None and isinstance(result, dict):
                    kwargs.update(result)
            except Exception as e:
                logger.error(f"Hook '{hook.name}' error on '{event}': {e}")
                results.append(None)

        return results

    # --- Convenience: 자주 쓰는 훅 등록 헬퍼 ---

    def before_turn(self, name: str, handler: Callable, priority: int = 0) -> None:
        """before_turn 훅 등록 단축"""
        self.handle_register_hook(HookDefinition(
            name=name, event="before_turn", handler=handler, priority=priority,
        ))

    def after_turn(self, name: str, handler: Callable, priority: int = 0) -> None:
        """after_turn 훅 등록 단축"""
        self.handle_register_hook(HookDefinition(
            name=name, event="after_turn", handler=handler, priority=priority,
        ))

    def before_tool(self, name: str, handler: Callable, priority: int = 0) -> None:
        """before_tool 훅 등록 단축"""
        self.handle_register_hook(HookDefinition(
            name=name, event="before_tool", handler=handler, priority=priority,
        ))

    def after_tool(self, name: str, handler: Callable, priority: int = 0) -> None:
        """after_tool 훅 등록 단축"""
        self.handle_register_hook(HookDefinition(
            name=name, event="after_tool", handler=handler, priority=priority,
        ))

    def on_error(self, name: str, handler: Callable, priority: int = 0) -> None:
        """on_error 훅 등록 단축"""
        self.handle_register_hook(HookDefinition(
            name=name, event="on_error", handler=handler, priority=priority,
        ))
