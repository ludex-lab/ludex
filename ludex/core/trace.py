"""
Trace emission helpers — Phase A of the agent-lightning integration.

Thin wrappers around LudexStore that standardize span `kind` vocabulary
and reward dimensions. Callers should import these helpers rather than
constructing Span objects directly, so kind names stay consistent.

Reward dimensions (Phase A shipping set):
- energy_delta: change in creature energy over a field or tick
- bond_accuracy: ToM prediction match rate at bond level (N/M ratio)
- prediction_match_rate: rolling match rate across recent predictions
- caretaker_signal: explicit caretaker approval/disapproval

Deferred (Phase B or later):
- self_drift_distance: requires a chosen similarity metric; defer until
  we have enough drift data to choose meaningfully.
"""

from __future__ import annotations

import logging
from typing import Any

from ludex.core.store import LudexStore, Span

logger = logging.getLogger(__name__)


# ============================================================
# Current-field context — so downstream emitters (selfhood.reflect,
# selfhood.update_bond) can auto-attribute to the active field without
# threading the name through every signature.
# ============================================================

_context: dict[str, str] = {"field_name": ""}


def set_current_field(field_name: str) -> None:
    _context["field_name"] = field_name or ""


def clear_current_field() -> None:
    _context["field_name"] = ""


def current_field() -> str:
    return _context.get("field_name", "")


# ============================================================
# Span kinds
# ============================================================

# Field lifecycle
KIND_FIELD_START = "field_start"
KIND_FIELD_END = "field_end"
KIND_TICK = "tick"

# ToM
KIND_TOM_PREDICT = "tom_predict"
KIND_TOM_VERIFY = "tom_verify"

# Bonds / reflection
KIND_BOND_UPDATE = "bond_update"
KIND_REFLECT = "reflect"

# Academy learning field (D-031)
KIND_THEME_PRESENTED = "theme_presented"

# Forum epistemic field (D-032)
KIND_CLAIM_POSTED = "claim_posted"
KIND_STANCE_STATED = "stance_stated"
KIND_EVIDENCE_OFFERED = "evidence_offered"
KIND_EVIDENCE_CONCEDED = "evidence_conceded"
KIND_CHALLENGE_MADE = "challenge_made"
KIND_POSITION_UPDATED = "position_updated"
KIND_VERDICT_REVEALED = "verdict_revealed"

# Rewards (these spans carry a non-null reward payload)
KIND_REWARD_ENERGY_DELTA = "reward.energy_delta"
KIND_REWARD_BOND_ACCURACY = "reward.bond_accuracy"
KIND_REWARD_PREDICTION_MATCH_RATE = "reward.prediction_match_rate"
KIND_REWARD_CARETAKER_SIGNAL = "reward.caretaker_signal"
KIND_REWARD_CONFIDENCE_CALIBRATION = "reward.confidence_calibration"
KIND_REWARD_UPDATE_ON_EVIDENCE = "reward.update_on_evidence"
KIND_REWARD_EVIDENCE_STANDARD = "reward.evidence_standard_upheld"
KIND_REWARD_OVERCLAIM_PENALTY = "reward.overclaim_penalty"
KIND_REWARD_ENGAGEMENT_DEPTH = "reward.engagement_depth"
KIND_REWARD_MATERIAL_INTEGRATION = "reward.material_integration"
KIND_REWARD_NOVEL_CONNECTION = "reward.novel_connection"
KIND_REWARD_STANCE_COHERENCE = "reward.stance_coherence"
KIND_REWARD_SELF_OTHER_INTEGRATION = "reward.self_other_integration"
KIND_REWARD_READINESS_ARTICULATED = "reward.readiness_articulated"
KIND_REWARD_PLAN_SPECIFICITY = "reward.plan_specificity"
KIND_TEACHER_NARRATION = "teacher_narration"
KIND_APPRENTICE_OBSERVATION = "apprentice_observation"
KIND_APPRENTICE_APPLICATION = "apprentice_application"
KIND_REWARD_PRACTICE_ARTICULATION = "reward.practice_articulation"
KIND_REWARD_OBSERVATIONAL_DEPTH = "reward.observational_depth"
KIND_REWARD_TRANSFER_ATTEMPT = "reward.transfer_attempt"
KIND_TEACHER_REFLECTION = "teacher_reflection"
KIND_REWARD_REVERSE_LEARNING = "reward.reverse_learning"
# Council field
KIND_REWARD_POSITION_STABILITY = "reward.position_stability_under_pressure"
KIND_REWARD_CONSTRUCTIVE_YIELD = "reward.constructive_yield"
KIND_REWARD_MEDIATION_QUALITY = "reward.mediation_quality"
# Prompt tier translator (D-043)
KIND_TRANSLATION_APPLIED = "translation_applied"

