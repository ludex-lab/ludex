"""Environment Bridge — the environment-agnostic contract (D-089).

A *bridge* connects a creature to an external environment (LxM, TextArena,
an open web game, a benchmark, …) and exposes a uniform set of
**capabilities**. Organs consume capabilities, never a specific
environment; each organ activates only on the capabilities a given
environment exposes — that is the generality.

Principle (D-089): organs are general, bridges are specific, no organ
ever depends on a specific environment. See
``docs/cross-environment-bridge-design.md``.

The contract is Gym-shaped (``reset`` / ``step`` / ``close``) so that
standard environments (TextArena's OpenAI-Gym-style interface) map
directly, while LxM's CLI+file protocol is wrapped to the same shape
inside its bridge. This is the v1 surface; concrete bridges (LxM,
TextArena) may surface refinements.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ============================================================
# Capabilities — what an environment may expose, and which organ reads it
# ============================================================
# environment_id (→ topos) and submit_action (→ engine) are ALWAYS present,
# so they are not optional capabilities. These four are.
CAP_AGENTS = "present_agents"          # → allos / ToM / bonds (who else is here)
CAP_REWARD = "state_action_reward"     # → physis (how this field works)
CAP_MESSAGES = "incoming_messages"     # → immune (deception scan of others)
CAP_PREDICT = "prediction_targets"     # → ToM (predict an agent → verify)
CAP_OPP_ACTIONS = "opponent_actions"   # → humoral immune (betrayal antigen)

ALL_CAPABILITIES = frozenset({
    CAP_AGENTS, CAP_REWARD, CAP_MESSAGES, CAP_PREDICT, CAP_OPP_ACTIONS,
})


@dataclass
class Observation:
    """One environment observation handed to the creature.

    `environment_id` and `text` are always populated. The rest carry the
    optional capabilities — empty when the environment does not expose
    them, so an organ reading an unexposed capability simply finds nothing
    and stays quiet (no environment is ever assumed).
    """
    environment_id: str                       # "<env>/<field>", e.g. "textarena/SecretMafia"
    text: str                                 # the prompt-facing observation
    # CAP_AGENTS — others present in the environment
    present_agents: tuple[str, ...] = ()
    # CAP_MESSAGES — (from_agent, message_text) since the last step
    incoming_messages: tuple[tuple[str, str], ...] = ()
    # CAP_REWARD — physis trace material
    state: dict = field(default_factory=dict)
    reward: float = 0.0
    # CAP_OPP_ACTIONS — other players' STRUCTURED moves since the last step,
    # as (agent_label, normalized_token) e.g. ("Defector", "DEFECT"). The
    # humoral immune's betrayal antigen: a structured move, not a message.
    # A bridge populates this only for games with a parseable move grammar.
    opponent_actions: tuple[tuple[str, str], ...] = ()
    # episode flow
    terminal: bool = False
    info: dict = field(default_factory=dict)


@runtime_checkable
class EnvironmentBridge(Protocol):
    """The environment-agnostic contract every bridge implements (D-089).

    Attributes
    ----------
    environment : str
        The environment namespace, e.g. ``"textarena"`` or ``"lxm"``.
    capabilities : frozenset[str]
        Which optional CAP_* this environment exposes (a subset of
        ALL_CAPABILITIES). Organs activate on what is present.

    Methods
    -------
    reset() -> Observation
        Begin an episode; return the first observation.
    step(action_text) -> Observation
        Submit the creature's text action; return the next observation.
    close() -> None
        Tear down the connection.
    """
    environment: str
    capabilities: frozenset

    def reset(self) -> Observation: ...
    def step(self, action_text: str) -> Observation: ...
    def close(self) -> None: ...


def organs_for(capabilities: frozenset) -> dict[str, str]:
    """Map a bridge's exposed capabilities to the organs that will engage.
    Pure helper for field harnesses + introspection; topos and the engine
    are always engaged, so they are not listed here."""
    table = {
        CAP_AGENTS: "allos",
        CAP_REWARD: "physis",
        CAP_MESSAGES: "immune",
        CAP_PREDICT: "tom",
        CAP_OPP_ACTIONS: "humoral_immune",
    }
    return {cap: organ for cap, organ in table.items() if cap in capabilities}
