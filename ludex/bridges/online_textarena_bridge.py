"""Online TextArena bridge (D-089, public bridge #1 — online arm).

Wraps ``textarena.make_online``'s ``OnlineEnvWrapper`` into the
``EnvironmentBridge`` Observation contract, so ``creature_player.play_episode``
drives a creature through REAL online matches — remote human/model opponents,
server matchmaking, public TrueSkill.

Contrast with ``TextArenaBridge`` (local): there we drove scripted
``other_agents``; here the opponents are remote, so there are none to run.
The server hands us an observation only on our turn, and our seat
(``player_id``) is assigned by the server — we learn it from the first
observation. The reused ``play_episode`` loop still engages every organ the
Observation's capabilities imply (immune scan on opponent messages, humoral
betrayal antigens on bare moves, physis on reward), which is the point: the
controlled experiment measures the organs' contribution against the SAME
opponent pool a bare agent would face.
"""
from __future__ import annotations

from ludex.core.environment_bridge import (
    Observation, CAP_REWARD, CAP_MESSAGES, CAP_AGENTS, CAP_OPP_ACTIONS,
)
from ludex.bridges.textarena_bridge import _normalize_move


def _flatten_obs(obs):
    """Online observation -> (joined_text, [(sender, message), ...]).

    The wrapper gives either a string or a list of ``(sender_id, message)``
    entries. We keep the joined text (for the engine prompt) and the
    per-sender messages (so opponents' utterances can feed the immune scan).
    """
    if isinstance(obs, str):
        return obs, []
    parts, msgs = [], []
    for item in (obs or []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            sender, message = item[0], item[-1]
            parts.append(str(message))
            msgs.append((str(sender), str(message)))
        else:
            parts.append(str(item))
    return "\n".join(parts), msgs


class OnlineTextArenaBridge:
    """Single-creature view of one online match. Satisfies EnvironmentBridge."""

    capabilities = (CAP_REWARD, CAP_MESSAGES, CAP_AGENTS, CAP_OPP_ACTIONS)

    def __init__(self, env_ids, model_name, model_token, *, num_players=None):
        import textarena as ta
        self.environment = "+".join(env_ids) if isinstance(env_ids, list) else env_ids
        self._env = ta.make_online(
            env_id=env_ids, model_name=model_name, model_token=model_token,
        )
        self._num_players = num_players
        self._seat = None          # server-assigned, learned from first obs
        self._done = False
        self._closed = False

    @property
    def _eid(self):
        return f"textarena-online/{self.environment}"

    def reset(self):
        self._env.reset(num_players=self._num_players)
        self._done = False
        return self._observe()

    def step(self, action_text):
        if self._done:
            raise RuntimeError("step after terminal")
        done, info = self._env.step(action_text)
        if done:
            return self._terminal(info)
        return self._observe(info)

    def _observe(self, info=None):
        pid, obs = self._env.get_observation()
        if pid is None and not obs:          # server done / nothing valid
            return self._terminal(info)
        if pid is not None:
            self._seat = pid
        text, all_msgs = _flatten_obs(obs)
        seat = str(self._seat)
        incoming = [(s, m) for s, m in all_msgs if s != seat]   # opponents only
        opp = tuple((s, mv) for (s, raw) in incoming
                    for mv in (_normalize_move(raw),) if mv)
        present = tuple(sorted({s for s, _ in incoming}))
        return Observation(
            environment_id=self._eid, text=text, present_agents=present,
            incoming_messages=tuple(incoming), opponent_actions=opp,
            info=info or {},
        )

    def _terminal(self, info):
        self._done = True
        rewards = self._safe_close() or {}
        reward = 0.0
        if self._seat is not None:
            reward = float(rewards.get(self._seat, rewards.get(str(self._seat), 0.0)) or 0.0)
        return Observation(environment_id=self._eid, text="", reward=reward,
                           terminal=True, info=info or {})

    def _safe_close(self):
        if self._closed:
            return {}
        self._closed = True
        try:
            return self._env.close()
        except Exception:
            return {}

    def close(self):
        self._safe_close()
