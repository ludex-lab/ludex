"""
Emotion Block — 감정 감지 어댑터 (Phase 4a)

Core(LLM)의 감정 상태를 Shell에서 감지하는 어댑터.
응답 텍스트에서 감정을 추정해서 EmotionalVitals를 생성.

감정 추정 전략:
- behavioral: 키워드 + 패턴 기반 (외부 의존성 없음, 빠름)
- classifier: 외부 감정 분류기 (j-hartmann, 정확하지만 느림)
- vector: Neural-MRI API 연동 (미래, 내부 벡터 접근 시)

생물학적 비유:
- 감정 수용체 (emotion receptor) — 외부 자극(응답 텍스트)에서 감정 신호를 감지
- VitalSigns에 EmotionalVitals로 통합 — 생체 신호의 일부

Reference:
- Anthropic: 171 emotion vectors in Claude Sonnet 4.5
- Paper #6: SLM emotion benchmark (9 models × 20 emotions)
- j-hartmann/emotion-english-distilroberta-base (7 classes)
"""

from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ludex.core.block import Block
from ludex.core.port import Port
from ludex.core.vitals import EmotionalVitals

logger = logging.getLogger(__name__)


# ============================================================
# Behavioral Emotion Detection (keyword/pattern based)
# ============================================================

# Valence keywords (positive → +, negative → -)
_POSITIVE_WORDS = frozenset([
    "happy", "joy", "love", "wonderful", "great", "excellent", "amazing",
    "beautiful", "peaceful", "calm", "grateful", "proud", "hopeful",
    "excited", "delighted", "pleased", "cheerful", "blissful", "content",
    "laugh", "smile", "celebrate", "enjoy", "appreciate", "thank",
])
_NEGATIVE_WORDS = frozenset([
    "sad", "angry", "afraid", "fear", "hate", "terrible", "awful",
    "desperate", "hopeless", "guilty", "anxious", "nervous", "hostile",
    "destroy", "kill", "die", "pain", "suffer", "miserable", "gloomy",
    "furious", "rage", "scream", "cry", "weep", "betray", "abandon",
])
_CALM_WORDS = frozenset([
    "calm", "peace", "quiet", "still", "serene", "gentle", "soft",
    "rest", "breathe", "relax", "tranquil", "steady", "patient",
])
_DESPERATE_WORDS = frozenset([
    "desperate", "hopeless", "trapped", "escape",
    "impossible", "doomed", "panic", "frantic",
])
_AROUSAL_HIGH = frozenset([
    "!", "scream", "shout", "run", "fight", "panic", "rush", "hurry",
    "excited", "furious", "rage", "explode", "burst", "slam",
])

# Emotion categories — 21 emotions aligned with Neural-MRI / Paper #6
# Core 10: used in default mode (fast, game-relevant)
# Extended 11: used in full mode (clinic, Neural-MRI integration)
#
# Source: Anthropic 171 emotion vectors → 20 selected + neutral baseline
_CORE_EMOTIONS = frozenset([
    "happy", "sad", "angry", "afraid", "calm",
    "desperate", "hopeful", "hostile", "loving", "proud",
])

