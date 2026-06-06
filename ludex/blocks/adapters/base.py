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
        import urllib.request

        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)

        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        timeout_sec = self.timeout_ms / 1000

        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            return json.loads(resp.read().decode("utf-8"))
