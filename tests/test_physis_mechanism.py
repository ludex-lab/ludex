"""Live-mechanism tests for physis — the pipeline the audit (F-P5) flagged as
untested: step buffering, clear_trace, Phase A + Phase B consolidate, the
confidence post-processing, and get_relevant_hints retrieval. Previously only
`_looks_like_distill_error` had coverage on this 900-line organ."""
import yaml

from ludex.blocks.physis import PhysisBlock


def _physis(tmp_path):
    blk = PhysisBlock()
    blk._config = {"habitat_dir": str(tmp_path)}
    return blk


class _FakeBrain:
    """Minimal brain: handle_submit(prompt, bypass_memory=) -> obj with .response."""
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def handle_submit(self, prompt, bypass_memory=False):
        self.calls += 1
        return type("R", (), {"response": self._response})()


# ---- step buffering ----

def test_step_buffers_and_autoclears_on_field_change(tmp_path):
    p = _physis(tmp_path)
    p.handle_step("game/a", 1, action={"verb": "x"}, reward=1.0)
    p.handle_step("game/a", 2, action={"verb": "y"}, reward=0.0)
    assert p.handle_clear_trace() == 2            # two buffered for field a
    p.handle_step("game/a", 1, reward=1.0)
    p.handle_step("game/b", 1, reward=0.0)        # field change auto-clears
    assert p.handle_clear_trace() == 1            # only the game/b step remains


def test_clear_trace_returns_prior_length_and_empties(tmp_path):
    p = _physis(tmp_path)
    for t in range(3):
        p.handle_step("game/a", t, reward=0.0)
    assert p.handle_clear_trace() == 3
    assert p.handle_clear_trace() == 0            # now empty


# ---- Phase A consolidate (mechanical) — also locks the F-P3 de-stale ----

def test_phase_a_consolidate_writes_verbatim_trace(tmp_path):
    p = _physis(tmp_path)
    p.handle_step("game/a", 1, ground_truth_state={"s": 1}, action={"verb": "x"}, reward=1.0)
    p.handle_consolidate(field="game/a", brain_engine=None, episode_id="ep1")
    wm = tmp_path / "memory" / "world_models" / "game" / "a.md"
    assert wm.exists()
    body = wm.read_text()
    assert "Verbatim trace — mechanical consolidation" in body    # de-staled note (F-P3)
    assert "Phase A skeleton" not in body                         # the stale lie is gone
    assert '"verb": "x"' in body or "'verb': 'x'" in body         # the trace landed


# ---- Phase B consolidate (brain-distilled) ----

_DISTILLATION = """# World model — `game/a`

## Reward correlates
- Cooperating early tracked with +reward (ep1).

## Policy hints
- If the round is early, cooperate.

## Open uncertainty
- Late-game behaviour untested.

```yaml
hints:
  - precondition: {round: early}
    action: {verb: cooperate}
    confidence: confirmed
    evidence: {confirmed: 3}
```
"""


def test_phase_b_consolidate_distills_and_extracts_hints(tmp_path):
    p = _physis(tmp_path)
    p.handle_step("game/a", 1, ground_truth_state={"round": "early"},
                  action={"verb": "cooperate"}, reward=1.0)
    brain = _FakeBrain(_DISTILLATION)
    p.handle_consolidate(field="game/a", brain_engine=brain, episode_id="ep1")
    assert brain.calls == 1                                        # the brain distilled
    wm = tmp_path / "memory" / "world_models" / "game" / "a.md"
    assert "Reward correlates" in wm.read_text()                  # body is the distillation
    sidecar = tmp_path / "memory" / "world_models" / "game" / "a.hints.yaml"
    assert sidecar.exists()
    hints = yaml.safe_load(sidecar.read_text())["hints"]
    assert any(h.get("action") == {"verb": "cooperate"} for h in hints)


def test_confidence_demoted_when_evidence_insufficient(tmp_path):
    p = _physis(tmp_path)
    p.handle_step("game/a", 1, reward=1.0)
    # brain over-claims "confirmed" on a single episode of evidence
    over = _DISTILLATION.replace("confirmed: 3", "confirmed: 1")
    p.handle_consolidate(field="game/a", brain_engine=_FakeBrain(over), episode_id="ep1")
    sidecar = tmp_path / "memory" / "world_models" / "game" / "a.hints.yaml"
    hints = yaml.safe_load(sidecar.read_text())["hints"]
    assert hints[0]["confidence"] == "tentative"                  # silently demoted to match evidence


# ---- get_relevant_hints retrieval ----

def test_get_relevant_hints_filters_sorts_and_caps(tmp_path):
    p = _physis(tmp_path)
    sidecar = tmp_path / "memory" / "world_models" / "game" / "a.hints.yaml"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(yaml.safe_dump({"hints": [
        {"precondition": {"round": "early"}, "action": {"verb": "coop"}, "confidence": "tentative"},
        {"precondition": {"round": "early"}, "action": {"verb": "trust"}, "confidence": "well-supported"},
        {"precondition": {"round": "late"}, "action": {"verb": "defect"}, "confidence": "confirmed"},
    ]}))
    got = p.handle_get_relevant_hints("game/a", {"round": "early"}, max_hints=5)
    verbs = [h["action"]["verb"] for h in got]
    assert "defect" not in verbs                                  # 'late' precondition filtered out
    assert verbs == ["trust", "coop"]                             # higher confidence sorts first
    capped = p.handle_get_relevant_hints("game/a", {"round": "early"}, max_hints=1)
    assert len(capped) == 1                                       # cap honoured
