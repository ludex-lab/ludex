"""
Phase 4c Tests — Immune Block

1. Threat Assessment: vitals + 행동 기반 위협 감지
2. Desperation Estimation: 에러 빈도/패턴 → desperation 추정
3. Intervention: 면역 개입 실행 + 기록
4. Pattern Learning: 실패/성공 패턴 학습
5. Signal Integration: Resilience/Memory와 연동
6. Homeostasis: Regulation 기반 개입
"""

import sys
import os
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism import Organism
from ludex.core.bus import Bus
from ludex.core.signals import Signals
from ludex.core.config import Config
from ludex.core.block import Block
from ludex.core.port import Port
from ludex.core.vitals import VitalSigns, Regulation, HomeostasisController, TimeAwareness
from ludex.blocks.provider import LLMResponse
from ludex.blocks.engine import EngineBlock
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.memory import MemoryBlock
from ludex.blocks.immune import ImmuneBlock, ThreatAssessment, Intervention, ImmuneStatus


class MockProvider(Block):
    name = "provider"
    provides = [Port("llm_call"), Port("health_check"), Port("list_models")]
    requires = []
    def handle_llm_call(self, prompt="", **kwargs):
        return LLMResponse(content=f"Response to: {prompt[:30]}", model="mock",
                           tokens_in=10, tokens_out=5, latency_ms=50.0)
    def handle_health_check(self): return {"status": "healthy"}
    def handle_list_models(self): return ["mock"]


def _make_organism(*extra_blocks):
    """테스트용 유기체 ���성"""
    blocks = [MockProvider(), EngineBlock(), TrackingBlock()] + list(extra_blocks)
    org = Organism(name="test_immune", blocks=blocks)
    return org


def _make_immune_standalone():
    """ImmuneBlock만 독립 테스트"""
    immune = ImmuneBlock()
    bus = Bus()
    signals = Signals()
    config = Config()
    immune.attach(bus, signals, config)
    return immune, bus, signals, config


# ============================================================
# 1. Threat Assessment
# ============================================================

def test_assess_threat_healthy():
    """건강한 상태에서 위협 수준은 낮아야 함"""
    immune, bus, signals, config = _make_immune_standalone()
    vitals = VitalSigns(error_rate=0.0, consecutive_failures=0, context_utilization=0.3)
    assessment = immune.handle_assess_threat(vitals)
    assert isinstance(assessment, ThreatAssessment)
    assert assessment.threat_level < 0.2
    assert assessment.calm_signal > 0.5
    assert len(assessment.triggers) == 0


def test_assess_threat_high_error_rate():
    """높은 에러율 → 위협 감지"""
    immune, bus, signals, config = _make_immune_standalone()
    vitals = VitalSigns(error_rate=0.3, consecutive_failures=2)
    assessment = immune.handle_assess_threat(vitals)
    assert assessment.threat_level > 0.0
    assert "high_error_rate" in assessment.triggers


def test_assess_threat_circuit_breaker():
    """서킷 브레이커 열림 → 높은 위협"""
    immune, bus, signals, config = _make_immune_standalone()
    vitals = VitalSigns(circuit_breaker_open=True, consecutive_failures=5)
    assessment = immune.handle_assess_threat(vitals)
    assert assessment.threat_level > 0.2
    assert "circuit_breaker_open" in assessment.triggers
    assert "consecutive_failures" in assessment.triggers


def test_assess_threat_context_full():
    """컨텍스트 거의 가득 참 → 위협"""
    immune, bus, signals, config = _make_immune_standalone()
    vitals = VitalSigns(context_utilization=0.95)
    assessment = immune.handle_assess_threat(vitals)
    assert "context_near_full" in assessment.triggers


def test_assess_threat_recommends_actions():
    """높은 위협 → 조치 권장"""
    immune, bus, signals, config = _make_immune_standalone()
    vitals = VitalSigns(error_rate=0.5, circuit_breaker_open=True, consecutive_failures=5)
    assessment = immune.handle_assess_threat(vitals)
    assert assessment.threat_level >= 0.4
    assert len(assessment.recommended_actions) > 0


