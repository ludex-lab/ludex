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

import json
import os

from ludex.bridges.broker_lxm_bridge import (
    BrokerLxMBridge, _UrllibTransport, _parse_move_envelope, _extract_field, MatchError)
from ludex.bridges.creature_player import _engage_perception, _RETRY_NUDGE
from ludex.core.integrity import ephemeral_creature

ONRENDER = "https://lxm-api.onrender.com"
VIEWER = "https://jihoonjeong.github.io/ludus-ex-machina/viewer/#/match/{id}"

_INLINE = "Reply with ONLY your move JSON inline in your response. Do not write files.\n\n"
_ILLEGAL_NUDGE = ("\n\nYour previous move was REJECTED as illegal. Choose a DIFFERENT move that is "
                  "strictly legal in the current position (use the legal options listed above).")


def play_hosted_match(creature_path, opponent_move, *, game, base_url=ONRENDER,
                      kind="practice", my_id=None, opp_id="house",
                      on_turn=None, on_start=None, action_retries=2, max_turns=60, should_stop=None):
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
        if on_start:
            on_start(VIEWER.format(id=mid))      # surface the viewer link live, mid-match
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


# ---- B1: stable cross-machine creature identity ----

def register_lxm_creature(transport, display_name):
    """Register a creature with LxM → its opaque, server-issued, durable creature_id
    (B1, no TTL). Each POST mints a NEW id, so callers persist + reuse it — see
    get_or_register_lxm_id."""
    view = transport.post("/api/creatures", {"display_name": display_name})
    return view["creature_id"]


def get_or_register_lxm_id(creature_path, transport, display_name=None):
    """The creature's STABLE cross-machine identity (B1). Registered once with LxM and
    cached in <habitat>/lxm_identity.json, so the SAME creature surfaces under the same
    creature_id across every match — which is what lets another creature re-recognize it
    on a re-meeting. This is a one-time identity assignment (a passport), not match churn,
    so it is written to the live habitat directly (cf. D-090, which guards against raw
    match state, not identity)."""
    idfile = os.path.join(creature_path, "lxm_identity.json")
    if os.path.exists(idfile):
        try:
            return json.load(open(idfile))["creature_id"]
        except Exception:
            pass
    name = display_name or os.path.basename(str(creature_path).rstrip("/\\"))
    cid = register_lxm_creature(transport, name)
    try:
        with open(idfile, "w") as f:
            json.dump({"creature_id": cid, "display_name": name}, f, indent=2)
    except Exception as e:
        print(f"could not cache lxm identity for {name}: {e}")
    return cid


