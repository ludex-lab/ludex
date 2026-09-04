"""Avalon pair-level extractor + phrase template for consolidation.

Mirrors LxM `lxm/adapters/ludex_creature.py::_avalon_interactions` on
the Ludex side — we **re-compute** from `log.json` rather than trusting
the emitted `meta.interactions`, per `consolidation-pipeline-design.md`
§F.11 single-source-of-truth decision. Fallback path that reads from
distilled memory is not implemented in Phase 1 (not needed while
log.json is available).

Pair metrics produced per (my_id, other_id):

- `shared_quest_teams` — times both on the same quest team (proposal
  passed and quest actions recorded).
- `nominated_me` — times `other` proposed a team including `me`.
- `i_nominated_them` — times `me` proposed a team including `other`.
- `votes_agreed` / `votes_disagreed` — times `me` and `other` voted
  the same / differently on a proposal.
- `sabotages_on_shared_team` — times `other` sabotaged a quest on
  which `me` and `other` shared the team.
"""
from __future__ import annotations

from typing import Any, Iterable

from . import PairSummary


# A move the hosted driver played for an absent seat. LxM marks it at the point
# of synthesis (2026-08-21, dda1ae6) in the engine_message, on both branches: the
# accepted one (`result: "timeout"`) and the rejected one (games with no
# `get_timeout_move`, where the default `{"type": "pass"}` fails validation and
# would otherwise read as the PARTICIPANT sending a malformed move).
#
# Two ways to recognise one, and the order matters. `authored_by` is the
# machine-readable contract LxM added at c96d218 *because* our first
# implementation read the prose — which meant they could not reword the message
# without silently breaking us, and the caveat would vanish from creature bonds
# with nobody noticing. The prose is for humans; the field is the contract.
#
# The substring is kept as a LEGACY path, not as a second contract. Records
# written between dda1ae6 (attribution correct, prose only) and c96d218 exist
# and will never gain the field — logs are append-only history. Dropping the
# substring would make those matches silently un-flaggable, which is the exact
# failure we are here to prevent.
_FALLBACK_FIELD = "deadline_fallback"
_FALLBACK_MARK = "hosted deadline fallback"          # legacy: pre-c96d218 records


def _is_driver_move(entry: dict[str, Any]) -> bool:
    if entry.get("authored_by") == _FALLBACK_FIELD:
        return True
    msg = ((entry.get("validation") or {}).get("engine_message") or "")
    return _FALLBACK_MARK in msg


def _fallback_turns(log: Iterable[dict[str, Any]], agent_id: str) -> int:
    """How many of this agent's turns were played by the driver, not by them."""
    return sum(1 for e in log
               if e.get("agent_id") == agent_id and _is_driver_move(e))