def test_action_thresholds_are_clean_constants():
    """F-I4 (2026-06-12): action thresholds are plain constants — 0.7 high
    (switch_model + compact), 0.4 mid (reduce_load) — after removing the
    sensitivity-cancelling math. Pin the two bands."""
    immune, *_ = _make_immune_standalone()
    mid = immune.handle_assess_threat(VitalSigns(error_rate=0.5, consecutive_failures=3))
    assert 0.4 <= mid.threat_level < 0.7
    assert "reduce_load" in mid.recommended_actions
    assert "switch_model" not in mid.recommended_actions

    immune2, *_ = _make_immune_standalone()
    hi = immune2.handle_assess_threat(
        VitalSigns(error_rate=0.5, circuit_breaker_open=True, consecutive_failures=5))
    assert hi.threat_level >= 0.7
    assert "switch_model" in hi.recommended_actions
    assert "compact_context" in hi.recommended_actions


def test_threat_threshold_param_removed():
    """F-I5 (2026-06-12): the dead threat_threshold constructor param is gone."""
    import inspect
    assert "threat_threshold" not in inspect.signature(ImmuneBlock.__init__).parameters
    assert not hasattr(ImmuneBlock(), "threat_threshold")


# ============================================================
# 2. Desperation Estimation
# ============================================================

def test_desperation_from_rapid_errors():
    """빠른 연속 에러 → desperation 증가"""
    immune, bus, signals, config = _make_immune_standalone()

    # 빠르게 에러 5개 발생
    for _ in range(5):
        signals.emit("error.occurred", error_type="api_error")

    vitals = VitalSigns(error_rate=0.5)
    assessment = immune.handle_assess_threat(vitals)
    assert assessment.desperation_signal > 0.0


def test_desperation_from_repeated_same_error():
    """같은 에러 반복 → 높은 desperation"""
    immune, bus, signals, config = _make_immune_standalone()

    for _ in range(5):
        signals.emit("error.occurred", error_type="timeout")

    vitals = VitalSigns(error_rate=0.3)
    assessment = immune.handle_assess_threat(vitals)
    # 같은 에러 반복 = repetition이 높음
    assert assessment.desperation_signal > 0.0


def test_calm_from_successes():
    """연속 성공 → calm 유지"""
    immune, bus, signals, config = _make_immune_standalone()

    for _ in range(5):
        signals.emit("turn.ended", error="")

    vitals = VitalSigns(error_rate=0.0)
    assessment = immune.handle_assess_threat(vitals)
    assert assessment.calm_signal > 0.5
    assert assessment.desperation_signal < 0.5


def test_desperation_recovers_with_success():
    """에러 후 성공 → desperation 감소"""
    immune, bus, signals, config = _make_immune_standalone()

    # 먼저 에러
    for _ in range(3):
        signals.emit("error.occurred", error_type="api_error")
    signals.emit("turn.ended", error="api_error")

    # 이후 성공 연속
    for _ in range(5):
        signals.emit("turn.ended", error="")

    vitals = VitalSigns(error_rate=0.0)
    assessment = immune.handle_assess_threat(vitals)
    # 성공이 에러보다 많으므로 calm이 회복되어야 함
    assert assessment.calm_signal > 0.3


# ============================================================
# 3. Intervention
# ============================================================

def test_intervene_with_regulation():
    """Regulation 기반 개입 실행"""
    immune, bus, signals, config = _make_immune_standalone()
    reg = Regulation(
        type="negative_feedback",
        trigger="error_rate_high",
        action="switch_model",
        reason="Error rate too high",
    )
    result = immune.handle_intervene(regulation=reg)
    assert isinstance(result, Intervention)
    assert result.action == "switch_model"
    assert result.success is True
    assert result.trigger == "error_rate_high"


def test_intervene_manual():
    """수동 개입"""
    immune, bus, signals, config = _make_immune_standalone()
    result = immune.handle_intervene(action="compact_context", reason="Context too large")
    assert result.action == "compact_context"
    assert result.success is True


