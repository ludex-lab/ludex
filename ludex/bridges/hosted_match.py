"""Hosted match runner (web UX, Enter→Watch→Reflect) — drive a Ludex creature
through a CROSS-MACHINE LxM match on the deployed hosted server.

The creature runs LOCALLY via an ephemeral copy (D-090 — the live creature is
never mutated during play); the match is orchestrated by the hosted LxM server.
Because the deployed server is all-remote (no server-side bot), the runner drives
BOTH seats: the creature via its brain+organs through the broker, and the
opponent via a caller-supplied move function (a built-in scripted bot for v1;
creature-vs-creature is a later variant).

`kind` ("practice" | "published") is passed to the server for its storage policy
(practice = 24h Redis; published = permanent static export + viewable). The
Ludex-side distilled-writeback for `published` (회고 + bond to the live creature)
is a separate step — this runner stays pure-ephemeral.

Returns {match_id, status, result, viewer_url, turns}. `on_turn(rec)` streams
progress for the Watch view.
"""
from __future__ import annotations

from ludex.bridges.broker_lxm_bridge import (
    BrokerLxMBridge, _UrllibTransport, _parse_move_envelope, _extract_field, MatchError)
from ludex.bridges.creature_player import _engage_perception, _RETRY_NUDGE
from ludex.core.integrity import ephemeral_creature

ONRENDER = "https://lxm-api.onrender.com"
VIEWER = "https://jihoonjeong.github.io/ludus-ex-machina/viewer/#/match/{id}"

_INLINE = "Reply with ONLY your move JSON inline in your response. Do not write files.\n\n"


def play_hosted_match(creature_path, opponent_move, *, game, base_url=ONRENDER,
                      kind="practice", my_id=None, opp_id="house",
                      on_turn=None, action_retries=2, max_turns=60, should_stop=None):
    """Drive `creature_path` (ephemeral) vs `opponent_move` in a hosted `game`
    match on `base_url`. `opponent_move(payload) -> move dict` is the opponent's
    policy. Returns the result envelope + the viewer deep-link."""
    t = _UrllibTransport(base_url, timeout=120)
    with ephemeral_creature(creature_path) as cfg:
        org = cfg.build()
        engine = org.get_block("engine")
        if engine is None:
            raise ValueError("creature has no engine; cannot play")
        me = my_id or (getattr(org, "name", None) or "creature").lower()
        mapper = BrokerLxMBridge(me, transport=t, game=game)   # reuse payload->Observation
        view = t.post("/api/matches", {
            "game": game, "kind": kind,
            "participants": [{"id": me, "kind": "remote", "display": me.title()},
                             {"id": opp_id, "kind": "remote", "display": opp_id.title()}],
            "config": {"max_turns": max_turns}})
        mid = view["match_id"]
        turns = 0
        for _ in range(max_turns * 2 + 8):
            if should_stop and should_stop():
                break
            st = t.get(f"/api/matches/{mid}/state")
            if st.get("status") == "complete":
                break
            who, turn = st.get("to_move"), st.get("to_move_turn")
            if turn is None:
                break
            payload = t.get(f"/api/matches/{mid}/turns/{turn}")
            if who == me:
                obs = mapper._obs_from_payload(payload)
                _engage_perception(org, obs)             # immune + humoral engage on the encounter
                move, dlg, resp = None, None, ""
                for k in range(action_retries + 1):
                    prompt = obs.text if k == 0 else obs.text + _RETRY_NUDGE
                    resp = (getattr(engine.handle_submit(_INLINE + prompt), "response", "") or "").strip()
                    try:
                        move = _parse_move_envelope(resp)
                        dlg = _extract_field(resp, "dialogue")
                        break
                    except MatchError:
                        continue
                if move is None:
                    move = _legal_fallback(payload)
                body = {"move": move}
                if dlg:
                    body["dialogue"] = dlg
                t.post(f"/api/matches/{mid}/turns/{turn}/move", body)
                turns += 1
                if on_turn:
                    on_turn({"turn": turn, "who": me, "move": move, "dialogue": dlg,
                             "readable": payload.get("state_readable")})
            else:
                mv = opponent_move(payload)
                body = mv if (isinstance(mv, dict) and "move" in mv) else {"move": mv}
                t.post(f"/api/matches/{mid}/turns/{turn}/move", body)
                if on_turn:
                    on_turn({"turn": turn, "who": opp_id, "move": body.get("move"),
                             "dialogue": body.get("dialogue")})
        final = t.get(f"/api/matches/{mid}/state")
    return {"match_id": mid, "status": final.get("status"), "result": final.get("result"),
            "viewer_url": VIEWER.format(id=mid), "turns": turns}


def _legal_fallback(payload):
    """A safe move when the creature's reply could not be parsed after retries —
    the first declared legal move, else a benign game default."""
    legal = payload.get("legal_moves")
    if isinstance(legal, list) and legal:
        return legal[0] if isinstance(legal[0], dict) else {"move": legal[0]}
    state = payload.get("state") or {}
    board = (state.get("game", {}).get("current", {}) or state).get("board")
    if isinstance(board, list):                                  # tic-tac-toe etc.
        for r, row in enumerate(board):
            for c, cell in enumerate(row):
                if cell is None:
                    return {"type": "place", "position": [r, c]}
    return {"type": "choice", "action": "cooperate"}             # trustgame-style default


# ---- built-in opponent policies (v1 "house" bots) ----

def first_empty_bot(payload):
    """Tic-tac-toe: first empty cell."""
    state = payload.get("state") or {}
    board = (state.get("game", {}).get("current", {}) or state).get("board") or \
            [[None] * 3 for _ in range(3)]
    for r in range(3):
        for c in range(3):
            if board[r][c] is None:
                return {"type": "place", "position": [r, c]}
    return {"type": "place", "position": [0, 0]}


def tit_for_tat_bot(payload):
    """Trust game: cooperate first, then mirror the opponent's last action."""
    last = payload.get("opponent_actions") or []
    if last:
        mv = last[-1].get("move", {}) if isinstance(last[-1], dict) else {}
        act = (mv.get("action") or "cooperate").lower()
        return {"type": "choice", "action": "defect" if act == "defect" else "cooperate",
                "dialogue": "I mirror what you bring."}
    return {"type": "choice", "action": "cooperate", "dialogue": "I'll open with trust."}


HOUSE_BOTS = {
    "tictactoe": first_empty_bot,
    "trustgame": tit_for_tat_bot,
}