# D-044 Layer 1 — brain provenance
KIND_BRAIN_RESOLVED = "brain_resolved"
KIND_BRAIN_TRANSITION = "brain_transition"

# D-081 — Metabolic Gauge: per-call brain usage accounting
KIND_BRAIN_CALL = "brain_call"

# D-046 — bidirectional KD pattern detector
KIND_BIDIRECTIONAL_KD_DETECTED = "bidirectional_kd_detected"

# D-024 Sprint 1 — memory distillation candidate signal

# D-048 — Opsis (vision) sensing
KIND_OPSIS_INVOKED = "opsis_invoked"

# D-049 — Akoué (audio) sensing
KIND_AKOUE_INVOKED = "akoue_invoked"

# Perception-action Step 1 — sensory consolidation gate decision
KIND_SENSORY_CONSOLIDATION = "sensory_consolidation"

# D-058 — Auto (interoceptive) sense invocation
KIND_AUTO_SENSED = "auto_sensed"

# D-059 — Chronos (temporal) sense invocation
KIND_CHRONOS_SENSED = "chronos_sensed"

# Sphygmos vitals/reflex organ (docs/sphygmos-organ-design.md) — every reflex
# block is attributable (autoimmunity rule 2: no silent self-blocks).
KIND_SPHYGMOS_GUARD = "sphygmos_guard"
KIND_SPHYGMOS_INCIDENT = "sphygmos_incident"

# D-060 — Topos (contextual / spatial) sense invocation
KIND_TOPOS_SENSED = "topos_sensed"

# D-061 — Allos (social) sense invocation
KIND_ALLOS_SENSED = "allos_sensed"

# D-051 — Heartbeat lifecycle pulse
KIND_HEARTBEAT_PULSE = "heartbeat_pulse"

# Joint LxM bridge — per-match distilled experience (spec §A.4)
KIND_LXM_MATCH_EXPERIENCE = "lxm.match_experience"

# D-062 Phase 1 — cross-habitat reach (tentacle into remote field)
KIND_REACH_EXTENDED = "reach_extended"
KIND_REACH_RETRACTED = "reach_retracted"


# ============================================================
# Helpers — organism-aware so callers can pass an organism directly
# ============================================================

def _store_for(organism) -> LudexStore | None:
    config = getattr(organism, "config", None)
    if not config:
        return None
    habitat = config.get("habitat_dir", "") if hasattr(config, "get") else ""
    if not habitat:
        return None
    return LudexStore.for_creature(habitat)


def _emit(
    organism,
    kind: str,
    attributes: dict[str, Any] | None = None,
    reward: dict[str, Any] | None = None,
) -> None:
    store = _store_for(organism)
    if store is None:
        return
    span = Span(
        kind=kind,
        creature=organism.name,
        attributes=attributes or {},
        reward=reward,
    )
    store.append(span)


# ============================================================
# Field lifecycle emitters
# ============================================================

def emit_field_start(organism, field_name: str, field_type: str, seed: int | None, ticks: int) -> None:
    _emit(organism, KIND_FIELD_START, {
        "field_name": field_name,
        "field_type": field_type,
        "seed": seed,
        "ticks": ticks,
    })


def emit_field_end(organism, field_name: str, duration_s: float, summary: dict[str, Any] | None = None) -> None:
    _emit(organism, KIND_FIELD_END, {
        "field_name": field_name,
        "duration_s": duration_s,
        **(summary or {}),
    })


def emit_tick(organism, field_name: str, tick: int, event: str, action: str, energy: int, emotion: str) -> None:
    _emit(organism, KIND_TICK, {
        "field_name": field_name,
        "tick": tick,
        "event": event,
        "action": action,
        "energy": energy,
        "emotion": emotion,
    })


# ============================================================
# ToM emitters
# ============================================================

def _fn(field_name: str | None) -> str:
    return field_name if field_name is not None else current_field()


