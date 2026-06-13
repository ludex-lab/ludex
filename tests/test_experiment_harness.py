"""Controlled experiment harness tests (D-089 §4) — orchestration only, no brain."""
from __future__ import annotations
from types import SimpleNamespace
from ludex.core.environment_bridge import Observation
from ludex.bridges.experiment import run_controlled, summarize, strip_to_bare


class _OneTurnBridge:
    def __init__(self, reward): self._r = reward; self._done = False
    def reset(self): return Observation(environment_id="t/g", text="go")
    def step(self, a):
        return Observation(environment_id="t/g", text="", reward=self._r, terminal=True)

class _Eng:
    def handle_submit(self, p): return SimpleNamespace(response="x")
class _Org:
    def __init__(self, b): self._b=b
    def get_block(self, n): return self._b.get(n)


def test_run_controlled_runs_both_arms():
    treat = _Org({"engine": _Eng()})
    ctrl = _Org({"engine": _Eng()})
    res = run_controlled(treat, ctrl, lambda: _OneTurnBridge(2.0), n_games=3)
    assert len(res["treatment"]) == 3 and len(res["control"]) == 3
    s = summarize(res)
    assert s["treatment"]["mean"] == 2.0 and s["treatment"]["n"] == 3
    assert s["control"]["final"] == 2.0


def test_strip_to_bare_disables_non_engine_organs():
    cfg = SimpleNamespace(organs={
        "engine": {"enabled": True, "system_prompt": "you are X"},
        "memory": {"enabled": True}, "physis": {"enabled": True},
        "immune": {"enabled": True},
    })
    strip_to_bare(cfg)
    assert cfg.organs["engine"]["enabled"] is True           # engine kept
    assert cfg.organs["memory"]["enabled"] is False
    assert cfg.organs["physis"]["enabled"] is False
    assert cfg.organs["immune"]["enabled"] is False