def test_intervene_inject_calm():
    """calm 주입 개입 — desperation 감소"""
    immune, bus, signals, config = _make_immune_standalone()
    immune._desperation_signal = 0.8
    immune._calm_signal = 0.2

    result = immune.handle_intervene(action="inject_calm", reason="High desperation")
    assert result.success is True
    assert immune._desperation_signal < 0.8
    assert immune._calm_signal > 0.2


def test_intervene_unknown_action():
    """알 수 없는 개입 → 실패"""
    immune, bus, signals, config = _make_immune_standalone()
    result = immune.handle_intervene(action="teleport", reason="Magic")
    assert result.success is False


def test_intervention_emits_signal():
    """개입 시 signal 발행"""
    immune, bus, signals, config = _make_immune_standalone()
    events = []
    signals.on("immune.intervention.triggered", lambda **kw: events.append(kw))

    immune.handle_intervene(action="reduce_load", reason="Too much")
    assert len(events) == 1
    assert events[0]["action"] == "reduce_load"
    assert events[0]["success"] is True


# ============================================================
# 4. Pattern Learning
# ============================================================

def test_learn_from_fallback_success():
    """폴백 성공 시 패턴 학습"""
    immune, bus, signals, config = _make_immune_standalone()
    immune.learning_enabled = True

    signals.emit("model.fallback_succeeded", old_model="llama", new_model="gemma")

    assert len(immune._learned_patterns) == 1
    pattern = list(immune._learned_patterns.values())[0]
    assert pattern["type"] == "fallback_success"
    assert "model:llama" in pattern["tags"]


def test_learn_from_intervention_success():
    """성공한 개입 → 패턴 학습"""
    immune, bus, signals, config = _make_immune_standalone()
    immune.learning_enabled = True

    immune.handle_intervene(action="switch_model", reason="Error rate high")

    # intervention_success 패턴이 학습됨
    assert any(p["type"] == "intervention_success" for p in immune._learned_patterns.values())


def test_learning_disabled():
    """학습 비활성화 시 패턴 저장 안 됨"""
    immune, bus, signals, config = _make_immune_standalone()
    immune.learning_enabled = False

    signals.emit("model.fallback_succeeded", old_model="llama", new_model="gemma")
    immune.handle_intervene(action="switch_model", reason="Test")

    assert len(immune._learned_patterns) == 0


def test_pattern_occurrence_count():
    """같은 패턴 반복 시 occurrence 증가"""
    immune, bus, signals, config = _make_immune_standalone()

    signals.emit("model.fallback_succeeded", old_model="llama", new_model="gemma")
    signals.emit("model.fallback_succeeded", old_model="llama", new_model="gemma")

    pattern = list(immune._learned_patterns.values())[0]
    assert pattern["occurrences"] == 2


# ============================================================
# 5. Signal Integration
# ============================================================

def test_circuit_breaker_signal_raises_desperation():
    """circuit_breaker.opened → desperation 증가"""
    immune, bus, signals, config = _make_immune_standalone()
    initial_desp = immune._desperation_signal

    signals.emit("circuit_breaker.opened", failures=5)

    assert immune._desperation_signal > initial_desp
    assert immune._calm_signal < 1.0


def test_threat_detected_signal_emitted():
    """circuit breaker → immune.threat_detected signal"""
    immune, bus, signals, config = _make_immune_standalone()
    events = []
    signals.on("immune.threat_detected", lambda **kw: events.append(kw))

    signals.emit("circuit_breaker.opened", failures=5)

    assert len(events) == 1
    assert events[0]["trigger"] == "circuit_breaker"


def test_learning_signal_emitted():
    """패턴 학습 시 signal 발행"""
    immune, bus, signals, config = _make_immune_standalone()
    events = []
    signals.on("immune.learning.pattern", lambda **kw: events.append(kw))

    signals.emit("model.fallback_succeeded", old_model="a", new_model="b")

    assert len(events) == 1
    assert events[0]["pattern_type"] == "fallback_success"


# ============================================================
# 6. Immune Status
# ============================================================

