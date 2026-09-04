"""Grok agentic timeout evidence without invoking a provider."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from ludex.blocks.adapters import grok_cli


def _adapter(tmp_path):
    return grok_cli.GrokCliAdapter(
        base_url="/fake/grok",
        cwd=str(tmp_path),
        timeout_ms=50,
    )


def test_agentic_success_preserves_liveness(monkeypatch, tmp_path):
    telemetry = {
        "outcome": "exited",
        "label": "exited",
        "bytes_out": 2,
    }

    trace = SimpleNamespace(ended_by="exited", signature="", signature_detail="",
                            summary=lambda: telemetry)

    def fake_run_streamed(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="ok", stderr="", lines=[], usage=None), trace

    monkeypatch.setattr(grok_cli, "run_streamed", fake_run_streamed)
    response = _adapter(tmp_path).call(
        model="grok-4.6",
        prompt="bounded work",
        tools=[{"type": "function"}],
        effort="high",
    )

    assert response.content == "ok"
    assert response.raw["liveness"] == telemetry


def test_agentic_timeout_records_stall_and_tool_error(monkeypatch, tmp_path):
    telemetry = {
        "cli": "grok",
        "last_state": "tool_running",
        "ended_by": "hard_cap",
        "label": "hard_cap",
    }

    trace = SimpleNamespace(ended_by="hard_cap", signature="", signature_detail="",
                            summary=lambda: telemetry)

    def fake_run_streamed(cmd, cli, **kwargs):
        # the state runner hit the hard cap; the adapter turns that into the
        # same TimeoutExpired evidence path as before (dying words + trace)
        return SimpleNamespace(returncode=None, stdout="", stderr="",
                               lines=['{"type":"tool_output_error","tool":"list_dir"}'],
                               usage=None), trace

    monkeypatch.setattr(grok_cli, "run_streamed", fake_run_streamed)
    response = _adapter(tmp_path).call(
        model="grok-4.6",
        prompt="bounded work",
        tools=[{"type": "function"}],
        effort="high",
    )

    assert response.content == "[Error: Grok CLI timed out]"
    assert response.raw["timeout"] is True
    assert response.raw["tool_output_error"] is True
    assert response.raw["liveness"] == telemetry
