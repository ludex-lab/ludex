"""
Registry Block — 근육계 보조 (도구 관리)

게임/실험에 사용할 도구를 정의, 등록, 필터링하는 장기.
LxM에서 각 게임별 액션(bet, fold, move 등)을 도구로 관리.

Reference:
- CC: tools.py (JSON snapshot registry, ToolPermissionContext filtering)
- CC: execution_registry.py (MirroredTool dispatch)
- OC: plugins/api-builder.ts (registerTool pattern)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolDefinition:
    """도구 정의 (CC frozen ToolDefinition 패턴)"""
    name: str
    description: str
    parameters: dict = field(default_factory=dict)  # JSON Schema
    tags: tuple[str, ...] = ()                      # 분류 태그 ("game", "analysis", "util")
    required_permission: str = ""                    # 필요 권한


@dataclass
class ToolResult:
    """도구 실행 결과"""
    tool_name: str
    success: bool
    output: Any = None
    error: Optional[str] = None


class RegistryBlock(Block):
    """
    도구 등록/관리 블록.

    provides: get_tools, execute_tool, register_tool
    requires: (없음)
    """

    name = "registry"
    provides = [
        Port("get_tools", description="Get available tools for LLM"),
        Port("execute_tool", description="Execute a tool by name"),
        Port("register_tool", description="Register a new tool"),
    ]
    requires = []

    def __init__(self, tools: list[ToolDefinition] | None = None, tools_file: str = ""):
        super().__init__()
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable] = {}
        self._denied_tools: set[str] = set()
        self._denied_prefixes: tuple[str, ...] = ()

        # 초기 도구 등록
        if tools:
            for tool in tools:
                self._tools[tool.name] = tool

        # JSON 파일에서 로드 (CC snapshot 패턴)
        if tools_file:
            self._load_from_file(tools_file)

    def on_attach(self):
        self._listen("config.changed", self._on_config_changed)
        # Config에서 권한 설정 로드
        denied = self._cfg("denied_tools", [])
        if denied:
            self._denied_tools = set(denied)
        prefixes = self._cfg("denied_prefixes", [])
        if prefixes:
            self._denied_prefixes = tuple(prefixes)

    def _on_config_changed(self, key: str = "", new: Any = None, **kwargs):
        if key == "denied_tools" and new:
            self._denied_tools = set(new)
        elif key == "denied_prefixes" and new:
            self._denied_prefixes = tuple(new)

    # --- Provides: get_tools ---

    def handle_get_tools(self, tags: list[str] | None = None, format: str = "definitions") -> list:
        """
        사용 가능한 도구 목록 반환.
        권한 필터링 적용 (CC ToolPermissionContext 패턴).
        """
        tools = []
        for name, tool in self._tools.items():
            # 권한 필터링
            if self._is_denied(name):
                continue
            # 태그 필터링
            if tags and not any(t in tool.tags for t in tags):
                continue
            tools.append(tool)

        if format == "definitions":
            return tools
        elif format == "llm":
            # LLM에 전달할 도구 스키마 형식
            return [self._to_llm_schema(t) for t in tools]
        elif format == "names":
            return [t.name for t in tools]
        return tools

    # --- Provides: execute_tool ---

    def handle_execute_tool(self, tool_name: str, arguments: dict | None = None) -> ToolResult:
        """도구 실행"""
        if self._is_denied(tool_name):
            return ToolResult(
                tool_name=tool_name, success=False,
                error=f"Tool '{tool_name}' is denied by permission policy",
            )

        if tool_name not in self._tools:
            return ToolResult(
                tool_name=tool_name, success=False,
                error=f"Tool '{tool_name}' not found",
            )

        handler = self._handlers.get(tool_name)
        if not handler:
            return ToolResult(
                tool_name=tool_name, success=False,
                error=f"Tool '{tool_name}' has no handler registered",
            )

        self._emit("tool.calling", tool_name=tool_name, arguments=arguments)

        try:
            output = handler(**(arguments or {}))
            result = ToolResult(tool_name=tool_name, success=True, output=output)
            self._publish("tool.executed", {
                "tool_name": tool_name, "success": True, "arguments": arguments,
            })
            self._emit("tool.executed", tool_name=tool_name, success=True)
            return result
        except Exception as e:
            result = ToolResult(tool_name=tool_name, success=False, error=str(e))
            self._publish("tool.executed", {
                "tool_name": tool_name, "success": False, "error": str(e),
            })
            self._emit("tool.executed", tool_name=tool_name, success=False, error=str(e))
            return result

    # --- Provides: register_tool ---

    def handle_register_tool(self, tool: ToolDefinition, handler: Callable | None = None) -> bool:
        """도구 등록 (OC registerTool 패턴)"""
        self._tools[tool.name] = tool
        if handler:
            self._handlers[tool.name] = handler
        self._emit("tool.registered", tool_name=tool.name)
        logger.debug(f"Registered tool: {tool.name}")
        return True

    # --- Permission Filtering (CC ToolPermissionContext) ---

    def _is_denied(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        if lowered in self._denied_tools:
            return True
        return any(lowered.startswith(p) for p in self._denied_prefixes)

    def deny_tool(self, name: str) -> None:
        """도구 차단"""
        self._denied_tools.add(name.lower())

    def allow_tool(self, name: str) -> None:
        """도구 허용"""
        self._denied_tools.discard(name.lower())

    # --- File Loading (CC snapshot pattern) ---

    def _load_from_file(self, filepath: str):
        """JSON 파일에서 도구 정의 로드"""
        path = Path(filepath)
        if not path.exists():
            logger.warning(f"Tools file not found: {filepath}")
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for item in data if isinstance(data, list) else data.get("tools", []):
            tool = ToolDefinition(
                name=item["name"],
                description=item.get("description", ""),
                parameters=item.get("parameters", {}),
                tags=tuple(item.get("tags", [])),
            )
            self._tools[tool.name] = tool

        logger.info(f"Loaded {len(self._tools)} tools from {filepath}")

    # --- LLM Schema ---

    @staticmethod
    def _to_llm_schema(tool: ToolDefinition) -> dict:
        """도구를 LLM 호출용 스키마로 변환"""
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters or {"type": "object", "properties": {}},
            },
        }