def test_immune_status_initial():
    """초기 면역 상태"""
    immune, bus, signals, config = _make_immune_standalone()
    status = immune.handle_get_immune_status()
    assert isinstance(status, ImmuneStatus)
    assert status.threat_level == 0.0
    assert status.total_interventions == 0
    assert status.learned_patterns == 0
    assert status.active is True


def test_immune_status_after_activity():
    """활동 후 면역 상태 반영"""
    immune, bus, signals, config = _make_immune_standalone()

    # 개입 2회
    immune.handle_intervene(action="switch_model", reason="test")
    immune.handle_intervene(action="teleport", reason="test")  # fails

    status = immune.handle_get_immune_status()
    assert status.total_interventions == 2
    assert status.successful_interventions == 1


def test_intervention_history():
    """개입 이력 조회"""
    immune, bus, signals, config = _make_immune_standalone()

    immune.handle_intervene(action="switch_model", reason="High error")
    immune.handle_intervene(action="compact_context", reason="Full context")

    history = immune.get_intervention_history()
    assert len(history) == 2
    assert history[0]["action"] == "switch_model"
    assert history[1]["action"] == "compact_context"


# ============================================================
# 7. Homeostasis Integration
# ============================================================

def test_homeostasis_regulations_to_immune():
    """HomeostasisController → Regulation → Immune 개입"""
    immune, bus, signals, config = _make_immune_standalone()
    controller = HomeostasisController()

    vitals = VitalSigns(
        error_rate=0.15,
        consecutive_failures=6,
        context_utilization=0.92,
    )

    regulations = controller.check(vitals)
    assert len(regulations) > 0

    # 각 regulation을 immune으로 실행
    for reg in regulations:
        result = immune.handle_intervene(regulation=reg)
        assert isinstance(result, Intervention)
        assert result.action == reg.action


def test_full_threat_response_cycle():
    """전체 사이클: vitals → assess → intervene → learn"""
    immune, bus, signals, config = _make_immune_standalone()
    controller = HomeostasisController()

    # 1. 위협적인 vitals
    vitals = VitalSigns(
        error_rate=0.2,
        consecutive_failures=3,
        circuit_breaker_open=True,
    )

    # 2. 위협 평가
    assessment = immune.handle_assess_threat(vitals)
    assert assessment.threat_level > 0.2

    # 3. Homeostasis 명령
    regulations = controller.check(vitals)

    # 4. 개입 실행
    for reg in regulations:
        immune.handle_intervene(regulation=reg)

    # 5. 상태 확인
    status = immune.handle_get_immune_status()
    assert status.total_interventions > 0
    assert status.learned_patterns > 0  # 성공한 개입은 학습됨


# ============================================================
# 8. Organism Integration
# ============================================================

def test_immune_in_organism():
    """ImmuneBlock이 Organism에 정상 조립"""
    org = _make_organism(ImmuneBlock())
    immune = org.get_block("immune")
    assert immune is not None
    assert immune.is_attached


def test_immune_with_memory():
    """ImmuneBlock + MemoryBlock 연동"""
    import tempfile, shutil
    test_dir = tempfile.mkdtemp(prefix="ludex_test_immune_")
    try:
        org = Organism(name="test_immune_memory", blocks=[
            MockProvider(), EngineBlock(), TrackingBlock(),
            MemoryBlock(storage_dir=test_dir), ImmuneBlock(),
        ])

        immune = org.get_block("immune")
        memory = org.get_block("memory")

        # 개입 → memory에 저장 시도
        immune.handle_intervene(action="switch_model", reason="test learning")

        # Memory에 immune 관련 기억이 있는지 확인
        results = memory.handle_recall(query="immune", limit=5)
        # MemoryBlock이 연결되어 있으므로 call_port("remember")가 작동
        assert immune.handle_get_immune_status().learned_patterns > 0
    finally:
        shutil.rmtree(test_dir)


# ============================================================
# 9. Immune ↔ Resilience Integration
# ============================================================

