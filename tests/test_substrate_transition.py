"""Tests for ludex.cadence.substrate_transition (D-086 span, generalized axes)."""

import pytest

from ludex.cadence.substrate_transition import record_transition_span, main
from ludex.core.store import LudexStore


# -------------------------------------------------------------------
# record_transition_span — idempotent single-creature recording
# -------------------------------------------------------------------

def test_record_transition_span_writes_once(tmp_path):
    store = LudexStore.for_creature(str(tmp_path))
    wrote = record_transition_span(
        store, "Tc",
        axis="P", from_desc="consumer_subscription", to_desc="paid_api_key",
        provider="gemini_cli", model="gemini-2.5-flash",
        magnitude="tiny", op="preserve",
        note="gemini consumer auth retirement 2026-06-18",
    )
    assert wrote is True
    spans = store.spans(kind="substrate_transition")
    assert len(spans) == 1
    a = spans[-1]["attributes"]
    assert a["axis"] == "P"
    assert a["from"] == "consumer_subscription"
    assert a["to"] == "paid_api_key"
    assert a["op"] == "preserve"

    # Same standing fact → suppressed (re-run after partial failure).
    again = record_transition_span(
        store, "Tc",
        axis="P", from_desc="consumer_subscription", to_desc="paid_api_key",
        provider="gemini_cli", model="gemini-2.5-flash",
    )
    assert again is False
    assert len(store.spans(kind="substrate_transition")) == 1


def test_record_transition_span_distinct_events_both_recorded(tmp_path):
    store = LudexStore.for_creature(str(tmp_path))
    assert record_transition_span(
        store, "Tc", axis="M",
        from_desc="claude-opus-4-7", to_desc="claude-opus-4-8",
        provider="claude_cli", model="claude-opus-4-8",
    ) is True
    assert record_transition_span(
        store, "Tc", axis="P",
        from_desc="consumer_subscription", to_desc="paid_api_key",
        provider="claude_cli", model="claude-opus-4-8",
    ) is True
    assert len(store.spans(kind="substrate_transition")) == 2


def test_record_transition_span_rejects_bad_enums(tmp_path):
    store = LudexStore.for_creature(str(tmp_path))
    with pytest.raises(ValueError):
        record_transition_span(store, "Tc", axis="X", from_desc="a", to_desc="b")
    with pytest.raises(ValueError):
        record_transition_span(
            store, "Tc", axis="P", from_desc="a", to_desc="b", magnitude="huge",
        )
    with pytest.raises(ValueError):
        record_transition_span(
            store, "Tc", axis="P", from_desc="a", to_desc="b", op="resurrect",
        )
    assert store.spans(kind="substrate_transition") == []


# -------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------

def test_cli_records_span_in_habitat(tmp_path, capsys):
    habitat = tmp_path / "Tc"
    habitat.mkdir()
    rc = main([
        "--creature", "Tc", "--root", str(tmp_path),
        "--axis", "P",
        "--from", "consumer_subscription", "--to", "paid_api_key",
        "--provider", "gemini_cli", "--model", "gemini-2.5-flash",
    ])
    assert rc == 0
    assert "span written" in capsys.readouterr().out
    store = LudexStore.for_creature(str(habitat))
    assert len(store.spans(kind="substrate_transition")) == 1


def test_cli_missing_habitat_fails(tmp_path):
    rc = main([
        "--creature", "Nope", "--root", str(tmp_path),
        "--axis", "P", "--from", "a", "--to", "b",
    ])
    assert rc == 1
