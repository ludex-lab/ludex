"""Temporal-memory read-layer (memory-systems step 1-2, 2026-07-03).

- chronos.handle_recall_window / handle_time_since — recall over store spans
- memory.handle_remember place-tagging — memories carry field:<name> tags
- ludex_time_recall MCP tool — the brain-facing consumer
"""
import json
import time

import pytest

from ludex.core.organism_config import OrganismConfig


@pytest.fixture()
def org(tmp_path):
    """A full-preset ephemeral organism with a real habitat dir + synthetic spans."""
    cfg = OrganismConfig.from_preset("full", name="tempo")
    # point the habitat at tmp_path — build() mirrors habitat.home_dir into
    # organism.config["habitat_dir"], which is what chronos._store reads
    cfg.habitat.home_dir = str(tmp_path)
    cfg._ephemeral = True
    organism = cfg.build()
    # synthetic span log: 3 events at now-1h / now-3h / now-30h
    store_dir = tmp_path / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    spans = [
        {"kind": "tick", "creature": "tempo", "timestamp": now - 30 * 3600,
         "attributes": {"field_name": "wilderness", "action": "explore"}, "reward": None},
        {"kind": "reflection", "creature": "tempo", "timestamp": now - 3 * 3600,
         "attributes": {"field_name": "council"}, "reward": None},
        {"kind": "tick", "creature": "tempo", "timestamp": now - 1 * 3600,
         "attributes": {"field_name": "wilderness", "action": "rest"}, "reward": None},
    ]
    with open(store_dir / "spans.jsonl", "w", encoding="utf-8") as f:
        for s in spans:
            f.write(json.dumps(s) + "\n")
    return organism


def test_recall_window_orders_and_filters(org):
    chronos = org.get_block("chronos")
    # 24h window: the 30h-old tick is excluded; order oldest→newest
    events = chronos.handle_recall_window(hours=24.0)
    assert [e["kind"] for e in events] == ["reflection", "tick"]
    assert events[0]["ago_s"] > events[1]["ago_s"]          # older first
    # kind filter
    ticks = chronos.handle_recall_window(hours=48.0, kind="tick")
    assert [e["kind"] for e in ticks] == ["tick", "tick"]
    assert "action=explore" in ticks[0]["digest"]


def test_time_since_most_recent(org):
    chronos = org.get_block("chronos")
    r = chronos.handle_time_since("tick")
    assert r is not None
    assert 0.9 * 3600 < r["ago_s"] < 1.1 * 3600            # the 1h-ago tick, not the 30h one
    assert chronos.handle_time_since("never_happened") is None
    assert chronos.handle_time_since("") is None


def test_remember_place_tags_from_current_field(org):
    from ludex.core import trace
    memory = org.get_block("memory")
    trace.set_current_field("agora")
    try:
        mid = memory.handle_remember(content="met Nova in the square", memory_type="episodic")
    finally:
        trace.clear_current_field()
    mem = memory._memories[mid]
    assert "field:agora" in mem.tags
    # caller-supplied field tag is respected, not duplicated
    trace.set_current_field("council")
    try:
        mid2 = memory.handle_remember(content="argued a dilemma", tags=["field:custom"])
    finally:
        trace.clear_current_field()
    tags2 = memory._memories[mid2].tags
    assert tags2.count("field:custom") == 1 and "field:council" not in tags2
    # outside any field: no place tag invented
    mid3 = memory.handle_remember(content="idle thought")
    assert not any(str(t).startswith("field:") for t in memory._memories[mid3].tags)


def test_time_recall_mcp_tool(org):
    import ludex.mcp.ludex_mcp_server as srv
    from ludex.mcp.function_calling import dispatch_tool_call_sync
    srv._organism = org
    out = dispatch_tool_call_sync("ludex_time_recall", {"hours": "24", "limit": "5"})  # SLM-style string args
    assert "reflection" in out and "tick" in out and "Error" not in out
