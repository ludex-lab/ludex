"""
VitalSigns — 생체 신호 (항상성 모니터링)

유기체의 현재 건강 상태를 나타내는 지표 집합.

Reference:
- CC: models.py UsageSummary, cost_tracker.py
- OC: infra/agent-events.ts AgentEventPayload, gateway/channel-health-monitor.ts
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TimeAwareness:
    """유기체의 시간 인식"""
    created_at: float = 0.0             # 탄생 시각 (epoch)
    age_seconds: float = 0.0            # 나이 (초)
    last_activity_at: float = 0.0       # 마지막 활동 시각
    idle_seconds: float = 0.0           # 유휴 시간 (초)
    is_idle: bool = False               # 유휴 상태인가 (idle_threshold 초과)
    turn_timestamps: tuple = ()         # 최근 턴들의 시각 (최대 20개)
    avg_turn_interval: float = 0.0      # 평균 턴 간격 (초) — 심박 주기와 유사


@dataclass(frozen=True)
class EmotionalVitals:
    """유기체의 감정 상태 (Anthropic 감정 벡터 연구 기반)"""
    valence: float = 0.0            # 긍/부정 (-1 ~ +1)
    arousal: float = 0.0            # 각성 수준 (0 ~ 1)
    desperation: float = 0.0        # 절망 수준 (misalignment 위험 지표)
    calm: float = 1.0               # 평온 수준 (안정성 지표)
    dominant_emotion: str = ""      # 가장 강한 감정
    confidence: float = 0.0         # 추정 신뢰도 (0 ~ 1)
    estimation_method: str = "none" # "behavioral", "classifier", "vector"
    emotion_scores: tuple = ()      # full mode: ((emotion, score), ...) sorted by score desc


@dataclass(frozen=True)
class VitalSigns:
    """유기체의 현재 생체 신호 (불변 스냅샷)"""

    # 시간 인식
    temporal: TimeAwareness = field(default_factory=TimeAwareness)

    # 대사 지표
    tokens_per_turn: float = 0.0
    token_budget_remaining: float = 0.0
    context_utilization: float = 0.0        # 0.0 ~ 1.0

    # 순환 지표
    total_turns: int = 0
    error_rate: float = 0.0                 # 0.0 ~ 1.0
    latency_ms: float = 0.0

    # 면역 지표
    consecutive_failures: int = 0
    circuit_breaker_open: bool = False

    # 기억 지표
    memory_entries: int = 0
    memory_recall_hit_rate: float = 0.0

    # 감정 지표
    emotional: EmotionalVitals = field(default_factory=EmotionalVitals)

    # 메타
    measured_at: float = field(default_factory=time.time)


@dataclass
class Regulation:
    """항상성 컨트롤러가 내리는 조절 명령"""
    type: str               # "negative_feedback", "positive_feedback", "immune_response"
    trigger: str            # 무엇이 이 조절을 촉발했는지
    action: str             # 어떤 조치를 취할지
    reason: str             # 사람이 읽을 수 있는 설명


class HomeostasisController:
    """유기체의 항상성을 유지하는 자동 조절 장치"""

    def __init__(self, setpoints: dict[str, float] | None = None):
        self.setpoints = setpoints or {
            "error_rate": 0.05,
            "context_utilization": 0.85,
            "latency_ms": 10000,
            "tokens_per_turn": 1000,
            "consecutive_failures_max": 5,
        }

    def check(self, vitals: VitalSigns) -> list[Regulation]:
        """생체 신호를 확인하고 필요한 조절 명령을 반환"""
        regulations = []

        # Negative feedback: 에러율 과다 → 모델 전환
        if vitals.error_rate > self.setpoints["error_rate"] * 2:
            regulations.append(Regulation(
                type="negative_feedback",
                trigger="error_rate_high",
                action="switch_model",
                reason=f"Error rate {vitals.error_rate:.1%} exceeds 2x setpoint ({self.setpoints['error_rate']:.0%})"
            ))

        # Negative feedback: 컨텍스트 포화 → 압축
        if vitals.context_utilization > self.setpoints["context_utilization"]:
            regulations.append(Regulation(
                type="negative_feedback",
                trigger="context_saturation",
                action="compact_context",
                reason=f"Context utilization {vitals.context_utilization:.0%} exceeds {self.setpoints['context_utilization']:.0%}"
            ))

        # Immune response: 연속 실패 → 서킷 브레이커
        if vitals.consecutive_failures >= self.setpoints["consecutive_failures_max"]:
            regulations.append(Regulation(
                type="immune_response",
                trigger="consecutive_failures",
                action="open_circuit_breaker",
                reason=f"{vitals.consecutive_failures} consecutive failures"
            ))

        # Positive feedback: 회복 → 서킷 브레이커 해제
        if vitals.consecutive_failures == 0 and vitals.circuit_breaker_open:
            regulations.append(Regulation(
                type="positive_feedback",
                trigger="recovery_detected",
                action="close_circuit_breaker",
                reason="System recovered"
            ))

        # Negative feedback: 토큰 소비 과다
        if vitals.tokens_per_turn > self.setpoints["tokens_per_turn"] * 1.5:
            regulations.append(Regulation(
                type="negative_feedback",
                trigger="high_token_consumption",
                action="reduce_context_window",
                reason=f"Token rate {vitals.tokens_per_turn:.0f}/turn exceeds budget"
            ))

        return regulations
