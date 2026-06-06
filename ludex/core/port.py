"""
Port — 결합조직 (블록 인터페이스)

각 블록이 다른 블록과 연결되는 표준 인터페이스. USB 포트처럼.
provides: 이 블록이 제공하는 기능
requires: 이 블록이 필요로 하는 기능 (없으면 기본 동작)

Reference:
- CC: execution_registry.py (MirroredTool dispatch), Tool.py (ToolDefinition)
- OC: plugins/api-builder.ts (17+ registerX() methods)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Port:
    """블록 간 연결 인터페이스 정의"""
    name: str                       # 포트 이름 (e.g., "llm_call", "get_tools")
    description: str = ""           # 사람이 읽을 수 있는 설명
    required: bool = True           # False면 선택적 (연결 안 돼도 됨)
    tags: tuple[str, ...] = ()      # 분류 태그 (e.g., ("provider", "async"))

    def __repr__(self) -> str:
        opt = "" if self.required else " (optional)"
        return f"Port({self.name!r}{opt})"


@dataclass
class PortConnection:
    """실제 연결된 포트 페어"""
    provider_block: str     # 제공하는 블록 이름
    consumer_block: str     # 소비하는 블록 이름
    port_name: str          # 포트 이름
    handler: Any = None     # 실제 핸들러 함수
