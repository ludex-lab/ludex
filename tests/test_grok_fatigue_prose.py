"""Vane 09-04: a sources table mentioning 'HTTP 429' was read as a rate limit — 470s of work discarded, 60-minute false cooldown."""
from types import SimpleNamespace
from ludex.blocks.adapters import grok_cli


def _ok_long(*a, **k):
    text = ("| VentureBeat (09-03) | **못 열음** 추측 URL HTTP 429 | 씨앗에 경로 없음 |\n" * 12)
    return SimpleNamespace(returncode=0, stdout=text, stderr=""), {}


def test_long_successful_table_with_429_is_not_fatigue(monkeypatch, tmp_path):
    monkeypatch.setattr(grok_cli, "run_streamed", lambda *a, **k: (SimpleNamespace(returncode=0, stdout=_ok_long()[0].stdout, stderr="", lines=[], usage=None),
                                                                     SimpleNamespace(ended_by="exited", signature="", signature_detail="", summary=lambda: {})))
    ad = grok_cli.GrokCliAdapter(base_url="/fake/grok", cwd=str(tmp_path), timeout_ms=1000)
    r = ad.call(model="grok-4.6", prompt="grade sources", tools=[{"bench": True}], effort="high")
    assert not r.content.startswith("[Error"), r.content[:100]
    assert "429" in r.content
