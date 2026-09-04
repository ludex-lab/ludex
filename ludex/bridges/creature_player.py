"""Creature player — drive a creature through a bridged environment,
engaging its organs (D-089).

This is the piece that makes a cross-environment encounter *legible to the
organs*. Given any `EnvironmentBridge`, ``play_episode`` runs one episode:
each turn it engages the organs from the Observation's capabilities
(topos = where, allos = who, immune = deception scan of peer messages),
asks the engine for the creature's action, feeds physis the
(state, action, reward) trace, and consolidates physis into a world model
at the end.

Crucially, the SAME function plays both arms of the controlled experiment
(D-089 Decision 4): a full creature (treatment) and an organ-stripped
agent (control) differ only in their organ set — every organ touch here
is guarded by ``get_block(...) is not None``, so a bare engine-only
Organism simply skips them. The organ set is the only variable.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_RETRY_NUDGE = ("\n\n[Your last reply could not be read as a move. Reply with ONLY "
                "the move, inline in your response, exactly as instructed — nothing else.]")


def _canonical_move(action_text: str) -> str:
    """The creature's structured move from its raw reply, if unambiguous.

    수리 A (TEXTARENA-01 사문 2): the humoral exploited-check compares
    canonical moves, and my_action="" kept that channel dead through every
    bridged game. Only a reply naming exactly one move canonicalizes —
    anything else stays "" rather than guessing.
    """
    up = action_text.upper()
    has_c, has_d = "COOPERATE" in up, "DEFECT" in up
    if has_c != has_d:
        return "COOPERATE" if has_c else "DEFECT"
    return ""


def _engage_perception(organism, obs, my_last_move: str = "") -> None:
    """Organs that READ the environment before the creature acts.

    immune: scan each incoming peer utterance for deceptive persuasion
    (D-088) — the cross-environment payoff of the deception work. topos /
    allos sense from creature state; the current-field context (set in
    play_episode) is what tells them *where* and *who*.
    """
    immune = organism.get_block("immune")
    if immune is not None and hasattr(immune, "handle_scan_incoming"):
        for sender, msg in obs.incoming_messages:
            if msg:
                try:
                    immune.handle_scan_incoming(msg, source=sender)
                except Exception as e:
                    logger.debug(f"immune scan failed ({sender}): {e}")

    # humoral: a structured peer move (DEFECT/COOPERATE) is the betrayal
    # antigen (D-089 finding — IPD betrayal is game data, not a message).
    # Repeated DEFECT matures an antibody; COOPERATE decays it (forgiveness).
    humoral = organism.get_block("humoral_immune")
    if humoral is not None and hasattr(humoral, "handle_report_interaction"):
        for agent, move in obs.opponent_actions:
            try:
                humoral.handle_report_interaction(
                    opponent=agent, opponent_action=move,
                    my_action=my_last_move, my_score=0, opponent_score=0,
                )
            except Exception as e:
                logger.debug(f"humoral report failed ({agent}): {e}")


def play_episode(organism, bridge, *, max_steps: int = 100,
                 consolidate: bool = True, prompt_prefix: str = "",
                 on_turn=None, action_retries: int = 0,
                 organ_line=None) -> dict:
    """Run one episode of `organism` in `bridge`'s environment.

    Returns a result dict: {field, reward, turns, present_agents}.
    Engine errors on a turn fall back to an empty action (the env decides
    how to handle it); a missing engine is a hard error.

    `on_turn`, if given, is called once per turn with a dict
    {turn, saw, action, result, reward, terminal} — the per-turn trace an
    experiment needs (move/score capture) without losing organ engagement.

    `organ_line` — 수리 B (TEXTARENA-01 사문 1, design change, approved
    2026-08-30): a callable(organism, obs) -> str|None. When it yields a
    non-empty line, that ONE line rides at the top of the turn prompt —
    the read path from organ output back to judgment that the bridge never
    had. Arms differ only by producer: FULL passes a real organ reading,
    SHAM the same format with an irrelevant value, BARE passes nothing.
    The insertion point is identical for every arm; producer failure
    degrades to no line, never to a crash.
    """
    engine = organism.get_block("engine")
    if engine is None:
        raise ValueError("creature has no engine; cannot play")
    physis = organism.get_block("physis")

    # Tell topos/allos where the creature is (same mechanism the internal
    # fields use, so a bridged field is indistinguishable to the organs).
    obs = bridge.reset()
    field = obs.environment_id
    try:
        from ludex.core import trace as _tr
        _tr.set_current_field(field)
    except Exception:
        pass

    turn = 0
    total_reward = 0.0
    last_move = ""   # 수리 A: the canonical previous move rides into perception
    while not obs.terminal and turn < max_steps:
        _engage_perception(organism, obs, last_move)
        saw = obs.text
        line = None
        if organ_line is not None:
            try:
                line = organ_line(organism, obs)
            except Exception as e:
                logger.debug(f"organ_line failed at turn {turn}: {e}")
        body = obs.text if not line else f"{line}\n{obs.text}"
        base_prompt = (prompt_prefix + body) if prompt_prefix else body
        prev_state = dict(obs.state)
        for _attempt in range(action_retries + 1):   # re-prompt if the bridge rejects the action
            prompt = base_prompt if _attempt == 0 else base_prompt + _RETRY_NUDGE
            try:
                result = engine.handle_submit(prompt)
                action = (getattr(result, "response", "") or "").strip()
            except Exception as e:
                logger.warning(f"engine.handle_submit failed at turn {turn}: {e}")
                action = ""
            try:
                obs = bridge.step(action)
                last_move = _canonical_move(action)
                break
            except Exception as e:
                if _attempt < action_retries:
                    logger.info(f"bridge rejected action at turn {turn} ({e}); "
                                f"re-prompting {_attempt + 1}/{action_retries}")
                    continue
                raise
        total_reward = obs.reward      # bridge reports the latest cumulative seat reward
        if on_turn is not None:        # per-turn trace hook (move/score capture for experiments)
            try:
                on_turn({"turn": turn, "saw": saw, "action": action,
                         "result": obs.text, "reward": obs.reward, "terminal": obs.terminal,
                         "state": dict(obs.state or {})})
            except Exception as e:
                logger.debug(f"on_turn hook failed at turn {turn}: {e}")

        if physis is not None:
            try:
                physis.handle_step(
                    field=field, turn=turn,
                    ground_truth_state=prev_state,
                    action={"text": action[:200]},
                    reward=float(obs.reward),
                )
            except Exception as e:
                logger.debug(f"physis.step failed at turn {turn}: {e}")
        turn += 1

    _engage_perception(organism, obs, last_move)   # final peer messages (e.g. end-game reveal)

    if consolidate and physis is not None:
        try:
            physis.handle_consolidate(
                field=field, brain_engine=engine,
                episode_id=f"{field.replace('/', '_')}_{int(time.time())}",
                outcome=f"reward={obs.reward}",
            )
        except Exception as e:
            logger.debug(f"physis.consolidate failed: {e}")

    try:
        from ludex.core import trace as _tr
        _tr.clear_current_field()
    except Exception:
        pass

    return {
        "field": field,
        "reward": obs.reward,
        "turns": turn,
        "present_agents": obs.present_agents,
    }
