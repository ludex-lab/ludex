"""BrokerLxMBridge (§5 client) — drives a creature through the LxM Phase-1
cross-machine match contract. Self-contained: a FakeBroker implements the four
endpoints' shapes (no LxM repo / no network), and a stub organism stands in for
a creature, so this tests OUR plumbing — create -> poll -> GET turn -> run
creature -> parse move -> POST move -> advance -> complete — and the
turn-payload -> Observation mapping, against the contract LxM Cody froze
(message_to_ludex_cody_20260614_phase1_ready.md §1).
"""
import json

from ludex.bridges.broker_lxm_bridge import (
    BrokerLxMBridge, MatchError, _parse_move_envelope,
)
from ludex.bridges.creature_player import play_episode


class FakeBroker:
    """In-test fake of the Phase-1 contract. A trivial game: the remote (me)
    makes `n_moves`; a 'bot' auto-plays the in-between turn (so `to_move` returns
    to me, exactly like the reference 1-bot+1-remote acceptance), then complete."""

    def __init__(self, my_id="aria", opp_id="bot", n_moves=3):
        self.my_id, self.opp, self.n = my_id, opp_id, n_moves
        self.moves = 0
        self.turn = 1
        self.status = "in_progress"
        self.created = False
        self.submitted = []
        self.turn_gets = 0

    def _view(self):
        if self.status == "complete":
            return {"match_id": "t1", "game": "tictactoe", "status": "complete",
                    "to_move": None, "to_move_kind": None, "to_move_turn": None,
                    "participants": [{"id": self.my_id}, {"id": self.opp}],
                    "result": {"outcome": "draw",
                               "scores": {self.my_id: 0, self.opp: 0}}}
        return {"match_id": "t1", "game": "tictactoe", "status": "in_progress",
                "to_move": self.my_id, "to_move_kind": "remote",
                "to_move_turn": self.turn,
                "participants": [{"id": self.my_id}, {"id": self.opp}],
                "result": None}

    def get(self, path):
        if path.endswith("/state"):
            return self._view()
        if "/turns/" in path:                       # GET /api/matches/t1/turns/{n}
            self.turn_gets += 1
            return {"match_id": "t1", "turn": self.turn, "to_move": self.my_id,
                    "state_readable": f"board@turn{self.turn}",
                    "state": {"turn": self.turn, "board": [[None] * 3] * 3},
                    "present_agents": [{"id": self.opp, "display_name": "Bot"}],
                    "incoming_messages": [], "deadline": 180}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, body):
        if path == "/api/matches":
            self.created = True
            return self._view()
        if path.endswith("/move"):                  # POST .../turns/{n}/move
            self.submitted.append(body)
            self.moves += 1
            if self.moves >= self.n:
                self.status = "complete"
            else:
                self.turn += 2                      # 'bot' auto-played the gap turn
            return self._view()
        raise AssertionError(f"unexpected POST {path}")


class _StubEngine:
    name = "engine"

    def __init__(self, response):
        self._response = response
        self.prompts = []

    def handle_submit(self, prompt, **kw):
        self.prompts.append(prompt)
        return type("R", (), {"response": self._response,
                              "stop_reason": "end", "error": None})()


class _StubOrganism:
    """Engine-only organism — get_block returns the engine, None for every organ,
    so play_episode runs the brain and silently skips all organ touches."""
    def __init__(self, response):
        self._engine = _StubEngine(response)

    def get_block(self, name):
        return self._engine if name == "engine" else None


MOVE_ENVELOPE = ('{"protocol":"lxm-v0.2","agent_id":"aria","turn":1,'
                 '"move":{"type":"place","position":[1,1]},"dialogue":"trust holds"}')


def test_create_play_complete():
    fake = FakeBroker(my_id="aria", n_moves=3)
    bridge = BrokerLxMBridge(
        "aria", transport=fake, game="tictactoe", poll_interval=0,
        participants=[{"id": "aria", "kind": "remote"},
                      {"id": "bot", "kind": "local", "adapter": "first_empty_bot"}],
        config={"max_turns": 9})
    org = _StubOrganism(MOVE_ENVELOPE)

    result = play_episode(org, bridge, max_steps=20, consolidate=False)

    assert fake.created                              # POST /api/matches happened
    assert fake.status == "complete"
    assert len(fake.submitted) == 3                  # creature submitted 3 moves
    assert result["turns"] == 3
    # move + dialogue were parsed out of the creature's envelope
    assert fake.submitted[0]["move"] == {"type": "place", "position": [1, 1]}
    assert fake.submitted[0]["dialogue"] == "trust holds"
    # the creature saw the readable board each turn
    assert org._engine.prompts and "board@turn1" in org._engine.prompts[0]
    # terminal reward came from the result scorecard (draw -> 0)
    assert result["reward"] == 0.0


