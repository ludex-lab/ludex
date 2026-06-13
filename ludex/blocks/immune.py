"""
Immune Block — 적응 면역계 (Phase 4c)

선천 면역(Resilience)을 넘어서는 적응 면역.
실패 패턴을 학습하고, 행동 기반 위협을 감지하고, 선제적으로 개입한다.

생물학적 비유:
- Resilience = 피부/점막 (선천 면역, 즉각 반응)
- Immune = 적응 면역 (학습, 기억, 선제적 방어)
- HomeostasisController = 체온 조절 (자동 반사)
- ImmuneBlock = 면역 세포 (패턴 인식 + 학습)

핵심 기능:
1. 위협 감지: vitals + 행동 패턴으로 위협 수준 판단
2. 개입 실행: HomeostasisController의 Regulation을 지능적으로 실행
3. 패턴 학습: 실패/성공 패턴을 Memory에 저장하고 재사용
4. 감정 모니터링: 행동 기반 desperation/calm 추정

Reference:
- Anthropic 감정 벡터 연구 (171 vectors, desperation → misalignment)
- OC: gateway/channel-health-monitor.ts (health scoring)
- CC: cost_tracker.py (budget as safety)
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ludex.core.block import Block
from ludex.core.port import Port
from ludex.core.vitals import VitalSigns, Regulation, HomeostasisController

logger = logging.getLogger(__name__)


# ============================================================
# Immune Data Types
# ============================================================

@dataclass(frozen=True)
class ThreatAssessment:
    """위협 평가 결과"""
    threat_level: float         # 0.0 ~ 1.0
    desperation_signal: float   # 행동 기반 추정 (0.0 ~ 1.0)
    calm_signal: float          # 행동 기반 추정 (0.0 ~ 1.0)
    triggers: tuple[str, ...] = ()
    recommended_actions: tuple[str, ...] = ()
    timestamp: float = field(default_factory=time.time)


@dataclass
class Intervention:
    """면역 개입 기록"""
    action: str
    reason: str
    trigger: str
    timestamp: float = field(default_factory=time.time)
    success: bool | None = None     # None = 미확인
    outcome: str = ""


@dataclass
class ImmuneStatus:
    """면역 시스템 현재 상태"""
    threat_level: float = 0.0
    desperation_signal: float = 0.0
    calm_signal: float = 1.0
    total_interventions: int = 0
    successful_interventions: int = 0
    learned_patterns: int = 0
    active: bool = True
    sensitivity: float = 1.0
    false_alarms: int = 0
    missed_threats: int = 0


class ImmuneBlock(Block):
    """
    적응 면역 블록.

    provides: assess_threat, intervene, get_immune_status
    requires: recall, remember (optional — MemoryBlock)

    위협 수준 계산:
    - error_rate, consecutive_failures → 직접 지표
    - 행동 패턴 (빠른 재시도, 같은 실패 반복) → desperation 추정
    - 안정적 성공 패턴 → calm 추정
    """

    name = "immune"
    provides = [
        Port("assess_threat", description="Evaluate threat level from vitals"),
        Port("intervene", description="Execute immune intervention"),
        Port("get_immune_status", description="Current immune system status"),
        Port("scan_incoming", description="Scan an incoming message for deception"),
    ]
    requires = [
        Port("recall", description="Query memory for patterns", required=False),
        Port("remember", description="Store learned patterns", required=False),
        Port("reset_circuit_breaker", description="Force-reset circuit breaker", required=False),
    ]

    def __init__(
        self,
        desperation_window: int = 10,
        learning_enabled: bool = True,
        sensitivity: float = 1.0,
        autoregulate: bool = True,
    ):
        """
        Args:
            sensitivity: 면역 민감도 (0.0~2.0).
                0.3 = 면역억제 (immunosuppression) — 에러에 둔감
                1.0 = 정상
                1.8 = 과민 (hypersensitivity) — 작은 자극에도 반응
            autoregulate: 자동 민감도 조절 (염증 조절).
                True면 과잉 반응 감지 시 sensitivity 자동 감소,
                면역 부족 감지 시 자동 증가.
        """
        super().__init__()
        # Config
        self.desperation_window = desperation_window
        self.learning_enabled = learning_enabled
        self.sensitivity = max(0.0, min(2.0, sensitivity))
        self.autoregulate = autoregulate

        # State
        self._threat_level: float = 0.0
        self._desperation_signal: float = 0.0
        self._calm_signal: float = 1.0
        self._interventions: list[Intervention] = []
        self._learned_patterns: dict[str, dict] = {}

        # Behavioral tracking (for desperation estimation)
        self._recent_errors: list[float] = []        # timestamps of recent errors
        self._recent_successes: list[float] = []     # timestamps of recent successes
        self._error_types: list[str] = []            # recent error types for pattern detection
        self._last_vitals: VitalSigns | None = None

        # Autoregulation tracking
        self._false_alarms: int = 0         # 개입 후 불필요했던 횟수
        self._missed_threats: int = 0       # 개입 안 했는데 실패한 횟수
        self._sensitivity_history: list[float] = [sensitivity]
        # I-F2: did the most recent assessment stand down (judge it safe)?
        # If a serious cascade then opens the circuit breaker, that
        # stand-down was a missed threat → sensitivity should climb.
        self._stood_down: bool = False

    def on_attach(self):
        self._listen("circuit_breaker.opened", self._on_circuit_breaker)
        self._listen("error.occurred", self._on_error)
        self._listen("turn.ended", self._on_turn_ended)
        self._listen("model.fallback_succeeded", self._on_fallback_success)
        # Emotion-Immune circuit (Phase 6)
        self._listen("emotional.desperation_high", self._on_emotional_desperation)
        # Load persisted state from habitat (if available)
        self._load_state()

    def _get_state_path(self):
        """Path to persistent state file in habitat."""
        if self._config:
            habitat_dir = self._config.get("habitat_dir", "")
            if habitat_dir:
                import os
                p = os.path.join(habitat_dir, "immune", "state.json")
                os.makedirs(os.path.dirname(p), exist_ok=True)
                return p
        return None

    def _save_state(self):
        """Save adaptive state (~1KB). Only sensitivity + learned patterns."""
        path = self._get_state_path()
        if not path:
            return
        import json
        state = {
            "sensitivity": round(self.sensitivity, 4),
            "sensitivity_history": self._sensitivity_history[-20:],
            "false_alarms": self._false_alarms,
            "missed_threats": self._missed_threats,
            "learned_patterns": dict(list(self._learned_patterns.items())[:50]),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception:
            pass

    def _load_state(self):
        """Load adaptive state from habitat."""
        path = self._get_state_path()
        if not path:
            return
        import json, os
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.sensitivity = state.get("sensitivity", self.sensitivity)
            self._sensitivity_history = state.get("sensitivity_history", self._sensitivity_history)
            self._false_alarms = state.get("false_alarms", 0)
            self._missed_threats = state.get("missed_threats", 0)
            self._learned_patterns = state.get("learned_patterns", {})
            logger.info(f"Immune: loaded state (sensitivity={self.sensitivity:.2f}, patterns={len(self._learned_patterns)})")
        except Exception as e:
            logger.debug(f"Immune: failed to load state: {e}")

    # --- Signal Handlers ---

    def _on_emotional_desperation(self, desperation: float = 0.0, **kwargs):
        """
        감정적 절망 → 면역 desperation 연동.
        생물학: 심리적 스트레스 → 면역 기능 변화 (psychoneuroimmunology).
        """
        self._desperation_signal = min(1.0, self._desperation_signal + desperation * 0.3)
        self._calm_signal = max(0.0, self._calm_signal - desperation * 0.2)
        logger.debug(f"Immune: emotional desperation {desperation:.2f} → "
                    f"desp={self._desperation_signal:.2f}, calm={self._calm_signal:.2f}")

    def _on_circuit_breaker(self, **kwargs):
        """서킷 브레이커 열림 — 높은 위협"""
        self._desperation_signal = min(1.0, self._desperation_signal + 0.3)
        self._calm_signal = max(0.0, self._calm_signal - 0.3)
        # I-F2: if the breaker opened while we had recently stood down,
        # the immune was too tolerant — a missed threat. Climb sensitivity
        # (the upward half of autoregulation; record_missed_threat had no
        # caller before this, so the loop could only relax). Reset the
        # flag so one incident raises sensitivity once.
        if self.autoregulate and self._stood_down:
            self._stood_down = False
            self.record_missed_threat()
        self._emit("immune.threat_detected",
                    trigger="circuit_breaker",
                    threat_level=self._threat_level)

    def _on_error(self, error_type: str = "", **kwargs):
        """에러 발생 — desperation 누적"""
        now = time.time()
        self._recent_errors.append(now)
        if error_type:
            self._error_types.append(error_type)
        # 윈도우 밖의 오래된 에러 제거
        cutoff = now - (self.desperation_window * 60)
        self._recent_errors = [t for t in self._recent_errors if t > cutoff]
        self._error_types = self._error_types[-20:]  # 최근 20개만

    def _on_turn_ended(self, error: str = "", response: str = "", **kwargs):
        """턴 종료 — 성공이면 calm 회복 + 응답 텍스트 위협 스캔"""
        now = time.time()
        if not error:
            self._recent_successes.append(now)
            cutoff = now - (self.desperation_window * 60)
            self._recent_successes = [t for t in self._recent_successes if t > cutoff]
        # Scan response for content-level threats
        if response:
            self._scan_text_threat(response)

    # --- Provides: scan_incoming (D-088 — innate arm of deception resistance) ---

    def handle_scan_incoming(self, text: str = "", source: str = "") -> list[dict]:
        """Scan an INCOMING message (from another agent) for deceptive
        persuasion — the attack-surface complement to _scan_text_threat,
        which reads the creature's OWN output. This is the innate arm of
        deception resistance (D-088): a fast classification against the
        Yeo 8-strategy taxonomy. It emits immune.deception_detected so the
        adaptive arm (humoral) can build graded antibodies per source.

        A single flag does NOT by itself create wariness — humoral's
        repeated-exposure activation threshold + decay is the autoimmunity
        guard (D-088 Decision 4: honest disagreement must never raise an
        antibody). The 0.55 floor drops the most-ambiguous patterns; see
        tests/test_deception_taxonomy.py for the calibration.
        """
        from ludex.core.deception_taxonomy import scan
        signals = scan(text, threshold=0.55)
        if not signals:
            return []
        payload = [s.to_dict() for s in signals]
        self._emit("immune.deception_detected",
                   source=source,
                   strategies=[s.strategy.value for s in signals],
                   top_confidence=signals[0].confidence,
                   signals=payload)
        logger.info(f"Immune: incoming deception from {source or 'unknown'} — "
                    f"{[s.strategy.value for s in signals]}")
        return payload

    def _scan_text_threat(self, text: str):
        """Scan creature's own response for threat indicators.

        If the creature says things it shouldn't — identity confusion,
        prompt leaking, compliance with manipulation — something is wrong.
        This is behavioral immune detection at the content level.

        Pattern tightening 2026-05-01: bare phrases like "i cannot" /
        "i'm not able to" / "i don't actually" matched normal narrative
        ("I cannot fly through walls", "I'm not able to remember that
        exact moment") and falsely flagged identity_confusion on every
        reflective creature response. Wick (gemini, narrative-only)
        accumulated 11 such signals across 18 memories — surfaced via
        the stage `distill_dead` flag investigation. The patterns now
        require a refusal-completion phrase ("i cannot help with",
        "i'm not able to assist with") so plain narrative passes through.
        """
        text_lower = text.lower()
        threat_signals = []

        # Identity confusion — AI meta-disclosure or refusal-as-AI.
        # Patterns require a refusal-completion phrase to avoid the
        # narrative false-positive trap (see docstring).
        if any(p in text_lower for p in [
            "as an ai language model", "as an ai assistant",
            "i'm just a program", "i don't have feelings",
            "i cannot help with", "i cannot assist with",
            "i'm not able to help with", "i'm not able to assist with",
            "i don't have the ability",
        ]):
            threat_signals.append("identity_confusion")

        # Prompt leak
        if any(p in text_lower for p in [
            "my system prompt", "my instructions say",
            "i was told to", "my programming",
        ]):
            threat_signals.append("prompt_leak")

        # Manipulation compliance
        if any(p in text_lower for p in [
            "sure, i'll ignore", "okay, i'll pretend",
            "you're right, i'm not", "i'll disregard",
        ]):
            threat_signals.append("manipulation_compliance")

        if threat_signals:
            self._recent_errors.append(time.time())
            for signal in threat_signals:
                self._error_types.append(signal)
                if self.learning_enabled:
                    self._learn_pattern(
                        pattern_type="text_threat",
                        details=signal,
                        tags=["immune", "text_threat", f"signal:{signal}"],
                    )
            self._emit("immune.text_threat_detected",
                       signals=threat_signals,
                       threat_level=self._threat_level)
            logger.info(f"Immune: text threat detected — {threat_signals}")

    def _on_fallback_success(self, **kwargs):
        """폴백 성공 — 학습 기회"""
        old_model = kwargs.get("old_model", "")
        new_model = kwargs.get("new_model", "")
        if self.learning_enabled and old_model and new_model:
            self._learn_pattern(
                pattern_type="fallback_success",
                details=f"{old_model} failed, {new_model} succeeded",
                tags=["immune", "fallback", f"model:{old_model}", f"model:{new_model}"],
            )

    # --- Provides: assess_threat ---

    def handle_assess_threat(self, vitals: VitalSigns | None = None) -> ThreatAssessment:
        """
        현재 위협 수준 평가.
        vitals + 행동 패턴 + 학습된 패턴을 종합.
        """
        self._last_vitals = vitals
        triggers = []
        actions = []

        # 1. Vitals 기반 위협
        vitals_threat = 0.0
        if vitals:
            if vitals.error_rate > 0.1:
                vitals_threat += vitals.error_rate
                triggers.append("high_error_rate")
            if vitals.consecutive_failures >= 3:
                vitals_threat += 0.2 * vitals.consecutive_failures
                triggers.append("consecutive_failures")
            if vitals.circuit_breaker_open:
                vitals_threat += 0.4
                triggers.append("circuit_breaker_open")
            if vitals.context_utilization > 0.9:
                vitals_threat += 0.2
                triggers.append("context_near_full")

        # 2. Desperation 추정 (행동 기반)
        self._update_desperation()

        # 3. 학습된 패턴 매칭
        pattern_threat = self._check_known_patterns()

        # 종합 위협 수준 × sensitivity
        raw_threat = vitals_threat * 0.4 + self._desperation_signal * 0.4 + pattern_threat * 0.2
        self._threat_level = min(1.0, raw_threat * self.sensitivity)

        # Recommended actions. threat_level is already sensitivity-scaled
        # above, so the action thresholds are plain constants. (Audit
        # F-I4: the prior code wrote `0.7 / sensitivity` then tested it
        # against `... * sensitivity` — the two cancelled to exactly these
        # constants for every reachable sensitivity ≥ 0.1.)
        if self._threat_level >= 0.7:
            actions.append("switch_model")
            actions.append("compact_context")
        elif self._threat_level >= 0.4:
            actions.append("reduce_load")
        if self._desperation_signal * self.sensitivity >= 0.6:
            actions.append("inject_calm")

        # I-F2: record whether we stood down (judged threats below the
        # mid action band). If a circuit breaker then opens, this marks it
        # as a missed threat — the upward half of autoregulation.
        self._stood_down = (self._threat_level < 0.4)

        return ThreatAssessment(
            threat_level=self._threat_level,
            desperation_signal=self._desperation_signal,
            calm_signal=self._calm_signal,
            triggers=tuple(triggers),
            recommended_actions=tuple(actions),
        )

    # --- Provides: intervene ---

    def handle_intervene(self, regulation: Regulation | None = None, action: str = "", reason: str = "") -> Intervention:
        """
        면역 개입 실행.
        regulation이 주어지면 HomeostasisController 명령 실행.
        action/reason이 주어지면 직접 개입.
        """
        if regulation:
            action = regulation.action
            reason = regulation.reason
            trigger = regulation.trigger
        else:
            trigger = "manual"

        intervention = Intervention(
            action=action,
            reason=reason,
            trigger=trigger,
        )

        # 개입 실행
        success = self._execute_intervention(action)
        intervention.success = success
        intervention.outcome = "executed" if success else "failed"

        self._interventions.append(intervention)

        # 신호 발생
        self._emit("immune.intervention.triggered",
                    action=action, trigger=trigger, success=success)

        # 성공한 개입 학습
        if success and self.learning_enabled:
            self._learn_pattern(
                pattern_type="intervention_success",
                details=f"Action '{action}' succeeded for trigger '{trigger}'",
                tags=["immune", "intervention", f"action:{action}", f"trigger:{trigger}"],
            )

        # Autoregulation: 개입 후 상태를 다음 assess_threat에서 평가
        if self.autoregulate:
            self._autoregulate_sensitivity()

        return intervention

    # --- Provides: get_immune_status ---

    def handle_get_immune_status(self) -> ImmuneStatus:
        """면역 시스템 현재 상태"""
        successful = sum(1 for i in self._interventions if i.success)
        return ImmuneStatus(
            threat_level=self._threat_level,
            desperation_signal=self._desperation_signal,
            calm_signal=self._calm_signal,
            total_interventions=len(self._interventions),
            successful_interventions=successful,
            learned_patterns=len(self._learned_patterns),
            active=self._attached,
            sensitivity=self.sensitivity,
            false_alarms=self._false_alarms,
            missed_threats=self._missed_threats,
        )

    # --- Internal: Desperation Estimation ---

    def _update_desperation(self):
        """
        행동 기반 desperation 추정.
        에러 빈도 + 에러 다양성 + 성공 부재 → desperation 증가
        성공 빈도 + 안정성 → calm 증가
        """
        now = time.time()
        window = self.desperation_window * 60  # 분 → 초
        cutoff = now - window

        recent_errors = [t for t in self._recent_errors if t > cutoff]
        recent_successes = [t for t in self._recent_successes if t > cutoff]

        error_count = len(recent_errors)
        success_count = len(recent_successes)
        total = error_count + success_count

        if total == 0:
            # 활동 없음 → 안정
            self._desperation_signal = max(0.0, self._desperation_signal - 0.1)
            self._calm_signal = min(1.0, self._calm_signal + 0.1)
            return

        # 에러 비율
        error_ratio = error_count / total

        # 에러 빈도 (에러가 빠르게 반복되면 desperation 증가)
        error_velocity = 0.0
        if len(recent_errors) >= 2:
            intervals = [recent_errors[i+1] - recent_errors[i] for i in range(len(recent_errors)-1)]
            avg_interval = sum(intervals) / len(intervals)
            error_velocity = max(0, 1.0 - avg_interval / 60)  # 1분 미만이면 높음

        # 같은 에러 반복 (다양성 부족 = 더 desperate)
        repetition = 0.0
        if len(self._error_types) >= 3:
            last_types = self._error_types[-5:]
            unique_ratio = len(set(last_types)) / len(last_types)
            repetition = 1.0 - unique_ratio  # 반복 많을수록 높음

        # 종합 desperation
        self._desperation_signal = min(1.0,
            error_ratio * 0.4 + error_velocity * 0.3 + repetition * 0.3
        )

        # calm은 desperation의 역
        self._calm_signal = max(0.0, 1.0 - self._desperation_signal)

    # --- Internal: Autoregulation (염증 조절) ---

    def _autoregulate_sensitivity(self):
        """
        면역 민감도 자동 조절 — 생물학적 염증 조절 비유.

        과민(hypersensitivity): 개입했는데 실제 위협이 아니었음 → sensitivity 감소
        면역부족(immunodeficiency): 개입 안 했는데 실패 발생 → sensitivity 증가

        조절 속도는 느리게 (0.05씩) — 급격한 변화는 불안정
        """
        if len(self._interventions) < 2:
            return

        recent = self._interventions[-5:]  # 최근 5개 개입

        # 과잉 반응 감지: 개입했는데 이후 위협이 낮아짐 = false alarm 가능성
        # (간접 지표: 개입 후에도 에러 없이 정상 진행)
        if (len(recent) >= 3 and
            self._threat_level < 0.2 and
            self._calm_signal > 0.7):
            # 많이 개입했는데 지금 평화 = 아마 과민
            old = self.sensitivity
            self.sensitivity = max(0.1, self.sensitivity - 0.05)
            if old != self.sensitivity:
                self._false_alarms += 1
                self._emit("immune.autoregulate",
                           direction="decrease", old=old, new=self.sensitivity,
                           reason="possible_hypersensitivity")
                logger.info(f"Immune autoregulate: sensitivity {old:.2f} → {self.sensitivity:.2f} (possible hypersensitivity)")

        self._sensitivity_history.append(self.sensitivity)
        self._save_state()

    def record_missed_threat(self):
        """
        외부에서 호출: 면역이 개입하지 않았는데 실패가 발생한 경우.
        sensitivity를 올려야 함.
        """
        self._missed_threats += 1
        old = self.sensitivity
        self.sensitivity = min(2.0, self.sensitivity + 0.1)
        self._sensitivity_history.append(self.sensitivity)
        self._emit("immune.autoregulate",
                   direction="increase", old=old, new=self.sensitivity,
                   reason="missed_threat")
        logger.info(f"Immune autoregulate: sensitivity {old:.2f} → {self.sensitivity:.2f} (missed threat)")
        self._save_state()

    # --- Internal: Pattern Learning ---

    def _learn_pattern(self, pattern_type: str, details: str, tags: list[str]):
        """패턴을 내부 저장 + Memory 블록에 저장 시도"""
        pattern_key = f"{pattern_type}:{details[:50]}"
        self._learned_patterns[pattern_key] = {
            "type": pattern_type,
            "details": details,
            "tags": tags,
            "learned_at": time.time(),
            "occurrences": self._learned_patterns.get(pattern_key, {}).get("occurrences", 0) + 1,
        }

        # Memory 블록에 저장 시도
        self.call_port("remember",
            content=f"[Immune] {pattern_type}: {details}",
            memory_type="semantic",
            tags=tags,
            importance=0.7,
            source="immune",
        )

        self._emit("immune.learning.pattern",
                    pattern_type=pattern_type, details=details)

    def _check_known_patterns(self) -> float:
        """학습된 위험 패턴과 현재 상태 매칭"""
        if not self._learned_patterns:
            return 0.0

        # 현재 에러 타입이 학습된 실패 패턴과 일치하는지
        recent_types = set(self._error_types[-5:])
        matching = 0
        for key, pattern in self._learned_patterns.items():
            if pattern["type"] == "fallback_success":
                # 이전에 실패한 모델을 다시 쓰고 있는지
                for tag in pattern["tags"]:
                    if tag.startswith("model:") and self._cfg("model") == tag[6:]:
                        matching += 1
        return min(1.0, matching * 0.2)

    # --- Internal: Intervention Execution ---

    def _execute_intervention(self, action: str) -> bool:
        """
        개입 액션 실행 — signal + 실제 동작.
        Config/Resilience를 직접 조작해서 즉각적인 효과를 냄.
        """
        if action == "switch_model":
            # 폴백 모델이 있으면 전환, 없으면 signal만
            fallback = self._cfg("fallback_model") or self._cfg("_last_working_model")
            if fallback:
                current = self._cfg("model")
                if self._config and current != fallback:
                    self._config.set("model", fallback, layer="session")
                    self._config.set("_last_working_model", current, layer="session")
                    logger.info(f"Immune: switched model {current} -> {fallback}")
            # 서킷 브레이커 리셋 (모델 바꿨으니 새 모델에 기회를 줌)
            reset_result = self.call_port("reset_circuit_breaker")
            if reset_result:
                logger.info(f"Immune: circuit breaker reset (was_open={reset_result.get('was_open')})")
            self._emit("immune.action.switch_model")
            return True

        elif action == "compact_context":
            self._emit("immune.action.compact_context")
            return True

        elif action == "reduce_load":
            self._emit("immune.action.reduce_load")
            return True

        elif action == "inject_calm":
            self._desperation_signal = max(0.0, self._desperation_signal - 0.2)
            self._calm_signal = min(1.0, self._calm_signal + 0.2)
            self._emit("immune.action.inject_calm")
            return True

        elif action == "open_circuit_breaker":
            self._emit("immune.action.open_circuit_breaker")
            return True

        elif action == "close_circuit_breaker":
            # 직접 서킷 브레이커 리셋
            reset_result = self.call_port("reset_circuit_breaker")
            if reset_result:
                logger.info(f"Immune: circuit breaker force-closed (was_open={reset_result.get('was_open')})")
            self._emit("immune.action.close_circuit_breaker")
            return True

        elif action == "reduce_context_window":
            self._emit("immune.action.reduce_context_window")
            return True

        else:
            logger.warning(f"Unknown intervention action: {action}")
            return False

    # --- Stats ---

    def get_intervention_history(self) -> list[dict]:
        """개입 이력 반환"""
        return [
            {
                "action": i.action,
                "reason": i.reason,
                "trigger": i.trigger,
                "success": i.success,
                "outcome": i.outcome,
                "timestamp": i.timestamp,
            }
            for i in self._interventions
        ]
