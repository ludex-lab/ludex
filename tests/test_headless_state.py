"""Headless state classifier — pinned to the 2026-09-03 captures (16 files).
If a CLI changes its wire shape, these go red before a bench session goes silent."""
import json
import os

import pytest

from ludex.blocks.adapters._headless_state import classify_line, extract_answer, extract_usage

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "headless")


def _lines(name):
    recs = [json.loads(l) for l in open(os.path.join(FIX, name), encoding="utf-8")]
    return [(r["stream"], r["line"]) for r in recs if "line" in r]


def _states(cli, name):
    seen, kinds = [], []
    for stream, line in _lines(name):
        ev = classify_line(cli, stream, line)
        kinds.append(ev.kind)
        if ev.state and (not seen or seen[-1] != ev.state):
            seen.append(ev.state)
    return seen, kinds


@pytest.mark.parametrize("cli", ["claude", "codex", "grok", "agy"])
def test_healthy_stream_reaches_done_and_has_answer(cli):
    states, kinds = _states(cli, f"{cli}-stream.jsonl")
    assert states[-1] == "done", states
    assert "result" in kinds
    out = [l for s, l in _lines(f"{cli}-stream.jsonl") if s == "out"]
    assert extract_answer(cli, out).strip()
    assert extract_usage(cli, out)


@pytest.mark.parametrize("cli", ["claude", "grok", "agy"])
def test_heartbeat_clis_emit_heartbeats(cli):
    _, kinds = _states(cli, f"{cli}-stream.jsonl")
    assert kinds.count("heartbeat") >= 3


def test_codex_has_no_heartbeat_between_turn_and_item():
    _, kinds = _states("codex", "codex-stream.jsonl")
    assert kinds.count("heartbeat") <= 1      # turn.started only — the idle clock must stay off


@pytest.mark.parametrize("cli", ["claude", "codex", "grok", "agy"])
def test_tool_use_is_seen(cli):
    states, kinds = _states(cli, f"{cli}-tool.jsonl")
    assert "tool_start" in kinds and "tool_running" in states


def test_claude_permission_denied_is_explicit():
    _, kinds = _states("claude", "claude-perm.jsonl")
    assert "permission" in kinds


def test_agy_permission_denied_from_stderr_and_tool_error_step():
    _, kinds = _states("agy", "agy-perm.jsonl")
    assert "permission" in kinds and "tool_error" in kinds


def test_codex_sandbox_block_reads_as_permission():
    _, kinds = _states("codex", "codex-perm.jsonl")
    assert "permission" in kinds


def test_grok_headless_ran_the_write_without_approval():
    # policy observation pinned: no permission event, tool ran, done
    states, kinds = _states("grok", "grok-perm.jsonl")
    assert "permission" not in kinds and states[-1] == "done"


@pytest.mark.parametrize("cli", ["claude", "grok", "agy"])
def test_logged_out_is_an_auth_signature(cli):
    _, kinds = _states(cli, f"{cli}-noauth.jsonl")
    assert "auth" in kinds, kinds


def test_agy_auth_signature_arrives_before_the_60s_wait():
    recs = [json.loads(l) for l in open(os.path.join(FIX, "agy-noauth.jsonl"), encoding="utf-8")]
    first = next(r for r in recs if "line" in r
                 and classify_line("agy", r["stream"], r["line"]).kind == "auth")
    assert first["t"] < 2.0
