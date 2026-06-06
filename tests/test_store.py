"""Tests for LudexStore + trace emitters (D-028 Phase A)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ludex.core.store import LudexStore, Span


def test_append_and_query():
    with tempfile.TemporaryDirectory() as d:
        store = LudexStore.for_creature(d)
        store.append(Span(kind="tick", creature="A", attributes={"tick": 1}))
        store.append(Span(kind="tick", creature="A", attributes={"tick": 2}))
        store.append(Span(kind="field_end", creature="A", attributes={"field_name": "x"}))
        assert len(store.spans()) == 3
        assert len(store.spans(kind="tick")) == 2
        assert len(store.spans(kind="field_end")) == 1


def test_reward_indexing():
    with tempfile.TemporaryDirectory() as d:
        store = LudexStore.for_creature(d)
        store.append(Span(
            kind="reward.energy_delta", creature="A",
            attributes={"field_name": "w"},
            reward={"dimension": "energy_delta", "value": -5.0},
        ))
        store.append(Span(
            kind="reward.bond_accuracy", creature="A",
            attributes={"other": "B"},
            reward={"dimension": "bond_accuracy", "value": 0.75},
        ))
        store.append(Span(kind="tick", creature="A", attributes={"tick": 1}))

        rewards = store.rewards()
        assert len(rewards) == 2
        assert len(store.rewards(dimension="bond_accuracy")) == 1
        assert store.reward_values("energy_delta") == [-5.0]
        assert store.reward_values("bond_accuracy") == [0.75]


def test_spans_by_field_and_episodes():
    with tempfile.TemporaryDirectory() as d:
        store = LudexStore.for_creature(d)
        # Episode A
        store.append(Span(kind="field_start", creature="X", timestamp=1.0,
                          attributes={"field_name": "A"}))
        store.append(Span(kind="tick", creature="X", timestamp=1.5,
                          attributes={"field_name": "A", "tick": 1}))
        store.append(Span(kind="reward.energy_delta", creature="X", timestamp=2.0,
                          attributes={"field_name": "A"},
                          reward={"dimension": "energy_delta", "value": -5.0}))
        store.append(Span(kind="field_end", creature="X", timestamp=2.1,
                          attributes={"field_name": "A"}))
        # Episode B
        store.append(Span(kind="field_start", creature="X", timestamp=5.0,
                          attributes={"field_name": "B"}))
        store.append(Span(kind="reward.bond_accuracy", creature="X", timestamp=5.5,
                          attributes={"field_name": "B", "other": "Y"},
                          reward={"dimension": "bond_accuracy", "value": 0.75}))
        store.append(Span(kind="field_end", creature="X", timestamp=6.0,
                          attributes={"field_name": "B"}))

        assert len(store.spans_by_field("A")) == 4
        assert len(store.spans_by_field("B")) == 3
        assert len(store.rewards_by_field("A", dimension="energy_delta")) == 1
        assert len(store.rewards_by_field("B", dimension="bond_accuracy")) == 1

        eps = store.episodes()
        assert [e["field_name"] for e in eps] == ["A", "B"]
        assert eps[0]["span_count"] == 4
        assert eps[0]["rewards"]["energy_delta"] == [-5.0]
        assert eps[1]["rewards"]["bond_accuracy"] == [0.75]
        assert eps[0]["started_at"] == 1.0
        assert eps[0]["ended_at"] == 2.1


def test_spans_file_is_jsonl():
    with tempfile.TemporaryDirectory() as d:
        store = LudexStore.for_creature(d)
        store.append(Span(kind="tick", creature="A"))
        store.append(Span(kind="tick", creature="A"))
        text = (Path(d) / "store" / "spans.jsonl").read_text()
        assert text.count("\n") == 2
        assert "tick" in text
