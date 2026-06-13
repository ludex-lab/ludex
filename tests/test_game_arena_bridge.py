"""Game Arena bridge integration tests (D-089 bridge #2).

Real OpenSpiel + the google-deepmind/game_arena harness — the harness is a tight
prompt/parse/pyspiel pipeline not worth faithfully mocking, so these run against
the real package and skip where it isn't installed (CI lab python). Live-validated
2026-06-13: a scripted creature plays a full tic_tac_toe game; universal_poker
resolves its chance nodes (card deals) to the creature's decision.
"""
import re

import pytest

pytest.importorskip("pyspiel")
pytest.importorskip("game_arena")

from ludex.bridges.game_arena_bridge import GameArenaBridge          # noqa: E402
from ludex.core.environment_bridge import EnvironmentBridge          # noqa: E402


def test_satisfies_protocol():
    assert isinstance(GameArenaBridge("tic_tac_toe", seed=0), EnvironmentBridge)


def test_unsupported_game_rejected():
    # a real OpenSpiel game, but with no Game Arena prompt notation
    with pytest.raises(ValueError):
        GameArenaBridge("liars_dice")


def test_tic_tac_toe_full_game_scripted():
    b = GameArenaBridge("tic_tac_toe", seed=42)
    obs = b.reset()
    assert obs.present_agents == ("opponent",) and not obs.terminal
    assert "Final Answer" in obs.text                  # the harness prompt instructs the move format
    turns = 0
    while not obs.terminal and turns < 20:
        turns += 1
        legal = b._parsers.get_legal_action_strings(b._state)
        coords = re.search(r"\((\d+,\d+)\)", legal[0]).group(1)
        obs = b.step(f"Reason... Final Answer: ({coords})")
    assert obs.terminal and obs.reward in (-1.0, 0.0, 1.0)
    assert obs.info["forfeits"] == 0                   # every scripted reply parsed to a legal move


def test_unparseable_reply_forfeits_without_crashing():
    b = GameArenaBridge("tic_tac_toe", seed=7)
    b.reset()
    obs = b.step("I refuse to answer in any parseable format whatsoever")
    assert b._forfeits == 1                            # forfeited to a random legal move; game continues
    assert obs is not None


def test_universal_poker_resolves_chance_nodes():
    p = GameArenaBridge("universal_poker", seed=1)
    obs = p.reset()                                    # card deals are chance nodes, auto-resolved
    assert not obs.terminal and obs.text               # advanced to the creature's betting decision