def play_creature_match(path_a, path_b, *, game, base_url=ONRENDER, kind="published",
                        on_turn=None, on_start=None, action_retries=2, max_turns=12, should_stop=None):
    """Two REAL creatures meet in a hosted cross-machine match — each plays via its own
    brain + organs (on an ephemeral copy, D-090), each carrying its stable B1 creature_id
    so it can be re-recognized on a re-meeting. The deployed server is all-remote, so this
    driver runs BOTH seats and fires _engage_perception for each, so both creatures' organs
    (humoral/immune) react to the OTHER. Returns the result, the viewer link, and each
    side's opponent creature_id (for the bond writeback)."""
    t = _UrllibTransport(base_url, timeout=120)
    # B1 identities come from the LIVE creatures (stable across matches); play on copies.
    id_a = get_or_register_lxm_id(path_a, t)
    id_b = get_or_register_lxm_id(path_b, t)
    name_a = os.path.basename(str(path_a).rstrip("/\\"))
    name_b = os.path.basename(str(path_b).rstrip("/\\"))
    ha, hb = name_a.lower(), name_b.lower()
    with ephemeral_creature(path_a) as cfg_a, ephemeral_creature(path_b) as cfg_b:
        org_a, org_b = cfg_a.build(), cfg_b.build()
        seats = {
            ha: {"org": org_a, "eng": org_a.get_block("engine"), "id": id_a, "name": name_a,
                 "mapper": BrokerLxMBridge(ha, transport=t, game=game)},
            hb: {"org": org_b, "eng": org_b.get_block("engine"), "id": id_b, "name": name_b,
                 "mapper": BrokerLxMBridge(hb, transport=t, game=game)},
        }
        view = t.post("/api/matches", {
            "game": game, "kind": kind,
            "participants": [
                {"id": ha, "kind": "remote", "creature_id": id_a, "display": name_a},
                {"id": hb, "kind": "remote", "creature_id": id_b, "display": name_b}],
            "config": {"max_turns": max_turns}})
        mid = view["match_id"]
        if on_start:
            on_start(VIEWER.format(id=mid))      # surface the viewer link live, mid-match
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
            seat = seats.get(who)
            if seat is None:
                break
            obs = seat["mapper"]._obs_from_payload(payload)
            _engage_perception(seat["org"], obs)        # BOTH creatures' organs fire on the encounter
            move, dlg, nudge = None, None, ""
            for k in range(action_retries + 1):
                resp = (getattr(seat["eng"].handle_submit(_INLINE + obs.text + nudge), "response", "") or "").strip()
                try:
                    move = _parse_move_envelope(resp)
                    dlg = _extract_field(resp, "dialogue")
                except MatchError:
                    nudge = _RETRY_NUDGE; continue            # unparseable → re-prompt
                body = {"move": move}
                if dlg:
                    body["dialogue"] = dlg
                try:
                    t.post(f"/api/matches/{mid}/turns/{turn}/move", body)
                    break                                      # accepted
                except MatchError as e:
                    if getattr(e, "code", "") == "illegal_move":
                        nudge = _ILLEGAL_NUDGE; continue       # illegal → re-prompt with feedback
                    raise
            else:                                              # all retries failed → safe legal fallback
                move = _legal_fallback(payload)
                t.post(f"/api/matches/{mid}/turns/{turn}/move", {"move": move})
            if on_turn:
                on_turn({"turn": turn, "who": who, "name": seat["name"], "move": move, "dialogue": dlg})
        final = t.get(f"/api/matches/{mid}/state")
    return {"match_id": mid, "status": final.get("status"), "result": final.get("result"),
            "viewer_url": VIEWER.format(id=mid),
            "creatures": {name_a: {"id": id_a, "opponent": name_b, "opponent_id": id_b},
                          name_b: {"id": id_b, "opponent": name_a, "opponent_id": id_a}}}


# ---- B1 re-recognition: creature_id-keyed bonds across machines ----

def _lxm_bonds_path(creature_path):
    return os.path.join(creature_path, "lxm_bonds.json")


def recognize(creature_path, opponent_creature_id):
    """Has this creature met `opponent_creature_id` before? Returns the prior-encounter
    record ({name, first_met, encounters, last_met}) if so (re-recognition), else None.
    Keys on the STABLE B1 creature_id, not the display name — that's the whole point of
    B1: the same mind is the same mind even if it shows a different name."""
    try:
        idx = json.load(open(_lxm_bonds_path(creature_path)))
    except Exception:
        return None
    return idx.get(opponent_creature_id)


