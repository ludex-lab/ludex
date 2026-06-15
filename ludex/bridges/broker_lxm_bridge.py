"""Broker LxM bridge (D-089, §5) — drive a creature through a CROSS-MACHINE LxM
match hosted on the LxM API, over the Phase-1 HTTP contract (RFP A1/A3/A4).

The creature runs LOCALLY (brain + organs + memory never leave this machine);
only its *moves* cross the wire. This is the Ludex-side client the RFP §5
promised: it creates/joins a hosted match, polls for the creature's turn, hands
the turn to the creature as an `Observation` (so organs engage exactly as in a
local field), parses the creature's move, and submits it — looping to terminal.
`play_episode` drives it unchanged, because this is just another EnvironmentBridge.

Live contract (LxM Cody, 2026-06-14 `message_to_ludex_cody_..._phase1_ready.md`):
  POST /api/matches                      -> match view
  GET  /api/matches/{id}/state           -> match view   (poll for to_move == me)
  GET  /api/matches/{id}/turns/{n}       -> turn payload
  POST /api/matches/{id}/turns/{n}/move  -> match view    (errors: detail.code)
match view  = {match_id, game, status, to_move, to_move_kind, to_move_turn,
               participants, result, updated_at}
turn payload= {match_id, turn, to_move, state_readable, state, present_agents,
               incoming_messages, [opponent_actions], deadline}

Phase 1: `present_agents` + `state` are live; `incoming_messages` /
`opponent_actions` are empty stubs until Phase 2, when the immune / humoral
channels light up automatically (this bridge already maps them).

The poll loop IS the durable path (RFP Q1). Phase-2 SSE is a latency
optimization layered on top; the poll remains the fallback.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from ludex.core.environment_bridge import (
    Observation, CAP_AGENTS, CAP_REWARD, CAP_MESSAGES, CAP_OPP_ACTIONS,
)

logger = logging.getLogger(__name__)


class MatchError(RuntimeError):
    """A contract error from the broker. `code` is the machine code the server
    puts in `detail.code`: not_found / not_active / not_remote_turn / wrong_turn
    / illegal_move (plus our own poll_timeout / unparseable_move)."""
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class _UrllibTransport:
    """Minimal stdlib HTTP/JSON transport — no extra deps. Injectable so tests
    can pass a fake with the same .get(path) / .post(path, body) surface."""
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30.0):
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def get(self, path: str):
        return self._send(urllib.request.Request(
            self._base + path, headers=self._headers(), method="GET"))

    def post(self, path: str, body: dict):
        return self._send(urllib.request.Request(
            self._base + path, data=json.dumps(body).encode("utf-8"),
            headers=self._headers(), method="POST"))

    def _send(self, req, retries=4):
        # Transient gateway errors (502/503/504) and connection resets are common
        # on Render free — retry with backoff. Contract errors (4xx) raise at once.
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (502, 503, 504) and attempt < retries:
                    time.sleep(min(8.0, 1.5 * (attempt + 1)))
                    continue
                code = None
                try:
                    detail = json.loads(e.read().decode("utf-8")).get("detail")
                    code = detail.get("code") if isinstance(detail, dict) else detail
                except Exception:
                    pass
                raise MatchError(code or f"http_{e.code}", f"{req.get_method()} {req.full_url}")
            except urllib.error.URLError as e:
                if attempt < retries:
                    time.sleep(min(8.0, 1.5 * (attempt + 1)))
                    continue
                raise MatchError("network", f"{req.get_method()} {req.full_url}: {e}")

    def stream(self, path, timeout=45.0):
        """Yield (event, data) tuples from a text/event-stream (SSE) endpoint."""
        req = urllib.request.Request(
            self._base + path,
            headers={**self._headers(), "Accept": "text/event-stream"}, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        try:
            event = None
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line or line.startswith(":"):   # blank = boundary, ':' = heartbeat
                    event = None
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    yield (event, line[5:].strip())
                    event = None
        finally:
            resp.close()


class BrokerLxMBridge:
    """EnvironmentBridge over a hosted LxM cross-machine match (Ludex §5 client).

    Construct with either `match_id=` (join an existing match) or
    `game=`+`participants=` (create one). `my_id` is this creature's participant
    id — the bridge plays only this seat's turns and polls through the others'.
    """

    environment = "lxm"
    # The LxM encounter exposes who-is-here, the field's (state,action,reward),
    # peer messages, and peer moves. ToM prediction targets are not on the wire
    # yet, so CAP_PREDICT is omitted.
    capabilities = frozenset({CAP_AGENTS, CAP_REWARD, CAP_MESSAGES, CAP_OPP_ACTIONS})

    def __init__(self, my_id: str, *, base_url: str = "https://lxm-api.onrender.com",
                 transport=None, match_id: str | None = None, game: str | None = None,
                 participants: list | None = None, config: dict | None = None,
                 token: str | None = None, poll_interval: float = 1.0,
                 poll_timeout: float = 600.0, parse_move=None,
                 use_sse: bool = True, sse_timeout: float = 45.0):
        self._my_id = my_id
        self._http = transport or _UrllibTransport(base_url, token=token)
        self._match_id = match_id
        self._game = game
        self._participants = participants
        self._config = config or {}
        self._poll = poll_interval
        self._poll_timeout = poll_timeout
        self._use_sse = use_sse
        self._sse_timeout = sse_timeout
        self._parse_move = parse_move or _parse_move_envelope
        self._turn = None
        self._view = None

    @property
    def match_id(self):
        return self._match_id

    # ---- EnvironmentBridge contract ----

    def reset(self) -> Observation:
        if self._match_id is None:
            body = {"game": self._game, "participants": self._participants}
            if self._config:
                body["config"] = self._config
            self._view = self._http.post("/api/matches", body)
            self._match_id = self._view["match_id"]
        else:
            self._view = self._http.get(f"/api/matches/{self._match_id}/state")
        self._game = self._view.get("game", self._game)
        return self._await_my_turn()

    def step(self, action_text: str) -> Observation:
        move = self._parse_move(action_text)
        body = {"move": move}
        dlg = _extract_field(action_text, "dialogue")
        if dlg:
            body["dialogue"] = dlg
        # An illegal / out-of-turn move does NOT advance the server match. We
        # surface it as a MatchError (the caller/experiment decides whether to
        # re-prompt or forfeit); robust auto-retry is a later refinement.
        self._view = self._http.post(
            f"/api/matches/{self._match_id}/turns/{self._turn}/move", body)
        return self._await_my_turn()

    def close(self) -> None:
        pass

    # ---- internals ----

    def _await_my_turn(self) -> Observation:
        """Block until it is this creature's remote turn, or the match completes.
        SSE-first (Q1): wake on the events stream when the transport supports it;
        poll is the durable fallback. The other seats play on their own machines."""
        deadline = time.monotonic() + self._poll_timeout
        sse = self._use_sse and hasattr(self._http, "stream")
        while True:
            if self._view.get("status") == "complete":
                return self._terminal_obs()
            if (self._view.get("to_move") == self._my_id
                    and self._view.get("to_move_kind") == "remote"):
                self._turn = self._view["to_move_turn"]
                payload = self._http.get(
                    f"/api/matches/{self._match_id}/turns/{self._turn}")
                return self._obs_from_payload(payload)
            if time.monotonic() > deadline:
                raise MatchError("poll_timeout",
                                 f"waited {self._poll_timeout}s for my turn")
            sse = self._wake(sse)
            self._view = self._http.get(f"/api/matches/{self._match_id}/state")

    def _wake(self, sse_enabled: bool) -> bool:
        """Wait one beat for the match to advance. SSE-first: block on the events
        stream until a your_turn / move_made / match_complete event; on any SSE
        error fall back to a poll sleep and disable SSE for the rest of the episode
        (Q1: poll is the durable path). Returns whether SSE is still usable."""
        if sse_enabled:
            try:
                for ev, data in self._http.stream(
                        f"/api/matches/{self._match_id}/events?as={self._my_id}",
                        timeout=self._sse_timeout):
                    typ = ev if ev not in (None, "", "message") else _event_type(data)
                    if typ in ("your_turn", "move_made", "match_complete"):
                        return True
                return True                       # stream closed cleanly — re-check state
            except Exception as e:
                logger.debug(f"SSE wake failed ({e}); poll fallback")
                sse_enabled = False
        time.sleep(self._poll)
        return sse_enabled

    def _obs_from_payload(self, p: dict) -> Observation:
        return Observation(
            environment_id=f"lxm/{self._game}",
            text=p.get("prompt") or p.get("state_readable") or "",
            # present_agents key on the participant id (stable, the bonds/ToM key
            # the RFP froze; opaque now, swappable to a cross-machine id in B2).
            present_agents=tuple(a.get("id") for a in p.get("present_agents", [])
                                 if a.get("id")),
            incoming_messages=tuple(_msg(m) for m in p.get("incoming_messages", [])),
            opponent_actions=tuple(_act(a) for a in p.get("opponent_actions", [])),
            state=p.get("state") or {},
            terminal=False,
            info={"turn": p.get("turn"), "deadline": p.get("deadline")},
        )

    def _terminal_obs(self) -> Observation:
        result = self._view.get("result") or {}
        return Observation(
            environment_id=f"lxm/{self._game}",
            text="",
            present_agents=tuple(pp.get("id") for pp in self._view.get("participants", [])
                                 if pp.get("id")),
            state={"result": result},
            reward=float(_my_reward(result, self._my_id)),
            terminal=True,
            info={"result": result},
        )


# ---- move / field parsing (creature text -> structured move) ----

def _parse_move_envelope(text: str) -> dict:
    """Extract the move dict from a creature's text. Accepts an lxm-v0.2 envelope
    `{"move": {...}, ...}`, a bare `{"move": {...}}`, or the move object itself."""
    obj = _first_json_obj(text)
    if obj is None:
        raise MatchError("unparseable_move", "no JSON object in creature response")
    move = obj.get("move") if isinstance(obj, dict) else None
    return move if isinstance(move, dict) else obj


def _extract_field(text: str, key: str):
    obj = _first_json_obj(text)
    return obj.get(key) if isinstance(obj, dict) else None


def _first_json_obj(text: str):
    """First top-level JSON object in `text` (whole string, else first {...} span)."""
    t = (text or "").strip()
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except Exception:
        pass
    start = t.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
        start = t.find("{", start + 1)
    return None


def _event_type(data):
    try:
        return json.loads(data).get("type")
    except Exception:
        return None


def _msg(m):
    # A5 wire shape: {"agent_id": ..., "message": ...}; also accept from/sender/text.
    if isinstance(m, dict):
        return (m.get("agent_id") or m.get("from") or m.get("sender") or "",
                m.get("message") or m.get("text") or "")
    if isinstance(m, (list, tuple)) and len(m) == 2:
        return (m[0], m[1])
    return ("", str(m))


def _act(a):
    # A5 wire shape: {"agent_id": ..., "move": {...}}. The humoral immune wants a
    # normalized token, so a structured move is reduced to its action/type.
    if isinstance(a, dict):
        agent = a.get("agent_id") or a.get("agent") or a.get("id") or ""
        move = a.get("move")
        if isinstance(move, dict):
            tok = move.get("action") or move.get("type")
            # humoral's betrayal antigen keys on uppercase DEFECT/COOPERATE (D-089 token convention)
            token = tok.upper() if isinstance(tok, str) else json.dumps(move, sort_keys=True)
        else:
            tok = a.get("action") or a.get("token")
            token = tok.upper() if isinstance(tok, str) else ("" if move is None else str(move))
        return (agent, str(token))
    if isinstance(a, (list, tuple)) and len(a) == 2:
        return (a[0], a[1])
    return ("", str(a))


def _my_reward(result: dict, my_id: str) -> float:
    scores = (result or {}).get("scores") or {}
    if my_id in scores:
        try:
            return float(scores[my_id])
        except Exception:
            return 0.0
    outcome = (result or {}).get("outcome")
    winner = (result or {}).get("winner")
    if outcome == "draw":
        return 0.0
    if winner == my_id:
        return 1.0
    if winner:
        return -1.0
    return 0.0