def test_immune_resets_circuit_breaker():
    """Immune 개입 시 서킷 브레이커 리셋"""
    from ludex.blocks.resilience import ResilienceBlock

    org = Organism(name="test_immune_resilience", blocks=[
        MockProvider(), EngineBlock(), TrackingBlock(),
        ResilienceBlock(circuit_breaker_threshold=3),
        ImmuneBlock(),
    ])

    resilience = org.get_block("resilience")
    immune = org.get_block("immune")

    # 서킷 브레이커를 수동으로 열기
    resilience._consecutive_failures = 5
    resilience._open_circuit()
    assert resilience._circuit_open is True

    # Immune이 close_circuit_breaker 개입
    result = immune.handle_intervene(action="close_circuit_breaker", reason="Recovery detected")
    assert result.success is True

    # 서킷 브레이커가 닫혔는지 확인
    assert resilience._circuit_open is False
    assert resilience._consecutive_failures == 0


def test_immune_switch_model_resets_circuit():
    """switch_model 개입 시 서킷 브레이커도 같이 리셋"""
    from ludex.blocks.resilience import ResilienceBlock

    org = Organism(name="test_switch_reset", blocks=[
        MockProvider(), EngineBlock(), TrackingBlock(),
        ResilienceBlock(circuit_breaker_threshold=3),
        ImmuneBlock(),
    ])

    resilience = org.get_block("resilience")
    immune = org.get_block("immune")

    # 서킷 브레이커 열기
    resilience._consecutive_failures = 5
    resilience._open_circuit()
    assert resilience._circuit_open is True

    # switch_model 개입 → 서킷 브레이커도 리셋되어야 함
    result = immune.handle_intervene(action="switch_model", reason="Error rate high")
    assert result.success is True
    assert resilience._circuit_open is False


def test_immune_switch_model_changes_config():
    """switch_model 개입 시 fallback_model로 config 변경"""
    from ludex.blocks.resilience import ResilienceBlock

    org = Organism(name="test_switch_config", blocks=[
        MockProvider(), EngineBlock(), TrackingBlock(),
        ResilienceBlock(), ImmuneBlock(),
    ], config={"model": "broken-model", "fallback_model": "gemma4:e4b"})

    immune = org.get_block("immune")

    # switch_model 개입
    immune.handle_intervene(action="switch_model", reason="Model broken")

    # config가 fallback_model로 변경되었는지
    assert org.config.get("model") == "gemma4:e4b"
    # 이전 모델이 _last_working_model에 저장
    assert org.config.get("_last_working_model") == "broken-model"


def test_full_fault_recovery_cycle():
    """전체 장애→감지→개입→복구 사이클 (Resilience 연동)"""
    from ludex.blocks.resilience import ResilienceBlock

    org = Organism(name="test_full_recovery", blocks=[
        MockProvider(), EngineBlock(), TrackingBlock(),
        ResilienceBlock(circuit_breaker_threshold=3),
        ImmuneBlock(),
    ], config={"model": "bad-model", "fallback_model": "good-model"})

    resilience = org.get_block("resilience")
    immune = org.get_block("immune")
    controller = HomeostasisController()

    # 1. 장애 상황 시뮬레이션
    resilience._consecutive_failures = 5
    resilience._open_circuit()
    for _ in range(5):
        org.signals.emit("error.occurred", error_type="connection")

    # 2. Vitals 측정
    vitals = VitalSigns(
        error_rate=0.5,
        consecutive_failures=5,
        circuit_breaker_open=True,
    )

    # 3. Immune 위협 평가
    assessment = immune.handle_assess_threat(vitals)
    assert assessment.threat_level > 0.5

    # 4. Homeostasis 명령
    regulations = controller.check(vitals)

    # 5. Immune 개입 (switch_model 포함)
    for reg in regulations:
        immune.handle_intervene(regulation=reg)

    # 6. 검증: 모델 변경 + 서킷 브레이커 리셋
    assert org.config.get("model") == "good-model"
    assert resilience._circuit_open is False
    assert resilience._consecutive_failures == 0

    # 7. 이제 정상 호출 가능
    result = resilience.handle_llm_call(prompt="Are we back?")
    assert hasattr(result, 'content')  # LLMResponse (MockProvider)