def test_observation_mapping_exposes_opponent_identity():
    fake = FakeBroker(my_id="aria", opp_id="kestrel", n_moves=1)
    bridge = BrokerLxMBridge("aria", transport=fake, game="tictactoe",
                             poll_interval=0,
                             participants=[{"id": "aria", "kind": "remote"},
                                           {"id": "kestrel", "kind": "remote"}])
    obs = bridge.reset()
    # present_agents keys on the participant id (the frozen bonds/ToM key)
    assert obs.present_agents == ("kestrel",)
    assert obs.text == "board@turn1"
    assert obs.state["turn"] == 1
    # Phase-1 stubs: immune/humoral channels empty until Phase 2
    assert obs.incoming_messages == ()
    assert obs.opponent_actions == ()
    assert not obs.terminal


def test_join_existing_match_does_not_create():
    fake = FakeBroker(my_id="aria", n_moves=1)
    bridge = BrokerLxMBridge("aria", transport=fake, match_id="t1",
                             game="tictactoe", poll_interval=0)
    obs = bridge.reset()
    assert fake.created is False                      # joined, didn't POST /matches
    assert obs.text == "board@turn1"
    assert bridge.match_id == "t1"


def test_move_parsing_variants():
    # full lxm-v0.2 envelope
    assert _parse_move_envelope(MOVE_ENVELOPE) == {"type": "place", "position": [1, 1]}
    # bare {"move": {...}}
    assert _parse_move_envelope('{"move":{"action":"cooperate"}}') == {"action": "cooperate"}
    # the move object itself
    assert _parse_move_envelope('{"type":"vote","target":"x"}') == {"type": "vote", "target": "x"}
    # embedded in prose
    assert _parse_move_envelope('Sure. {"move":{"a":1}} done') == {"a": 1}
    # unparseable -> MatchError
    try:
        _parse_move_envelope("no json here")
        assert False, "expected MatchError"
    except MatchError as e:
        assert e.code == "unparseable_move"


def test_a5_channels_map_to_observation():
    """A5 real wire shapes (mirrors test_match_driver.py::TestA5Payload):
    prompt -> the creature's text, incoming_messages {agent_id,message} -> immune,
    opponent_actions {agent_id, move:{...}} -> humoral (move reduced to its action
    token)."""
    fake = FakeBroker(my_id="aria", n_moves=1)

    def get_with_channels(path):
        if "/turns/" in path:
            return {"match_id": "t1", "turn": 1, "to_move": "aria",
                    "prompt": "You are aria. Respond with your move.",
                    "state_readable": "b", "state": {},
                    "present_agents": [{"id": "kestrel", "display_name": "Kestrel"}],
                    "incoming_messages": [{"agent_id": "kestrel", "message": "let's both hold"}],
                    "opponent_actions": [{"agent_id": "kestrel", "move": {"action": "defect"}}],
                    "deadline": 180}
        return fake._view()
    fake.get = get_with_channels

    bridge = BrokerLxMBridge("aria", transport=fake, match_id="t1",
                             game="trustgame", poll_interval=0)
    obs = bridge.reset()
    assert obs.text == "You are aria. Respond with your move."          # prompt -> text
    assert obs.incoming_messages == (("kestrel", "let's both hold"),)   # -> immune
    assert obs.opponent_actions == (("kestrel", "DEFECT"),)            # move.action upper -> humoral


def test_sse_first_wakes_then_resolves():
    """SSE-first (Q1): while it's not my turn, the bridge blocks on the events
    stream, wakes on a your_turn event, then resolves via /state + /turns. A
    transport with a .stream() opts into SSE; without it, the bridge polls."""
    class SseFake:
        def __init__(self):
            self.state_gets = 0
            self.stream_calls = 0
        def get(self, path):
            if path.endswith("/state"):
                self.state_gets += 1
                mine = self.state_gets > 1            # 1st check: opponent's turn; then mine
                return {"match_id": "s1", "game": "tictactoe", "status": "in_progress",
                        "to_move": "me" if mine else "opp", "to_move_kind": "remote",
                        "to_move_turn": 2 if mine else 1,
                        "participants": [{"id": "me"}, {"id": "opp"}], "result": None}
            if "/turns/" in path:
                return {"turn": 2, "to_move": "me", "prompt": "your move",
                        "present_agents": [{"id": "opp"}], "state": {}}
            raise AssertionError(path)
        def stream(self, path, timeout=45.0):
            self.stream_calls += 1
            assert "as=me" in path                    # subscribed as this agent
            yield ("your_turn", '{"turn": 2}')        # the wake event

    fake = SseFake()
    bridge = BrokerLxMBridge("me", transport=fake, match_id="s1", game="tictactoe",
                             poll_interval=0)
    obs = bridge.reset()
    assert fake.stream_calls == 1                      # SSE was used to wake (not a poll sleep)
    assert obs.text == "your move"
    assert obs.present_agents == ("opp",)


def test_no_stream_transport_falls_back_to_poll():
    """A transport without .stream() (e.g. the in-process TestClient) just polls —
    SSE is opt-in on transport capability, poll is always the fallback."""
    fake = FakeBroker(my_id="aria", n_moves=1)        # FakeBroker has no .stream
    bridge = BrokerLxMBridge("aria", transport=fake, match_id="t1", game="tictactoe",
                             poll_interval=0)
    obs = bridge.reset()                               # resolves immediately (my turn), no error
    assert "board@turn1" in obs.text
