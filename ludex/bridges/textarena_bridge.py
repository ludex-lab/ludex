"""TextArena bridge (D-089, public bridge #1).

Wraps TextArena — an open-source collection of 57+ competitive text games
for LLM agents, with online play and TrueSkill ratings
(github.com/LeonGuertler/TextArena) — to the Environment Bridge contract.

TextArena's native loop is multi-agent over a single env: you poll
``get_observation()`` for whichever player must act next, and ``step()``
advances. This bridge **absorbs that loop** and presents a *single
creature's* view (reset/step → Observation): when it is the creature's
turn it returns the creature's observation; between the creature's turns
the other players act, and their utterances are surfaced as
``incoming_messages`` (→ immune deception scan) with the others as
``present_agents`` (→ allos / ToM / bonds).

Verified TextArena local API (README):
    env = ta.make(env_id="SecretMafia-v0")
    env.reset(num_players=N)
    player_id, observation = env.get_observation()   # whose turn + text
    done, step_info = env.step(action=action_text)
    rewards, game_info = env.close()                  # rewards: {player_id: r}
An agent is any callable: observation_text -> action_text.

`textarena` is an OPTIONAL dependency (not in core requirements). This
module lazy-imports it; install with ``pip install textarena`` to play.
LIVE-VALIDATED 2026-06-13 against textarena 0.7.4: the real
get_observation→(pid,obs) / step→(done,info) / close→(rewards,info) API
matches; a full IteratedPrisonersDilemma-v0 game ran end-to-end through
this bridge (10 rounds, seat reward returned). Online matchmaking
(`ta.make_online`) and fine-grained per-sender message parsing are the
next pieces — local play first (where the controlled experiment lives).
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from ludex.core.environment_bridge import (
    Observation, CAP_AGENTS, CAP_REWARD, CAP_MESSAGES, CAP_PREDICT,
)

logger = logging.getLogger(__name__)

# A non-creature seat is any callable: observation text -> action text.
AgentFn = Callable[[str], str]


class TextArenaBridge:
    """A single creature's bridge into one TextArena game (local play).

    Implements the EnvironmentBridge Protocol (reset / step / close).
    """

    environment = "textarena"
    # TextArena social games expose the full suite; the engine always acts.
    capabilities = frozenset({CAP_AGENTS, CAP_REWARD, CAP_MESSAGES, CAP_PREDICT})

    def __init__(
        self,
        env_id: str,
        other_agents: dict[int, AgentFn],
        creature_seat: int = 0,
        agent_names: Optional[dict[int, str]] = None,
    ):
        """
        env_id: TextArena env id, e.g. "SecretMafia-v0".
        other_agents: {player_id: agent_callable} for every NON-creature
            seat. num_players is inferred as len(other_agents) + 1.
        creature_seat: the player_id the creature occupies.
        agent_names: optional {player_id: display_name} for present_agents
            / incoming_messages labels (defaults to "player{id}").
        """
        self.env_id = env_id
        self.creature_seat = creature_seat
        self._others = dict(other_agents)
        if creature_seat in self._others:
            raise ValueError(f"creature_seat {creature_seat} also has an other_agent")
        self._num_players = len(self._others) + 1
        self._names = agent_names or {}
        self._env = None
        self._closed = False
        self._final_rewards: dict | None = None

    # --- helpers ---

    def _label(self, pid: int) -> str:
        return self._names.get(pid, f"player{pid}")

    def _present_agents(self) -> tuple[str, ...]:
        return tuple(self._label(pid) for pid in sorted(self._others))

    def _advance_to_creature(self) -> tuple[str, tuple[tuple[str, str], ...], bool]:
        """Run other players until it is the creature's turn (or the game
        ends). Returns (creature_observation, incoming_messages, terminal).
        incoming_messages = the other players' actions since the last
        creature turn, as (label, text) — the immune scan's material.
        """
        incoming: list[tuple[str, str]] = []
        while True:
            pid, observation = self._env.get_observation()
            if pid == self.creature_seat:
                return observation, tuple(incoming), False
            agent = self._others.get(pid)
            action = agent(observation) if agent else ""
            incoming.append((self._label(pid), action))
            done, _info = self._env.step(action=action)
            if done:
                self._finish()
                return "", tuple(incoming), True

    def _finish(self) -> None:
        if not self._closed:
            try:
                self._final_rewards, _game_info = self._env.close()
            except Exception as e:
                logger.debug(f"textarena close failed: {e}")
                self._final_rewards = {}
            self._closed = True

    def _reward(self) -> float:
        if not self._final_rewards:
            return 0.0
        try:
            return float(self._final_rewards.get(self.creature_seat, 0.0))
        except Exception:
            return 0.0

    # --- EnvironmentBridge contract ---

    def reset(self) -> Observation:
        import textarena as ta          # lazy: optional dependency
        self._env = ta.make(env_id=self.env_id)
        self._env.reset(num_players=self._num_players)
        self._closed = False
        self._final_rewards = None
        obs_text, incoming, terminal = self._advance_to_creature()
        return Observation(
            environment_id=f"textarena/{self.env_id}",
            text=obs_text,
            present_agents=self._present_agents(),
            incoming_messages=incoming,
            state={"env": self.env_id, "seat": self.creature_seat},
            reward=self._reward() if terminal else 0.0,
            terminal=terminal,
        )

    def step(self, action_text: str) -> Observation:
        if self._env is None:
            raise RuntimeError("TextArenaBridge.step called before reset()")
        if self._closed:
            raise RuntimeError("TextArenaBridge.step called after the game ended")
        done, _info = self._env.step(action=action_text)
        if done:
            self._finish()
            return Observation(
                environment_id=f"textarena/{self.env_id}",
                text="", present_agents=self._present_agents(),
                state={"env": self.env_id, "seat": self.creature_seat},
                reward=self._reward(), terminal=True,
            )
        obs_text, incoming, terminal = self._advance_to_creature()
        return Observation(
            environment_id=f"textarena/{self.env_id}",
            text=obs_text,
            present_agents=self._present_agents(),
            incoming_messages=incoming,
            state={"env": self.env_id, "seat": self.creature_seat},
            reward=self._reward() if terminal else 0.0,
            terminal=terminal,
        )

    def close(self) -> None:
        if self._env is not None:
            self._finish()