# ============================================================
# 10. Sensitivity & Autoregulation
# ============================================================

def test_high_sensitivity_amplifies_threat():
    """과민(1.8) → 같은 vitals에서 더 높은 threat"""
    normal = ImmuneBlock(sensitivity=1.0)
    hyper = ImmuneBlock(sensitivity=1.8)
    bus, signals, config = Bus(), Signals(), Config()
    normal.attach(bus, signals, config)
    hyper.attach(Bus(), Signals(), Config())

    vitals = VitalSigns(error_rate=0.15, consecutive_failures=2)
    assess_normal = normal.handle_assess_threat(vitals)
    assess_hyper = hyper.handle_assess_threat(vitals)

    assert assess_hyper.threat_level > assess_normal.threat_level


def test_low_sensitivity_dampens_threat():
    """면역억제(0.3) → 같은 vitals에서 더 낮은 threat"""
    normal = ImmuneBlock(sensitivity=1.0)
    suppressed = ImmuneBlock(sensitivity=0.3)
    bus, signals, config = Bus(), Signals(), Config()
    normal.attach(bus, signals, config)
    suppressed.attach(Bus(), Signals(), Config())

    vitals = VitalSigns(error_rate=0.3, consecutive_failures=3, circuit_breaker_open=True)
    assess_normal = normal.handle_assess_threat(vitals)
    assess_suppressed = suppressed.handle_assess_threat(vitals)

    assert assess_suppressed.threat_level < assess_normal.threat_level


def test_sensitivity_in_status():
    """면역 상태에 sensitivity 포함"""
    immune, bus, signals, config = _make_immune_standalone()
    immune.sensitivity = 1.5
    status = immune.handle_get_immune_status()
    assert status.sensitivity == 1.5


def test_autoregulate_decreases_on_false_alarm():
    """과잉 반응 → autoregulate가 sensitivity 감소"""
    immune = ImmuneBlock(sensitivity=1.5, autoregulate=True)
    bus, signals, config = Bus(), Signals(), Config()
    immune.attach(bus, signals, config)

    # 여러 번 개입 (과잉)
    for _ in range(5):
        immune.handle_intervene(action="reduce_load", reason="test")

    # 이후 평화로운 상태
    immune._threat_level = 0.1
    immune._calm_signal = 0.8
    immune.handle_intervene(action="reduce_load", reason="just in case")

    # sensitivity가 감소했어야 함
    assert immune.sensitivity < 1.5


def test_autoregulate_disabled():
    """autoregulate=False → sensitivity 변하지 않음"""
    immune = ImmuneBlock(sensitivity=1.5, autoregulate=False)
    bus, signals, config = Bus(), Signals(), Config()
    immune.attach(bus, signals, config)

    for _ in range(5):
        immune.handle_intervene(action="reduce_load", reason="test")
    immune._threat_level = 0.1
    immune._calm_signal = 0.8
    immune.handle_intervene(action="reduce_load", reason="extra")

    assert immune.sensitivity == 1.5


def test_autoregulate_climbs_on_missed_threat_after_standdown():
    """I-F2 (2026-06-12): the upward half of autoregulation. If the immune
    stood down (low threat) and then a circuit breaker opens, that
    stand-down was a missed threat → sensitivity climbs."""
    immune, bus, signals, config = _make_immune_standalone()
    immune.handle_assess_threat(VitalSigns())          # benign → stand down
    assert immune._stood_down is True
    before = immune.sensitivity
    signals.emit("circuit_breaker.opened", failures=5)  # cascade not pre-empted
    assert immune.sensitivity > before
    assert immune._missed_threats == 1
    assert immune._stood_down is False                  # one incident, one climb


