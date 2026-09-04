"""A successful, long answer that talks ABOUT rate limits is not a rate limit (09-04, Wisp's ARC report)."""
from types import SimpleNamespace

from ludex.blocks.adapters import codex_cli


def test_long_successful_answer_mentioning_429_is_not_fatigue(monkeypatch, tmp_path):
    long_report = ("# ARC harness report\n" + "The harness counts actions, level budget, HTTP 429 and rate limit responses "
                   "as separate ceilings. " * 20)

    def fake_run_capture(cmd, **kw):
        # the adapter passes --output-last-message <file>; write the answer there
        out = cmd[cmd.index("--output-last-message") + 1]
        open(out, "w", encoding="utf-8").write(long_report)
        return SimpleNamespace(returncode=0, stdout="", stderr="tokens used\n9,417\n")

    monkeypatch.setattr(codex_cli, "run_capture", fake_run_capture)
    monkeypatch.setattr(codex_cli, "_codex_auto_flag", lambda: ["--approve-for-me"])
    ad = codex_cli.CodexCliAdapter(cwd=str(tmp_path), timeout_ms=1000)
    r = ad.call(prompt="report", tools=[{"bench": True}], model="gpt-5.6-luna")
    assert not r.content.startswith("[Error"), r.content[:120]
    assert "HTTP 429" in r.content


def test_short_failed_answer_with_429_is_fatigue(monkeypatch, tmp_path):
    def fake_run_capture(cmd, **kw):
        out = cmd[cmd.index("--output-last-message") + 1]
        open(out, "w", encoding="utf-8").write("")
        return SimpleNamespace(returncode=1, stdout="", stderr="\ncodex\nError: rate limit exceeded (429)\n")

    monkeypatch.setattr(codex_cli, "run_capture", fake_run_capture)
    monkeypatch.setattr(codex_cli, "_codex_auto_flag", lambda: ["--approve-for-me"])
    ad = codex_cli.CodexCliAdapter(cwd=str(tmp_path), timeout_ms=1000)
    r = ad.call(prompt="x", tools=[{"bench": True}], model="gpt-5.6-luna")
    assert r.content.startswith("[Error") and "rate_limited" in r.content
