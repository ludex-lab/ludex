"""
Function Calling Adapter — bridge Ludex MCP tools to OpenAI/Ollama format.

Lets non-MCP brains (Ollama SLMs, OpenAI, Gemini, etc.) call Ludex organ tools
via standard OpenAI-compatible function calling.

Usage:
    from ludex.mcp import create_ludex_mcp, mcp_to_openai_tools, dispatch_tool_call

    organism = ...  # Built from OrganismConfig
    create_ludex_mcp(organism)  # Sets up _organism context

    # Get OpenAI-format tool schemas
    tools_schema = mcp_to_openai_tools()

    # Pass to Ollama
    response = ollama.chat(model="llama3.1:8b", messages=[...], tools=tools_schema)

    # If LLM calls a tool, dispatch it
    for tc in response.get("message", {}).get("tool_calls", []):
        result = await dispatch_tool_call(tc["function"]["name"], tc["function"]["arguments"])
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ludex.mcp.ludex_mcp_server import ALL_TOOLS

logger = logging.getLogger(__name__)


def mcp_to_openai_tools(tool_names: list[str] | None = None) -> list[dict]:
    """
    Convert Ludex MCP tool definitions to OpenAI/Ollama function calling format.

    Args:
        tool_names: Optional list of tool names to include (default: all)

    Returns:
        List of OpenAI-format tool definitions:
        [
            {
                "type": "function",
                "function": {
                    "name": "ludex_immune_assess",
                    "description": "...",
                    "parameters": { ... JSON schema ... }
                }
            },
            ...
        ]
    """
    selected = ALL_TOOLS
    if tool_names:
        selected = [t for t in ALL_TOOLS if t.name in tool_names]

    result = []
    for t in selected:
        # MCP tools have name, description, input_schema, handler
        schema = t.input_schema
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        result.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": schema,
            },
        })
    return result


def dispatch_tool_call_sync(name: str, arguments: dict | str) -> str:
    """
    Synchronously dispatch a tool call to the matching Ludex MCP tool.

    Args:
        name: Tool name (e.g., "ludex_immune_assess")
        arguments: Tool arguments (dict or JSON string)

    Returns:
        Tool result as a string (JSON or text)
    """
    import time as _time
    from ludex.mcp.ludex_mcp_server import _organism as _ludex_org
    from ludex.core.tracing import get_or_create_logger

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            err = f"Error: invalid JSON arguments: {arguments}"
            return err

    handler_map = {t.name: t for t in ALL_TOOLS}
    tool = handler_map.get(name)
    if not tool:
        return f"Error: unknown tool '{name}'. Available: {list(handler_map.keys())}"

    start = _time.time()
    try:
        # Tool handlers are async. Run them WITHOUT assuming a current event loop:
        # asyncio.get_event_loop() RAISES in py3.12+ (and this env's 3.14) when none is
        # running, which broke the llama/ollama tools path in a threaded harness (Ray,
        # 2026-07-03 — qwen/exaone don't support tools so never hit this). Detect a
        # *running* loop explicitly (get_running_loop); otherwise asyncio.run() creates
        # and owns a fresh one — clean, no exception-driven control flow.
        try:
            asyncio.get_running_loop()
            in_running_loop = True
        except RuntimeError:
            in_running_loop = False
        if in_running_loop:
            # Inside a running loop — run the handler in a separate thread with its own loop.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as ex:
                result = ex.submit(asyncio.run, tool.handler(arguments)).result(timeout=30)
        else:
            result = asyncio.run(tool.handler(arguments))

        # Extract text content from MCP response format
        contents = result.get("content", [])
        texts = [c.get("text", "") for c in contents if c.get("type") == "text"]
        result_text = "\n".join(texts) if texts else json.dumps(result)
    except Exception as e:
        logger.exception(f"Tool dispatch failed for {name}")
        result_text = f"Error executing {name}: {e}"

    duration_ms = (_time.time() - start) * 1000

    # Trace logging — best effort
    try:
        if _ludex_org is not None and hasattr(_ludex_org, "config"):
            habitat_dir = _ludex_org.config.get("habitat_dir", "") if hasattr(_ludex_org.config, "get") else ""
            if habitat_dir:
                tlog = get_or_create_logger(habitat_dir, _ludex_org.name)
                tlog.record_organ_call(
                    tool=name,
                    args=arguments if isinstance(arguments, dict) else {},
                    result_summary=result_text[:300],
                    duration_ms=duration_ms,
                    source="brain",
                )
    except Exception as e:
        logger.debug(f"Trace logging failed for {name}: {e}")

    return result_text


async def dispatch_tool_call(name: str, arguments: dict | str) -> str:
    """Async version of dispatch_tool_call."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return f"Error: invalid JSON arguments: {arguments}"

    handler_map = {t.name: t for t in ALL_TOOLS}
    tool = handler_map.get(name)
    if not tool:
        return f"Error: unknown tool '{name}'. Available: {list(handler_map.keys())}"

    try:
        result = await tool.handler(arguments)
        contents = result.get("content", [])
        texts = [c.get("text", "") for c in contents if c.get("type") == "text"]
        return "\n".join(texts) if texts else json.dumps(result)
    except Exception as e:
        logger.exception(f"Tool dispatch failed for {name}")
        return f"Error executing {name}: {e}"