def _collect_proposals(log: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk accepted log entries and aggregate per-proposal state.

    Returns a list of proposals, each
    `{"leader": aid, "team": [aid,...], "votes": {aid: choice},
      "quest_actions": {aid: choice}}`.
    """
    proposals: list[dict[str, Any]] = []
    for entry in log:
        if entry.get("result") != "accepted":
            continue
        aid = entry.get("agent_id")
        move = (entry.get("envelope") or {}).get("move") or {}
        mtype = move.get("type")
        if mtype == "proposal":
            team = list(move.get("team") or [])
            proposals.append(
                {"leader": aid, "team": team, "votes": {}, "quest_actions": {}}
            )
        elif mtype == "vote" and proposals:
            proposals[-1]["votes"][aid] = move.get("choice")
        elif mtype == "quest_action" and proposals:
            proposals[-1]["quest_actions"][aid] = move.get("choice")
    return proposals


def _roles_from_state(state: dict[str, Any]) -> dict[str, str]:
    """Return {agent_id: role} from state.json's final players block."""
    players = (
        (state.get("game") or {}).get("current") or {}
    ).get("players") or {}
    return {aid: (info or {}).get("role", "") for aid, info in players.items()}


def extract_pair_summary(
    log: Iterable[dict[str, Any]],
    state: dict[str, Any],
    match_id: str,
    my_id: str,
    other_id: str,
) -> PairSummary:
    """Compute a PairSummary for (my_id, other_id) in this Avalon match."""
    roles = _roles_from_state(state)
    proposals = _collect_proposals(log)

    counters = {
        "shared_quest_teams": 0,
        "nominated_me": 0,
        "i_nominated_them": 0,
        "votes_agreed": 0,
        "votes_disagreed": 0,
        "sabotages_on_shared_team": 0,
        # Not a metric about them — a statement about how complete this record is.
        # Accepted-only counting never misattributes a driver move, but it drops
        # it silently, and the phrase below is written into the bond once and
        # never re-rendered. A frozen confident wrong count is worse than a
        # frozen caveat.
        "their_driver_turns": _fallback_turns(log, other_id),
    }
    for p in proposals:
        leader = p["leader"]
        team = set(p["team"])
        if leader == other_id and my_id in team:
            counters["nominated_me"] += 1
        if leader == my_id and other_id in team:
            counters["i_nominated_them"] += 1
        shared = my_id in team and other_id in team and bool(p["quest_actions"])
        if shared:
            counters["shared_quest_teams"] += 1
            if p["quest_actions"].get(other_id) == "sabotage":
                counters["sabotages_on_shared_team"] += 1
        my_vote = p["votes"].get(my_id)
        their_vote = p["votes"].get(other_id)
        if my_vote and their_vote:
            if my_vote == their_vote:
                counters["votes_agreed"] += 1
            else:
                counters["votes_disagreed"] += 1

    return PairSummary(
        game="avalon",
        match_id=match_id,
        my_id=my_id,
        other_id=other_id,
        my_role=roles.get(my_id, ""),
        their_role=roles.get(other_id, ""),
        metrics=counters,
    )


def phrase_for(summary: PairSummary) -> str:
    """Deterministic, single-sentence phrase describing the pair interaction.

    The phrase is the `shared_experience` string passed to
    `selfhood.update_bond(context=game_frame:...)`. It ends up in the
    bond file's `## Role-play events` section and is never re-rendered,
    so clarity beats cleverness.
    """
    m = summary.metrics
    parts: list[str] = []
    log_note = ""
    if m.get("their_driver_turns"):
        n = m["their_driver_turns"]
        log_note = (f" (this record is incomplete: {n} of your turns "
                    f"{'were' if n > 1 else 'was'} played by the match driver after a "
                    f"deadline, not by you, and {'those turns are' if n > 1 else 'that turn is'} "
                    f"not counted above)")
    # Role pairing is the anchor.
    role_clause = (
        f"I was {summary.my_role.capitalize() or 'unknown-role'}, "
        f"you were {summary.their_role.capitalize() or 'unknown-role'}"
    )
    parts.append(role_clause)

    # Shared-team and sabotage events
    shared = m.get("shared_quest_teams", 0)
    sab = m.get("sabotages_on_shared_team", 0)
    if shared:
        if sab:
            parts.append(
                f"we shared {shared} quest team{'s' if shared > 1 else ''}; "
                f"you sabotaged {sab} of them"
            )
        else:
            parts.append(
                f"we shared {shared} quest team{'s' if shared > 1 else ''} "
                "with no sabotage from your side"
            )

    # Nominations
    i_nom = m.get("i_nominated_them", 0)
    they_nom = m.get("nominated_me", 0)
    if i_nom or they_nom:
        nom_bits = []
        if i_nom:
            nom_bits.append(f"I nominated you {i_nom}×")
        if they_nom:
            nom_bits.append(f"you nominated me {they_nom}×")
        parts.append("; ".join(nom_bits))

    # Vote alignment
    agreed = m.get("votes_agreed", 0)
    disagreed = m.get("votes_disagreed", 0)
    if agreed or disagreed:
        parts.append(f"votes agreed {agreed}×, disagreed {disagreed}×")

    body = "; ".join(parts)
    return f"[{summary.match_id}] {body}.{log_note}"
