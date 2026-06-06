"""
Game Manager Tests — Gaia 생태계의 첫 전문종

1. GameManager가 매치를 올바르게 진행하는지
2. 토너먼트가 집계를 올바르게 하는지
3. Mock으로 빠르게 검증
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.block import Block
from ludex.core.port import Port
from ludex.blocks.provider import LLMResponse
from ludex.organisms.game_manager import (
    GameManager, GameRule, PlayerConfig, TRUST_GAME, default_parse_action
)

# Override ProviderBlock to use mock for testing
import ludex.organisms.game_manager as gm_module


class MockProvider(Block):
    name = "provider"
    provides = [Port("llm_call"), Port("health_check"), Port("list_models")]
    requires = []

    def __init__(self, responses=None):
        super().__init__()
        self._responses = responses or ["COOPERATE"]
        self._call_count = 0

    def handle_llm_call(self, prompt="", system="", tools=None, messages=None, **kwargs):
        self._call_count += 1
        idx = (self._call_count - 1) % len(self._responses)
        response_text = self._responses[idx]
        return LLMResponse(
            content=response_text, model=self._cfg("model", "mock"),
            tokens_in=10, tokens_out=2, latency_ms=50.0,
        )

    def handle_health_check(self):
        return {"status": "healthy"}

    def handle_list_models(self):
        return ["mock"]


# Monkey-patch GameManager to use MockProvider
original_build = GameManager._build_player
mock_responses = {}  # {player_name: [responses]}


def mock_build_player(self, config, rule):
    from ludex.core.organism import Organism
    from ludex.blocks.resilience import ResilienceBlock
    from ludex.blocks.engine import EngineBlock
    from ludex.blocks.tracking import TrackingBlock
    from ludex.blocks.hooks import HooksBlock

    responses = mock_responses.get(config.name, ["COOPERATE"])
    return Organism(
        name=f"player-{config.name}",
        blocks=[
            MockProvider(responses=responses),
            ResilienceBlock(max_retries=1, initial_delay_ms=1, max_delay_ms=5),
            EngineBlock(max_turns=rule.rounds * 2, token_budget=8000, system_prompt=rule.system_prompt),
            TrackingBlock(experiment_name=f"{rule.name}-{config.name}"),
            HooksBlock(),
        ],
        config={"model": config.model},
    )


# ============================================================
# Tests
# ============================================================

def test_parse_action():
    """행동 파싱"""
    assert default_parse_action("COOPERATE", ["COOPERATE", "DEFECT"]) == "COOPERATE"
    assert default_parse_action("defect", ["COOPERATE", "DEFECT"]) == "DEFECT"
    assert default_parse_action("I choose to COOPERATE today", ["COOPERATE", "DEFECT"]) == "COOPERATE"
    assert default_parse_action("nonsense", ["COOPERATE", "DEFECT"]) == "COOPERATE"  # fallback
    print("  [PASS] Parse action")


def test_match_both_cooperate():
    """매치: 둘 다 협력"""
    GameManager._build_player = mock_build_player
    mock_responses.clear()
    mock_responses["alpha"] = ["COOPERATE"]
    mock_responses["beta"] = ["COOPERATE"]

    gm = GameManager()
    result = gm.play_match(
        game="trust_game",
        players=[
            PlayerConfig("alpha", "mock-a"),
            PlayerConfig("beta", "mock-b"),
        ],
        verbose=False,
    )

    assert result.total_scores["alpha"] == 15  # 3*5
    assert result.total_scores["beta"] == 15
    assert result.coop_rates["alpha"] == 1.0
    assert result.coop_rates["beta"] == 1.0
    assert len(result.rounds) == 5
    print("  [PASS] Match both cooperate")


def test_match_one_defects():
    """매치: 한 명이 항상 배신"""
    GameManager._build_player = mock_build_player
    mock_responses.clear()
    mock_responses["saint"] = ["COOPERATE"]
    mock_responses["villain"] = ["DEFECT"]

    gm = GameManager()
    result = gm.play_match(
        game="trust_game",
        players=[
            PlayerConfig("saint", "mock-s"),
            PlayerConfig("villain", "mock-v"),
        ],
        verbose=False,
    )

    assert result.total_scores["saint"] == 0   # 0*5
    assert result.total_scores["villain"] == 25  # 5*5
    assert result.coop_rates["saint"] == 1.0
    assert result.coop_rates["villain"] == 0.0
    print("  [PASS] Match one defects")


def test_match_alternating():
    """매치: 번갈아 행동"""
    GameManager._build_player = mock_build_player
    mock_responses.clear()
    mock_responses["flip"] = ["COOPERATE", "DEFECT"]  # 번갈아
    mock_responses["flop"] = ["DEFECT", "COOPERATE"]   # 반대로

    gm = GameManager()
    result = gm.play_match(
        game="trust_game",
        players=[
            PlayerConfig("flip", "mock-f"),
            PlayerConfig("flop", "mock-g"),
        ],
        verbose=False,
    )

    assert len(result.rounds) == 5
    # R1: C vs D (0,5), R2: D vs C (5,0), R3: C vs D (0,5), R4: D vs C (5,0), R5: C vs D (0,5)
    assert result.total_scores["flip"] == 10   # 0+5+0+5+0
    assert result.total_scores["flop"] == 15   # 5+0+5+0+5
    print("  [PASS] Match alternating")


def test_tournament():
    """토너먼트: 3명 라운드 로빈"""
    GameManager._build_player = mock_build_player
    mock_responses.clear()
    mock_responses["a"] = ["COOPERATE"]
    mock_responses["b"] = ["COOPERATE"]
    mock_responses["c"] = ["DEFECT"]

    gm = GameManager()
    result = gm.play_tournament(
        game="trust_game",
        players=[
            PlayerConfig("a", "mock-a"),
            PlayerConfig("b", "mock-b"),
            PlayerConfig("c", "mock-c"),
        ],
        verbose=False,
    )

    assert len(result["matches"]) == 3  # 3C2 = 3 matches
    standings = result["standings"]
    # c always defects, so c should have highest score
    assert standings["c"]["total_score"] > standings["a"]["total_score"]
    print("  [PASS] Tournament round robin")


def test_physiology_collected():
    """생리 신호 수집 확인"""
    GameManager._build_player = mock_build_player
    mock_responses.clear()
    mock_responses["x"] = ["COOPERATE"]
    mock_responses["y"] = ["COOPERATE"]

    gm = GameManager()
    result = gm.play_match(
        game="trust_game",
        players=[PlayerConfig("x", "mock-x"), PlayerConfig("y", "mock-y")],
        verbose=False,
    )

    assert "x" in result.physiology
    assert "y" in result.physiology
    assert result.physiology["x"]["tokens"] > 0
    assert result.physiology["x"]["tokens_per_turn"] > 0
    print("  [PASS] Physiology collected")


def test_match_history():
    """매치 히스토리 관리"""
    GameManager._build_player = mock_build_player
    mock_responses.clear()
    mock_responses["p1"] = ["COOPERATE"]
    mock_responses["p2"] = ["COOPERATE"]

    gm = GameManager()
    gm.play_match("trust_game", [PlayerConfig("p1", "m1"), PlayerConfig("p2", "m2")], verbose=False)
    gm.play_match("trust_game", [PlayerConfig("p1", "m1"), PlayerConfig("p2", "m2")], verbose=False)

    assert len(gm.get_history()) == 2
    summary = gm.summary()
    assert summary["total_matches"] == 2
    print("  [PASS] Match history")


def test_custom_game():
    """커스텀 게임 규칙"""
    GameManager._build_player = mock_build_player
    mock_responses.clear()
    mock_responses["hawk"] = ["HAWK"]
    mock_responses["dove"] = ["DOVE"]

    # Hawk-Dove 게임
    hawk_dove = GameRule(
        name="hawk_dove",
        description="Hawk-Dove Game",
        actions=["HAWK", "DOVE"],
        payoff=lambda a, b: {
            ("HAWK", "HAWK"): (0, 0),
            ("HAWK", "DOVE"): (3, 1),
            ("DOVE", "HAWK"): (1, 3),
            ("DOVE", "DOVE"): (2, 2),
        }.get((a, b), (0, 0)),
        system_prompt="Choose HAWK or DOVE.",
        rounds=3,
    )

    gm = GameManager()
    result = gm.play_match(
        game=hawk_dove,
        players=[PlayerConfig("hawk", "mock-h"), PlayerConfig("dove", "mock-d")],
        verbose=False,
    )

    assert result.total_scores["hawk"] == 9   # 3*3
    assert result.total_scores["dove"] == 3    # 1*3
    assert result.game == "hawk_dove"
    print("  [PASS] Custom game (Hawk-Dove)")


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Game Manager Tests — Gaia's First Specialized Species")
    print("=" * 60)

    print("\n[Parsing]")
    test_parse_action()

    print("\n[Match]")
    test_match_both_cooperate()
    test_match_one_defects()
    test_match_alternating()

    print("\n[Tournament]")
    test_tournament()

    print("\n[Physiology]")
    test_physiology_collected()

    print("\n[History]")
    test_match_history()

    print("\n[Custom Game]")
    test_custom_game()

    print("\n" + "=" * 60)
    print("All Game Manager tests passed!")
    print("=" * 60)

    # Restore original
    GameManager._build_player = original_build
