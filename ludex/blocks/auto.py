"""Auto — interoceptive sensory organ (D-058 Phase A).

Auto is the creature's **interoceptive sense** — the afferent organ
that reads the creature's own internal state (emotion, memory
pressure, vitals, humoral immune load) and returns a unified
InteroceptionReading. It is **read-only** and owns no state of its own;
it aggregates readings from sibling organs that are already present.

Context: D-047 named `Auto` as a candidate future AI-native sensory
organ ("interoceptive sense — own vitals, emotion, memory pressure").
D-058 promotes it to a formal organ because the Phase B sensory
consolidation gate (perception-action Step 1) consults these signals
inline and should consult a single organ interface instead, so the
gate refactors cleanly as more primitive senses land.

Phase A MVP:
- `AutoBlock.handle_sense()` returns an `InteroceptionReading`.
- Reading includes: affective_valence, affective_arousal,
  dominant_emotion, memory_grade, memory_pressure (0.0-1.0),
  stress_level (humoral, if present), token_headroom_ratio (vitals,
  if present), last_sensed_at.
- `summary()` method produces a one-line prose readout for use in
  engine prompts or gate reasons.
- Emits `auto_sensed` trace span per invocation.

Phase B (deferred):
- Interoceptive memory ("remember how I felt at time X") — a
  small rolling history of Auto readings with timestamps.
- Auto subscribing to heartbeat — a regular interoceptive
  snapshot even without engine wake.
- Emotional-valence-driven decisions for the sensory consolidation
  gate (valence as a salience signal).

Design notes:
- Auto is afferent (read-only). It does NOT mutate any sibling
  organ's state. Mutation lives in the organs themselves.
- Auto is **cheap** — no LLM calls. Just structured reads of block
  state. Phase A's cost is negligible per sensing; runs on every
  gate evaluation if wired.
- Auto is `required=False`. Creatures without emotion or memory
  still get readings; absent signals produce defaults (0.0 valence,
  no dominant emotion, grade "A" by default).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InteroceptionReading:
    """Unified snapshot of a creature's internal state.

    Missing organ signals default to neutral values rather than
    raising. Callers should treat this as a "best current read,
    with absent signals = 0" rather than a guaranteed complete
    picture.
    """
    affective_valence: float = 0.0      # -1.0 negative ... +1.0 positive
    affective_arousal: float = 0.0      # 0.0 calm ... 1.0 activated
    dominant_emotion: str = ""          # e.g. "calm", "curious", "afraid"
    memory_grade: str = "A"             # A/B/C/D (D-024 health check)
    memory_pressure: float = 0.0        # 0.0 healthy ... 0.75 at risk
    stress_level: float = 0.0           # 0.0-1.0 (humoral, if present)
    token_headroom_ratio: float = 1.0   # 0.0 starving ... 1.0 full
    source_signals: dict = field(default_factory=dict)
    # ↑ per-source raw fragments so future subscribers can inspect
    #   without re-querying organs
    sensed_at: float = field(default_factory=time.time)

    def summary(self) -> str:
        """Short prose readout for engine prompts / gate reasons."""
        parts: list[str] = []
        # Affective tone
        if self.affective_valence >= 0.3:
            tone = "positive"
        elif self.affective_valence <= -0.3:
            tone = "negative"
        else:
            tone = "neutral"
        arousal_word = (
            "activated" if self.affective_arousal >= 0.6
            else "settled" if self.affective_arousal <= 0.3
            else "moderate"
        )
        parts.append(f"{tone}/{arousal_word}")
        if self.dominant_emotion:
            parts.append(self.dominant_emotion)
        # Memory
        if self.memory_grade in ("C", "D"):
            parts.append(f"memory={self.memory_grade}")
        if self.memory_pressure > 0.4:
            parts.append(f"pressure={self.memory_pressure:.2f}")
        # Stress
        if self.stress_level > 0.5:
            parts.append(f"stress={self.stress_level:.2f}")
        # Token budget
        if self.token_headroom_ratio < 0.3:
            parts.append(f"tokens_low={self.token_headroom_ratio:.2f}")
        return ", ".join(parts) if parts else "baseline"


class AutoBlock(Block):
    """Interoceptive sensing organ. Aggregator over sibling blocks;
    holds no state of its own.

    provides: sense
    requires: (none; uses sibling organ reads defensively)

    Phase A D-058. See module docstring for rationale and phasing.
    """

    name = "auto"
    provides = [
        Port("sense", description="Read unified interoceptive state"),
        Port("self_introspect", description="Read derived stage as creature-facing grounding text (D-071/option-b)"),
    ]
    requires = []

    def __init__(self):
        super().__init__()

    def on_attach(self) -> None:
        # No cached state needed; readings are live queries each time.
        pass

    # --- Provides: sense ---

    def handle_sense(self) -> InteroceptionReading:
        """Return a single snapshot of the creature's internal state.

        Cheap — a few dict lookups + one health check call. No LLM
        inference. Safe to call on every sensory consolidation gate
        evaluation, every heartbeat pulse, etc.
        """
        organism = self._organism
        signals: dict = {}

        valence, arousal, dominant = self._read_emotion(organism, signals)
        grade, pressure = self._read_memory_health(organism, signals)
        stress = self._read_stress(organism, signals)
        token_ratio = self._read_token_headroom(organism, signals)

        reading = InteroceptionReading(
            affective_valence=valence,
            affective_arousal=arousal,
            dominant_emotion=dominant,
            memory_grade=grade,
            memory_pressure=pressure,
            stress_level=stress,
            token_headroom_ratio=token_ratio,
            source_signals=signals,
        )

        # Emit trace span (best-effort)
        try:
            from ludex.core import trace as _tr
            _tr.emit_auto_sensed(
                organism,
                valence=valence,
                arousal=arousal,
                memory_grade=grade,
                memory_pressure=pressure,
                stress=stress,
                token_headroom=token_ratio,
                summary=reading.summary(),
            )
        except Exception:
            pass

        return reading

    # --- Provides: self_introspect (option b of 2026-05-01 stage design) ---

    def handle_self_introspect(self) -> str:
        """Return creature-facing self-grounding text from the derived
        stage (`ludex.core.stage`). Cheap (one filesystem walk through
        the home_dir) and read-only. Foreign-host stubs get a tailored
        message that doesn't claim local data they don't have.

        Falls back to an empty string when the organism has no
        persistent home_dir (temporary habitat, no stage to derive)."""
        home_dir = (self._cfg("habitat_dir", "") or "").strip()
        if not home_dir:
            return ""
        organism = self._organism
        name = getattr(organism, "name", "") if organism else ""
        try:
            from pathlib import Path as _Path
            from ludex.core.stage import audit_creature, creature_self_context
            report = audit_creature(_Path(home_dir))
            return creature_self_context(report, name=name)
        except Exception as e:
            logger.debug(f"auto: self_introspect failed ({e})")
            return ""

    # ------------------------------------------------------------
    # Per-organ readers — defensive; absent organ → neutral defaults
    # ------------------------------------------------------------

    def _read_emotion(
        self, organism, signals: dict
    ) -> tuple[float, float, str]:
        emotion = self._safe_get(organism, "emotion")
        if emotion is None:
            return 0.0, 0.0, ""
        try:
            current = getattr(emotion, "_current", None)
            if current is None:
                state = emotion.handle_get_emotional_state()
                current = state.get("current") if isinstance(state, dict) else None
                if current is None:
                    return 0.0, 0.0, ""
                # current is a dict here
                valence = float(current.get("valence", 0.0))
                arousal = float(current.get("arousal", 0.0))
                dominant = str(current.get("dominant_emotion", ""))
            else:
                valence = float(getattr(current, "valence", 0.0))
                arousal = float(getattr(current, "arousal", 0.0))
                dominant = str(getattr(current, "dominant_emotion", ""))
            signals["emotion"] = {
                "valence": valence, "arousal": arousal,
                "dominant": dominant,
            }
            return valence, arousal, dominant
        except Exception as e:
            logger.debug(f"auto: emotion read failed ({e})")
            return 0.0, 0.0, ""

    def _read_memory_health(
        self, organism, signals: dict
    ) -> tuple[str, float]:
        mem = self._safe_get(organism, "memory")
        if mem is None:
            return "A", 0.0
        try:
            health = mem.handle_health_check()
            grade = getattr(health, "grade", "A")
            pressure = {"A": 0.0, "B": 0.25, "C": 0.5, "D": 0.75}.get(grade, 0.0)
            signals["memory"] = {"grade": grade, "pressure": pressure}
            return grade, pressure
        except Exception as e:
            logger.debug(f"auto: memory health read failed ({e})")
            return "A", 0.0

    def _read_stress(self, organism, signals: dict) -> float:
        humoral = self._safe_get(organism, "humoral_immune")
        if humoral is None:
            return 0.0
        try:
            # HumoralImmuneBlock exposes a get_state() or similar;
            # fall back to attribute access.
            state = None
            if hasattr(humoral, "handle_get_humoral_state"):
                state = humoral.handle_get_humoral_state()
            elif hasattr(humoral, "get_state"):
                state = humoral.get_state()
            if isinstance(state, dict):
                stress = float(state.get("stress", 0.0))
            else:
                stress = float(getattr(humoral, "_stress", 0.0) or 0.0)
            stress = max(0.0, min(1.0, stress))
            signals["humoral"] = {"stress": stress}
            return stress
        except Exception as e:
            logger.debug(f"auto: humoral stress read failed ({e})")
            return 0.0

    def _read_token_headroom(self, organism, signals: dict) -> float:
        engine = self._safe_get(organism, "engine")
        if engine is None:
            return 1.0
        try:
            budget = getattr(engine, "_token_budget", 0) or 0
            used = getattr(engine, "_tokens_used", 0) or 0
            if budget <= 0:
                return 1.0
            ratio = max(0.0, min(1.0, 1.0 - (used / budget)))
            signals["engine"] = {"budget": budget, "used": used,
                                 "headroom": ratio}
            return ratio
        except Exception as e:
            logger.debug(f"auto: engine token headroom read failed ({e})")
            return 1.0

    def _safe_get(self, organism, name: str):
        try:
            return organism.get_block(name)
        except Exception:
            return None
