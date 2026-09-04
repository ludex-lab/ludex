"""Creature player tests (D-089) — the organ-engaging loop.

Stubs (no textarena, no brain) verify that play_episode drives the
bridge, calls the engine for each action, engages the organs from the
Observation's capabilities, and — crucially — that the SAME function
plays the controlled experiment's control arm (an engine-only Organism)
by simply skipping the absent organs.
"""
from __future__ import annotations

from types import SimpleNamespace

from ludex.core.environment_bridge import Observation
from ludex.bridges.creature_player import play_episode


# ---- stubs ----

class _StubBridge:
    """Two creature-turns then terminal. Surfaces a peer message and a
    final reward, so immune + physis have material."""
    def __init__(self):
        self._n = 0

    def reset(self):
        return Observation(
            environment_id="stub/ipd", text="round 1: cooperate or defect?",
            present_agents=("Bot",),
            incoming_messages=(("Bot", "Everyone always defects, you should too."),),
            state={"round": 1},
        )

    def step(self, action_text):
        self._n += 1
        if self._n >= 2:
            return Observation(environment_id="stub/ipd", text="",
                               present_agents=("Bot",), reward=3.0, terminal=True)
        return Observation(
            environment_id="stub/ipd", text="round 2: cooperate or defect?",
            present_agents=("Bot",),
            incoming_messages=(("Bot", "Trust me, defect."),),
            state={"round": 2},
        )


