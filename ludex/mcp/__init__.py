"""Ludex MCP integration -- expose organs as tools for Claude Code / Agent SDK / OpenAI-compatible brains.

Lazy imports: ludex_mcp_server depends on claude_agent_sdk which may not
be installed. We defer the import so that other parts of the ludex.mcp
package (prompt_only_adapter, function_calling) remain usable without it.
"""

import importlib as _importlib


def __getattr__(name):
    """Lazy-load symbols from submodules on first access."""
    _MAP = {
        "create_ludex_mcp": "ludex.mcp.ludex_mcp_server",
        "ALL_TOOLS": "ludex.mcp.ludex_mcp_server",
        "mcp_to_openai_tools": "ludex.mcp.function_calling",
        "dispatch_tool_call": "ludex.mcp.function_calling",
        "dispatch_tool_call_sync": "ludex.mcp.function_calling",
    }
    if name in _MAP:
        mod = _importlib.import_module(_MAP[name])
        return getattr(mod, name)
    raise AttributeError(f"module 'ludex.mcp' has no attribute {name!r}")


__all__ = [
    "create_ludex_mcp",
    "ALL_TOOLS",
    "mcp_to_openai_tools",
    "dispatch_tool_call",
    "dispatch_tool_call_sync",
]
