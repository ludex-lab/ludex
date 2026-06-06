"""Per-game adapters for the post-match consolidation pipeline.

Each game module exports a pair-extraction function and a
phrase-generation function with a consistent signature:

    extract_pair_summary(log, state, my_id, other_id) -> PairSummary
    phrase_for(summary, match_id) -> str

The consolidation pipeline (`ludex.core.post_match_consolidation`)
picks the right adapter based on the `game` field in the match
directory's `result.json`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class PairSummary:
    """Game-agnostic pair-level summary between two creatures in a match.

    Game-specific metrics live under `metrics`. `my_role` / `their_role`
    are game-specific strings (e.g. "evil" / "good" for Avalon, "trustee"
    / "truster" for Trust Game). Roles come from the match's final
    state, not from any creature's in-game reasoning.
    """
    game: str
    match_id: str
    my_id: str
    other_id: str
    my_role: str
    their_role: str
    metrics: Mapping[str, int] = field(default_factory=dict)


# Registry of game → adapter module. Populated lazily on first import
# so modules don't have to load until used.
_REGISTRY: dict[str, object] = {}


def register(game: str, module: object) -> None:
    """Register a game adapter module. Module must expose
    `extract_pair_summary` and `phrase_for`.
    """
    _REGISTRY[game] = module


def get(game: str) -> object:
    """Return the registered adapter module for `game`. Raises KeyError
    with a message listing registered games if unknown.
    """
    if game not in _REGISTRY:
        known = sorted(_REGISTRY.keys())
        raise KeyError(
            f"No consolidation adapter registered for game {game!r}. "
            f"Registered games: {known or '(none)'}."
        )
    return _REGISTRY[game]


# Eager registration of the Avalon adapter. Other game adapters follow
# the same pattern.
from ludex.core.game_adapters import avalon as _avalon  # noqa: E402

register("avalon", _avalon)
