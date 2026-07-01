"""The opsis claude-CLI interpreter must honor subscription auth — strip
ANTHROPIC_API_KEY from the spawn so the CLI uses the logged-in subscription
instead of billing the API. Regression for the 2026-06-30 credit-balance leak:
the interpreter spawned `claude` with no `env=`, inheriting the ambient key that
Ludex loads from .env for the model-currency check, so the CLI ran on API auth
and hit the API credit balance. Mirrors the 2026-06-18 brain.auth billing-leak
fix (adapters/_cli_env.py)."""
from __future__ import annotations

from ludex.blocks.opsis_adapters import claude_cli_interpreter as interp


def test_interpreter_strips_api_key_for_subscription(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-stripped")
    captured = {}

    class _Proc:
        returncode = 0
        stdout = "A dense description another creature can use."
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return _Proc()

    monkeypatch.setattr(interp.subprocess, "run", _fake_run)

    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    desc, meta = interp.interpret(img, purpose="see what is here")

    assert captured["env"] is not None, "interpret must pass an explicit env, not inherit os.environ"
    assert "ANTHROPIC_API_KEY" not in captured["env"], (
        "subscription auth must strip ANTHROPIC_API_KEY so the CLI uses the login, not the billed API"
    )
    assert desc == "A dense description another creature can use."
    assert meta["returncode"] == 0
