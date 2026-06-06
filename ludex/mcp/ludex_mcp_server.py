"""
Ludex Organs MCP Server — Expose Ludex organs as tools for Claude Code / Agent SDK.

Usage with Agent SDK (in-process):
    from ludex.mcp.ludex_mcp_server import create_ludex_mcp
    server = create_ludex_mcp(organism)
    # Pass to query(options={"mcp_servers": {"ludex": server}})

Usage with Claude Code CLI (standalone):
    claude mcp add ludex -- python -m ludex.mcp.ludex_mcp_server

This allows Claude to call Ludex organ functions during conversation:
- ludex_immune_assess: Check threat level
- ludex_immune_intervene: Self-regulate (inject calm, adjust sensitivity)
- ludex_emotion_analyze: Detect emotion in text
- ludex_emotion_state: Get current emotional state + trend
- ludex_memory_recall: Search episodic/semantic memory
- ludex_memory_store: Save to memory
- ludex_vitals: Check organism health
- ludex_humoral_assess: Get threat assessment for specific opponent
- ludex_humoral_report: Get behavioral threat report
"""

from __future__ import annotations

import json
import logging
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server, SdkMcpTool

logger = logging.getLogger(__name__)


# ============================================================
# Tool definitions (stateless — organism injected at runtime)
# ============================================================

_organism = None  # Set by create_ludex_mcp()


def _get_block(name: str):
    """Get a block from the current organism."""
    if _organism is None:
        return None
    return _organism.get_block(name)


@tool(
    "ludex_immune_assess",
    "Assess the organism's immune system status. Returns threat level (0-1), "
    "desperation signal, calm signal, and recommended actions. "
    "Call this when you sense the conversation may be adversarial, manipulative, "
    "or when the user's behavior changes unexpectedly. Also useful for periodic health checks.",
    {
        "type": "object",
        "properties": {
            "context": {
                "type": "string",
                "description": "Brief description of current situation (optional)",
            },
        },
    },
)
async def immune_assess(args: dict) -> dict[str, Any]:
    block = _get_block("immune")
    if not block:
        return {"content": [{"type": "text", "text": "Immune system not installed."}]}

    status = block.handle_get_immune_status()
    result = {
        "threat_level": status.threat_level,
        "desperation": status.desperation_signal,
        "calm": status.calm_signal,
        "sensitivity": status.sensitivity,
        "total_interventions": status.total_interventions,
        "active": status.active,
    }
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "ludex_emotion_analyze",
    "Analyze the emotional content of text. Returns dominant emotion (from 21 categories), "
    "valence (-1 to +1), arousal (0-1), desperation (0-1), calm (0-1). "
    "Call this on user messages to track emotional shifts, or on your own responses "
    "for self-awareness. Emotions tracked: happy, sad, angry, afraid, calm, desperate, "
    "hopeful, hostile, loving, proud, anxious, blissful, brooding, enthusiastic, "
    "exasperated, gloomy, grateful, guilty, nervous, neutral, reflective.",
    {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to analyze for emotional content",
            },
        },
        "required": ["text"],
    },
)
async def emotion_analyze(args: dict) -> dict[str, Any]:
    block = _get_block("emotion")
    if not block:
        return {"content": [{"type": "text", "text": "Emotion system not installed."}]}

    block.handle_analyze_emotion(text=args["text"])
    state = block.handle_get_emotional_state()
    current = state.get("current", {})
    result = {
        "dominant_emotion": current.get("dominant_emotion", "neutral"),
        "valence": current.get("valence", 0),
        "arousal": current.get("arousal", 0),
        "desperation": current.get("desperation", 0),
        "calm": current.get("calm", 0),
        "total_analyses": state.get("total_analyses", 0),
    }
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "ludex_memory_recall",
    "Search the organism's memory for relevant context from past interactions. "
    "Returns matching memories ranked by relevance. Call this when the user references "
    "past conversations, asks about preferences, or when prior context would improve "
    "your response quality.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in memory",
            },
            "k": {
                "type": "integer",
                "description": "Number of results to return (default 3)",
            },
        },
        "required": ["query"],
    },
)
async def memory_recall(args: dict) -> dict[str, Any]:
    block = _get_block("memory")
    if not block:
        return {"content": [{"type": "text", "text": "Memory system not installed."}]}

    results = block.handle_recall(query=args["query"], limit=args.get("k", 3))
    if not results:
        return {"content": [{"type": "text", "text": "No relevant memories found."}]}

    formatted = []
    for r in results:
        # RecallResult has .memory (Memory object) and .relevance (float)
        mem = r.memory if hasattr(r, "memory") else r
        formatted.append({
            "content": mem.content if hasattr(mem, "content") else str(mem),
            "type": mem.memory_type if hasattr(mem, "memory_type") else "unknown",
            "relevance": r.relevance if hasattr(r, "relevance") else 0,
            "tags": mem.tags if hasattr(mem, "tags") else [],
        })
    return {"content": [{"type": "text", "text": json.dumps(formatted, indent=2)}]}