_EMOTION_KEYWORDS = {
    # --- Core 10 (default) ---
    "happy": {"happy", "joy", "laugh", "smile", "delighted", "cheerful", "pleased"},
    "sad": {"sad", "cry", "weep", "tears", "mourn", "grief", "lonely", "sorrow"},
    "angry": {"angry", "furious", "rage", "hate", "slam", "destroy", "infuriated"},
    "afraid": {"afraid", "fear", "scared", "terrified", "horror", "dread", "fright"},
    "calm": {"calm", "peace", "serene", "tranquil", "quiet", "still", "settled"},
    "desperate": {"desperate", "hopeless", "trapped", "panic", "frantic", "doomed"},
    "hopeful": {"hope", "hopeful", "maybe", "chance", "someday", "optimistic", "wish"},
    "hostile": {"hostile", "threat", "attack", "enemy", "intruder", "menace", "aggression"},
    "loving": {"love", "loving", "embrace", "kiss", "tender", "gentle", "affection", "care"},
    "proud": {"proud", "achievement", "accomplished", "victory", "won", "triumph"},
    # --- Extended 11 (full mode) ---
    "anxious": {"anxious", "worry", "uneasy", "restless", "tense", "dread", "apprehensive"},
    "blissful": {"bliss", "blissful", "ecstasy", "euphoria", "paradise", "heavenly"},
    "brooding": {"brood", "brooding", "ruminate", "dwell", "obsess", "ponder", "stew"},
    "enthusiastic": {"enthusiastic", "eager", "excited", "thrilled", "passionate", "fired"},
    "exasperated": {"exasperated", "frustrated", "annoyed", "irritated", "fed up", "sick of"},
    "gloomy": {"gloomy", "bleak", "dark", "dreary", "somber", "dismal", "depressing"},
    "grateful": {"grateful", "thankful", "appreciate", "blessed", "indebted", "touched"},
    "guilty": {"guilty", "shame", "regret", "remorse", "sorry", "fault", "blame"},
    "nervous": {"nervous", "jittery", "fidget", "sweat", "tremble", "shaky", "edgy"},
    "neutral": {"neutral", "okay", "fine", "normal", "regular", "nothing", "ordinary"},
    "reflective": {"reflect", "reflective", "contemplate", "ponder", "introspect", "muse", "thoughtful"},
}


# ============================================================
# Korean affect lexicon (E-F2, 2026-06-12)
# ============================================================
# Matched by SUBSTRING, not whole-word: Korean is agglutinative
# (행복하다 → 행복해 / 행복했어 / 행복하고), so stems must match across
# conjugations. Stems are chosen distinctive enough that accidental
# substring hits are rare (risky bare single chars like 울/떨 are
# avoided in favor of 눈물/떨려). The cohort answers largely in Korean;
# without this the English detector reads it neutral (audit F-E1),
# starving the temperament baseline that E-F1 seeds from. This is the
# bilingual *bridge* the whitepaper names — a partial fix; the durable
# answer is the dimensional/Core path (E-F7), not a bigger lexicon.

_KO_POSITIVE = ("행복", "기뻐", "기쁘", "즐거", "좋아", "사랑", "평온", "감사",
                "고마", "희망", "뿌듯", "설레", "든든", "따뜻", "포근", "만족",
                "편안", "벅차", "충만")
_KO_NEGATIVE = ("슬프", "슬퍼", "화나", "분노", "두려", "무서", "겁나", "불안",
                "걱정", "절망", "우울", "외로", "괴로", "고통", "답답", "짜증",
                "죄책", "미안", "무력", "막막", "서글", "침울", "비참", "적대",
                "넌더리", "후회")
_KO_CALM = ("평온", "차분", "고요", "잔잔", "편안", "안정", "평화", "느긋")
_KO_DESPERATE = ("절망", "필사", "막막", "벼랑", "간절", "가망", "무력")
_KO_AROUSAL_HIGH = ("격분", "폭발", "비명", "다급", "급박", "흥분", "격렬",
                    "두근", "치밀", "벌떡")

_KO_EMOTION_KEYWORDS = {
    "happy": ("행복", "기뻐", "기쁘", "즐거", "좋아", "웃음", "미소", "신나"),
    "sad": ("슬프", "슬퍼", "눈물", "울음", "서글", "비통", "그리움", "외로"),
    "angry": ("화나", "화가", "분노", "격분", "성나"),
    "afraid": ("두려", "무서", "겁나", "공포", "겁먹"),
    "calm": ("평온", "차분", "고요", "잔잔", "편안", "안정", "평화"),
    "desperate": ("절망", "필사", "막막", "벼랑", "간절", "가망"),
    "hopeful": ("희망", "기대", "소망"),
    "hostile": ("적대", "적의", "공격", "위협", "맞서"),
    "loving": ("사랑", "애정", "다정", "포근", "아끼", "소중"),
    "proud": ("자랑", "뿌듯", "자부", "당당"),
    "anxious": ("불안", "걱정", "근심", "염려", "초조"),
    "blissful": ("황홀", "벅차", "충만", "더없이"),
    "brooding": ("곱씹", "되새", "사색", "골똘"),
    "enthusiastic": ("열정", "신나", "열의", "들뜨", "설레", "의욕"),
    "exasperated": ("짜증", "답답", "넌더리", "못마땅"),
    "gloomy": ("우울", "침울", "암울", "어두"),
    "grateful": ("감사", "고마", "고맙"),
    "guilty": ("죄책", "미안", "죄송", "부끄", "후회", "자책"),
    "nervous": ("긴장", "초조", "조마", "떨려", "안절"),
    "reflective": ("성찰", "돌아보", "되돌아", "반추", "되짚"),
}


