"""Wilderness → web Field tab plumbing tests (v1, 2026-06-12).

The web server renders council/forum from `field.rounds`; wilderness has
no rounds — it has a tick log. These tests pin the mapping contract the
observe UI depends on, without booting FastAPI or any brain.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_server_helpers():
    # web/server.py needs FastAPI; the lab env may not have it (the
    # server runs in the public install's env). Skip rather than fail.
    pytest.importorskip("fastapi")
    # Imports just the helpers from web/server.py without running
    # uvicorn — module-level code builds the app but starts nothing.
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("ludex_web_server",
                                                  root / "web" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ludex_web_server"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeWild:
    """Duck-typed Wilderness: has .log and .creatures, no .rounds."""
    def __init__(self):
        self.log = [{
            "tick": 1, "event": "storm",
            "event_description": "A violent storm.",
            "event_category": "challenge",
            "creatures": [
                {"name": "Mote", "action": "defend", "energy": 85,
                 "response": "I brace against the wind.", "emotion": "fear",
                 "threat": 0.4},
                {"name": "Pulsar", "action": "support", "energy": 90,
                 "response": "I move toward Mote.", "emotion": "", "threat": 0},
            ],
        }]
        self.creatures = [types.SimpleNamespace(name="Mote"),
                          types.SimpleNamespace(name="Pulsar")]


def test_wilderness_transcript_mapping():
    srv = _load_server_helpers()
    recs = srv._session_transcript_records(_FakeWild())
    assert len(recs) == 3
    world, a1, a2 = recs
    assert world["participant"] == "world" and world["kind"] == "event"
    assert "storm" in world["content"]
    assert a1["participant"] == "Mote" and a1["kind"] == "action"
    assert a1["attributes"]["action"] == "defend"
    assert a1["attributes"]["energy"] == 85
    assert a2["participant"] == "Pulsar"


def test_wilderness_participant_names():
    srv = _load_server_helpers()
    assert srv._field_participant_names(_FakeWild()) == ["Mote", "Pulsar"]
    assert srv._field_participant_names(None) == []


def test_count_turns_excludes_world_events():
    """Regression: a done wilderness session in memory must not crash the
    sessions list. _count_turns reads the mapped transcript (no field.rounds)
    and excludes world-event rows — 10 events + 30 actions → 30 turns."""
    srv = _load_server_helpers()
    recs = srv._session_transcript_records(_FakeWild())
    assert srv._count_turns(recs) == 2          # the _FakeWild has 1 tick, 2 actions
    # council/forum-shaped records: posting rows excluded, turns counted
    council = [{"phase": "dilemma_posed"}, {"phase": "first_position"},
               {"phase": "argument"}, {"phase": "claim"}]
    assert srv._count_turns(council) == 2


def test_wilderness_hooks_exist_and_default_off():
    from ludex.fields.wilderness import Wilderness
    w = Wilderness(total_ticks=3)
    assert w.progress_cb is None and w.stop_check is None
    assert w._stopped() is False           # no stop_check → never stops
    w._progress("tick", "1/3")             # no cb → silently fine

    seen = []
    w2 = Wilderness(total_ticks=3,
                    progress_cb=lambda s, d: seen.append((s, d)),
                    stop_check=lambda: True)
    w2._progress("tick", "1/3:storm")
    assert seen == [("tick", "1/3:storm")]
    assert w2._stopped() is True