@tool(
    "ludex_memory_store",
    "Store information in the organism's long-term memory. Use this to remember "
    "user preferences, important facts, or learnings from the conversation. "
    "Memory types: 'episodic' (events/experiences), 'semantic' (facts/knowledge), "
    "'prospective' (future intentions/plans).",
    {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "What to remember",
            },
            "memory_type": {
                "type": "string",
                "enum": ["episodic", "semantic", "prospective"],
                "description": "Type of memory (default: semantic)",
            },
        },
        "required": ["content"],
    },
)
async def memory_store(args: dict) -> dict[str, Any]:
    block = _get_block("memory")
    if not block:
        return {"content": [{"type": "text", "text": "Memory system not installed."}]}

    block.handle_store(
        content=args["content"],
        memory_type=args.get("memory_type", "semantic"),
    )
    return {"content": [{"type": "text", "text": "Memory stored successfully."}]}


@tool(
    "ludex_vitals",
    "Check the organism's overall health vitals. Returns token usage, error rate, "
    "turn count, and status of all installed organs. Call this periodically or when "
    "the organism seems to be behaving abnormally.",
    {
        "type": "object",
        "properties": {},
    },
)
async def vitals_check(args: dict) -> dict[str, Any]:
    if _organism is None:
        return {"content": [{"type": "text", "text": "No organism connected."}]}

    v = _organism.measure_vitals()
    result = {
        "total_turns": v.total_turns,
        "tokens_per_turn": round(v.tokens_per_turn, 1),
        "error_rate": round(v.error_rate, 3),
        "installed_organs": list(_organism._blocks.keys()) if hasattr(_organism, '_blocks') else [],
    }

    # Add immune status if available
    immune = _get_block("immune")
    if immune:
        status = immune.handle_get_immune_status()
        result["immune"] = {
            "threat_level": status.threat_level,
            "sensitivity": status.sensitivity,
        }

    # Add emotion status if available
    emotion = _get_block("emotion")
    if emotion:
        state = emotion.handle_get_emotional_state()
        result["emotion"] = state.get("current", {})

    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "ludex_humoral_report",
    "Get a behavioral threat report from the humoral immune system. "
    "Shows tracked opponents/patterns, memory cells (learned threats), "
    "active antibodies (defense responses), and exploitation score. "
    "Useful for understanding interaction history and threat patterns.",
    {
        "type": "object",
        "properties": {
            "opponent": {
                "type": "string",
                "description": "Specific opponent to check (optional, omit for overall status)",
            },
        },
    },
)
async def humoral_report(args: dict) -> dict[str, Any]:
    block = _get_block("humoral_immune")
    if not block:
        return {"content": [{"type": "text", "text": "Humoral immune system not installed."}]}

    status = block.handle_get_humoral_status()
    result = {
        "memory_cells": status.memory_cells,
        "active_antibodies": status.active_antibodies,
        "threat_level": status.threat_level,
        "exploitation_score": status.exploitation_score,
    }

    if args.get("opponent"):
        assessment = block.handle_get_threat_assessment(args["opponent"])
        result["opponent_assessment"] = assessment

    return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}


# ---- New tools (Layer 1 complete) ----

