from __future__ import annotations

import subprocess

from ludex.blocks.adapters import agy_cli


def test_routed_model_is_reported_in_response_metadata(monkeypatch):
    seen: dict[str, list[str]] = {}

    def fake_run_traced(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout='{"ping":"pong"}', stderr=""
        ), {}

    monkeypatch.setattr(agy_cli, "run_traced", fake_run_traced)
    adapter = agy_cli.AgyCliAdapter(base_url="agy")

    response = adapter.call(
        model="gemini-3.7-flash",
        effort="high",
        prompt="reply with JSON",
    )

    assert seen["cmd"][-4:] == [
        "--model", "gemini-3.7-flash", "--effort", "high"
    ]
    assert response.raw["model"] == "gemini-3.7-flash"
    assert response.raw["effort"] == "high"


def test_default_model_metadata_stays_pinned(monkeypatch):
    def fake_run_traced(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr=""), {}

    monkeypatch.setattr(agy_cli, "run_traced", fake_run_traced)
    adapter = agy_cli.AgyCliAdapter(base_url="agy")

    response = adapter.call(model="gemini-3.5-flash", prompt="hello")

    assert response.raw["model"] == "gemini-3.5-flash"
    assert response.raw["effort"] == ""
