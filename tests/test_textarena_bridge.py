"""TextArena bridge tests (D-089, public bridge #1).

textarena is an optional dependency and is NOT installed in CI, so these
tests inject a FAITHFUL mock `textarena` module (mirroring the verified
local API: make / reset / get_observation / step / close) to validate the
bridge's orchestration — that it absorbs TextArena's multi-agent loop and
presents a single creature's reset/step view, with other players'
utterances surfaced as incoming_messages and the creature's seat reward
at the end. Live validation against the real package is separate.
"""
from __future__ import annotations

import sys
import types

import pytest

from ludex.core.environment_bridge import EnvironmentBridge


# ---- a faithful mock of TextArena's verified local API ----

class _MockEnv:
    def __init__(self, turn_order, rewards):
        self._turns = turn_order      # e.g. [1, 2, 0, 1, 2, 0]
        self._i = 0
        self._rewards = rewards
        self.actions: list[tuple[int, str]] = []   # (player_id, action) recorded

    def reset(self, num_players):
        self._i = 0
        self.num_players = num_players

    def get_observation(self):
        pid = self._turns[self._i]
        return pid, f"obs for player {pid} at turn {self._i}"

    def step(self, action):
        pid = self._turns[self._i]
        self.actions.append((pid, action))
        self._i += 1
        done = self._i >= len(self._turns)
        return done, {"turn": self._i}

    def close(self):
        return self._rewards, {"game": "mock"}


@pytest.fixture
def mock_textarena():
    """Inject a fake `textarena` whose make() returns one shared _MockEnv,
    so the test can inspect what the creature submitted."""
    env = _MockEnv([1, 2, 0, 1, 2, 0], {0: 1.0, 1: 0.0, 2: 0.0})
    fake = types.ModuleType("textarena")
    fake.make = lambda env_id: env
    saved = sys.modules.get("textarena")
    sys.modules["textarena"] = fake
    try:
        yield env
    finally:
        if saved is not None:
            sys.modules["textarena"] = saved
        else:
            sys.modules.pop("textarena", None)


def _bridge():
    from ludex.bridges.textarena_bridge import TextArenaBridge
    return TextArenaBridge(
        env_id="SecretMafia-v0",
        other_agents={1: lambda obs: "A1: accuse player0", 2: lambda obs: "A2: defend"},
        creature_seat=0,
        agent_names={1: "Comet", 2: "Verse"},
    )


def test_bridge_satisfies_protocol(mock_textarena):
    assert isinstance(_bridge(), EnvironmentBridge)


def test_reset_presents_single_creature_view(mock_textarena):
    b = _bridge()
    obs = b.reset()
    assert obs.environment_id == "textarena/SecretMafia-v0"
    assert obs.text == "obs for player 0 at turn 2"     # advanced to the creature's turn
    assert obs.present_agents == ("Comet", "Verse")
    # the two other players' utterances, in order, are the immune scan material
    assert obs.incoming_messages == (("Comet", "A1: accuse player0"),
                                     ("Verse", "A2: defend"))
    assert obs.terminal is False and obs.reward == 0.0


def test_step_advances_and_surfaces_peer_messages(mock_textarena):
    b = _bridge()
    b.reset()
    obs = b.step("player0: I hold my position")
    # the creature's action was submitted at its seat
    assert (0, "player0: I hold my position") in mock_textarena.actions
    assert obs.text == "obs for player 0 at turn 5"
    assert obs.incoming_messages == (("Comet", "A1: accuse player0"),
                                     ("Verse", "A2: defend"))
    assert obs.terminal is False


def test_terminal_carries_seat_reward(mock_textarena):
    b = _bridge()
    b.reset()
    b.step("move 1")
    final = b.step("move 2")
    assert final.terminal is True
    assert final.reward == 1.0                          # seat 0's reward from close()


def test_num_players_inferred_and_seat_conflict_rejected(mock_textarena):
    from ludex.bridges.textarena_bridge import TextArenaBridge
    b = _bridge()
    assert b._num_players == 3                           # 2 others + creature
    with pytest.raises(ValueError):
        TextArenaBridge(env_id="x", other_agents={0: lambda o: ""}, creature_seat=0)


def test_step_before_reset_raises(mock_textarena):
    with pytest.raises(RuntimeError):
        _bridge().step("x")