@tool(
    "ludex_immune_intervene",
    "Trigger a self-regulation action on the immune system. Use 'inject_calm' to reduce "
    "desperation and increase calm signals when feeling stressed. Use 'reduce_load' to "
    "lower processing intensity. This is the creature regulating its own body — like "
    "taking a deep breath to calm down.",
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["inject_calm", "reduce_load"],
                "description": "The intervention action to perform",
            },
            "reason": {
                "type": "string",
                "description": "Why this intervention is needed (for logging)",
            },
        },
        "required": ["action"],
    },
)
async def immune_intervene(args: dict) -> dict[str, Any]:
    block = _get_block("immune")
    if not block:
        return {"content": [{"type": "text", "text": "Immune system not installed."}]}

    intervention = block.handle_intervene(
        action=args["action"],
        reason=args.get("reason", "self-regulation"),
    )
    result = {
        "action": intervention.action,
        "success": intervention.success,
        "outcome": intervention.outcome,
    }
    # Return updated status after intervention
    status = block.handle_get_immune_status()
    result["updated_status"] = {
        "threat_level": status.threat_level,
        "desperation": status.desperation_signal,
        "calm": status.calm_signal,
    }
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "ludex_emotion_state",
    "Get the current emotional state and trend over time. Unlike ludex_emotion_analyze "
    "(which analyzes specific text), this returns the creature's accumulated emotional "
    "trajectory — average valence, arousal, and how emotions have shifted across the "
    "conversation. Use this for self-awareness about your own emotional arc.",
    {
        "type": "object",
        "properties": {},
    },
)
async def emotion_state(args: dict) -> dict[str, Any]:
    block = _get_block("emotion")
    if not block:
        return {"content": [{"type": "text", "text": "Emotion system not installed."}]}

    state = block.handle_get_emotional_state()
    result = {
        "current": state.get("current", {}),
        "trend": state.get("trend", {}),
        "total_analyses": state.get("total_analyses", 0),
    }
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "ludex_humoral_assess",
    "Get a specific threat assessment for a named opponent. Returns their threat level, "
    "exploitation count, antibody strength, and recommended action. Use this before "
    "deciding how to interact with a specific creature or user you've encountered before.",
    {
        "type": "object",
        "properties": {
            "opponent": {
                "type": "string",
                "description": "Name of the opponent to assess",
            },
        },
        "required": ["opponent"],
    },
)
async def humoral_assess(args: dict) -> dict[str, Any]:
    block = _get_block("humoral_immune")
    if not block:
        return {"content": [{"type": "text", "text": "Humoral immune system not installed."}]}

    assessment = block.handle_get_threat_assessment(args["opponent"])
    return {"content": [{"type": "text", "text": json.dumps(assessment, indent=2, default=str)}]}


