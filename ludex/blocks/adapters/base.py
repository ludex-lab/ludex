"""
Base Adapter — 모든 LLM 어댑터의 인터페이스

각 어댑터는 이 인터페이스를 구현하여 API별 차이를 흡수한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class AdapterResponse:
    """어댑터가 반환하는 통합 응답"""
    content: str
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class BaseAdapter(ABC):
    """모든 LLM 어댑터의 기본 클래스"""

    provider_name: str = "base"

    def __init__(self, base_url: str, api_key: str = "", timeout_ms: int = 30000):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_ms = timeout_ms

    def set_timeout_ms(self, timeout_ms: int) -> None:
        """Update the adapter's per-call timeout. Enables callers
        (e.g. LxM `LudexCreatureAdapter`, joint spec §D.7) to cap
        subprocess wait below an outer match timeout without rebuilding
        the organism. Takes effect from the next `call()` onward.
        """
        self.timeout_ms = int(timeout_ms)

    @abstractmethod
    def call(
        self,
        model: str,
        prompt: str = "",
        system: str = "",
        messages: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list | None = None,
        effort: str = "",
    ) -> AdapterResponse:
        """
        LLM API 호출. 각 어댑터가 자체 API 형식으로 변환.

        messages가 주어지면 멀티턴 대화로 처리 (prompt 무시).
        messages가 없으면 prompt를 단일 user 메시지로 처리.
        """
        ...

    @abstractmethod
    def health_check(self) -> dict:
        """프로바이더 건강 상태 확인"""
        ...

    @abstractmethod
    def list_models(self) -> list[str]:
        """사용 가능한 모델 목록"""
        ...

    def _request(self, url: str, data: bytes | None = None, headers: dict | None = None, method: str = "GET") -> dict:
        """공통 HTTP 요청 헬퍼"""
        import json
        import socket
        import time
        import urllib.error
        import urllib.request

        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)

        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        timeout_sec = self.timeout_ms / 1000
        started = time.monotonic()

        # Ollama's streaming transport is selected by the request body.  Keep
        # the generic JSON path unchanged for every other adapter/request.
        streaming = False
        if data:
            try:
                streaming = json.loads(data.decode("utf-8")).get("stream") is True
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                pass

        chunks: list[dict] = []
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                if not streaming:
                    return json.loads(resp.read().decode("utf-8"))

                timed_out = False
                while True:
                    # urlopen's timeout is a socket-idle timeout, not a total
                    # generation deadline.  Check a wall clock as chunks arrive
                    # so a continuously streaming model cannot run forever.
                    if time.monotonic() - started >= timeout_sec:
                        timed_out = True
                        break
                    try:
                        line = resp.readline()
                    except (TimeoutError, socket.timeout):
                        timed_out = True
                        break
                    if not line:
                        break
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if isinstance(chunk, dict):
                        chunks.append(chunk)
                    if chunk.get("done") is True:
                        break

                return self._aggregate_json_stream(
                    chunks,
                    timed_out=timed_out,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                )
        except (TimeoutError, socket.timeout) as exc:
            if streaming:
                return self._aggregate_json_stream(
                    chunks,
                    timed_out=True,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                    transport_error=str(exc),
                )
            raise
        except urllib.error.URLError as exc:
            if streaming and isinstance(exc.reason, (TimeoutError, socket.timeout)):
                return self._aggregate_json_stream(
                    chunks,
                    timed_out=True,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                    transport_error=str(exc),
                )
            raise

    @staticmethod
    def _aggregate_json_stream(
        chunks: list[dict],
        *,
        timed_out: bool,
        elapsed_ms: float,
        transport_error: str = "",
    ) -> dict:
        """Combine Ollama NDJSON chunks without inventing token usage.

        Exact prompt/eval counts are emitted only in Ollama's terminal chunk.
        When a deadline interrupts generation we preserve the partial text,
        thinking trace, and observed chunk count, but deliberately leave token
        counts absent so callers cannot mislabel an estimate as measurement.
        """
        last = dict(chunks[-1]) if chunks else {}
        message: dict[str, Any] = {"role": "assistant", "content": ""}
        thinking_parts: list[str] = []
        content_parts: list[str] = []
        tool_calls: list[Any] = []

        for chunk in chunks:
            msg = chunk.get("message") or {}
            if not isinstance(msg, dict):
                continue
            if msg.get("role"):
                message["role"] = msg["role"]
            content_parts.append(msg.get("content") or "")
            thinking_parts.append(msg.get("thinking") or "")
            if isinstance(msg.get("tool_calls"), list):
                tool_calls.extend(msg["tool_calls"])

        message["content"] = "".join(content_parts)
        thinking = "".join(thinking_parts)
        if thinking:
            message["thinking"] = thinking
        if tool_calls:
            message["tool_calls"] = tool_calls

        result = last
        result["message"] = message
        result["stream_chunks"] = len(chunks)
        result["elapsed_ms"] = round(float(elapsed_ms), 3)
        result["partial_usage"] = {
            "token_counts_available": (
                "prompt_eval_count" in result and "eval_count" in result
            ),
            "stream_chunks": len(chunks),
            "thinking_chars": len(thinking),
            "content_chars": len(message["content"]),
        }
        if transport_error:
            result["transport_error"] = transport_error

        terminal = bool(chunks and chunks[-1].get("done") is True)
        if timed_out:
            result.update({
                "done": False,
                "done_reason": "timeout",
                "timeout": True,
                "partial": True,
            })
        elif not terminal:
            result.update({
                "done": False,
                "done_reason": result.get("done_reason") or "stream_incomplete",
                "stream_incomplete": True,
                "partial": True,
            })
        else:
            result["partial"] = False
        return result