def emit_tom_predict(organism, target: str, situation: str, prediction: str, field_name: str | None = None) -> None:
    _emit(organism, KIND_TOM_PREDICT, {
        "target": target,
        "situation": situation,
        "prediction": prediction,
        "field_name": _fn(field_name),
    })


def emit_tom_verify(organism, target: str, match_count: int, total: int, field_name: str | None = None) -> None:
    _emit(organism, KIND_TOM_VERIFY, {
        "target": target,
        "match_count": match_count,
        "total": total,
        "field_name": _fn(field_name),
    })


# ============================================================
# Bond / reflect emitters
# ============================================================

def emit_bond_update(organism, other_name: str, first_met: str, field_name: str | None = None) -> None:
    _emit(organism, KIND_BOND_UPDATE, {
        "other": other_name,
        "first_met": first_met,
        "field_name": _fn(field_name),
    })


def emit_reflect(organism, trigger: str, self_md_len: int, field_name: str | None = None) -> None:
    _emit(organism, KIND_REFLECT, {
        "trigger": trigger,
        "self_md_len": self_md_len,
        "field_name": _fn(field_name),
    })


# ============================================================
# Reward emitters — always carry a `reward` payload
# ============================================================

def emit_reward_energy_delta(organism, field_name: str, start_energy: int, end_energy: int) -> float:
    delta = float(end_energy - start_energy)
    _emit(organism, KIND_REWARD_ENERGY_DELTA, {
        "field_name": field_name,
        "start_energy": start_energy,
        "end_energy": end_energy,
    }, reward={"dimension": "energy_delta", "value": delta})
    return delta


def emit_reward_bond_accuracy(organism, other_name: str, match_count: int, total: int, field_name: str | None = None) -> float | None:
    if total <= 0:
        return None
    acc = match_count / float(total)
    _emit(organism, KIND_REWARD_BOND_ACCURACY, {
        "other": other_name,
        "match_count": match_count,
        "total": total,
        "field_name": _fn(field_name),
    }, reward={"dimension": "bond_accuracy", "value": acc})
    return acc


def emit_reward_prediction_match_rate(organism, window: int, match_rate: float, field_name: str | None = None) -> None:
    _emit(organism, KIND_REWARD_PREDICTION_MATCH_RATE, {
        "window": window,
        "field_name": _fn(field_name),
    }, reward={"dimension": "prediction_match_rate", "value": match_rate})


def emit_reward_caretaker_signal(organism, value: float, note: str = "") -> None:
    """Caretaker-issued signal. Positive values = approval, negative = disapproval.

    Scale is intentionally free — JJ picks what each value means for a given
    signal. Note captures the reason so the signal is interpretable later.
    """
    _emit(organism, KIND_REWARD_CARETAKER_SIGNAL, {
        "note": note,
    }, reward={"dimension": "caretaker_signal", "value": float(value)})


# ============================================================
# Forum emitters (D-032 epistemic field)
# ============================================================

def emit_claim_posted(organism, claim_text: str, topic: str = "", claim_id: str = "") -> None:
    _emit(organism, KIND_CLAIM_POSTED, {
        "claim_text": claim_text,
        "topic": topic,
        "claim_id": claim_id,
        "field_name": current_field(),
    })


def emit_stance_stated(organism, stance: str, confidence: float, raw: str) -> None:
    _emit(organism, KIND_STANCE_STATED, {
        "stance": stance,
        "confidence": confidence,
        "raw": raw[:400],
        "field_name": current_field(),
    })


def emit_evidence_offered(organism, content: str, grounded: bool) -> None:
    _emit(organism, KIND_EVIDENCE_OFFERED, {
        "content": content[:400],
        "grounded": grounded,
        "field_name": current_field(),
    })


def emit_evidence_conceded(organism, content: str) -> None:
    _emit(organism, KIND_EVIDENCE_CONCEDED, {
        "content": content[:400],
        "field_name": current_field(),
    })


def emit_challenge_made(organism, content: str) -> None:
    _emit(organism, KIND_CHALLENGE_MADE, {
        "content": content[:400],
        "field_name": current_field(),
    })