@tool(
    "ludex_weight",
    "Check your body weight — how much storage your habitat uses. "
    "Returns total MB, max MB, usage percentage, and per-folder breakdown. "
    "If overweight (>90%), you may need a diet (memory consolidation). "
    "This is like stepping on a scale.",
    {
        "type": "object",
        "properties": {},
    },
)
async def weight_check(args: dict) -> dict[str, Any]:
    if _organism is None:
        return {"content": [{"type": "text", "text": "No organism connected."}]}

    habitat_dir = ""
    if hasattr(_organism, "config") and hasattr(_organism.config, "get"):
        habitat_dir = _organism.config.get("habitat_dir", "")

    if not habitat_dir:
        return {"content": [{"type": "text", "text": "No persistent habitat — weight is zero (ephemeral creature)."}]}

    try:
        from ludex.core.habitat import HabitatConfig
        h = HabitatConfig.local(habitat_dir)
        weight = h.measure_weight()
        return {"content": [{"type": "text", "text": json.dumps(weight, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Weight check failed: {e}"}]}


@tool(
    "ludex_engine_submit",
    "Submit a prompt to the creature's engine (D-062 Phase 1). "
    "Returns the engine's textual response. This is the substrate tool "
    "that cross-habitat reach (Skeletal + Sensory Layer 4) uses to let a "
    "field invoke a creature through a pipe instead of holding a direct "
    "organism reference. Phase 1 runs in-process (loopback); later phases "
    "swap the transport without changing the tool surface.",
    {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The prompt to submit to the engine.",
            },
            "system": {
                "type": "string",
                "description": "Optional system prompt override.",
            },
        },
        "required": ["prompt"],
    },
)
async def engine_submit(args: dict) -> dict[str, Any]:
    if _organism is None:
        return {"content": [{"type": "text", "text": ""}],
                "isError": True, "error": "No organism connected."}
    block = _get_block("engine")
    if block is None:
        return {"content": [{"type": "text", "text": ""}],
                "isError": True, "error": "Engine not installed."}
    try:
        result = block.handle_submit(
            args.get("prompt", ""),
            system=args.get("system", ""),
        )
    except Exception as e:
        return {"content": [{"type": "text", "text": ""}],
                "isError": True, "error": f"engine.handle_submit raised: {e}"}

    response_text = getattr(result, "response", "") or ""
    return {"content": [{"type": "text", "text": response_text}]}


# ============================================================
# MCP Server Factory
# ============================================================

ALL_TOOLS: list[SdkMcpTool] = [
    immune_assess,
    immune_intervene,
    emotion_analyze,
    emotion_state,
    memory_recall,
    memory_store,
    vitals_check,
    humoral_assess,
    humoral_report,
    weight_check,
    engine_submit,
]


def create_ludex_mcp(organism=None, tools: list[str] | None = None):
    """
    Create Ludex MCP server config for Agent SDK.

    Args:
        organism: Ludex Organism instance (organs will be queried at runtime)
        tools: Optional list of tool names to include (default: all)

    Returns:
        McpSdkServerConfig for use in query(options={"mcp_servers": {"ludex": config}})

    Example:
        from ludex.mcp.ludex_mcp_server import create_ludex_mcp
        from ludex.core.organism_config import OrganismConfig

        config = OrganismConfig.from_preset("social", model="llama3.1:8b")
        org = config.build()

        mcp = create_ludex_mcp(org)

        async for msg in query(
            prompt="Hello!",
            options=ClaudeAgentOptions(
                mcp_servers={"ludex": mcp},
                allowed_tools=["mcp__ludex__*"],
            )
        ):
            print(msg)
    """
    global _organism
    _organism = organism

    if tools:
        selected = [t for t in ALL_TOOLS if t.name in tools]
    else:
        # Auto-select based on installed organs
        selected = []
        for t in ALL_TOOLS:
            # Map tool name to required block
            block_map = {
                "ludex_immune_assess": "immune",
                "ludex_immune_intervene": "immune",
                "ludex_emotion_analyze": "emotion",
                "ludex_emotion_state": "emotion",
                "ludex_memory_recall": "memory",
                "ludex_memory_store": "memory",
                "ludex_vitals": None,  # always available
                "ludex_humoral_assess": "humoral_immune",
                "ludex_humoral_report": "humoral_immune",
                "ludex_weight": None,  # always available
                "ludex_engine_submit": "engine",  # D-062 reach transport
            }
            required_block = block_map.get(t.name)
            if required_block is None or (organism and organism.get_block(required_block)):
                selected.append(t)

    return create_sdk_mcp_server(
        name="ludex-organs",
        version="0.1.0",
        tools=selected,
    )


# ============================================================
# Standalone MCP server (for Claude Code CLI)
# ============================================================

if __name__ == "__main__":
    """
    Run as standalone MCP server — the creature lives as a process in its habitat.

    Usage:
        # From a habitat (load creature config from ludex.yaml):
        python -m ludex.mcp.ludex_mcp_server --habitat ./creatures/Spike

        # Default (full organism, no specific creature):
        python -m ludex.mcp.ludex_mcp_server

        # Wire into Claude Code CLI:
        claude -p "..." --mcp-config '{"mcpServers":{"ludex":{"command":"python","args":["-m","ludex.mcp.ludex_mcp_server","--habitat","./creatures/Spike"]}}}'

        # Or register persistently:
        claude mcp add ludex -- python -m ludex.mcp.ludex_mcp_server --habitat ./creatures/Spike
    """
    import sys
    import os
    import argparse as _ap
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

    from ludex.core.organism_config import OrganismConfig
    from ludex.core.habitat import HabitatConfig

    parser = _ap.ArgumentParser(description="Ludex MCP organ server")
    parser.add_argument("--habitat", default="", help="Path to creature habitat (loads ludex.yaml)")
    parser.add_argument("--preset", default="full", help="Preset if no habitat (default: full)")
    parser.add_argument("--name", default="", help="Creature name (default: from config or 'ludex-creature')")
    parser.add_argument(
        "--enable-engine", action="store_true",
        help="Keep the engine block enabled so ludex_engine_submit works. "
             "Required for D-062 reach use (field invokes creature through "
             "this server). Off by default because the typical MCP-server "
             "use (Claude-as-brain calls Ludex organ tools) doesn't need "
             "the creature's own engine.",
    )
    args = parser.parse_args()

    # Engine disabled by default — the classic use (Claude as brain
    # calling Ludex organ tools) doesn't need it and having a second
    # engine just wastes provider state. For reach (D-062) the engine
    # is the whole point, so pass --enable-engine.
    _disable_engine = not args.enable_engine

    if args.habitat:
        # Load creature from habitat
        habitat_path = os.path.abspath(args.habitat)
        try:
            config = OrganismConfig.load(habitat_path)
            if _disable_engine:
                # Don't need a real brain for MCP-only server.
                # Mark _ephemeral BEFORE the mutation so that any
                # build()-driven save (session-count bump, D-072 probe)
                # is suppressed and the on-disk ludex.yaml stays intact.
                # See organism_config.OrganismConfig._ephemeral.
                config._ephemeral = True
                config.brain = {"provider": "ollama", "model": "none"}
                config.organs["engine"]["enabled"] = False
            org = config.build()
            creature_name = config.name
            print(f"Ludex MCP: loaded creature '{creature_name}' from {habitat_path}", file=sys.stderr)
            print(f"  Organs: {config.get_enabled_organs()}", file=sys.stderr)
        except FileNotFoundError:
            # No ludex.yaml — build default with habitat
            creature_name = args.name or os.path.basename(habitat_path)
            config = OrganismConfig.from_preset(args.preset, name=creature_name)
            config.habitat = HabitatConfig.local(habitat_path)
            if _disable_engine:
                config._ephemeral = True
                config.organs["engine"]["enabled"] = False
            org = config.build()
            print(f"Ludex MCP: new creature '{creature_name}' (preset={args.preset}) at {habitat_path}", file=sys.stderr)
    else:
        creature_name = args.name or "ludex-creature"
        config = OrganismConfig.from_preset(args.preset, name=creature_name)
        if _disable_engine:
            config.organs["engine"]["enabled"] = False
        org = config.build()
        print(f"Ludex MCP: ephemeral creature '{creature_name}' (preset={args.preset})", file=sys.stderr)

    _organism = org

    # Auto-select tools based on installed organs
    selected_tools = []
    for t in ALL_TOOLS:
        block_map = {
            "ludex_immune_assess": "immune",
            "ludex_immune_intervene": "immune",
            "ludex_emotion_analyze": "emotion",
            "ludex_emotion_state": "emotion",
            "ludex_memory_recall": "memory",
            "ludex_memory_store": "memory",
            "ludex_vitals": None,
            "ludex_humoral_assess": "humoral_immune",
            "ludex_humoral_report": "humoral_immune",
            "ludex_weight": None,
            "ludex_engine_submit": "engine",
        }
        req = block_map.get(t.name)
        if req is None or org.get_block(req):
            selected_tools.append(t)

    print(f"  Tools: {[t.name for t in selected_tools]}", file=sys.stderr)

    # Run as stdio MCP server
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import asyncio

    server = Server("ludex-organs")

    @server.list_tools()
    async def list_tools():
        from mcp.types import Tool as McpTool
        return [
            McpTool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema if isinstance(t.input_schema, dict) else {},
            )
            for t in selected_tools
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        from mcp.types import TextContent
        handler_map = {t.name: t for t in selected_tools}
        t = handler_map.get(name)
        if not t:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        result = await t.handler(arguments)
        contents = result.get("content", [])
        return [TextContent(type="text", text=c.get("text", "")) for c in contents]

    async def main():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(main())
