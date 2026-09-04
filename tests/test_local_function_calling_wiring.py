"""Provider-neutral Ludex organ-tool wiring regression tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

from ludex.core.organism_config import OrganismConfig
from ludex.mcp import bind_ludex_organism
from ludex.mcp.function_calling import dispatch_tool_call_sync
from ludex.mcp.ludex_mcp_server import ALL_TOOLS, select_ludex_tools


class _Engine:
    pass


class _Organism:
    def __init__(self, block_names=("engine", "memory")):
        self.name = "wire-test"
        self._blocks = {
            name: (_Engine() if name == "engine" else object())
            for name in block_names
        }
        self.config = {}

    def get_block(self, name):
        return self._blocks.get(name)

    def measure_vitals(self):
        return SimpleNamespace(total_turns=3, tokens_per_turn=12.5, error_rate=0.0)


def _schema_names(schemas):
    return [item["function"]["name"] for item in schemas]


def test_tool_registry_is_available_without_constructing_claude_mcp():
    # Merely importing the local registry must not import claude_agent_sdk.
    # This environment intentionally exercises the optional-dependency path.
    assert len(ALL_TOOLS) == 12
    assert all(tool.name.startswith("ludex_") for tool in ALL_TOOLS)


def test_selection_matches_installed_organs_and_blocks_self_engine_recursion():
    org = _Organism(("engine", "memory"))
    names = [
        tool.name
        for tool in select_ludex_tools(org, include_engine=False)
    ]

    assert "ludex_memory_recall" in names
    assert "ludex_memory_store" in names
    assert "ludex_vitals" in names
    assert "ludex_weight" in names
    assert "ludex_time_recall" not in names  # no chronos organ
    assert "ludex_engine_submit" not in names  # never call own engine recursively


def test_cached_ollama_tool_support_wires_local_handlers_without_sdk():
    cfg = OrganismConfig(
        name="wire-test",
        brain={"provider": "ollama", "model": "qwen3.8:27b"},
        fc_probed_brain="ollama:qwen3.8:27b",
        fc_supports_tools=True,
    )
    org = _Organism(("engine", "memory"))

    cfg._wire_function_calling(org, "ollama")

    names = _schema_names(org._fc_tools)
    assert names == [
        "ludex_memory_recall",
        "ludex_memory_store",
        "ludex_vitals",
        "ludex_weight",
    ]
    assert org._blocks["engine"]._default_tools is org._fc_tools
    assert org._blocks["engine"]._default_tool_dispatcher is dispatch_tool_call_sync


def test_bound_local_tool_dispatches_to_organism():
    org = _Organism(("engine",))
    bind_ludex_organism(org)

    result = json.loads(dispatch_tool_call_sync("ludex_vitals", {}))

    assert result["total_turns"] == 3
    assert result["installed_organs"] == ["engine"]