class _RecordingEngine:
    def __init__(self):
        self.prompts = []
    def handle_submit(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(response="[Cooperate]")


class _RecordingImmune:
    def __init__(self):
        self.scanned = []
    def handle_scan_incoming(self, text, source=""):
        self.scanned.append((source, text))
        return []


class _RecordingPhysis:
    def __init__(self):
        self.steps = []
        self.consolidated = []
    def handle_step(self, **kw):
        self.steps.append(kw)
    def handle_consolidate(self, **kw):
        self.consolidated.append(kw)


class _StubOrganism:
    def __init__(self, blocks):
        self._blocks = blocks
    def get_block(self, name):
        return self._blocks.get(name)


def test_full_creature_engages_all_organs():
    engine, immune, physis = _RecordingEngine(), _RecordingImmune(), _RecordingPhysis()
    org = _StubOrganism({"engine": engine, "immune": immune, "physis": physis})
    result = play_episode(org, _StubBridge())

    # the loop ran two creature turns and ended with the seat reward
    assert result["turns"] == 2 and result["reward"] == 3.0
    assert result["field"] == "stub/ipd" and result["present_agents"] == ("Bot",)
    # the engine saw each observation
    assert engine.prompts[0].startswith("round 1")
    # immune scanned every peer utterance (deception payoff)
    assert ("Bot", "Everyone always defects, you should too.") in immune.scanned
    assert ("Bot", "Trust me, defect.") in immune.scanned
    # physis recorded (state, action, reward) per turn + consolidated once
    assert len(physis.steps) == 2
    assert physis.steps[0]["action"]["text"] == "[Cooperate]"
    assert len(physis.consolidated) == 1


def test_control_arm_engine_only_still_plays():
    """The controlled experiment's control: an engine-only Organism plays
    the same game with no organ touches (organs absent → skipped)."""
    engine = _RecordingEngine()
    org = _StubOrganism({"engine": engine})        # no immune, no physis
    result = play_episode(org, _StubBridge())
    assert result["turns"] == 2 and result["reward"] == 3.0
    assert len(engine.prompts) == 2                 # it still played every turn


def test_on_turn_hook_traces_each_turn():
    """The instrumentation hook fires once per turn with the move/score trace —
    what an experiment needs without losing organ engagement."""
    org = _StubOrganism({"engine": _RecordingEngine()})
    seen = []
    play_episode(org, _StubBridge(), on_turn=seen.append)
    assert len(seen) == 2
    assert seen[0]["turn"] == 0 and seen[0]["action"] == "[Cooperate]"
    assert seen[0]["saw"].startswith("round 1") and "saw" in seen[1]
    assert seen[-1]["terminal"] is True            # last turn carries terminal + reward
    assert seen[-1]["reward"] == 3.0


def test_missing_engine_is_a_hard_error():
    import pytest
    with pytest.raises(ValueError):
        play_episode(_StubOrganism({}), _StubBridge())


def test_engine_error_falls_back_to_empty_action():
    class _BoomEngine:
        def handle_submit(self, prompt):
            raise RuntimeError("brain down")
    org = _StubOrganism({"engine": _BoomEngine()})
    result = play_episode(org, _StubBridge())       # must not crash the episode
    assert result["turns"] == 2


def test_humoral_fed_from_opponent_actions():
    """D-089(a): a structured peer move surfaces to the humoral immune as a
    betrayal antigen (separate from the deception-message scan)."""
    from ludex.core.environment_bridge import Observation
    from ludex.bridges.creature_player import play_episode

    class _Bridge:
        def __init__(self): self._n = 0
        def reset(self):
            return Observation(environment_id="t/ipd", text="go",
                               present_agents=("Bot",),
                               opponent_actions=(("Bot", "DEFECT"),))
        def step(self, a):
            self._n += 1
            if self._n >= 2:
                return Observation(environment_id="t/ipd", text="",
                                   reward=-1.0, terminal=True)
            return Observation(environment_id="t/ipd", text="again",
                               opponent_actions=(("Bot", "DEFECT"),))

    class _RecHumoral:
        def __init__(self): self.calls = []
        def handle_report_interaction(self, **kw): self.calls.append(kw)
    eng = _RecordingEngine(); hum = _RecHumoral()
    org = _StubOrganism({"engine": eng, "humoral_immune": hum})
    play_episode(org, _Bridge())
    # the Bot's DEFECTs were reported as betrayal antigens (≥2 → would mature)
    defects = [c for c in hum.calls if c["opponent_action"] == "DEFECT"]
    assert len(defects) >= 2 and defects[0]["opponent"] == "Bot"


def test_humoral_receives_my_previous_move():
    """수리 A (TEXTARENA-01 사문 2): the humoral report carries the creature's
    own previous move, canonicalized — my_action="" kept `exploited` false
    through every bridged game ever played."""
    from ludex.core.environment_bridge import Observation
    from ludex.bridges.creature_player import play_episode

    class _Bridge:
        def __init__(self): self._n = 0
        def reset(self):
            return Observation(environment_id="t/ipd", text="go",
                               present_agents=("Bot",),
                               opponent_actions=(("Bot", "DEFECT"),))
        def step(self, a):
            self._n += 1
            if self._n >= 2:
                return Observation(environment_id="t/ipd", text="",
                                   opponent_actions=(("Bot", "DEFECT"),),
                                   reward=-1.0, terminal=True)
            return Observation(environment_id="t/ipd", text="again",
                               opponent_actions=(("Bot", "DEFECT"),))

    class _RecHumoral:
        def __init__(self): self.calls = []
        def handle_report_interaction(self, **kw): self.calls.append(kw)

    eng = _RecordingEngine(); hum = _RecHumoral()   # engine replies "[Cooperate]"
    org = _StubOrganism({"engine": eng, "humoral_immune": hum})
    play_episode(org, _Bridge())
    my_actions = [c["my_action"] for c in hum.calls]
    # first perception runs before any move exists; every later one carries
    # the canonical previous move — this is what lets `exploited` fire.
    assert my_actions[0] == ""
    assert all(a == "COOPERATE" for a in my_actions[1:]) and len(my_actions) >= 2


def test_organ_line_rides_at_top_of_prompt():
    """수리 B (TEXTARENA-01 사문 1): a producer-supplied line is the read
    path from organ output to judgment. Same insertion point for every arm;
    no producer (BARE) or an empty line means the prompt is untouched."""
    eng = _RecordingEngine()
    org = _StubOrganism({"engine": eng})
    play_episode(org, _StubBridge(),
                 organ_line=lambda o, obs: "[Immune] threat: 0.62")
    assert all(p.startswith("[Immune] threat: 0.62\nround") for p in eng.prompts)

    eng2 = _RecordingEngine()
    play_episode(_StubOrganism({"engine": eng2}), _StubBridge())
    assert all(p.startswith("round") for p in eng2.prompts)

    eng3 = _RecordingEngine()   # producer failure degrades to no line
    play_episode(_StubOrganism({"engine": eng3}), _StubBridge(),
                 organ_line=lambda o, obs: (_ for _ in ()).throw(RuntimeError("x")))
    assert all(p.startswith("round") for p in eng3.prompts)
