"""
Engine Block — 뇌 (세션/턴/컨텍스트 관리)

유기체의 사고를 담당하는 장기.
턴 기반 대화를 관리하고, 토큰 예산을 제어하고, 컨텍스트를 압축한다.

Reference:
- OC: src/context-engine/ (pluggable context, assemble/compact, token budget)
- CC: query_engine.py (QueryEngineConfig, TurnResult, compact_messages_if_needed)
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ludex.core.block import Block
from ludex.core.port import Port
from ludex.blocks.provider import LLMResponse, LLMError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnResult:
    """한 턴의 결과 (CC TurnResult 패턴)"""
    turn_number: int
    prompt: str
    response: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    model: str = ""
    error: Optional[str] = None
    stop_reason: str = "completed"  # completed, max_turns, max_budget, error


@dataclass
class Message:
    """대화 메시지"""
    role: str           # "system", "user", "assistant"
    content: str
    turn: int = 0
    tokens: int = 0
    timestamp: float = field(default_factory=time.time)


class EngineBlock(Block):
    """
    세션/턴/컨텍스트 관리 블록.

    provides: submit, compact, get_history
    requires: llm_call (Provider 또는 Resilience에서 충족)

    컨텍스트 관리 전략 (OC context-engine 패턴):
    - assemble: 현재 토큰 예산 내에서 메시지 조합
    - compact: 예산 초과 시 오래된 메시지 요약/삭제
    """

    name = "engine"
    provides = [
        Port("submit", description="Submit a turn and get response"),
        Port("compact", description="Compact context to save tokens"),
        Port("get_history", description="Get conversation history"),
    ]
    requires = [
        Port("llm_call", description="LLM API call"),
        Port("get_tools", description="Get available tools", required=False),
        Port("recall", description="Recall from memory", required=False),
        Port("self_introspect", description="D-071/option-b — stage grounding from auto", required=False),
    ]

    def __init__(
        self,
        max_turns: int = 50,
        token_budget: int = 4000,
        compact_after: int = 20,
        system_prompt: str = "",
    ):
        super().__init__()
        self._init_config = {
            "max_turns": max_turns,
            "token_budget": token_budget,
            "compact_after": compact_after,
            "system_prompt": system_prompt,
        }
        self._messages: list[Message] = []
        self._turn_count: int = 0
        self._tokens_used: int = 0
        self._turn_results: list[TurnResult] = []

    def on_attach(self):
        for key, value in self._init_config.items():
            if self._config and self._config.get(key) is None:
                self._config.set(key, value, layer="defaults")
        self._listen("config.changed", self._on_config_changed)
        # 시스템 프롬프트 초기화
        sys_prompt = self._cfg("system_prompt", "")
        if sys_prompt:
            self._update_system_prompt(sys_prompt)

    def _on_config_changed(self, key: str = "", **kwargs):
        if key == "system_prompt":
            # 시스템 프롬프트 변경 시 메시지 목록 업데이트
            new_prompt = kwargs.get("new", "")
            self._update_system_prompt(new_prompt)

    # --- Provides: submit ---

    def handle_submit(self, prompt: str, system: str = "", tools: list | None = None, tool_dispatcher=None,
                      bypass_memory: bool = False, bypass_history: bool = False) -> TurnResult:
        """한 턴 실행: 프롬프트 → LLM 호출 → 결과 반환

        tools/tool_dispatcher가 주어지면 multi-turn 도구 호출 루프 실행:
        1. LLM 응답에 tool_calls가 있으면 dispatcher로 실행
        2. 결과를 다음 LLM 호출에 포함
        3. tool_calls가 없을 때까지 반복 (max 5턴)

        Phase 5e: if no tools passed, use _default_tools (auto-wired by OrganismConfig).
        """
        # Use auto-wired FC tools if none explicitly provided
        if tools is None:
            tools = getattr(self, "_default_tools", None)
        if tool_dispatcher is None:
            tool_dispatcher = getattr(self, "_default_tool_dispatcher", None)
        max_turns = self._cfg("max_turns", 50)
        token_budget = self._cfg("token_budget", 4000)

        # 턴 제한 체크
        if self._turn_count >= max_turns:
            return TurnResult(
                turn_number=self._turn_count,
                prompt=prompt,
                response="",
                stop_reason="max_turns",
            )

        # 토큰 예산 체크
        if self._tokens_used >= token_budget:
            return TurnResult(
                turn_number=self._turn_count,
                prompt=prompt,
                response="",
                stop_reason="max_budget",
            )

        self._turn_count += 1
        self._emit("turn.started", turn_number=self._turn_count)

        # 시스템 프롬프트 결정
        sys_prompt = system or self._cfg("system_prompt", "")

        # Stage grounding (D-071 option b, 2026-05-01) — if Auto provides
        # `self_introspect`, fold the creature-facing stage paragraph into
        # the system prompt so every brain call is grounded in *who they
        # are right now* by observable signals. Cheap (single filesystem
        # walk via stage.audit_creature) and read-only. Skipped silently
        # for organisms without a persistent home_dir or without Auto.
        try:
            stage_text = self.call_port("self_introspect") or ""
            if stage_text and sys_prompt:
                sys_prompt = f"{sys_prompt}\n\n[Self]\n{stage_text}"
            elif stage_text:
                sys_prompt = f"[Self]\n{stage_text}"
        except Exception as e:
            logger.debug(f"engine: self_introspect skipped ({e})")

        # 메모리 리콜 (선택적). MemoryBlock.handle_recall returns
        # list[RecallResult]; serialize to readable lines before
        # injection so the f-string below doesn't dump Python repr
        # into the system prompt (haiku-tier brains misread the
        # repr blob and produce garbage — surfaced by Stacker run
        # 2026-04-26). Callers that want a clean policy-emission
        # call (no biographical memory leaking into prompt) can
        # pass `bypass_memory=True` on handle_submit.
        if not bypass_memory:
            memory_context_raw = self.call_port("recall", prompt) or ""
            memory_context = self._format_memory_context(memory_context_raw)
            if memory_context and sys_prompt:
                sys_prompt = f"{sys_prompt}\n\n[Recalled Memory]\n{memory_context}"

        # 사용자 메시지 기록
        self._messages.append(Message(
            role="user", content=prompt,
            turn=self._turn_count, tokens=self._estimate_tokens(prompt),
        ))

        # 컨텍스트 압축 체크 (OC compact 패턴)
        compact_after = self._cfg("compact_after", 20)
        if len(self._messages) > compact_after:
            self.handle_compact()

        # 대화 히스토리 조립 (OC context-engine assemble 패턴)
        assembled_messages = self._assemble_messages(sys_prompt)

        # LLM 호출 — 멀티턴 히스토리 전달 (+ optional tools)
        with self._timed("engine_submit"):
            if tools:
                result = self.call_port("llm_call", messages=assembled_messages, tools=tools)
            else:
                result = self.call_port("llm_call", messages=assembled_messages)

        # 결과 처리
        if isinstance(result, LLMError):
            turn_result = TurnResult(
                turn_number=self._turn_count,
                prompt=prompt,
                response="",
                error=f"{result.error_type}: {result.message}",
                stop_reason="error",
            )
            self._emit("turn.ended", turn_number=self._turn_count, success=False,
                       response="")
            # Vital signs 업데이트
            self._update_vitals(turn_result)
            self._turn_results.append(turn_result)
            return turn_result

        # 성공
        response = result if isinstance(result, LLMResponse) else LLMResponse(
            content=str(result), model=self._cfg("model", "unknown"),
        )

        total_tokens_in = response.tokens_in
        total_tokens_out = response.tokens_out
        total_latency = response.latency_ms

        # Tool dispatch loop — only if tools + dispatcher provided AND model returned tool_calls
        if tools and tool_dispatcher and response.tool_calls:
            for hop in range(5):  # max 5 tool dispatch hops
                # Append assistant message with tool_calls
                self._messages.append(Message(
                    role="assistant",
                    content=response.content or "",
                    turn=self._turn_count,
                    tokens=response.tokens_out,
                ))
                # Execute each tool call
                for tc in response.tool_calls:
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    tool_args = fn.get("arguments", {})
                    try:
                        tool_result = tool_dispatcher(tool_name, tool_args)
                    except Exception as e:
                        tool_result = f"Error: {e}"
                    # Append tool result message
                    self._messages.append(Message(
                        role="tool",
                        content=str(tool_result),
                        turn=self._turn_count,
                        tokens=self._estimate_tokens(str(tool_result)),
                    ))
                # Re-call LLM with updated history
                assembled_messages = self._assemble_messages(sys_prompt)
                next_result = self.call_port("llm_call", messages=assembled_messages, tools=tools)
                if isinstance(next_result, LLMError):
                    response = LLMResponse(
                        content=f"[tool dispatch error: {next_result.message}]",
                        model=response.model,
                    )
                    break
                response = next_result if isinstance(next_result, LLMResponse) else LLMResponse(
                    content=str(next_result), model=self._cfg("model", "unknown"),
                )
                total_tokens_in += response.tokens_in
                total_tokens_out += response.tokens_out
                total_latency += response.latency_ms
                if not response.tool_calls:
                    break  # Done — no more tools to call

        # 어시스턴트 메시지 기록 (최종)
        self._messages.append(Message(
            role="assistant", content=response.content,
            turn=self._turn_count, tokens=response.tokens_out,
        ))

        self._tokens_used += total_tokens_in + total_tokens_out

        turn_result = TurnResult(
            turn_number=self._turn_count,
            prompt=prompt,
            response=response.content,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            latency_ms=total_latency,
            model=response.model,
            stop_reason="completed",
        )

        # Bus에 턴 결과 발행
        self._publish("turn.result", {
            "turn": self._turn_count,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "model": response.model,
            "latency_ms": response.latency_ms,
        })
        self._emit("turn.ended", turn_number=self._turn_count, success=True,
                   response=response.content)

        # Vital signs 업데이트
        self._update_vitals(turn_result)
        self._turn_results.append(turn_result)

        return turn_result

    # --- Provides: compact ---

    def handle_compact(self) -> int:
        """
        컨텍스트 압축. 오래된 메시지를 요약 후 제거 (Cody 피드백 #4).
        CC의 sliding window + OC의 token-aware + 요약 보존.
        반환: 제거된 메시지 수.
        """
        if len(self._messages) <= 2:
            return 0

        # 시스템 메시지 분리
        system_msgs = [m for m in self._messages if m.role == "system"]
        non_system = [m for m in self._messages if m.role != "system"]

        # 최근 절반 유지, 나머지 요약
        keep_count = max(len(non_system) // 2, 4)
        to_remove = non_system[:-keep_count]
        to_keep = non_system[-keep_count:]

        # 제거할 메시지들의 요약 생성 (LLM 호출 없이 간단 요약)
        if to_remove:
            summary_parts = []
            for msg in to_remove:
                role_prefix = "[User]" if msg.role == "user" else "[Assistant]"
                summary_parts.append(f"{role_prefix} Turn {msg.turn}: {msg.content[:60]}...")

            summary_text = f"[Context Summary — {len(to_remove)} earlier messages]\n" + "\n".join(summary_parts[-5:])  # 최근 5개만 요약에 포함

            summary_msg = Message(
                role="system",
                content=summary_text,
                turn=0,
                tokens=self._estimate_tokens(summary_text),
            )

            # 재조립: system + summary + 최근 메시지
            self._messages = system_msgs + [summary_msg] + to_keep
        else:
            self._messages = system_msgs + to_keep

        removed = len(to_remove)

        # 토큰 재계산
        self._tokens_used = sum(m.tokens for m in self._messages)

        self._emit("context.compacted", removed=removed, remaining=len(self._messages))
        logger.info(f"Compacted context: removed {removed}, summary preserved, {len(self._messages)} remaining")

        return removed

    # --- Provides: get_history ---

    def handle_get_history(self) -> list[dict]:
        """대화 히스토리 반환"""
        return [
            {"role": m.role, "content": m.content, "turn": m.turn}
            for m in self._messages
        ]

    # --- Context Assembly (OC context-engine assemble pattern) ---

    def _assemble_messages(self, system_prompt: str = "") -> list[dict]:
        """
        현재 대화 히스토리를 LLM에 전달할 메시지 리스트로 조립.
        시스템 프롬프트 + 이전 대화 + 현재 턴 메시지.
        """
        assembled = []

        # 시스템 프롬프트
        if system_prompt:
            assembled.append({"role": "system", "content": system_prompt})

        # 이전 대화 히스토리 (시스템 메시지 제외 — 위에서 별도 처리)
        for msg in self._messages:
            if msg.role == "system":
                continue
            assembled.append({"role": msg.role, "content": msg.content})

        return assembled

    # --- Helpers ---

    def _update_system_prompt(self, prompt: str):
        """시스템 프롬프트 메시지 업데이트"""
        # 기존 시스템 메시지 제거
        self._messages = [m for m in self._messages if m.role != "system"]
        if prompt:
            self._messages.insert(0, Message(
                role="system", content=prompt, turn=0,
                tokens=self._estimate_tokens(prompt),
            ))

    def _estimate_tokens(self, text: str) -> int:
        """간단한 토큰 추정 (단어 수 * 1.3). 나중에 Rust로 교체 가능."""
        return int(len(text.split()) * 1.3)

    def _format_memory_context(self, raw) -> str:
        """Convert recall port output into clean text for prompt
        injection.

        MemoryBlock.handle_recall returns list[RecallResult]; older
        callers may return a string. We support both. Falsy → "".
        """
        if not raw:
            return ""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            lines = []
            for item in raw:
                # Duck-typed: RecallResult.memory.{content, importance, tags}
                memory = getattr(item, "memory", None)
                if memory is None:
                    lines.append(str(item))
                    continue
                content = getattr(memory, "content", "") or ""
                importance = getattr(memory, "importance", None)
                tags = getattr(memory, "tags", []) or []
                # Trim very long memory bodies to keep injection compact.
                snippet = content if len(content) <= 400 else content[:400] + "…"
                tag_str = f" [{', '.join(tags[:4])}]" if tags else ""
                imp_str = f" (importance={importance:.2f})" if isinstance(importance, (int, float)) else ""
                lines.append(f"- {snippet}{tag_str}{imp_str}")
            return "\n".join(lines)
        return str(raw)

    def _update_vitals(self, turn: TurnResult):
        """
        Vital signs 업데이트.
        Bus로 발행 (Config 남용 수정 — Cody 피드백 #2).
        Config는 호르몬(설정 변경), Bus는 혈액(데이터 흐름).
        """
        budget = self._cfg("token_budget", 4000)
        tokens_per_turn = self._tokens_used / max(self._turn_count, 1)
        context_util = min(self._tokens_used / max(budget, 1), 1.0)

        # 에러율 계산 (최근 10턴)
        recent = self._turn_results[-10:]
        error_rate = sum(1 for t in recent if t.error) / max(len(recent), 1) if recent else 0.0

        # Bus로 발행 (올바른 채널)
        self._publish("vitals.update", {
            "total_turns": self._turn_count,
            "tokens_used": self._tokens_used,
            "tokens_per_turn": tokens_per_turn,
            "context_utilization": context_util,
            "latency_ms": turn.latency_ms,
            "error_rate": error_rate,
        })

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def tokens_used(self) -> int:
        return self._tokens_used
