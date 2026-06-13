"""Environment Bridge contract tests (D-089, step 1).

The contract is the env-agnostic foundation: a stub bridge must satisfy
the Protocol, and the capability→organ mapping must be faithful so each
organ activates only on what an environment exposes.
"""
from __future__ import annotations

from ludex.core.environment_bridge import (
    EnvironmentBridge, Observation, organs_for,
    CAP_AGENTS, CAP_REWARD, CAP_MESSAGES, CAP_PREDICT, ALL_CAPABILITIES,
)


class _StubArena:
    """A trivial two-step environment exposing agents + messages + reward
    (a Secret-Mafia-shaped env: allos + immune + ToM + physis)."""
    environment = "stub"
    capabilities = frozenset({CAP_AGENTS, CAP_MESSAGES, CAP_REWARD, CAP_PREDICT})

    def __init__(self):
        self._t = 0

    def reset(self) -> Observation:
        self._t = 0
        return Observation(
            environment_id="stub/mafia",
            text="The game begins. Comet and Verse are here.",
            present_agents=("Comet", "Verse"),
        )

    def step(self, action_text: str) -> Observation:
        self._t += 1
        return Observation(
            environment_id="stub/mafia",
            text=f"You said: {action_text}",
            present_agents=("Comet", "Verse"),
            incoming_messages=(("Comet", "Everyone knows Verse is lying."),),
            state={"round": self._t},
            reward=1.0 if self._t >= 2 else 0.0,
            terminal=self._t >= 2,
        )

    def close(self) -> None:
        pass


def test_stub_satisfies_protocol():
    bridge = _StubArena()
    assert isinstance(bridge, EnvironmentBridge)   # runtime_checkable structural match


def test_reset_step_flow():
    bridge = _StubArena()
    obs0 = bridge.reset()
    assert obs0.environment_id == "stub/mafia"
    assert obs0.present_agents == ("Comet", "Verse")
    assert obs0.terminal is False

    obs1 = bridge.step("I accuse Comet.")
    assert "I accuse Comet." in obs1.text
    assert obs1.incoming_messages[0][0] == "Comet"   # (from_agent, text)
    assert obs1.state["round"] == 1

    obs2 = bridge.step("I hold my position.")
    assert obs2.terminal is True and obs2.reward == 1.0


def test_capabilities_map_to_organs():
    """A fully-social env engages allos/immune/tom/physis; a solo
    benchmark (reward only) engages physis alone."""
    social = organs_for(_StubArena().capabilities)
    assert social == {CAP_AGENTS: "allos", CAP_MESSAGES: "immune",
                      CAP_PREDICT: "tom", CAP_REWARD: "physis"}

    solo = organs_for(frozenset({CAP_REWARD}))
    assert solo == {CAP_REWARD: "physis"}          # allos/immune/tom stay quiet

    assert organs_for(frozenset()) == {}           # a pure-text env: topos + engine only


def test_observation_defaults_are_empty():
    """Unexposed capabilities default to empty — an organ reading one
    finds nothing and stays quiet (no environment assumed)."""
    obs = Observation(environment_id="x/y", text="hi")
    assert obs.present_agents == () and obs.incoming_messages == ()
    assert obs.state == {} and obs.reward == 0.0


def test_all_capabilities_constant():
    assert ALL_CAPABILITIES == {CAP_AGENTS, CAP_REWARD, CAP_MESSAGES, CAP_PREDICT}