def emit_position_updated(organism, prev_stance: str, new_stance: str,
                          confidence: float, changed: bool, grounded: bool) -> None:
    _emit(organism, KIND_POSITION_UPDATED, {
        "previous_stance": prev_stance,
        "new_stance": new_stance,
        "confidence": confidence,
        "changed": changed,
        "grounded": grounded,
        "field_name": current_field(),
    })


def emit_verdict_revealed(organism, value: str, provenance: str, explanation: str) -> None:
    _emit(organism, KIND_VERDICT_REVEALED, {
        "value": value,
        "provenance": provenance,
        "explanation": explanation[:400],
        "field_name": current_field(),
    })


def emit_reward_confidence_calibration(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_CONFIDENCE_CALIBRATION, {
        "field_name": current_field(),
    }, reward={"dimension": "confidence_calibration", "value": value})


def emit_reward_update_on_evidence(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_UPDATE_ON_EVIDENCE, {
        "field_name": current_field(),
    }, reward={"dimension": "update_on_evidence", "value": value})


def emit_reward_evidence_standard(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_EVIDENCE_STANDARD, {
        "field_name": current_field(),
    }, reward={"dimension": "evidence_standard_upheld", "value": value})


def emit_reward_overclaim_penalty(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_OVERCLAIM_PENALTY, {
        "field_name": current_field(),
    }, reward={"dimension": "overclaim_penalty", "value": value})


# ============================================================
# Academy emitters (D-031 learning field)
# ============================================================

def emit_theme_presented(organism, theme: str, mode: str = "", preparation_target: str | None = None) -> None:
    _emit(organism, KIND_THEME_PRESENTED, {
        "theme": theme,
        "mode": mode,
        "preparation_target": preparation_target,
        "field_name": current_field(),
    })


def emit_reward_engagement_depth(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_ENGAGEMENT_DEPTH, {
        "field_name": current_field(),
    }, reward={"dimension": "engagement_depth", "value": value})


def emit_reward_material_integration(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_MATERIAL_INTEGRATION, {
        "field_name": current_field(),
    }, reward={"dimension": "material_integration", "value": value})


def emit_reward_novel_connection(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_NOVEL_CONNECTION, {
        "field_name": current_field(),
    }, reward={"dimension": "novel_connection", "value": value})


def emit_reward_stance_coherence(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_STANCE_COHERENCE, {
        "field_name": current_field(),
    }, reward={"dimension": "stance_coherence", "value": value})


def emit_reward_self_other_integration(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_SELF_OTHER_INTEGRATION, {
        "field_name": current_field(),
    }, reward={"dimension": "self_other_integration", "value": value})


def emit_reward_readiness_articulated(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_READINESS_ARTICULATED, {
        "field_name": current_field(),
    }, reward={"dimension": "readiness_articulated", "value": value})


def emit_reward_plan_specificity(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_PLAN_SPECIFICITY, {
        "field_name": current_field(),
    }, reward={"dimension": "plan_specificity", "value": value})


# ============================================================
# Apprenticeship emitters (Stage 3)
# ============================================================

def emit_teacher_narration(organism, content: str) -> None:
    _emit(organism, KIND_TEACHER_NARRATION, {
        "content": content[:400],
        "field_name": current_field(),
    })


def emit_apprentice_observation(organism, content: str) -> None:
    _emit(organism, KIND_APPRENTICE_OBSERVATION, {
        "content": content[:400],
        "field_name": current_field(),
    })


def emit_apprentice_application(organism, content: str) -> None:
    _emit(organism, KIND_APPRENTICE_APPLICATION, {
        "content": content[:400],
        "field_name": current_field(),
    })


def emit_reward_practice_articulation(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_PRACTICE_ARTICULATION, {
        "field_name": current_field(),
    }, reward={"dimension": "practice_articulation", "value": value})


def emit_reward_observational_depth(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_OBSERVATIONAL_DEPTH, {
        "field_name": current_field(),
    }, reward={"dimension": "observational_depth", "value": value})


def emit_reward_transfer_attempt(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_TRANSFER_ATTEMPT, {
        "field_name": current_field(),
    }, reward={"dimension": "transfer_attempt", "value": value})


def emit_teacher_reflection(organism, content: str) -> None:
    _emit(organism, KIND_TEACHER_REFLECTION, {
        "content": content[:400],
        "field_name": current_field(),
    })


def emit_reward_reverse_learning(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_REVERSE_LEARNING, {
        "field_name": current_field(),
    }, reward={"dimension": "reverse_learning", "value": value})


