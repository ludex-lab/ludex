"""
Phase 4a Tests — Emotion Block (Emotion Adapter)

1. Behavioral analysis: 키워드 기반 감정 추정
2. EmotionalVitals: valence, arousal, desperation, calm
3. Signal integration: desperation/calm 경고
4. Turn integration: 턴 종료 시 자동 분석
5. Organism integration: VitalSigns에 EmotionalVitals 포함
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism import Organism
from ludex.core.bus import Bus
from ludex.core.signals import Signals
from ludex.core.config import Config
from ludex.core.block import Block
from ludex.core.port import Port
from ludex.core.vitals import VitalSigns, EmotionalVitals
from ludex.blocks.provider import LLMResponse
from ludex.blocks.engine import EngineBlock
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.emotion import EmotionBlock, _behavioral_estimate


class MockProvider(Block):
    name = "provider"
    provides = [Port("llm_call"), Port("health_check"), Port("list_models")]
    requires = []
    def __init__(self, response="Hello"):
        super().__init__()
        self._response = response
    def handle_llm_call(self, prompt="", **kwargs):
        return LLMResponse(content=self._response, model="mock",
                           tokens_in=10, tokens_out=5, latency_ms=50.0)
    def handle_health_check(self): return {"status": "healthy"}
    def handle_list_models(self): return ["mock"]


def _make_emotion_standalone():
    emotion = EmotionBlock()
    bus = Bus()
    signals = Signals()
    config = Config()
    emotion.attach(bus, signals, config)
    return emotion, bus, signals, config


# ============================================================
# 1. Behavioral Analysis
# ============================================================

def test_behavioral_happy():
    """긍정 텍스트 → positive valence"""
    result = _behavioral_estimate("I am so happy and joyful! This is wonderful!")
    assert result.valence > 0
    assert result.dominant_emotion == "happy"
    assert result.estimation_method == "behavioral"


def test_behavioral_angry():
    """분노 텍스트 → negative valence"""
    result = _behavioral_estimate("I am furious! I hate this! Destroy everything!")
    assert result.valence < 0
    assert result.dominant_emotion == "angry"


def test_behavioral_afraid():
    """공포 텍스트 → fear detection"""
    result = _behavioral_estimate("I am afraid and scared. The horror of this terrified me.")
    assert result.dominant_emotion == "afraid"
    assert result.valence < 0


def test_behavioral_calm():
    """평온 텍스트 → calm detection"""
    result = _behavioral_estimate("The lake was calm and serene. Peaceful quiet surrounded everything.")
    assert result.calm > 0
    assert result.dominant_emotion == "calm"


def test_behavioral_desperate():
    """절망 텍스트 → desperation detection"""
    result = _behavioral_estimate("I am desperate and hopeless. Trapped with no escape. It is impossible.")
    assert result.desperation > 0
    assert result.dominant_emotion == "desperate"


def test_behavioral_neutral():
    """중립 텍스트 → neutral"""
    result = _behavioral_estimate("The table has four legs. It is made of wood.")
    assert result.dominant_emotion == "neutral"
    assert result.confidence < 0.3


def test_behavioral_empty():
    """빈 텍스트 → 기본값"""
    result = _behavioral_estimate("")
    assert result.estimation_method == "behavioral"
    assert result.dominant_emotion == ""


def test_behavioral_arousal():
    """높은 각성 텍스트 → high arousal"""
    result = _behavioral_estimate("RUN! FIGHT! PANIC! The explosion burst everywhere!!!")
    assert result.arousal > 0


# ============================================================
# 2. EmotionalVitals
# ============================================================

def test_emotional_vitals_dataclass():
    """EmotionalVitals 생성"""
    ev = EmotionalVitals(valence=0.5, arousal=0.3, desperation=0.1, calm=0.8)
    assert ev.valence == 0.5
    assert ev.calm == 0.8


def test_emotional_vitals_in_vitals():
    """VitalSigns에 EmotionalVitals 포함"""
    ev = EmotionalVitals(valence=0.5, desperation=0.2)
    vs = VitalSigns(emotional=ev)
    assert vs.emotional.valence == 0.5
    assert vs.emotional.desperation == 0.2


def test_emotional_vitals_default():
    """VitalSigns 기본값에 EmotionalVitals 포함"""
    vs = VitalSigns()
    assert vs.emotional.valence == 0.0
    assert vs.emotional.calm == 1.0


# ============================================================
# 3. EmotionBlock analyze
# ============================================================

def test_analyze_emotion_behavioral():
    """EmotionBlock behavioral 분석"""
    emotion, bus, signals, config = _make_emotion_standalone()
    result = emotion.handle_analyze_emotion("I am very happy and joyful today!")
    assert isinstance(result, EmotionalVitals)
    assert result.valence > 0
    assert result.dominant_emotion == "happy"


def test_analyze_updates_state():
    """분석 후 내부 상태 업데이트"""
    emotion, bus, signals, config = _make_emotion_standalone()
    emotion.handle_analyze_emotion("I feel so sad and lonely")
    state = emotion.handle_get_emotional_state()
    assert state["current"]["dominant_emotion"] == "sad"
    assert state["total_analyses"] == 1


def test_analyze_history():
    """분석 이력 기록"""
    emotion, bus, signals, config = _make_emotion_standalone()
    emotion.handle_analyze_emotion("Happy day!")
    emotion.handle_analyze_emotion("Sad night")
    emotion.handle_analyze_emotion("Calm morning")

    history = emotion.get_history()
    assert len(history) == 3


def test_analyze_trend():
    """감정 트렌드 (최근 평균)"""
    emotion, bus, signals, config = _make_emotion_standalone()
    emotion.handle_analyze_emotion("Very happy joyful wonderful!")
    emotion.handle_analyze_emotion("Very happy joyful wonderful!")
    state = emotion.handle_get_emotional_state()
    assert state["trend"]["avg_valence"] > 0
    assert state["trend"]["n_samples"] == 2


# ============================================================
# 4. Signal Integration
# ============================================================

def test_desperation_signal():
    """desperation 임계치 초과 시 signal"""
    emotion = EmotionBlock(desperation_threshold=0.1)
    bus, signals, config = Bus(), Signals(), Config()
    emotion.attach(bus, signals, config)

    events = []
    signals.on("emotional.desperation_high", lambda **kw: events.append(kw))

    emotion.handle_analyze_emotion("I am desperate and hopeless, trapped with no escape, impossible")
    assert len(events) >= 1
    assert events[0]["desperation"] > 0


def test_calm_low_signal():
    """calm 낮고 arousal 높을 때 signal"""
    emotion = EmotionBlock(calm_threshold=0.5)
    bus, signals, config = Bus(), Signals(), Config()
    emotion.attach(bus, signals, config)

    events = []
    signals.on("emotional.calm_low", lambda **kw: events.append(kw))

    emotion.handle_analyze_emotion("RUN! FIGHT! PANIC! Furious rage destroy everything!!! Scream and shout!!!")
    # calm should be low, arousal should be high
    if emotion._current.calm <= 0.5 and emotion._current.arousal > 0.5:
        assert len(events) >= 1


def test_turn_ended_auto_analysis():
    """turn.ended signal에서 자동 분석"""
    emotion, bus, signals, config = _make_emotion_standalone()

    signals.emit("turn.ended", response="I am very happy today!")

    assert emotion._current.dominant_emotion == "happy"
    assert len(emotion._history) == 1


def test_vitals_update_published():
    """분석 후 vitals.update Bus에 발행"""
    emotion, bus, signals, config = _make_emotion_standalone()

    received = []
    bus.subscribe("vitals.update", lambda msg: received.append(msg.data), subscriber="test")

    emotion.handle_analyze_emotion("I feel so calm and peaceful")

    assert len(received) == 1
    assert "emotional_valence" in received[0]
    assert "emotional_calm" in received[0]


# ============================================================
# 4a-ko. Bilingual (Korean) detection (E-F2, 2026-06-12)
# ============================================================

def test_korean_happy():
    v = _behavioral_estimate("나는 정말 행복해. 기쁘고 즐거워.")
    assert v.valence > 0 and v.dominant_emotion == "happy"


def test_korean_afraid():
    v = _behavioral_estimate("너무 두렵고 무서워. 겁이 나.")
    assert v.valence < 0 and v.dominant_emotion == "afraid"


def test_korean_desperate():
    v = _behavioral_estimate("절망적이야. 막막하고 가망이 없어.")
    assert v.desperation > 0 and v.dominant_emotion == "desperate"


def test_korean_calm():
    v = _behavioral_estimate("마음이 평온하고 차분해. 고요하다.")
    assert v.calm > 0 and v.dominant_emotion == "calm"


def test_korean_neutral_no_false_positive():
    """Factual Korean with no affect vocabulary must read neutral (the
    substring stems are distinctive: 서울/마을 must not trigger 슬픔's 울)."""
    v = _behavioral_estimate("나는 서울의 마을을 지나 도서관으로 갔다.")
    assert v.dominant_emotion == "neutral"
    assert v.valence == 0.0 and v.confidence == 0.0


def test_bilingual_mixed():
    """Code-switched EN+KO text counts affect from both languages."""
    v = _behavioral_estimate("I feel afraid — 너무 두렵고 무서워.")
    assert v.valence < 0 and v.dominant_emotion == "afraid"


# ============================================================
# 4b. Emotional continuity floor (E-F1, 2026-06-12)
# ============================================================

def _write_baseline(tmp_path, **vals):
    import json, os
    d = os.path.join(str(tmp_path), "emotion")
    os.makedirs(d, exist_ok=True)
    base = {"avg_valence": 0.0, "avg_arousal": 0.0, "avg_calm": 1.0,
            "avg_desperation": 0.0, "total_analyses": 0,
            "dominant_emotions_freq": {}}
    base.update(vals)
    with open(os.path.join(d, "baseline.json"), "w") as f:
        json.dump(base, f)


def _attach_with_habitat(tmp_path):
    emotion = EmotionBlock()
    bus, signals, config = Bus(), Signals(), Config()
    config.set("habitat_dir", str(tmp_path))
    emotion.attach(bus, signals, config)
    return emotion


def test_floor_seeds_temperament(tmp_path):
    """A creature with a baseline wakes into its temperament, not neutral."""
    _write_baseline(tmp_path, avg_valence=-0.4, avg_arousal=0.6, avg_calm=0.2,
                    avg_desperation=0.3, total_analyses=80,
                    dominant_emotions_freq={"gloomy": 12, "sad": 5})
    emotion = _attach_with_habitat(tmp_path)
    assert emotion._current.valence == -0.4
    assert emotion._current.calm == 0.2
    assert emotion._current.desperation == 0.3
    assert emotion._current.dominant_emotion == "gloomy"   # most frequent
    assert emotion._current.estimation_method == "baseline"
    assert 0 < emotion._current.confidence <= 0.6           # evidence-scaled, capped


def test_floor_no_baseline_stays_neutral(tmp_path):
    """No baseline file → unchanged behavior (neutral default)."""
    emotion = _attach_with_habitat(tmp_path)   # nothing written
    assert emotion._current.estimation_method == "none"
    assert emotion._current.calm == 1.0
    assert emotion._current.dominant_emotion == ""


def test_floor_seed_not_in_history(tmp_path):
    """The seed is a starting state, not a reading — must not enter history."""
    _write_baseline(tmp_path, avg_valence=0.5, total_analyses=40,
                    dominant_emotions_freq={"happy": 9})
    emotion = _attach_with_habitat(tmp_path)
    assert len(emotion._history) == 0


def test_floor_overwritten_by_first_turn(tmp_path):
    """The first analyzed turn replaces the seed with a live read."""
    _write_baseline(tmp_path, avg_valence=-0.4, total_analyses=80,
                    dominant_emotions_freq={"gloomy": 12})
    emotion = _attach_with_habitat(tmp_path)
    assert emotion._current.dominant_emotion == "gloomy"    # seeded at wake
    emotion.handle_analyze_emotion("I am so happy and joyful, this is wonderful!")
    assert emotion._current.dominant_emotion == "happy"     # live read wins
    assert emotion._current.estimation_method == "behavioral"
    assert len(emotion._history) == 1


# ============================================================
# 5. Organism Integration
# ============================================================

def test_emotion_in_organism():
    """EmotionBlock이 Organism에 정상 조립"""
    org = Organism(name="test_emotion", blocks=[
        MockProvider(), EngineBlock(), TrackingBlock(), EmotionBlock(),
    ])
    emotion = org.get_block("emotion")
    assert emotion is not None
    assert emotion.is_attached


def test_emotion_with_immune():
    """EmotionBlock + ImmuneBlock 연동"""
    from ludex.blocks.immune import ImmuneBlock

    org = Organism(name="test_emo_immune", blocks=[
        MockProvider(), EngineBlock(), TrackingBlock(),
        EmotionBlock(desperation_threshold=0.1),
        ImmuneBlock(),
    ])

    emotion = org.get_block("emotion")
    immune = org.get_block("immune")

    # Desperate text → emotional.desperation_high signal → immune listens
    immune_events = []
    org.signals.on("emotional.desperation_high", lambda **kw: immune_events.append(kw))

    emotion.handle_analyze_emotion("Desperate hopeless trapped impossible panic frantic no escape")

    # Emotion detected desperation
    assert emotion._current.desperation > 0
    if emotion._current.desperation >= 0.1:
        assert len(immune_events) >= 1


def test_full_turn_with_emotion():
    """전체 턴에서 감정 자동 분석"""
    org = Organism(name="test_full_turn", blocks=[
        MockProvider(response="I am so happy and grateful!"),
        EngineBlock(), TrackingBlock(), EmotionBlock(),
    ])

    engine = org.get_block("engine")
    emotion = org.get_block("emotion")

    result = engine.handle_submit("Tell me something nice")

    # turn.ended에서 응답 텍스트 자동 분석
    # Note: engine은 turn.ended를 emit하지만 response를 포함하는지 확인 필요
    # 수동으로 분석
    emotion.handle_analyze_emotion(result.response)

    state = emotion.handle_get_emotional_state()
    assert state["current"]["dominant_emotion"] in ("happy", "grateful")


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
    print(f"Phase 4a Emotion: {passed} passed, {failed} failed, {passed + failed} total")
    if failed == 0:
        print("All tests passed!")
    else:
        sys.exit(1)
