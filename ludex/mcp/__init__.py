"""Expose Ludex organs to MCP and OpenAI-compatible function calling.

The organ registry is provider-neutral. Only ``create_ludex_mcp`` imports the
optional Claude Agent SDK, and it does so lazily when that path is requested.
"""

import importlib as _importlib


def __getattr__(name):
    """Lazy-load symbols from submodules on first access."""
    _MAP = {
        "create_ludex_mcp": "ludex.mcp.ludex_mcp_server",
        "bind_ludex_organism": "ludex.mcp.ludex_mcp_server",
        "select_ludex_tools": "ludex.mcp.ludex_mcp_server",
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
    "bind_ludex_organism",
    "select_ludex_tools",
    "ALL_TOOLS",
    "mcp_to_openai_tools",
    "dispatch_tool_call",
    "dispatch_tool_call_sync",
]