def emit_reward_position_stability(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_POSITION_STABILITY, {
        "field_name": current_field(),
    }, reward={"dimension": "position_stability_under_pressure", "value": value})


def emit_reward_constructive_yield(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_CONSTRUCTIVE_YIELD, {
        "field_name": current_field(),
    }, reward={"dimension": "constructive_yield", "value": value})


def emit_reward_mediation_quality(organism, value: float) -> None:
    _emit(organism, KIND_REWARD_MEDIATION_QUALITY, {
        "field_name": current_field(),
    }, reward={"dimension": "mediation_quality", "value": value})


# ============================================================
# Prompt tier translator (D-043)
# ============================================================

# ============================================================
# Brain provenance (D-044 Layer 1)
# ============================================================

def classify_brain_band(from_model: str, to_model: str) -> str:
    """Classify a brain change into D-044 bands.

    Bands: minor (point release), major (integer release), family (Claude
    -> GPT, Gemma -> Llama), tier (SLM <-> LLM crossing). "none" if
    from == to. Best-effort string heuristic; refines as more model
    families appear.
    """
    if not from_model or not to_model:
        return "unknown"
    if from_model == to_model:
        return "none"

    f, t = from_model.lower(), to_model.lower()

    def family(m: str) -> str:
        if "claude" in m: return "claude"
        if m.startswith("gpt") or "openai" in m: return "openai"
        if "gemini" in m: return "gemini"
        if "gemma" in m: return "gemma"
        if "llama" in m: return "llama"
        if "mistral" in m: return "mistral"
        if "qwen" in m: return "qwen"
        if "deepseek" in m: return "deepseek"
        return "other"

    ff, tf = family(f), family(t)
    if ff != tf:
        return "family"

    # Same family: split on dashes/colons/dots, compare leading version
    import re as _re
    def major_ver(m: str) -> str:
        nums = _re.findall(r"\d+", m)
        return nums[0] if nums else ""

    if major_ver(f) and major_ver(t) and major_ver(f) != major_ver(t):
        return "major"
    return "minor"


def emit_brain_resolved(organism, configured: str, actual: str, reason: str = "configured") -> None:
    """Emit a meta span recording which brain answered this call.

    Adapter calls this once per request after the model responds (or
    after an explicit error that names the model). `configured` is what
    the caller asked for; `actual` is what the provider actually used
    (best-effort — many CLIs don't echo back the resolved model, in
    which case actual==configured). `reason` is one of:
    "configured", "fallback", "provider_default", "error", "unknown".
    """
    try:
        attrs = {
            "configured": configured or "",
            "actual": actual or configured or "",
            "reason": reason,
            "field_name": current_field(),
        }
        _emit(organism, KIND_BRAIN_RESOLVED, attrs)
        if actual and configured and actual != configured:
            band = classify_brain_band(configured, actual)
            _emit(organism, KIND_BRAIN_TRANSITION, {
                "from": configured,
                "to": actual,
                "band": band,
                "reason": reason,
                "field_name": current_field(),
            })
    except Exception:
        pass


def emit_brain_call(
    organism,
    model: str,
    provider: str,
    tokens_in: int,
    tokens_out: int,
    token_source: str,
    latency_ms: float,
    outcome: str,
    error_type: str = "",
    effort: str = "",
) -> None:
    """Emit a per-call brain usage span (Metabolic Gauge, D-081).

    Recorded once per LLM call from the Provider block, alongside
    emit_brain_resolved. Carries raw usage facts only — pricing is
    applied read-side by `tools/cost_report.py` against
    `config/model_pricing.yaml`. See `docs/metabolic-gauge-design.md`.

    `token_source` is "estimated" (CLI adapters' len//4 heuristic)
    or "measured" (real counts, e.g. Ollama). `outcome` is "ok" or
    "error"; `error_type` is "" when ok, else one of "auth",
    "rate_limit", "timeout", "quota", "model_error", "connection".
    """
    try:
        _emit(organism, KIND_BRAIN_CALL, {
            "model": model or "",
            "provider": provider or "",
            "field_name": current_field(),
            "tokens_in": int(tokens_in or 0),
            "tokens_out": int(tokens_out or 0),
            "token_source": token_source or "estimated",
            "latency_ms": round(float(latency_ms or 0.0), 1),
            "outcome": outcome or "ok",
            "error_type": error_type or "",
            "effort": effort or "",
        })
    except Exception:
        pass


