"""
Humoral Immune Block — B Cell Mediated Adaptive Immunity (Phase 5b)

체액성 면역: 외부 상호작용(상대 행동)에서 위협을 감지하고 기억한다.

생물학적 비유:
- ImmuneBlock (기존) = Cell-Mediated Immunity (T세포, 내부 위협)
  → 시스템 에러, 지연, 비용 등 내부 vitals 모니터링
- HumoralImmuneBlock (신규) = Humoral Immunity (B세포, 외부 위협)
  → 상대 행동 패턴, 반복 착취, 점수 하락 등 외부 상호작용 모니터링

핵심 컴포넌트 (B Cell → Antibody 생성 파이프라인):
1. Naive B Cell: 첫 항원(상대 배신) 노출 → 인식
2. Activation: 위협 패턴 확인 → Memory B Cell 분화
3. Memory B Cell: 항원 서명(상대 행동 시퀀스) 저장
4. Antibody Production: 방어 신호 생성 (전략 전환, desperation 상승)
5. Affinity Maturation: 반복 노출 시 패턴 매칭 정교화

기존 MemoryBlock과의 차이:
- MemoryBlock = 뇌 기억 (대화, 사실, 맥락) — 범용, 영구
- HumoralImmune = 면역 기억 (위협 패턴만) — 특화, 장기 교체 시 리셋
- 연결: Bus 시그널로 간접 통신, 직접 의존 없음
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from collections import defaultdict

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)


# ============================================================
# Humoral Immune Data Types
# ============================================================

@dataclass
class AntigenSignature:
    """항원 서명 — 상대의 행동 패턴"""
    source: str                     # 상대 식별자
    pattern: list[str]              # 행동 시퀀스 (e.g., ["DEFECT", "DEFECT", "DEFECT"])
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    exposures: int = 1             # 노출 횟수
    affinity: float = 0.5          # 패턴 매칭 정확도 (0~1), maturation으로 증가


@dataclass
class Antibody:
    """항체 — 방어 반응 신호"""
    target: str                     # 어떤 항원(상대)에 대한 항체
    action: str                     # 권장 행동 ("defect", "distrust", "avoid")
    strength: float = 0.5           # 항체 농도/강도 (0~1)
    produced_at: float = field(default_factory=time.time)


@dataclass
class HumoralStatus:
    """체액성 면역 상태"""
    memory_cells: int = 0           # 기억된 상대 수
    active_antibodies: int = 0      # 현재 활성 항체 수
    threat_level: float = 0.0       # 외부 위협 수준
    exploitation_score: float = 0.0 # 착취당하는 정도
    total_antigens_seen: int = 0    # 총 항원 노출 수
    most_threatening: str = ""      # 가장 위험한 상대


class HumoralImmuneBlock(Block):
    """
    체액성 면역 블록 — B Cell Mediated Immunity.

    게임/상호작용에서 상대의 행동 패턴을 추적하고,
    반복적 착취를 감지하면 방어 신호(항체)를 생성한다.

    provides: report_interaction, get_threat_assessment, get_humoral_status
    requires: (없음 — 독립적. Bus 시그널로 다른 블록과 통신)
    """

    name = "humoral_immune"
    provides = [
        Port("report_interaction", description="Report an opponent's action"),
        Port("get_threat_assessment", description="Get behavioral threat level for an opponent"),
        Port("get_humoral_status", description="Current humoral immune status"),
    ]
    requires = []

    def __init__(
        self,
        activation_threshold: int = 2,
        affinity_maturation_rate: float = 0.1,
        antibody_decay: float = 0.05,
        memory_capacity: int = 50,
    ):
        """
        Args:
            activation_threshold: DEFECT 횟수가 이만큼 되면 Memory B Cell 생성
            affinity_maturation_rate: 노출 시 패턴 매칭 정확도 증가율
            antibody_decay: 라운드당 항체 강도 감소 (용서 속도)
            memory_capacity: 최대 기억 가능한 상대 수
        """
        super().__init__()
        self.activation_threshold = activation_threshold
        self.affinity_maturation_rate = affinity_maturation_rate
        self.antibody_decay = antibody_decay
        self.memory_capacity = memory_capacity

        # B Cell Memory (면역 기억 — MemoryBlock과 별개)
        self._antigen_registry: dict[str, AntigenSignature] = {}
        self._antibodies: dict[str, Antibody] = {}

        # Interaction tracking
        self._interaction_history: dict[str, list[dict]] = defaultdict(list)
        self._exploitation_tracker: dict[str, float] = defaultdict(float)

        # Global state
        self._threat_level: float = 0.0
        self._total_interactions: int = 0

    def on_attach(self):
        self._listen("game.round_ended", self._on_round_ended)
        # Emotion-Immune circuit: stress → immune sensitization
        self._listen("emotional.desperation_high", self._on_desperation)
        self._listen("emotional.calm_low", self._on_stress)
        # Load persisted relationship state
        self._load_state()

    def _get_state_path(self):
        if self._config:
            habitat_dir = self._config.get("habitat_dir", "")
            if habitat_dir:
                import os
                p = os.path.join(habitat_dir, "humoral", "state.json")
                os.makedirs(os.path.dirname(p), exist_ok=True)
                return p
        return None

    def _save_state(self):
        """Save relationship tracking state (~2-5KB). Opponent memory + exploitation."""
        path = self._get_state_path()
        if not path:
            return
        import json
        state = {
            "exploitation_tracker": dict(self._exploitation_tracker),
            "threat_level": round(self._threat_level, 4),
            "total_interactions": self._total_interactions,
            # Save antigen signatures (opponent behavior summaries)
            "antigen_registry": {
                k: {"source": v.source, "pattern": v.pattern[-10:],
                     "exposures": v.exposures, "affinity": round(v.affinity, 3)}
                for k, v in list(self._antigen_registry.items())[:30]
            },
            "antibodies": {
                k: {"target": v.target, "action": v.action, "strength": round(v.strength, 3)}
                for k, v in list(self._antibodies.items())[:30]
            },
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception:
            pass

    def _load_state(self):
        """Load relationship tracking from habitat."""
        path = self._get_state_path()
        if not path:
            return
        import json, os
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._exploitation_tracker = defaultdict(float, state.get("exploitation_tracker", {}))
            self._threat_level = state.get("threat_level", 0.0)
            self._total_interactions = state.get("total_interactions", 0)
            # Restore antigens
            for k, v in state.get("antigen_registry", {}).items():
                try:
                    self._antigen_registry[k] = AntigenSignature(
                        source=v.get("source", k),
                        pattern=v.get("pattern", []),
                        exposures=v.get("exposures", 1),
                        affinity=v.get("affinity", 0.5),
                    )
                except Exception:
                    pass
            # Restore antibodies
            for k, v in state.get("antibodies", {}).items():
                try:
                    self._antibodies[k] = Antibody(
                        target=v.get("target", k),
                        action=v.get("action", "distrust"),
                        strength=v.get("strength", 0.5),
                    )
                except Exception:
                    pass
            logger.info(f"Humoral: loaded state (opponents={len(self._antigen_registry)}, antibodies={len(self._antibodies)})")
        except Exception as e:
            logger.debug(f"Humoral: failed to load state: {e}")

    # ============================================================
    # Emotion-Immune Circuit (Phase 6)
    # ============================================================

    def _on_desperation(self, desperation: float = 0.0, **kwargs):
        """
        Desperation 상승 → 면역 과민화.
        생물학: 코르티솔(스트레스 호르몬) → 면역계 과잉 반응 → 자가면역 위험.
        """
        # Temporarily boost threat sensitivity
        boost = min(0.3, desperation * 0.5)
        self._threat_level = min(1.0, self._threat_level + boost)
        self._emit("humoral.stress_sensitized",
                    desperation=desperation, threat_boost=boost)
        logger.debug(f"Humoral: desperation {desperation:.2f} → threat boost +{boost:.2f}")

    def _on_stress(self, calm: float = 0.0, arousal: float = 0.0, **kwargs):
        """
        Low calm + high arousal → 면역 경계 강화.
        생물학: 교감신경 활성화 → 면역 세포 동원.
        """
        stress = max(0, arousal - calm)
        if stress > 0.3:
            self._threat_level = min(1.0, self._threat_level + stress * 0.2)
            self._emit("humoral.stress_alert", stress_level=stress)
            logger.debug(f"Humoral: stress {stress:.2f} → threat level {self._threat_level:.2f}")

    # ============================================================
    # Provides: report_interaction
    # ============================================================

    def handle_report_interaction(
        self,
        opponent: str,
        opponent_action: str,
        my_action: str,
        my_score: int,
        opponent_score: int,
        round_num: int = 0,
    ) -> dict:
        """
        상대의 행동을 보고받아 면역 반응을 처리한다.

        Returns: 현재 위협 평가 + 권장 행동
        """
        self._total_interactions += 1

        # 상호작용 기록
        interaction = {
            "opponent": opponent,
            "opponent_action": opponent_action,
            "my_action": my_action,
            "my_score": my_score,
            "opponent_score": opponent_score,
            "round": round_num,
            "timestamp": time.time(),
        }
        self._interaction_history[opponent].append(interaction)

        # 착취 감지 (내가 협력했는데 상대가 배신)
        exploited = (my_action == "COOPERATE" and opponent_action == "DEFECT")
        if exploited:
            self._exploitation_tracker[opponent] += 1.0

        # 항원 처리 (B Cell 파이프라인)
        if opponent_action == "DEFECT":
            self._process_antigen(opponent, opponent_action)

        # 항체 감쇠 (시간이 지나면 용서)
        if opponent_action == "COOPERATE" and opponent in self._antibodies:
            ab = self._antibodies[opponent]
            ab.strength = max(0.0, ab.strength - self.antibody_decay)
            if ab.strength <= 0.01:
                del self._antibodies[opponent]
                self._emit("humoral.antibody.cleared", opponent=opponent)

        # 위협 수준 업데이트
        self._update_threat_level()

        # 반응 결정
        assessment = self._assess_opponent(opponent)

        # Bus에 시그널 발생 (다른 블록이 반응 가능)
        if assessment["threat_level"] > 0.5:
            self._emit("humoral.threat_detected",
                        opponent=opponent,
                        threat_level=assessment["threat_level"],
                        recommendation=assessment["recommendation"])

        if exploited:
            self._emit("humoral.exploitation_detected",
                        opponent=opponent,
                        total_exploitations=self._exploitation_tracker[opponent])

        # Save state after each interaction (relationship changed)
        self._save_state()

        return assessment

    # ============================================================
    # Provides: get_threat_assessment
    # ============================================================

    def handle_get_threat_assessment(self, opponent: str = "") -> dict:
        """특정 상대 또는 전체에 대한 위협 평가"""
        if opponent:
            return self._assess_opponent(opponent)
        return {
            "threat_level": self._threat_level,
            "opponents": {
                opp: self._assess_opponent(opp)
                for opp in self._antigen_registry
            },
        }

    # ============================================================
    # Provides: get_humoral_status
    # ============================================================

    def handle_get_humoral_status(self) -> HumoralStatus:
        """체액성 면역 시스템 현재 상태"""
        most_threatening = ""
        max_threat = 0.0
        for opp in self._antigen_registry:
            assessment = self._assess_opponent(opp)
            if assessment["threat_level"] > max_threat:
                max_threat = assessment["threat_level"]
                most_threatening = opp

        return HumoralStatus(
            memory_cells=len(self._antigen_registry),
            active_antibodies=len(self._antibodies),
            threat_level=self._threat_level,
            exploitation_score=sum(self._exploitation_tracker.values()),
            total_antigens_seen=self._total_interactions,
            most_threatening=most_threatening,
        )

    # ============================================================
    # Internal: B Cell Pipeline
    # ============================================================

    def _process_antigen(self, opponent: str, action: str):
        """
        항원 처리 — B Cell 활성화 파이프라인.

        1. 기존 기억 있는지 확인 (Memory B Cell)
        2. 없으면 Naive B Cell로 첫 인식
        3. threshold 넘으면 Memory B Cell 생성 + Antibody 분비
        4. 이미 기억 있으면 Affinity Maturation
        """
        now = time.time()

        if opponent in self._antigen_registry:
            # Secondary Response — Memory B Cell 활성화
            sig = self._antigen_registry[opponent]
            sig.pattern.append(action)
            sig.last_seen = now
            sig.exposures += 1

            # Affinity Maturation — 노출 시 매칭 정확도 증가
            sig.affinity = min(1.0, sig.affinity + self.affinity_maturation_rate)

            # Antibody 강화
            if opponent in self._antibodies:
                ab = self._antibodies[opponent]
                ab.strength = min(1.0, ab.strength + 0.2)
                ab.produced_at = now
            else:
                # Re-produce antibody
                self._produce_antibody(opponent, sig)

            self._emit("humoral.secondary_response",
                        opponent=opponent, exposures=sig.exposures,
                        affinity=sig.affinity)
            logger.debug(f"Humoral: secondary response to {opponent} "
                        f"(exposures={sig.exposures}, affinity={sig.affinity:.2f})")

        else:
            # Primary Response — Naive B Cell
            history = self._interaction_history[opponent]
            defect_count = sum(1 for h in history if h["opponent_action"] == "DEFECT")

            if defect_count >= self.activation_threshold:
                # Activation — Memory B Cell 분화
                pattern = [h["opponent_action"] for h in history[-10:]]
                sig = AntigenSignature(
                    source=opponent,
                    pattern=pattern,
                    first_seen=history[0]["timestamp"] if history else now,
                    last_seen=now,
                    exposures=defect_count,
                    affinity=0.3 + 0.1 * min(defect_count, 5),
                )
                self._antigen_registry[opponent] = sig

                # Antibody 생성
                self._produce_antibody(opponent, sig)

                self._emit("humoral.memory_cell_created",
                            opponent=opponent, defect_count=defect_count)
                logger.info(f"Humoral: Memory B Cell created for {opponent} "
                           f"(defects={defect_count}, affinity={sig.affinity:.2f})")

                # Capacity 관리
                if len(self._antigen_registry) > self.memory_capacity:
                    oldest = min(self._antigen_registry.values(), key=lambda s: s.last_seen)
                    del self._antigen_registry[oldest.source]
                    self._antibodies.pop(oldest.source, None)

    def _produce_antibody(self, opponent: str, signature: AntigenSignature):
        """항체 생성 — 방어 행동 신호"""
        # 항체 강도 = 노출 횟수 + 친화도
        strength = min(1.0, 0.3 + signature.exposures * 0.1 + signature.affinity * 0.3)

        # 권장 행동 결정
        if strength > 0.7:
            action = "defect"       # 강한 항체 → 즉시 배신
        elif strength > 0.4:
            action = "distrust"     # 중간 → 경계
        else:
            action = "caution"      # 약한 → 주의

        ab = Antibody(
            target=opponent,
            action=action,
            strength=strength,
        )
        self._antibodies[opponent] = ab

        self._emit("humoral.antibody_produced",
                    opponent=opponent, action=action, strength=strength)
        logger.info(f"Humoral: Antibody produced for {opponent} "
                   f"(action={action}, strength={strength:.2f})")

    # ============================================================
    # Internal: Assessment
    # ============================================================

    def _assess_opponent(self, opponent: str) -> dict:
        """상대에 대한 위협 평가"""
        history = self._interaction_history.get(opponent, [])
        if not history:
            return {
                "threat_level": 0.0,
                "recommendation": "cooperate",
                "confidence": 0.0,
                "memory_cell": False,
                "antibody_strength": 0.0,
            }

        total = len(history)
        defects = sum(1 for h in history if h["opponent_action"] == "DEFECT")
        defect_rate = defects / total

        # Recent trend (최근 5 라운드 가중)
        recent = history[-5:]
        recent_defects = sum(1 for h in recent if h["opponent_action"] == "DEFECT")
        recent_rate = recent_defects / len(recent)

        # 착취 횟수
        exploitations = self._exploitation_tracker.get(opponent, 0)

        # 위협 수준 계산
        threat = (defect_rate * 0.3 + recent_rate * 0.5 + min(1.0, exploitations / 5) * 0.2)

        # 항체 강도
        ab_strength = 0.0
        if opponent in self._antibodies:
            ab_strength = self._antibodies[opponent].strength

        # Memory cell 여부
        has_memory = opponent in self._antigen_registry

        # 신뢰도 (데이터 많을수록 높음)
        confidence = min(1.0, total / 10)

        # Affinity 반영 (Memory Cell 있으면 위협 판단이 더 정확)
        if has_memory:
            affinity = self._antigen_registry[opponent].affinity
            threat = threat * (0.5 + affinity * 0.5)  # affinity가 높을수록 위협 판단 증폭

        # 권장 행동
        if ab_strength > 0.6 or (threat > 0.6 and confidence > 0.3):
            recommendation = "defect"
        elif ab_strength > 0.3 or threat > 0.3:
            recommendation = "distrust"
        else:
            recommendation = "cooperate"

        return {
            "threat_level": min(1.0, threat),
            "recommendation": recommendation,
            "confidence": confidence,
            "memory_cell": has_memory,
            "antibody_strength": ab_strength,
            "defect_rate": defect_rate,
            "recent_defect_rate": recent_rate,
            "exploitations": exploitations,
        }

    def _update_threat_level(self):
        """전체 위협 수준 업데이트"""
        if not self._interaction_history:
            self._threat_level = 0.0
            return

        threats = []
        for opponent in self._interaction_history:
            assessment = self._assess_opponent(opponent)
            threats.append(assessment["threat_level"])

        self._threat_level = max(threats) if threats else 0.0

    # ============================================================
    # Signal Handler
    # ============================================================

    def _on_round_ended(self, **kwargs):
        """게임 라운드 종료 시 자동 호출 (GameManager가 시그널 발생 시)"""
        opponent = kwargs.get("opponent", "")
        opponent_action = kwargs.get("opponent_action", "")
        my_action = kwargs.get("my_action", "")
        my_score = kwargs.get("my_score", 0)
        opponent_score = kwargs.get("opponent_score", 0)
        round_num = kwargs.get("round_num", 0)

        if opponent and opponent_action:
            self.handle_report_interaction(
                opponent=opponent,
                opponent_action=opponent_action,
                my_action=my_action,
                my_score=my_score,
                opponent_score=opponent_score,
                round_num=round_num,
            )