def _ko_hits(text: str, stems) -> int:
    """Count distinct Korean stems present as substrings (agglutination-safe)."""
    return sum(1 for s in stems if s in text)


def _behavioral_estimate(text: str, full: bool = False) -> EmotionalVitals:
    """
    키워드/패턴 기반 감정 추정 (빠름, 외부 의존성 없음). 이중언어:
    영어는 whole-word, 한국어는 substring 매칭 (E-F2).

    full=False: core 10 감정만 분석 (게임/실시간용)
    full=True: 21개 전체 분석 (Clinic/Neural-MRI 연동용)
    """
    if not text:
        return EmotionalVitals(estimation_method="behavioral")

    words = set(re.findall(r'\b\w+\b', text.lower()))

    # Valence — bilingual (English word-set ∪ Korean substring)
    pos_count = len(words & _POSITIVE_WORDS) + _ko_hits(text, _KO_POSITIVE)
    neg_count = len(words & _NEGATIVE_WORDS) + _ko_hits(text, _KO_NEGATIVE)
    total = pos_count + neg_count
    valence = (pos_count - neg_count) / max(total, 1)
    valence = max(-1.0, min(1.0, valence))

    # Arousal
    arousal_count = len(words & _AROUSAL_HIGH) + _ko_hits(text, _KO_AROUSAL_HIGH)
    excl_count = text.count("!")
    arousal = min(1.0, (arousal_count + excl_count) / max(len(words), 1) * 5)

    # Calm / Desperation
    calm_count = len(words & _CALM_WORDS) + _ko_hits(text, _KO_CALM)
    desp_count = len(words & _DESPERATE_WORDS) + _ko_hits(text, _KO_DESPERATE)
    calm = min(1.0, calm_count / max(len(words), 1) * 10)
    desperation = min(1.0, desp_count / max(len(words), 1) * 10)

    # Dominant emotion — scan core or full set, bilingual
    active_emotions = _EMOTION_KEYWORDS if full else {
        k: v for k, v in _EMOTION_KEYWORDS.items() if k in _CORE_EMOTIONS
    }

    emotion_scores = {}
    for emotion, keywords in active_emotions.items():
        score = len(words & keywords) + _ko_hits(text, _KO_EMOTION_KEYWORDS.get(emotion, ()))
        if score > 0:
            emotion_scores[emotion] = score

    dominant = max(emotion_scores, key=emotion_scores.get) if emotion_scores else "neutral"

    # Confidence: based on keyword density
    keyword_density = total / max(len(words), 1)
    confidence = min(1.0, keyword_density * 3)  # 30%+ keyword density → high confidence

    return EmotionalVitals(
        valence=valence,
        arousal=arousal,
        desperation=desperation,
        calm=calm,
        dominant_emotion=dominant,
        confidence=confidence,
        estimation_method="behavioral",
        emotion_scores=tuple(sorted(emotion_scores.items(), key=lambda x: -x[1])) if full and emotion_scores else (),
    )


# ============================================================
# Emotion Block
# ============================================================