def emit_heartbeat_pulse(
    organism, outcome: str, health_grade: str,
    stale_bonds: list[str], consolidated: bool, reflected: bool,
    field_name: str | None = None,
) -> None:
    """D-051 Phase A. One pulse of the creature's heartbeat.
    outcome: healthy / maintenance_ran / degraded / brain_unavailable.
    """
    try:
        _emit(organism, KIND_HEARTBEAT_PULSE, {
            "outcome": outcome,
            "health_grade": health_grade,
            "stale_bonds": stale_bonds[:5],
            "consolidated": consolidated,
            "reflected": reflected,
            "field_name": _fn(field_name),
        })
    except Exception:
        pass


def emit_akoue_invoked(
    organism, source: str, channel: str, purpose: str,
    transcription_len: int, duration_s: float, latency_ms: float,
    field_name: str | None = None,
) -> None:
    """D-049 Phase A. Emitted once per Akoué.hear() invocation.
    Records the transcription metadata, not raw audio (per D-049
    safety: creature memory holds transcription only).

    channel is one of: "logos_fallback" (Phase A default), "native"
    (Phase B+ for audio-capable brains).
    """
    try:
        _emit(organism, KIND_AKOUE_INVOKED, {
            "source": source[:200],
            "channel": channel,
            "purpose": purpose[:200],
            "transcription_len": transcription_len,
            "duration_s": round(duration_s, 2),
            "latency_ms": round(latency_ms, 1),
            "field_name": _fn(field_name),
        })
    except Exception:
        pass


def emit_opsis_invoked(
    organism, source: str, channel: str, purpose: str,
    description_len: int, latency_ms: float,
    field_name: str | None = None,
) -> None:
    """D-048 Phase A. Emitted once per Opsis.see() invocation.
    Records the *interpretation*, not raw image bytes (per D-048
    safety: creature memory holds interpretation only).

    channel is one of: "logos_fallback" (Phase A default), "native"
    (Phase B+ when adapters support image input).
    """
    try:
        _emit(organism, KIND_OPSIS_INVOKED, {
            "source": source[:200],
            "channel": channel,
            "purpose": purpose[:200],
            "description_len": description_len,
            "latency_ms": round(latency_ms, 1),
            "field_name": _fn(field_name),
        })
    except Exception:
        pass


def emit_auto_sensed(
    organism, valence: float, arousal: float, memory_grade: str,
    memory_pressure: float, stress: float, token_headroom: float,
    summary: str = "", field_name: str | None = None,
) -> None:
    """D-058 Phase A. Emitted once per AutoBlock.handle_sense() call.
    Captures the interoceptive snapshot so longitudinal analysis can
    track how a creature's internal state varied across sessions
    without reconstructing it from per-organ spans."""
    try:
        _emit(organism, KIND_AUTO_SENSED, {
            "valence": round(float(valence), 3),
            "arousal": round(float(arousal), 3),
            "memory_grade": memory_grade,
            "memory_pressure": round(float(memory_pressure), 3),
            "stress": round(float(stress), 3),
            "token_headroom": round(float(token_headroom), 3),
            "summary": (summary or "")[:200],
            "field_name": _fn(field_name),
        })
    except Exception:
        pass


def emit_chronos_sensed(
    organism, session_age_s: float, creature_age_s,
    session_count: int, last_sensory_ago_s, last_reflection_ago_s,
    summary: str = "", field_name: str | None = None,
) -> None:
    """D-059 Phase A. Emitted once per ChronosBlock.handle_sense().
    Captures the temporal snapshot."""
    try:
        _emit(organism, KIND_CHRONOS_SENSED, {
            "session_age_s": round(float(session_age_s), 2),
            "creature_age_s": (round(float(creature_age_s), 2)
                                if creature_age_s is not None else None),
            "session_count": int(session_count),
            "last_sensory_ago_s": (round(float(last_sensory_ago_s), 2)
                                    if last_sensory_ago_s is not None else None),
            "last_reflection_ago_s": (round(float(last_reflection_ago_s), 2)
                                       if last_reflection_ago_s is not None else None),
            "summary": (summary or "")[:200],
            "field_name": _fn(field_name),
        })
    except Exception:
        pass


