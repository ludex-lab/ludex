"""Unit tests for the model-currency classifier (network-free)."""

from __future__ import annotations

from pathlib import Path

import ludex.cadence.model_check as mc
from ludex.cadence.model_check import (
    classify, _parse_model, available_for_sources, record_currency_span,
    CURRENT, SUPERSEDED, DEPRECATED, UNVERIFIABLE,
)
from ludex.core.store import LudexStore


def test_parse_basic_version():
    fam, ver, date = _parse_model("claude-opus-4-8")
    assert fam == ("claude", "opus")
    assert ver == (4, 8)
    assert date is None


def test_parse_dotted_and_dated():
    fam, ver, date = _parse_model("claude-haiku-4-5-20251001")
    assert fam == ("claude", "haiku")
    assert ver == (4, 5)
    assert date == 20251001

    fam, ver, _ = _parse_model("gpt-5.4-nano")
    assert fam == ("gpt", "nano")
    assert ver == (5, 4)


def test_parse_dash_separated_date():
    # OpenAI style: gpt-5-2025-08-07 — date is dash-separated, not version
    fam, ver, date = _parse_model("gpt-5-2025-08-07")
    assert fam == ("gpt",)
    assert ver == (5,)
    assert date == 20250807


def test_parse_strips_gemini_models_prefix():
    fam, ver, _ = _parse_model("models/gemini-3.5-flash")
    assert fam == ("gemini", "flash")
    assert ver == (3, 5)


def test_dotted_newer_not_superseded_by_dated_snapshot():
    # gpt-5.5 must NOT be flagged as superseded by an older dated gpt-5 snapshot
    res = classify("gpt-5.5", ["gpt-5.5", "gpt-5-2025-08-07", "gpt-5.4-nano"])
    assert res.status == CURRENT


def test_gemini_family_match_across_models_prefix():
    res = classify("gemini-2.5-flash", ["models/gemini-3.5-flash"])
    assert res.status == DEPRECATED
    assert res.successor == "models/gemini-3.5-flash"


def test_parse_placeholder_has_no_version():
    fam, ver, date = _parse_model("claude-code (default)")
    assert fam is None and ver == () and date is None


def test_current_when_model_present_and_newest():
    res = classify("claude-opus-4-8", ["claude-opus-4-8", "claude-sonnet-4-6"])
    assert res.status == CURRENT
    assert res.successor is None


def test_superseded_same_family_higher_version():
    res = classify("claude-haiku-4-5", ["claude-haiku-4-5", "claude-haiku-4-6"])
    assert res.status == SUPERSEDED
    assert res.successor == "claude-haiku-4-6"


def test_superseded_by_newer_dated_snapshot():
    res = classify(
        "claude-haiku-4-5-20251001",
        ["claude-haiku-4-5-20251001", "claude-haiku-4-5-20260301"],
    )
    assert res.status == SUPERSEDED
    assert res.successor == "claude-haiku-4-5-20260301"


def test_deprecated_when_model_gone_family_lives():
    res = classify("claude-opus-4-6", ["claude-opus-4-8", "claude-sonnet-4-6"])
    assert res.status == DEPRECATED
    assert res.successor == "claude-opus-4-8"


def test_deprecated_when_family_gone():
    res = classify("gemini-2.5-flash", ["gemini-3.5-flash", "gemini-3.1-flash-lite"])
    # family ('gemini','flash') still present, version 2.5 gone -> deprecated + successor
    assert res.status == DEPRECATED
    assert res.successor == "gemini-3.5-flash"


def test_dated_alias_matches_undated():
    # creature on undated alias, provider lists dated snapshot of same ver
    res = classify("claude-haiku-4-5", ["claude-haiku-4-5-20251001"])
    assert res.status == CURRENT


def test_unverifiable_empty_available():
    assert classify("claude-opus-4-8", []).status == UNVERIFIABLE
    assert classify("claude-opus-4-8", None).status == UNVERIFIABLE


def test_unverifiable_placeholder_list():
    res = classify("claude-opus-4-8", ["claude-code (default)"])
    assert res.status == UNVERIFIABLE


def test_unverifiable_unparseable_model():
    res = classify("mystery-model", ["claude-opus-4-8"])
    assert res.status == UNVERIFIABLE


# -------------------------------------------------------------------
# available_for_sources — disk TTL cache gates the live /models query
# -------------------------------------------------------------------

def test_available_for_sources_caches_within_ttl(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_query(source):
        calls["n"] += 1
        return ["claude-opus-4-8"]

    monkeypatch.setattr(mc, "_available_for_source", fake_query)
    cache = tmp_path / "cache.json"

    first = available_for_sources(["anthropic"], cache_path=cache)
    assert first == {"anthropic": ["claude-opus-4-8"]}
    assert calls["n"] == 1
    assert cache.exists()

    # Second call within TTL serves cache — no extra query.
    second = available_for_sources(["anthropic"], cache_path=cache)
    assert second == {"anthropic": ["claude-opus-4-8"]}
    assert calls["n"] == 1


def test_available_for_sources_refetches_after_ttl(tmp_path, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        mc, "_available_for_source",
        lambda s: (calls.__setitem__("n", calls["n"] + 1), ["m"])[1],
    )
    cache = tmp_path / "cache.json"
    available_for_sources(["anthropic"], ttl_hours=24, cache_path=cache)
    # ttl_hours=0 forces every entry stale → re-query.
    available_for_sources(["anthropic"], ttl_hours=0, cache_path=cache)
    assert calls["n"] == 2


def test_available_for_sources_does_not_cache_none(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "_available_for_source", lambda s: None)
    cache = tmp_path / "cache.json"
    out = available_for_sources(["anthropic"], cache_path=cache)
    assert out == {"anthropic": None}
    # A failed/keyless source is not persisted (retried next run).
    assert not cache.exists()


# -------------------------------------------------------------------
# record_currency_span — idempotent single-creature recording
# -------------------------------------------------------------------

def test_record_currency_span_writes_once(tmp_path):
    store = LudexStore.for_creature(str(tmp_path))
    wrote = record_currency_span(
        store, "Tc", SUPERSEDED, "claude-opus-4-6", "claude_cli",
        successor="claude-opus-4-8", source="anthropic",
    )
    assert wrote is True
    spans = store.spans(kind="model_currency")
    assert len(spans) == 1
    assert spans[-1]["attributes"]["successor"] == "claude-opus-4-8"

    # Same standing fact → suppressed.
    again = record_currency_span(
        store, "Tc", SUPERSEDED, "claude-opus-4-6", "claude_cli",
        successor="claude-opus-4-8", source="anthropic",
    )
    assert again is False
    assert len(store.spans(kind="model_currency")) == 1


def test_record_currency_span_skips_current_and_ollama(tmp_path):
    store = LudexStore.for_creature(str(tmp_path))
    assert record_currency_span(store, "Tc", CURRENT, "m", "p") is False
    assert record_currency_span(store, "Tc", UNVERIFIABLE, "m", "p") is False
    assert record_currency_span(
        store, "Tc", DEPRECATED, "gemma:7b", "ollama", source="ollama",
    ) is False
    assert store.spans(kind="model_currency") == []
