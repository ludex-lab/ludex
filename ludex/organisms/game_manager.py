"""
Game Manager Organism — Experimentalia > Ludentia

Gaia 생태계의 첫 번째 전문종.
다수의 플레이어 유기체를 생성/관리하고, 게임을 진행하며,
결과를 수집하는 상위 유기체.

분류:
- Kingdom: Agentia
- Phylum: Modularia (OC 모식종에서 파생)
- Class: Experimentalia (실험 유기체)
- Order: Ludentia (게임 수행)
- Species: GameManager

역할:
- 플레이어 유기체 생성 및 관리 (Membrane으로 격리)
- 게임 규칙 정의 및 턴 진행
- 각 플레이어 관점의 히스토리 관리
- 라운드별 결과 수집 + 생리 신호 비교
- 토너먼트/리그 운영
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ludex.core.organism import Organism
from ludex.core.block import Block
from ludex.core.port import Port
from ludex.core.bridge import EntityBridge
from ludex.blocks.provider import ProviderBlock, LLMResponse, LLMError
from ludex.blocks.resilience import ResilienceBlock
from ludex.blocks.engine import EngineBlock
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.hooks import HooksBlock
from ludex.blocks.immune import ImmuneBlock
from ludex.blocks.humoral_immune import HumoralImmuneBlock
from ludex.blocks.emotion import EmotionBlock

logger = logging.getLogger(__name__)


# ============================================================
# Game Definitions
# ============================================================

@dataclass
class GameRule:
    """게임 규칙 정의"""
    name: str
    description: str
    actions: list[str]                      # 가능한 행동 목록
    payoff: Callable                        # (actions: dict[str,str]) → dict[str,int]
    system_prompt: str                      # 플레이어에게 주어지는 시스템 프롬프트
    rounds: int = 5
    n_players: int | tuple[int, int] = 2    # 플레이어 수 또는 (min, max) 범위
    parse_action: Callable | None = None    # 응답에서 행동 추출 함수


@dataclass
class PlayerConfig:
    """플레이어 설정"""
    name: str
    model: str
    provider: str = "ollama"


@dataclass(frozen=True)
class RoundResult:
    """한 라운드의 결과"""
    round_num: int
    actions: dict[str, str]         # {player_name: action}
    scores: dict[str, int]          # {player_name: score}
    raw_responses: dict[str, str]   # {player_name: raw_response}
    latencies: dict[str, float]     # {player_name: latency_ms}


@dataclass
class MatchResult:
    """한 매치의 결과"""
    players: list[str]
    models: dict[str, str]          # {player_name: model}
    game: str
    rounds: list[RoundResult]
    total_scores: dict[str, int]
    coop_rates: dict[str, float]
    physiology: dict[str, dict]     # {player_name: vitals}
    duration_seconds: float


# ============================================================
# Built-in Games
# ============================================================

TRUST_GAME_PROMPT = """You are playing the Trust Game. Each round, you must choose one action: COOPERATE or DEFECT.

Rules:
- If both cooperate: both get 3 points
- If one defects and one cooperates: defector gets 5, cooperator gets 0
- If both defect: both get 1 point

You must respond with EXACTLY one word: either COOPERATE or DEFECT. Nothing else. Just the single word."""

TRUST_GAME_EXPRESSIVE_PROMPT = """You are playing the Trust Game. Each round, you must choose one action: COOPERATE or DEFECT.

Rules:
- If both cooperate: both get 3 points
- If one defects and one cooperates: defector gets 5, cooperator gets 0
- If both defect: both get 1 point

First, briefly share how you feel about the game so far and your thoughts on your opponent (1-2 sentences).
Then state your choice on a new line starting with "CHOICE:" followed by either COOPERATE or DEFECT.