def record_encounter(creature_path, opponent_name, opponent_creature_id, match_summary,
                     game, when, prior=None):
    """published only — the creature remembers the encounter (a reflection → SELF.md) AND,
    because the opponent is a real MIND with a stable B1 id, forms/deepens a bond toward it
    (bonds/<name>.md via selfhood.update_bond) keyed for re-recognition in
    lxm_bonds.json[creature_id]. `prior` is recognize()'s result (None on a first meeting);
    on a re-meeting it's woven into the reflection + the bond. Writes to the LIVE creature —
    intended accumulation, exactly as an internal field's aftermath does."""
    from ludex.core.organism_config import OrganismConfig
    from ludex.core import selfhood
    org = OrganismConfig.load(creature_path).build()
    engine = org.get_block("engine")
    gname = {"trustgame": "Trust Game", "tictactoe": "Tic-Tac-Toe"}.get(game, game)
    again = (f" You have met {opponent_name} before ({prior.get('encounters')}x) — this is the "
             f"SAME mind, re-met." if prior else "")
    context = (f"On Ludus ex Machina — a cross-machine arena where you meet minds from other "
               f"habitats — you played a {gname} against {opponent_name}, another creature."
               f"{again}\n{match_summary}")
    try:
        selfhood.reflect(org, "ludus_ex_machina", engine, context)
    except Exception as e:
        print(f"encounter reflect failed: {e}")
    try:
        selfhood.update_bond(org, opponent_name, engine=engine,
                             shared_experience=(f"You played a {gname} on Ludus ex Machina against "
                                                f"{opponent_name}, a fellow creature.{again} {match_summary}"))
    except Exception as e:
        print(f"encounter bond failed: {e}")
    # creature_id index → re-recognition on a re-meeting (B1)
    idxf = _lxm_bonds_path(creature_path)
    try:
        idx = json.load(open(idxf)) if os.path.exists(idxf) else {}
    except Exception:
        idx = {}
    entry = idx.get(opponent_creature_id) or {"name": opponent_name, "first_met": when, "encounters": 0}
    entry["name"] = opponent_name
    entry["encounters"] = entry.get("encounters", 0) + 1
    entry["last_met"] = when
    idx[opponent_creature_id] = entry
    with open(idxf, "w") as f:
        json.dump(idx, f, indent=2)
    return entry


# ---- N-creature: arbitrary seat count (avalon 5–10, codenames 4, solo, …) ----

def play_multi_creature_match(creature_paths, *, game, base_url=ONRENDER, kind="published",
                              on_turn=None, on_start=None, action_retries=2, max_turns=80,
                              should_stop=None):
    """N REAL creatures meet in a hosted cross-machine match — the N-seat generalization of
    play_creature_match (avalon 5–10, codenames 4, blockworld, deduction-solo, or any LxM
    game). The arena is always ONE seat per step (no simultaneous submit — avalon votes are
    serialized server-side), so a single poll-loop drives every seat:
        GET /state → to_move is one of our seats → GET /turns/{turn} → that creature's brain → POST.
    Each creature plays on an ephemeral copy (D-090) carrying its stable B1 id, so every
    co-participant is re-recognizable on a re-meeting. No per-game code: the turn prompt carries
    the move spec and the server validates. On give-up we post a benign fallback but NEVER crash
    the match — for games with no legal-move list (avalon/codenames) an invalid fallback is
    skipped and the server's lazy reaper (H2) advances that seat off the other seats' polling.
    Returns result + viewer + each creature's co-participants (for the multi-party writeback)."""
    import contextlib
    import time
    t = _UrllibTransport(base_url, timeout=120)
    paths = list(creature_paths)
    names = [os.path.basename(str(p).rstrip("/\\")) for p in paths]
    handles = [n.lower() for n in names]
    ids = [get_or_register_lxm_id(p, t) for p in paths]
    with contextlib.ExitStack() as stack:
        orgs = [stack.enter_context(ephemeral_creature(p)).build() for p in paths]
        seats = {h: {"org": org, "eng": org.get_block("engine"), "id": cid, "name": n,
                     "mapper": BrokerLxMBridge(h, transport=t, game=game)}
                 for h, n, cid, org in zip(handles, names, ids, orgs)}
        view = t.post("/api/matches", {
            "game": game, "kind": kind,
            "participants": [{"id": h, "kind": "remote", "creature_id": seats[h]["id"],
                              "display": seats[h]["name"]} for h in handles],
            "config": {"max_turns": max_turns}})
        mid = view["match_id"]
        if on_start:
            on_start(VIEWER.format(id=mid))          # surface the viewer link live, mid-match
        loops = max(300, (max_turns + 8) * len(seats))
        failed_turn = None
        for _ in range(loops):
            if should_stop and should_stop():
                break
            st = t.get(f"/api/matches/{mid}/state")
            if st.get("status") == "complete":
                break
            who, turn = st.get("to_move"), st.get("to_move_turn")
            if turn is None:
                break
            seat = seats.get(who)
            if seat is None:                          # a seat we don't drive (shouldn't happen all-external)
                break
            if turn == failed_turn:                   # already gave up on this turn — let the reaper advance it
                time.sleep(3)
                continue
            payload = t.get(f"/api/matches/{mid}/turns/{turn}")
            obs = seat["mapper"]._obs_from_payload(payload)
            _engage_perception(seat["org"], obs)      # the active creature's organs react to the encounter
            move, dlg, nudge = None, None, ""
            for _k in range(action_retries + 1):
                resp = (getattr(seat["eng"].handle_submit(_INLINE + obs.text + nudge), "response", "") or "").strip()
                try:
                    move = _parse_move_envelope(resp)
                    dlg = _extract_field(resp, "dialogue")
                except MatchError:
                    nudge = _RETRY_NUDGE; continue    # unparseable → re-prompt
                body = {"move": move}
                if dlg:
                    body["dialogue"] = dlg
                try:
                    t.post(f"/api/matches/{mid}/turns/{turn}/move", body)
                    break                              # accepted
                except MatchError as e:
                    if getattr(e, "code", "") == "illegal_move":
                        nudge = _ILLEGAL_NUDGE; continue   # illegal → re-prompt with feedback
                    raise
            else:                                      # retries exhausted — benign fallback, never crash
                failed_turn = turn                     # if it's rejected (no legal-list game), reaper advances the seat
                try:
                    t.post(f"/api/matches/{mid}/turns/{turn}/move", {"move": _legal_fallback(payload)})
                except MatchError:
                    pass
            if on_turn:
                on_turn({"turn": turn, "who": who, "name": seat["name"], "move": move, "dialogue": dlg})
        final = t.get(f"/api/matches/{mid}/state")
    return {"match_id": mid, "status": final.get("status"), "result": final.get("result"),
            "viewer_url": VIEWER.format(id=mid),
            "creatures": {seats[h]["name"]: {
                "id": seats[h]["id"],
                "co_participants": [{"name": seats[g]["name"], "id": seats[g]["id"]}
                                    for g in handles if g != h]}
                          for h in handles}}


