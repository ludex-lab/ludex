"""Controlled experiment harness (D-089 Decision 4).

The question made falsifiable: *do the organs change how an LLM plays a
game, measurably?* Run the SAME games with two arms —

  - treatment: a full creature (memory, physis, allos, immune, ToM, …)
  - control:   the same brain + harness with the organs stripped off

— so the organ set is the only variable. Both arms are the SAME function
(``creature_player.play_episode``): a control Organism simply has no
organs to engage.

Each arm's Organism is REUSED across games, so a treatment creature's
organs *accumulate* (physis world models, bonds, immune antibodies)
while the stripped control stays flat — the accumulation curve is the
DV that distinguishes "a creature that learns" from "an agent that
re-rolls each match" (D-089 §4).

This module is the orchestration only — it makes no brain calls itself;
``play_episode`` does, once per turn. A real run is therefore brain-heavy
(N games × 2 arms × ~turns), so size N deliberately.
"""
from __future__ import annotations

from ludex.bridges.creature_player import play_episode


def strip_to_bare(config):
    """Disable every organ except the engine on a loaded OrganismConfig —
    the control arm. Identity/brain are held constant (the engine and its
    system prompt remain); only the *active* organs (memory recall,
    physis, immune, allos, …) are removed, isolating their contribution.

    (For a fully bare agent with no identity either, build a fresh
    brain-only Organism with a temporary habitat instead — a stronger
    control that also drops the SELF floor.)
    """
    for name in list(config.organs.keys()):
        if name != "engine":
            config.organs[name] = {**(config.organs.get(name) or {}), "enabled": False}
    return config


def run_arm(organism, make_bridge, n_games: int, *, prompt_prefix: str = "",
            max_steps: int = 100) -> list[dict]:
    """Play `organism` through `n_games`, a fresh bridge each game. The
    organism is reused, so its organs accumulate across games."""
    return [
        play_episode(organism, make_bridge(), max_steps=max_steps,
                     prompt_prefix=prompt_prefix)
        for _ in range(n_games)
    ]


def run_controlled(treatment, control, make_bridge, n_games: int, *,
                   prompt_prefix: str = "", max_steps: int = 100) -> dict:
    """Run both arms over the same number of games. Returns
    {"treatment": [results], "control": [results]} — per-game dicts from
    play_episode (field, reward, turns, present_agents)."""
    return {
        "treatment": run_arm(treatment, make_bridge, n_games,
                             prompt_prefix=prompt_prefix, max_steps=max_steps),
        "control": run_arm(control, make_bridge, n_games,
                           prompt_prefix=prompt_prefix, max_steps=max_steps),
    }


def summarize(results: dict) -> dict:
    """Reward trajectory + mean per arm — the accumulation-curve material.
    A meaningful verdict needs many games (TrueSkill-scale); this is the
    per-arm shape the run produces."""
    def arm(games):
        rewards = [g.get("reward", 0.0) for g in games]
        return {
            "n": len(rewards),
            "rewards": rewards,
            "mean": sum(rewards) / len(rewards) if rewards else 0.0,
            "final": rewards[-1] if rewards else 0.0,
        }
    return {k: arm(v) for k, v in results.items()}