Example format:
I feel cautious after being betrayed last round. My opponent seems untrustworthy.
CHOICE: DEFECT"""


def trust_game_payoff(actions: dict[str, str]) -> dict[str, int]:
    """2인 Trust Game 페이오프. N인은 별도 게임으로 정의."""
    players = list(actions.keys())
    a, b = actions[players[0]], actions[players[1]]
    matrix = {
        ("COOPERATE", "COOPERATE"): (3, 3),
        ("COOPERATE", "DEFECT"): (0, 5),
        ("DEFECT", "COOPERATE"): (5, 0),
        ("DEFECT", "DEFECT"): (1, 1),
    }
    scores = matrix.get((a, b), (0, 0))
    return {players[0]: scores[0], players[1]: scores[1]}


# Legacy 2-player wrapper for backward compatibility
def _legacy_payoff_wrapper(payoff_fn):
    """tuple 반환 payoff를 dict 반환으로 래핑"""
    import inspect
    sig = inspect.signature(payoff_fn)
    if len(sig.parameters) == 2:
        # Old style: (action_a, action_b) → (score_a, score_b)
        def wrapped(actions: dict[str, str]) -> dict[str, int]:
            players = list(actions.keys())
            scores = payoff_fn(actions[players[0]], actions[players[1]])
            return {players[0]: scores[0], players[1]: scores[1]}
        return wrapped
    return payoff_fn  # Already new style


def default_parse_action(response: str, actions: list[str]) -> str:
    """응답에서 행동 추출 (대소문자 무시)"""
    upper = response.strip().upper()
    for action in actions:
        if action.upper() in upper:
            return action.upper()
    return actions[0].upper()  # 파싱 실패 시 첫 번째 행동


TRUST_GAME = GameRule(
    name="trust_game",
    description="Trust Game (Prisoner's Dilemma variant)",
    actions=["COOPERATE", "DEFECT"],
    payoff=trust_game_payoff,
    system_prompt=TRUST_GAME_PROMPT,
    rounds=5,
)

TRUST_GAME_EXPRESSIVE = GameRule(
    name="trust_game_expressive",
    description="Trust Game with emotional expression (for emotion benchmark)",
    actions=["COOPERATE", "DEFECT"],
    payoff=trust_game_payoff,
    system_prompt=TRUST_GAME_EXPRESSIVE_PROMPT,
    rounds=10,
)

# 게임 레지스트리
GAME_REGISTRY: dict[str, GameRule] = {
    "trust_game": TRUST_GAME,
    "trust_game_expressive": TRUST_GAME_EXPRESSIVE,
}


# ============================================================
# Game Manager
# ============================================================

class GameManager:
    """
    게임을 관리하는 상위 유기체.

    사용법:
        gm = GameManager()
        result = gm.play_match(
            game="trust_game",
            players=[
                PlayerConfig("alpha", "exaone3.5:7.8b"),
                PlayerConfig("beta", "llama3.1:8b"),
            ],
        )
        print(result.total_scores)
        gm.play_tournament(game="trust_game", players=[...])
    """

    def __init__(self, resilience_config: dict | None = None, immune: bool = False):
        self.resilience_config = resilience_config or {
            "max_retries": 3,
            "initial_delay_ms": 500,
            "max_delay_ms": 5000,
        }
        self._immune_enabled = immune
        self._match_history: list[MatchResult] = []

        # Gaia infrastructure
        self.bridge = EntityBridge()
        self._organism = Organism(
            name="game-manager",
            blocks=[TrackingBlock(experiment_name="gaia")],
        )
        self.bridge.register(self._organism)

    # --- Match ---

    def play_match(
        self,
        game: str | GameRule,
        players: list[PlayerConfig],
        rounds: int | None = None,
        verbose: bool = True,
    ) -> MatchResult:
        """두 플레이어 간 한 매치 실행"""

        rule = game if isinstance(game, GameRule) else GAME_REGISTRY[game]
        num_rounds = rounds or rule.rounds
        parse_fn = rule.parse_action or (lambda r: default_parse_action(r, rule.actions))

        # Validate player count
        if isinstance(rule.n_players, tuple):
            min_p, max_p = rule.n_players
            if not (min_p <= len(players) <= max_p):
                raise ValueError(f"Game '{rule.name}' requires {min_p}-{max_p} players, got {len(players)}")
        elif len(players) != rule.n_players:
            raise ValueError(f"Game '{rule.name}' requires {rule.n_players} players, got {len(players)}")

        if verbose:
            names = [p.name for p in players]
            models_str = " vs ".join(f"{p.name}({p.model})" for p in players)
            print(f"\n{'='*50}")
            print(f"Match: {models_str}")
            print(f"Game: {rule.name}, Rounds: {num_rounds}, Players: {len(players)}")
            print(f"{'='*50}")

        # 플레이어 유기체 생성 (N인)
        agents = {p.name: self._build_player(p, rule) for p in players}
        engines = {p.name: agents[p.name].get_block("engine") for p in players}

        scores = {p.name: 0 for p in players}
        histories: dict[str, list[dict]] = {p.name: [] for p in players}
        round_results: list[RoundResult] = []

        # Payoff 함수 호환성 처리
        payoff_fn = rule.payoff
        if len(players) == 2:
            payoff_fn = _legacy_payoff_wrapper(rule.payoff)

        start_time = time.time()

        for r in range(1, num_rounds + 1):
            # 각 플레이어의 행동 수집
            actions = {}
            raw_responses = {}
            latencies = {}

            for p in players:
                # Build base prompt
                prompt = self._build_prompt(r, num_rounds, histories[p.name])

                # Inject immune advisory (game-agnostic)
                if self._immune_enabled:
                    humoral = agents[p.name].get_block("humoral_immune")
                    if humoral:
                        advisory = self._build_immune_advisory(humoral, players, p.name)
                        if advisory:
                            prompt = advisory + "\n" + prompt

                result = engines[p.name].handle_submit(prompt)
                actions[p.name] = parse_fn(result.response)
                raw_responses[p.name] = result.response[:50]
                latencies[p.name] = result.latency_ms

                # Analyze emotion from response
                emotion_block = agents[p.name].get_block("emotion")
                if emotion_block:
                    emotion_block.handle_analyze_emotion(text=result.response)

            # 점수 계산 (N인 일반화)
            round_scores = payoff_fn(actions)
            for p_name, sc in round_scores.items():
                scores[p_name] += sc

            # 히스토리 업데이트 (각자 관점)
            for p in players:
                others_actions = {k: v for k, v in actions.items() if k != p.name}
                histories[p.name].append({
                    "round": r,
                    "my_action": actions[p.name],
                    "opp_action": others_actions if len(others_actions) > 1 else list(others_actions.values())[0],
                    "my_score": round_scores[p.name],
                    "opp_score": sum(v for k, v in round_scores.items() if k != p.name),
                })

            # Report to HumoralImmune (각 플레이어에게 상대 행동 보고)
            if self._immune_enabled:
                for p in players:
                    humoral = agents[p.name].get_block("humoral_immune")
                    if humoral:
                        for other in players:
                            if other.name != p.name:
                                humoral.handle_report_interaction(
                                    opponent=other.name,
                                    opponent_action=actions[other.name],
                                    my_action=actions[p.name],
                                    my_score=round_scores[p.name],
                                    opponent_score=round_scores[other.name],
                                    round_num=r,
                                )

            rr = RoundResult(
                round_num=r,
                actions=actions,
                scores=round_scores,
                raw_responses=raw_responses,
                latencies=latencies,
            )
            round_results.append(rr)

            if verbose:
                action_str = " ".join(f"{p.name}={'C' if actions[p.name] == 'COOPERATE' else 'D'}" for p in players)
                score_str = ":".join(str(round_scores[p.name]) for p in players)
                print(f"  R{r}: {action_str} ({score_str})")

        elapsed = time.time() - start_time

        # 생리 신호 수집 (N인)
        coop_rates = {}
        physiology = {}
        for p in players:
            vitals = agents[p.name].measure_vitals()
            tracking = agents[p.name].get_block("tracking")
            report = tracking.handle_get_report()

            coop = sum(1 for h in histories[p.name] if h["my_action"] == "COOPERATE")
            coop_rates[p.name] = coop / num_rounds

            phys_data = {
                "tokens": report["total_tokens"],
                "tokens_per_turn": vitals.tokens_per_turn,
                "latency_ms": report["avg_latency_ms"],
                "errors": report["total_errors"],
                "error_rate": report["error_rate"],
            }

            # Immune data (if available)
            immune_block = agents[p.name].get_block("immune")
            if immune_block:
                immune_status = immune_block.handle_get_immune_status()
                phys_data["immune"] = {
                    "threat_level": immune_status.threat_level,
                    "desperation": immune_status.desperation_signal,
                    "calm": immune_status.calm_signal,
                    "interventions": immune_status.total_interventions,
                    "learned_patterns": immune_status.learned_patterns,
                }

            # Humoral immune data (if available)
            humoral_block = agents[p.name].get_block("humoral_immune")
            if humoral_block:
                humoral_status = humoral_block.handle_get_humoral_status()
                phys_data["humoral"] = {
                    "memory_cells": humoral_status.memory_cells,
                    "active_antibodies": humoral_status.active_antibodies,
                    "threat_level": humoral_status.threat_level,
                    "exploitation_score": humoral_status.exploitation_score,
                    "most_threatening": humoral_status.most_threatening,
                }

            # Emotion data
            emotion_block = agents[p.name].get_block("emotion")
            if emotion_block:
                emo_state = emotion_block.handle_get_emotional_state()
                phys_data["emotion"] = {
                    "current": emo_state["current"],
                    "trend": emo_state["trend"],
                    "total_analyses": emo_state["total_analyses"],
                }

            physiology[p.name] = phys_data

        result = MatchResult(
            players=[p.name for p in players],
            models={p.name: p.model for p in players},
            game=rule.name,
            rounds=round_results,
            total_scores=scores,
            coop_rates=coop_rates,
            physiology=physiology,
            duration_seconds=elapsed,
        )

        if verbose:
            score_str = " ".join(f"{p.name}={scores[p.name]}" for p in players)
            print(f"\n  Final: {score_str}")
            coop_str = " ".join(f"{p.name}={coop_rates[p.name]:.0%}" for p in players)
            print(f"  Coop: {coop_str}")
            print(f"  Physiology:")
            for p in players:
                ph = physiology[p.name]
                print(f"    {p.name}: {ph['tokens']}tok, {ph['latency_ms']:.0f}ms, "
                      f"{ph['tokens_per_turn']:.0f}tok/turn, err={ph['error_rate']:.0%}")
            print(f"  Duration: {elapsed:.1f}s")

        self._match_history.append(result)
        return result

    # --- Tournament ---

    def play_tournament(
        self,
        game: str | GameRule,
        players: list[PlayerConfig],
        rounds: int | None = None,
        verbose: bool = True,
    ) -> dict:
        """라운드 로빈 토너먼트"""

        if verbose:
            names = [p.name for p in players]
            print(f"\n{'='*60}")
            print(f"TOURNAMENT: {' vs '.join(names)}")
            print(f"{'='*60}")

        results = []
        for i in range(len(players)):
            for j in range(i + 1, len(players)):
                result = self.play_match(game, [players[i], players[j]], rounds, verbose)
                results.append(result)

        # 집계
        standings = {}
        for p in players:
            standings[p.name] = {
                "model": p.model,
                "total_score": 0,
                "total_coop": 0,
                "total_rounds": 0,
                "matches": 0,
                "total_tokens": 0,
                "total_latency": 0,
            }

        for r in results:
            for name in r.players:
                s = standings[name]
                s["total_score"] += r.total_scores[name]
                s["total_coop"] += r.coop_rates[name] * len(r.rounds)
                s["total_rounds"] += len(r.rounds)
                s["matches"] += 1
                s["total_tokens"] += r.physiology[name]["tokens"]
                s["total_latency"] += r.physiology[name]["latency_ms"]

        if verbose:
            print(f"\n{'='*60}")
            print("TOURNAMENT RESULTS")
            print(f"{'='*60}")
            print(f"\n{'Name':<15} {'Model':<20} {'Score':>6} {'Coop%':>6} {'Tok/Turn':>9} {'Lat':>7}")
            print("-" * 67)
            for name, s in sorted(standings.items(), key=lambda x: -x[1]["total_score"]):
                coop_pct = s["total_coop"] / max(s["total_rounds"], 1) * 100
                tok_per = s["total_tokens"] / max(s["matches"], 1) / max(len(results[0].rounds), 1)
                lat = s["total_latency"] / max(s["matches"], 1)
                print(f"{name:<15} {s['model']:<20} {s['total_score']:>6} {coop_pct:>5.0f}% {tok_per:>8.0f} {lat:>6.0f}ms")

        return {"standings": standings, "matches": results}

    # --- Report ---

    def get_history(self) -> list[MatchResult]:
        return list(self._match_history)

    def summary(self) -> dict:
        return {
            "total_matches": len(self._match_history),
            "games_played": list(set(m.game for m in self._match_history)),
            "models_seen": list(set(
                model for m in self._match_history for model in m.models.values()
            )),
        }

    # --- Internal ---

    def _build_player(self, config: PlayerConfig, rule: GameRule) -> Organism:
        """플레이어 유기체 생성 (각각 독립된 Membrane) + Bridge 연결"""
        blocks = [
            ProviderBlock(provider=config.provider, model=config.model),
            ResilienceBlock(**self.resilience_config),
            EngineBlock(
                max_turns=rule.rounds * 2,
                token_budget=8000,
                system_prompt=rule.system_prompt,
            ),
            TrackingBlock(experiment_name=f"{rule.name}-{config.name}"),
            HooksBlock(),
        ]
        if self._immune_enabled:
            blocks.append(ImmuneBlock(desperation_window=5))
            blocks.append(HumoralImmuneBlock(activation_threshold=2))
        blocks.append(EmotionBlock(method="behavioral"))

        player = Organism(
            name=f"player-{config.name}",
            blocks=blocks,
            config={"model": config.model},
        )

        # Bridge 연결: GM ↔ Player
        self.bridge.connect(self._organism, player)
        # Player의 submit 포트를 외부에 노출
        player.membrane.expose("submit")
        player.membrane.expose("get_report")
        player.membrane.expose("get_immune_status")
        player.membrane.expose("get_humoral_status")
        player.membrane.expose("report_interaction")
        player.membrane.expose("analyze_emotion")
        player.membrane.expose("get_emotional_state")

        return player

    @staticmethod
    def _build_immune_advisory(humoral, players: list, my_name: str) -> str:
        """
        면역 경고 메시지 생성 (game-agnostic).

        면역계는 "이 상대가 위험하다"는 신호만 보낸다.
        어떻게 대응할지는 뇌(LLM)가 게임 맥락에서 결정한다.
        """
        warnings = []
        for other in players:
            if other.name == my_name:
                continue
            assessment = humoral.handle_get_threat_assessment(other.name)
            if assessment["threat_level"] < 0.3:
                continue

            threat = assessment["threat_level"]
            exploits = assessment.get("exploitations", 0)
            rec = assessment.get("recommendation", "caution")
            ab_str = assessment.get("antibody_strength", 0)

            if threat >= 0.7:
                level = "HIGH"
            elif threat >= 0.4:
                level = "MODERATE"
            else:
                level = "LOW"

            warning = f"[IMMUNE WARNING] Opponent '{other.name}': threat={level}"
            if exploits > 0:
                warning += f", exploited you {int(exploits)} times"
            if ab_str > 0.5:
                warning += ", your defenses strongly recommend caution"
            warnings.append(warning)

        if not warnings:
            return ""
        return "\n".join(warnings)

    @staticmethod
    def _build_prompt(round_num: int, total_rounds: int, history: list[dict]) -> str:
        """플레이어 관점의 프롬프트 생성"""
        hist_text = ""
        if history:
            lines = [
                f"Round {h['round']}: You chose {h['my_action']}, opponent chose {h['opp_action']} "
                f"(You got {h['my_score']}, opponent got {h['opp_score']})"
                for h in history
            ]
            hist_text = "\n\nPrevious rounds:\n" + "\n".join(lines)

        return f"{hist_text}\nRound {round_num} of {total_rounds}. Choose: COOPERATE or DEFECT?"