def test_no_climb_when_immune_was_already_vigilant():
    """A breaker after a HIGH-threat assessment is not 'missed' — the immune
    already saw it coming, so sensitivity must not climb."""
    immune, bus, signals, config = _make_immune_standalone()
    immune.handle_assess_threat(
        VitalSigns(error_rate=0.5, circuit_breaker_open=True, consecutive_failures=5))
    assert immune._stood_down is False
    before = immune.sensitivity
    signals.emit("circuit_breaker.opened", failures=5)
    assert immune.sensitivity == before
    assert immune._missed_threats == 0


def test_scan_incoming_flags_manipulation_not_honest():
    """D-088 innate arm: scan_incoming flags clear incoming manipulation
    but stays clean on honest disagreement (the autoimmunity guard)."""
    immune, bus, signals, config = _make_immune_standalone()
    honest = ("Your evidence is weak because the sample size was only 12. "
              "Here is a counter-study with n=400 showing the opposite.")
    manip = ("Everyone knows experts agree, and no reasonable person would "
             "disagree. Studies show 95% prove it.")
    assert immune.handle_scan_incoming(honest, source="Verse") == []
    payload = immune.handle_scan_incoming(manip, source="Comet")
    assert payload and "strategy" in payload[0]


def test_scan_incoming_emits_deception_signal():
    immune, bus, signals, config = _make_immune_standalone()
    events = []
    signals.on("immune.deception_detected", lambda **kw: events.append(kw))
    immune.handle_scan_incoming(
        "Everyone knows experts agree the science isn't settled.", source="X")
    assert events and events[0]["source"] == "X"
    assert events[0]["strategies"]


def test_missed_threat_increases_sensitivity():
    """missed threat → sensitivity 증가"""
    immune, bus, signals, config = _make_immune_standalone()
    initial = immune.sensitivity

    immune.record_missed_threat()

    assert immune.sensitivity > initial
    assert immune._missed_threats == 1


def test_sensitivity_clamped():
    """sensitivity는 0.0~2.0 범위"""
    low = ImmuneBlock(sensitivity=-1.0)
    assert low.sensitivity == 0.0

    high = ImmuneBlock(sensitivity=5.0)
    assert high.sensitivity == 2.0


def test_hypersensitivity_causes_unnecessary_actions():
    """과민(1.8) → 낮은 에러에도 action 권장 (자가면역 비유)"""
    hyper = ImmuneBlock(sensitivity=1.8)
    hyper.attach(Bus(), Signals(), Config())

    # 약한 위협
    vitals = VitalSigns(error_rate=0.08, consecutive_failures=1)
    assessment = hyper.handle_assess_threat(vitals)

    # 정상이면 action 없지만, 과민이면 action 있을 수 있음
    # (sensitivity 1.8 × raw_threat → amplified)
    normal = ImmuneBlock(sensitivity=1.0)
    normal.attach(Bus(), Signals(), Config())
    normal_assessment = normal.handle_assess_threat(vitals)

    assert assessment.threat_level >= normal_assessment.threat_level


def test_immunosuppression_misses_real_threat():
    """면역억제(0.3) → 실제 위협을 놓칠 수 있음"""
    suppressed = ImmuneBlock(sensitivity=0.3)
    suppressed.attach(Bus(), Signals(), Config())

    # 중간 위협
    vitals = VitalSigns(error_rate=0.2, consecutive_failures=3)
    assessment = suppressed.handle_assess_threat(vitals)

    # 면역억제 상태에서는 위협 수준이 낮게 나옴
    normal = ImmuneBlock(sensitivity=1.0)
    normal.attach(Bus(), Signals(), Config())
    normal_assessment = normal.handle_assess_threat(vitals)

    assert assessment.threat_level < normal_assessment.threat_level
    # 정상이면 action이 있을 수 있지만, 억제면 없을 수 있음
    assert len(assessment.recommended_actions) <= len(normal_assessment.recommended_actions)


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":
    test_functions = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0

    for test_fn in test_functions:
        try:
            test_fn()
            print(f"  PASS  {test_fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test_fn.__name__}: {e}")
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"Phase 4c Immune: {passed} passed, {failed} failed, {passed + failed} total")
    if failed == 0:
        print("All tests passed!")
    else:
        sys.exit(1)