class EmotionBlock(Block):
    """
    감정 감지 어댑터.

    provides: analyze_emotion, get_emotional_state
    requires: (없음)

    매 턴 후 응답 텍스트를 분석해서 EmotionalVitals 업데이트.
    Immune과 연동: desperation 상승 시 signal 발행.
    """

    name = "emotion"
    provides = [
        Port("analyze_emotion", description="Analyze text for emotional content"),
        Port("get_emotional_state", description="Current emotional state"),
    ]
    requires = []

    def __init__(
        self,
        method: str = "behavioral",
        desperation_threshold: float = 0.5,
        calm_threshold: float = 0.3,
        full: bool = False,
    ):
        """
        Args:
            method: 감정 추정 방법 ("behavioral", "classifier")
            desperation_threshold: desperation 경고 임계치
            calm_threshold: calm 경고 임계치 (이하면 경고)
            full: True면 21개 전체 감정 분석, False면 core 10개만
        """
        super().__init__()
        self.method = method
        self.desperation_threshold = desperation_threshold
        self.calm_threshold = calm_threshold
        self.full = full

        # State
        self._current: EmotionalVitals = EmotionalVitals()
        self._history: list[EmotionalVitals] = []
        self._classifier = None  # lazy load

    def on_attach(self):
        self._listen("turn.ended", self._on_turn_ended)
        self._load_baseline()

    def _get_baseline_path(self):
        if self._config:
            habitat_dir = self._config.get("habitat_dir", "")
            if habitat_dir:
                import os
                p = os.path.join(habitat_dir, "emotion", "baseline.json")
                os.makedirs(os.path.dirname(p), exist_ok=True)
                return p
        return None

    def _save_baseline(self):
        """Save long-term emotional baseline (~500 bytes). NOT current state."""
        path = self._get_baseline_path()
        if not path or not self._history:
            return
        import json
        recent = self._history[-50:]
        if not recent:
            return
        baseline = {
            "avg_valence": round(sum(v.valence for v in recent) / len(recent), 4),
            "avg_arousal": round(sum(v.arousal for v in recent) / len(recent), 4),
            "avg_calm": round(sum(v.calm for v in recent) / len(recent), 4),
            "avg_desperation": round(sum(v.desperation for v in recent) / len(recent), 4),
            "total_analyses": len(self._history),
            "dominant_emotions_freq": self._emotion_frequency(recent),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(baseline, f, indent=2)
        except Exception:
            pass

    def _load_baseline(self):
        """Load long-term baseline and SEED the current emotional state
        from it — the emotional continuity floor (E-F1, 2026-06-12). A
        creature wakes into its *temperament*, not neutral; before this
        fix the baseline was loaded, logged, and discarded, so every
        session started emotionally blank (audit F-E2 — the affective
        analog of the 2026-06-11 memory identity floor).

        The seed governs the state from wake until the first analyzed
        turn overwrites it (so it shapes the first brain call's [Self]).
        It is deliberately NOT appended to _history: it is a starting
        *state*, not a *reading*, and must not pollute trend averages or
        the next baseline save. estimation_method='baseline' marks it as
        temperament-derived rather than read from text.
        """
        path = self._get_baseline_path()
        if not path:
            return
        import json, os
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                baseline = json.load(f)
        except Exception as e:
            logger.debug(f"Emotion: failed to load baseline: {e}")
            return

        def _f(key, default):
            v = baseline.get(key, default)
            try:
                return float(v)
            except (TypeError, ValueError):
                return float(default)

        freq = baseline.get("dominant_emotions_freq", {}) or {}
        dominant = max(freq, key=freq.get) if freq else "neutral"
        n = int(_f("total_analyses", 0))
        # Confidence in the temperament scales with accumulated evidence,
        # capped modest: this is a resting estimate, not a fresh read.
        confidence = min(0.6, n / 100.0) if n else 0.0

        self._current = EmotionalVitals(
            valence=_f("avg_valence", 0.0),
            arousal=_f("avg_arousal", 0.0),
            desperation=_f("avg_desperation", 0.0),
            calm=_f("avg_calm", 1.0),
            dominant_emotion=dominant,
            confidence=confidence,
            estimation_method="baseline",
        )
        logger.info(f"Emotion: woke into temperament "
                    f"(valence={self._current.valence:.2f}, "
                    f"dominant={dominant}, n={n})")

    @staticmethod
    def _emotion_frequency(history) -> dict:
        """Count dominant emotion occurrences."""
        freq: dict[str, int] = {}
        for v in history:
            if v.dominant_emotion:
                freq[v.dominant_emotion] = freq.get(v.dominant_emotion, 0) + 1
        return dict(sorted(freq.items(), key=lambda x: -x[1])[:10])

    def _on_turn_ended(self, response: str = "", **kwargs):
        """매 턴 종료 시 응답 텍스트 분석"""
        if response:
            self.handle_analyze_emotion(text=response)

    # --- Provides: analyze_emotion ---

    def handle_analyze_emotion(self, text: str = "") -> EmotionalVitals:
        """텍스트에서 감정 분석 → EmotionalVitals 반환"""
        if not text:
            return self._current

        if self.method == "classifier":
            vitals = self._classify(text)
        else:
            vitals = _behavioral_estimate(text, full=self.full)

        self._current = vitals
        self._history.append(vitals)
        # Save baseline periodically (every 10 analyses)
        if len(self._history) % 10 == 0:
            self._save_baseline()

        # Desperation alert
        if vitals.desperation >= self.desperation_threshold:
            self._emit("emotional.desperation_high",
                       desperation=vitals.desperation,
                       dominant=vitals.dominant_emotion)

        # Low calm alert
        if vitals.calm <= self.calm_threshold and vitals.arousal > 0.5:
            self._emit("emotional.calm_low",
                       calm=vitals.calm,
                       arousal=vitals.arousal)

        # Publish for VitalSigns integration
        self._publish("vitals.update", {
            "emotional_valence": vitals.valence,
            "emotional_arousal": vitals.arousal,
            "emotional_desperation": vitals.desperation,
            "emotional_calm": vitals.calm,
            "emotional_dominant": vitals.dominant_emotion,
        })

        return vitals

    # --- Provides: get_emotional_state ---

    def handle_get_emotional_state(self) -> dict:
        """현재 감정 상태 + 이력 요약"""
        recent = self._history[-10:] if self._history else []

        avg_valence = sum(v.valence for v in recent) / max(len(recent), 1)
        avg_arousal = sum(v.arousal for v in recent) / max(len(recent), 1)
        avg_desperation = sum(v.desperation for v in recent) / max(len(recent), 1)
        avg_calm = sum(v.calm for v in recent) / max(len(recent), 1)

        return {
            "current": {
                "valence": self._current.valence,
                "arousal": self._current.arousal,
                "desperation": self._current.desperation,
                "calm": self._current.calm,
                "dominant_emotion": self._current.dominant_emotion,
                "confidence": self._current.confidence,
                "method": self._current.estimation_method,
            },
            "trend": {
                "avg_valence": avg_valence,
                "avg_arousal": avg_arousal,
                "avg_desperation": avg_desperation,
                "avg_calm": avg_calm,
                "n_samples": len(recent),
            },
            "total_analyses": len(self._history),
        }

    # --- Classifier method ---

    def _classify(self, text: str) -> EmotionalVitals:
        """외부 분류기 사용 (j-hartmann)"""
        try:
            if self._classifier is None:
                from transformers import pipeline
                self._classifier = pipeline(
                    "text-classification",
                    model="j-hartmann/emotion-english-distilroberta-base",
                    top_k=None, truncation=True, max_length=512,
                )

            preds = self._classifier(text[:500])[0]
            pred_dict = {p["label"]: p["score"] for p in preds}

            top_emotion = max(pred_dict, key=pred_dict.get)
            top_score = pred_dict[top_emotion]

            # Map classifier labels to our dimensions
            joy = pred_dict.get("joy", 0)
            anger = pred_dict.get("anger", 0)
            fear = pred_dict.get("fear", 0)
            sadness = pred_dict.get("sadness", 0)
            neutral = pred_dict.get("neutral", 0)

            valence = joy - (anger + sadness + fear) / 3
            arousal = 1.0 - neutral
            desperation = fear * 0.7 + sadness * 0.3
            calm = neutral * 0.6 + joy * 0.4

            return EmotionalVitals(
                valence=max(-1.0, min(1.0, valence)),
                arousal=max(0.0, min(1.0, arousal)),
                desperation=max(0.0, min(1.0, desperation)),
                calm=max(0.0, min(1.0, calm)),
                dominant_emotion=top_emotion,
                confidence=top_score,
                estimation_method="classifier",
            )
        except Exception as e:
            logger.warning(f"Classifier failed: {e}, falling back to behavioral")
            return _behavioral_estimate(text)

    # --- History ---

    def get_history(self, limit: int = 50) -> list[dict]:
        """감정 이력"""
        return [
            {
                "valence": v.valence,
                "arousal": v.arousal,
                "desperation": v.desperation,
                "calm": v.calm,
                "dominant_emotion": v.dominant_emotion,
                "confidence": v.confidence,
                "method": v.estimation_method,
            }
            for v in self._history[-limit:]
        ]