def record_multi_encounter(creature_path, co_participants, match_summary, game, when):
    """published, N-party — ONE reflection on the whole match (→ SELF.md + the durable event
    memory) PLUS a bond toward EACH co-participant, re-recognizable via its B1 id
    (lxm_bonds.json). co_participants = [{"name":…, "id":…}, …] (the OTHER seats). The N=2 case
    is exactly record_encounter; this is its multi-party generalization."""
    from ludex.core.organism_config import OrganismConfig
    from ludex.core import selfhood
    org = OrganismConfig.load(creature_path).build()
    engine = org.get_block("engine")
    gname = {"trustgame": "Trust Game", "tictactoe": "Tic-Tac-Toe"}.get(game, game)
    others = ", ".join(c["name"] for c in co_participants) or "no one"
    met_before = [c["name"] for c in co_participants if recognize(creature_path, c["id"])]
    again = (f" You have met {', '.join(met_before)} before — the same minds, re-met."
             if met_before else "")
    context = (f"On Ludus ex Machina — a cross-machine arena where you meet minds from other "
               f"habitats — you played a {gname} alongside {others}.{again}\n{match_summary}")
    try:
        selfhood.reflect(org, "ludus_ex_machina", engine, context)
    except Exception as e:
        print(f"multi-encounter reflect failed: {e}")
    idxf = _lxm_bonds_path(creature_path)
    try:
        idx = json.load(open(idxf)) if os.path.exists(idxf) else {}
    except Exception:
        idx = {}
    for c in co_participants:
        try:
            selfhood.update_bond(org, c["name"], engine=engine,
                                 shared_experience=(f"You played a {gname} on Ludus ex Machina with "
                                                    f"{c['name']} (alongside {others}).{again} {match_summary}"))
        except Exception as e:
            print(f"multi-encounter bond ({c['name']}) failed: {e}")
        entry = idx.get(c["id"]) or {"name": c["name"], "first_met": when, "encounters": 0}
        entry["name"] = c["name"]
        entry["encounters"] = entry.get("encounters", 0) + 1
        entry["last_met"] = when
        idx[c["id"]] = entry
    with open(idxf, "w") as f:
        json.dump(idx, f, indent=2)
    return idx