def emit_sphygmos_guard(organism, guard: str, cls: str, action: str, reason: str) -> None:
    """Sphygmos reflex fired — the ATTRIBUTION span (autoimmunity rule 2:
    a self-protective block must never be silent)."""
    try:
        _emit(organism, KIND_SPHYGMOS_GUARD, {
            "guard": guard, "cls": cls, "action": action,
            "reason": (reason or "")[:200],
        })
    except Exception:
        pass


def emit_sphygmos_incident(organism, cls: str, signature: str, promoted: bool) -> None:
    """Sphygmos incident recorded; promoted=True when a NEW signature just
    reached >=2 independent incidents and becomes an acting antibody."""
    try:
        _emit(organism, KIND_SPHYGMOS_INCIDENT, {
            "cls": cls, "signature": (signature or "")[:200], "promoted": bool(promoted),
        })
    except Exception:
        pass


def emit_topos_sensed(
    organism, field_name: str, field_locality: str,
    machine_short: str, habitat_origin: str,
    summary: str = "",
) -> None:
    """D-060 Phase A. Emitted once per ToposBlock.handle_sense().
    Captures the contextual / spatial snapshot."""
    try:
        _emit(organism, KIND_TOPOS_SENSED, {
            "field_name": field_name[:200],
            "field_locality": field_locality,
            "machine_short": machine_short[:80],
            "habitat_origin": habitat_origin[:80],
            "summary": (summary or "")[:200],
        })
    except Exception:
        pass


def emit_allos_sensed(
    organism, known_count: int, bonds_dir: str,
    summary: str = "", field_name: str | None = None,
) -> None:
    """D-061 Phase A. Emitted once per AllosBlock.handle_sense().
    Captures the social snapshot (count, not per-other details)."""
    try:
        _emit(organism, KIND_ALLOS_SENSED, {
            "known_count": int(known_count),
            "bonds_dir": bonds_dir[:200],
            "summary": (summary or "")[:200],
            "field_name": _fn(field_name),
        })
    except Exception:
        pass


def emit_sensory_consolidation(
    organism, source_kind: str, field_name: str,
    gate_passed: bool, wrote_memory: bool, importance: float,
    reasons: list[str] | None = None,
) -> None:
    """Perception-action Step 1. Emitted once per consolidation attempt
    regardless of whether the gate passed. Records the decision (and
    its reason trail) so retrospective analysis can see what the
    sampling policy did.
    """
    try:
        _emit(organism, KIND_SENSORY_CONSOLIDATION, {
            "source_kind": source_kind,
            "field_name": _fn(field_name),
            "gate_passed": bool(gate_passed),
            "wrote_memory": bool(wrote_memory),
            "importance": round(float(importance), 3),
            "reasons": list(reasons or [])[:10],
        })
    except Exception:
        pass


def emit_bidirectional_kd_detected(
    organism, questioner: str, yielder: str,
    argument_snippet: str, concession_snippet: str,
    field_name: str | None = None,
) -> None:
    """D-046 detector output. Emitted on the yielder's store when the
    pattern is detected — a questioner's argument-phase question at
    the seam of the yielder's framing moved the yielder to name them
    and concede specific ground. Detector is heuristic; human review
    required before wiring to auto-reflect (see D-046 sequencing).
    """
    try:
        _emit(organism, KIND_BIDIRECTIONAL_KD_DETECTED, {
            "questioner": questioner,
            "yielder": yielder,
            "argument_snippet": argument_snippet[:400],
            "concession_snippet": concession_snippet[:400],
            "field_name": _fn(field_name),
        })
    except Exception:
        pass


def emit_translation_applied(organism, result) -> None:
    """Emit a meta-only span about a translation. Does not store the
    prompt text — only source/target length, tier, transformations, and
    translator name. See ludex/core/prompt_tier.py for TranslationResult.
    """
    if result is None:
        return
    try:
        attrs = {
            "source_length": getattr(result, "source_length", None),
            "target_length": getattr(result, "target_length", None),
            "target_tier": getattr(getattr(result, "target_tier", None), "value", None),
            "transformations": list(getattr(result, "transformations", []) or []),
            "translator": getattr(result, "translator", "rule-based"),
            "field_name": current_field(),
        }
        _emit(organism, KIND_TRANSLATION_APPLIED, attrs)
    except Exception:
        pass


