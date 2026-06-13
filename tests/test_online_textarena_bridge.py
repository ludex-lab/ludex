"""Online TextArena bridge tests (D-089, online arm).

The live matchmaking server can't be exercised in CI (and was down 502 when
built), so we inject a faithful mock of ``OnlineEnvWrapper``'s verified
interface — ``reset`` / ``get_observation()->(player_id, obs_list)`` /
``step()->(done, info)`` / ``close()->rewards`` — and validate the bridge's
single-creature Observation view: server-assigned seat learned from the first
observation, list-observation flattened, opponents' utterances surfaced for the
immune scan, bare moves normalized to humoral antigens, and the seat reward
carried on terminal.
"""
from __future__ import annotations

import sys
import types

import pytest

from ludex.core.environment_bridge import EnvironmentBridge


class _MockOnline:
    """Mimics OnlineEnvWrapper: hands the creature (seat 0) a scripted sequence
    of observations, records submitted actions, returns seat rewards on close."""
    def __init__(self, script, rewards):
        self._script = script          # [(player_id, obs), ...] one per creature turn
        self._i = 0
        self._rewards = rewards
        self.actions = []
        self.reset_num_players = None
        self.close_calls = 0

    def reset(self, num_players=None, seed=None):
        self.reset_num_players = num_players

    def get_observation(self):
        return self._script[self._i] if self._i < len(self._script) else (None, [])

    def step(self, action):
        self.actions.append(action)
        self._i += 1
        return self._i >= len(self._script), {"step": self._i}

    def close(self):
        self.close_calls += 1
        return self._rewards


@pytest.fixture
def mock_ta():
    holder = {}
    fake = types.ModuleType("textarena")

    def make_online(env_id, model_name, model_token=None, **kw):
        w = _MockOnline(
            script=[
                (0, [[1, "hi - let's both cooperate, ok?"], [1, "[Defect]"]]),
                (0, [[1, "your move?"]]),
            ],
            rewards={0: 5.0, 1: 0.0},
        )
        holder["w"] = w
        holder["make_online_args"] = dict(env_id=env_id, model_name=model_name,
                                          model_token=model_token)
        return w

    fake.make_online = make_online
    saved = sys.modules.get("textarena")
    sys.modules["textarena"] = fake
    try:
        yield holder
    finally:
        if saved is not None:
            sys.modules["textarena"] = saved
        else:
            sys.modules.pop("textarena", None)


def _bridge():
    from ludex.bridges.online_textarena_bridge import OnlineTextArenaBridge
    return OnlineTextArenaBridge(["IteratedPrisonersDilemma-v0"],
                                 "ludex-pulsar", "tok-123", num_players=2)


def test_satisfies_protocol(mock_ta):
    assert isinstance(_bridge(), EnvironmentBridge)


def test_make_online_receives_token_not_email(mock_ta):
    _bridge()
    args = mock_ta["make_online_args"]
    assert args["model_token"] == "tok-123" and args["model_name"] == "ludex-pulsar"


def test_reset_learns_seat_and_surfaces_opponent(mock_ta):
    b = _bridge()
    obs = b.reset()
    assert mock_ta["w"].reset_num_players == 2
    assert b._seat == 0                                   # learned from the server
    assert obs.environment_id == "textarena-online/IteratedPrisonersDilemma-v0"
    assert "cooperate" in obs.text and "[Defect]" in obs.text   # list flattened
    # opponent (seat 1) utterances surface for the deception scan, our own do not
    assert ("1", "hi - let's both cooperate, ok?") in obs.incoming_messages
    # the bare move normalizes to a humoral betrayal antigen; the prose does not
    assert ("1", "DEFECT") in obs.opponent_actions
    assert all(m != "cooperate" for _, m in obs.opponent_actions)
    assert obs.present_agents == ("1",) and not obs.terminal


def test_step_submits_and_advances(mock_ta):
    b = _bridge()
    b.reset()
    obs = b.step("Round 1: [Cooperate]")
    assert "Round 1: [Cooperate]" in mock_ta["w"].actions
    assert obs.text == "your move?" and not obs.terminal


def test_terminal_carries_seat_reward_and_closes_once(mock_ta):
    b = _bridge()
    b.reset()
    b.step("a")
    final = b.step("b")                                   # exhausts the script
    assert final.terminal is True and final.reward == 5.0  # seat 0's reward
    b.close()                                             # idempotent
    assert mock_ta["w"].close_calls == 1