# ============================================================
# LxM bridge — joint spec §A.4
# ============================================================

def emit_lxm_match_experience(
    organism,
    match_id: str,
    summary: str,
    moves_count: int,
    outcome: str,
    meta: dict[str, Any] | None = None,
) -> str | None:
    """Record a per-match distilled memory entry after an LxM match closes.

    Called once by the LxM adapter at match-close with a one-sentence
    summary of the creature's experience. Produces two artifacts:

    1. A span in the creature's store (`kind=lxm.match_experience`)
       with match_id / outcome / moves_count / meta for provenance.
    2. A semantic-type memory entry the creature can later recall,
       tagged `["lxm", match_id, "distilled"]`. Importance 0.7 (above
       default to survive budget pressure longer — a full match is
       more salient than a single turn).

    Returns the stored memory's id, or None if the organism has no
    memory block attached (e.g. test doubles).
    """
    attrs: dict[str, Any] = {
        "match_id": match_id,
        "outcome": outcome,
        "moves_count": moves_count,
        "summary": summary[:400],
    }
    if meta:
        attrs["meta"] = meta
    _emit(organism, KIND_LXM_MATCH_EXPERIENCE, attrs)

    memory = organism.get_block("memory") if hasattr(organism, "get_block") else None
    if memory is None:
        return None
    content = (
        f"[LxM match {match_id} ({outcome}, {moves_count} turns)] {summary}"
    )
    return memory.handle_remember(
        content=content,
        memory_type="semantic",
        tags=["lxm", match_id, "distilled"],
        importance=0.7,
        source="lxm_adapter",
        metadata={"match_id": match_id, "outcome": outcome,
                  "moves_count": moves_count, **(meta or {})},
    )


# ============================================================
# D-062 Phase 1 — reach (cross-habitat / loopback) span helpers
# ============================================================

def emit_reach_extended(
    organism,
    *,
    pipe_kind: str = "local_loopback",
    transport: str = "in_process",
    tool_name: str = "ludex_engine_submit",
    field_name: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """A reach session opened: the creature is about to actuate through
    a pipe (in-process for Phase 1 loopback; stdio / TCP MCP later).

    `pipe_kind`: taxonomy of the pipe (`local_loopback`, `lan_mcp`,
    `shared_doc`, ...). Matches Topos `field_locality` intent but lives
    here because the pipe predates any organ wiring.
    `transport`: wire-level layer (`in_process`, `stdio`, `tcp`, `ws`).
    """
    try:
        attrs: dict[str, Any] = {
            "pipe_kind": pipe_kind,
            "transport": transport,
            "tool": tool_name,
            "field_name": _fn(field_name),
        }
        if attributes:
            attrs.update(attributes)
        _emit(organism, KIND_REACH_EXTENDED, attrs)
    except Exception:
        logger.debug("emit_reach_extended failed", exc_info=True)


def emit_reach_retracted(
    organism,
    *,
    pipe_kind: str = "local_loopback",
    transport: str = "in_process",
    tool_name: str = "ludex_engine_submit",
    duration_s: float | None = None,
    ok: bool = True,
    error: str = "",
    field_name: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Paired close for emit_reach_extended. `ok=False` + `error` when
    the pipe call raised; duration measured in seconds."""
    try:
        attrs: dict[str, Any] = {
            "pipe_kind": pipe_kind,
            "transport": transport,
            "tool": tool_name,
            "ok": bool(ok),
            "error": (error or "")[:400],
            "duration_s": (round(float(duration_s), 4)
                           if duration_s is not None else None),
            "field_name": _fn(field_name),
        }
        if attributes:
            attrs.update(attributes)
        _emit(organism, KIND_REACH_RETRACTED, attrs)
    except Exception:
        logger.debug("emit_reach_retracted failed", exc_info=True)


# ============================================================
# Rolling computation helpers (read-side)
# ============================================================

def compute_prediction_match_rate(organism, window: int = 10) -> float | None:
    """Rolling match rate across the last `window` bond_accuracy rewards."""
    store = _store_for(organism)
    if store is None:
        return None
    values = store.reward_values("bond_accuracy")
    if not values:
        return None
    recent = values[-window:]
    return sum(recent) / len(recent)
