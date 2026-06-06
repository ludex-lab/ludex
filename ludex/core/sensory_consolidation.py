"""Sensory consolidation — sampling-aware integration of Opsis / Akoué
observations into creature memory (Phase B Step 1 of perception-action
roadmap, see `docs/perception-action-systems-design.md`).

The creature's sensory organs (Opsis, Akoué) produce interpretive
output (description, transcription). Phase A passed that output through
to the engine as text without folding it into the creature's
substrate — the creature *saw* but did not *experience*. This module
closes that gap.

Design choices (documented in the perception-action design doc,
§7-§8):

- **Separate module, not organ internals.** Opsis/Akoué emit a rich
  `SensoryEvent`; this module is the subscriber that decides whether
  to consolidate. Organ autonomy preserved.
- **Sampling-aware gate.** Not every observation becomes memory. The
  gate inspects Topos (current_field), Chronos (elapsed since last
  sensing), Auto (vitals / memory pressure), and content salience,
  and decides pass / skip / bypass.
- **Inline signals, not organs yet.** Auto/Chronos/Topos are not yet
  promoted to organs (candidate D-056+ work). The gate reads their
  raw signals directly; when they are promoted, the gate refactors to
  organ queries without changing its semantics.
- **Phase B MVP scope:** memory write + span emit. Emotion feedback
  and bond updates are deferred to Phase C (§8 of the design doc).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Event + decision shapes
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class SensoryEvent:
    """A single sensing event produced by Opsis or Akoué.

    `source_kind` is "opsis" or "akoue". `content` is the full
    interpretive output (description / transcription). `purpose` is the
    caretaker-supplied reason for sensing (may be empty). `metadata`
    carries organ-specific extras (channel, interpreter, latency_ms,
    duration_s for audio, etc.) without coupling this module to
    concrete organ shapes.
    """
    source_kind: str
    content: str
    source: str = ""                       # original path or URL
    purpose: str = ""
    channel: str = ""
    field_name: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GateDecision:
    """Gate output. `write_memory` True means the consolidator emitted
    an episodic memory entry; `importance` was assigned 0.0-1.0.
    `reasons` carries the human-readable trail of which signals
    triggered the decision."""
    pass_gate: bool
    write_memory: bool
    importance: float
    reasons: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Gate policy constants (tune-able; frozen before real cohort use)
# ----------------------------------------------------------------------

SALIENCE_DESC_LEN_HIGH = 600    # chars; above this, treat as salient content
SALIENCE_DESC_LEN_LOW = 80      # chars; below this, treat as thin content
AMBIENT_WINDOW_SECONDS = 5 * 60   # skip repeats within this window if low salience
STALE_WINDOW_SECONDS = 30 * 60    # after this long, always record even low salience

_NOVELTY_HINTS = (
    "new", "first time", "never", "unexpected", "surprising",
    "novel", "unusual", "strange", "different",
)
_REMEMBER_HINTS = ("remember", "keep", "save", "log this", "record")


# ----------------------------------------------------------------------
# Last-seen tracker — keeps per-organism, per-field recency.
# Module-level dict keyed by (organism_name, source_kind, field_name).
# ----------------------------------------------------------------------

_LAST_SENSING: dict[tuple[str, str, str], float] = {}


def _last_seen_key(organism, source_kind: str, field_name: str) -> tuple[str, str, str]:
    name = getattr(organism, "name", "") or "?"
    return (name, source_kind, field_name or "")


def _mark_sensed(organism, event: SensoryEvent) -> None:
    _LAST_SENSING[_last_seen_key(organism, event.source_kind, event.field_name)] = event.timestamp


def _elapsed_since_last(organism, event: SensoryEvent) -> Optional[float]:
    prev = _LAST_SENSING.get(_last_seen_key(organism, event.source_kind, event.field_name))
    if prev is None:
        return None
    return event.timestamp - prev


# ----------------------------------------------------------------------
# Gate evaluation
# ----------------------------------------------------------------------

def _content_salience(content: str) -> float:
    """Return a 0.0-1.0 salience score from surface markers only.
    Low-cost, no LLM call. Used as one of several gate signals."""
    if not content:
        return 0.0
    n = len(content)
    length_score = 0.0
    if n >= SALIENCE_DESC_LEN_HIGH:
        length_score = 0.6
    elif n >= SALIENCE_DESC_LEN_LOW:
        length_score = 0.3
    else:
        length_score = 0.1

    lc = content.lower()
    novelty_hits = sum(1 for h in _NOVELTY_HINTS if h in lc)
    novelty_score = min(0.4, novelty_hits * 0.15)

    return min(1.0, length_score + novelty_score)


def _purpose_has_remember_hint(purpose: str) -> bool:
    if not purpose:
        return False
    lc = purpose.lower()
    return any(h in lc for h in _REMEMBER_HINTS)


def _memory_pressure(organism) -> float:
    """Return 0.0-1.0 pressure signal from the creature's interoceptive
    layer. Higher = more pressure = stricter gate.

    Prefers the Auto organ (D-058) when present — it's the proper
    interoceptive sensing surface and already aggregates memory + other
    internal state. Falls back to a direct memory health-check when
    Auto is absent, preserving Phase B Step 1 behavior for creatures
    that don't yet have the organ.
    """
    auto = _get_block(organism, "auto")
    if auto is not None:
        try:
            reading = auto.handle_sense()
            return float(getattr(reading, "memory_pressure", 0.0))
        except Exception:
            pass  # fall through to direct memory read
    mem = _get_block(organism, "memory")
    if mem is None:
        return 0.0
    try:
        health = mem.handle_health_check()
    except Exception:
        return 0.0
    grade = getattr(health, "grade", "A")
    return {"A": 0.0, "B": 0.25, "C": 0.5, "D": 0.75}.get(grade, 0.0)


def _get_block(organism, name: str):
    """Defensive block lookup — handles test doubles + disabled organs."""
    try:
        return organism.get_block(name)
    except Exception:
        return None


def evaluate_gate(organism, event: SensoryEvent) -> GateDecision:
    """Decide whether a sensing event should be consolidated into
    memory. Returns a GateDecision with `pass_gate`/`write_memory`/
    `importance` and a reason trail.

    Policy (matches design doc §3 sampling criteria, tune-able):

    1. `purpose` hint ("remember", "keep"...) → bypass; always write.
    2. First observation in a field → always write (baseline anchor).
    3. Content salience high → write.
    4. Elapsed > STALE window OR novelty markers present → write.
    5. Elapsed < AMBIENT window AND low salience → skip.
    6. Fallback → write with low importance.

    Memory pressure slightly raises the bar on (6); at pressure ≥ 0.5
    the default-write case is skipped (the creature is already full).
    """
    reasons: list[str] = []

    # (1) remember-hint bypass
    if _purpose_has_remember_hint(event.purpose):
        reasons.append("purpose:remember-hint")
        return GateDecision(
            pass_gate=True, write_memory=True, importance=0.9,
            reasons=reasons,
        )

    elapsed = _elapsed_since_last(organism, event)
    salience = _content_salience(event.content)
    pressure = _memory_pressure(organism)
    reasons.append(f"salience={salience:.2f}")
    reasons.append(f"pressure={pressure:.2f}")
    if elapsed is None:
        reasons.append("elapsed:first-in-field")
    else:
        reasons.append(f"elapsed={elapsed:.0f}s")

    # (2) first-in-field anchor
    if elapsed is None:
        return GateDecision(
            pass_gate=True, write_memory=True,
            importance=max(0.55, salience),
            reasons=reasons + ["gate:first-in-field"],
        )

    # (3) high salience
    if salience >= 0.7:
        return GateDecision(
            pass_gate=True, write_memory=True,
            importance=min(0.9, salience),
            reasons=reasons + ["gate:high-salience"],
        )

    # (4) stale window OR novelty marker
    if elapsed >= STALE_WINDOW_SECONDS or salience >= 0.5:
        return GateDecision(
            pass_gate=True, write_memory=True,
            importance=max(0.45, salience),
            reasons=reasons + ["gate:stale-or-novel"],
        )

    # (5) ambient skip — inclusive boundary on the low-salience side.
    # An 89-char interpreter response scores salience 0.30 exactly
    # (length_score floor at DESC_LEN_LOW), which we classify as
    # "thin enough to skip if recent" rather than "just barely above
    # the fallback line." Boundary observed on Ray-habitat 2026-04-23
    # smoke; tuning documented in the session journal.
    if elapsed < AMBIENT_WINDOW_SECONDS and salience <= 0.3:
        return GateDecision(
            pass_gate=False, write_memory=False, importance=0.0,
            reasons=reasons + ["gate:ambient-skip"],
        )

    # (6) fallback — write at low importance unless pressure is high
    if pressure >= 0.5:
        return GateDecision(
            pass_gate=False, write_memory=False, importance=0.0,
            reasons=reasons + ["gate:pressure-skip"],
        )
    return GateDecision(
        pass_gate=True, write_memory=True,
        importance=0.35,
        reasons=reasons + ["gate:fallback-write"],
    )


# ----------------------------------------------------------------------
# Consolidation entry point
# ----------------------------------------------------------------------

def consolidate_observation(organism, event: SensoryEvent) -> GateDecision:
    """Main entry. Call this after Opsis / Akoué produces a successful
    interpretation. Evaluates the gate and — if gate passes — writes
    an episodic memory and emits a consolidation span.

    Returns the GateDecision for observability / tests.
    """
    decision = evaluate_gate(organism, event)

    # Always update the last-seen tracker so subsequent ambient events
    # see this moment even if we decided to skip the memory write.
    _mark_sensed(organism, event)

    if decision.write_memory:
        _apply_memory_write(organism, event, decision)

    _emit_span(organism, event, decision)
    return decision


def _apply_memory_write(organism, event: SensoryEvent, decision: GateDecision) -> None:
    mem = _get_block(organism, "memory")
    if mem is None:
        return
    try:
        mem.handle_remember(
            content=_format_memory_content(event),
            memory_type="episodic",
            tags=[event.source_kind, "sensed", event.field_name or "unscoped"],
            importance=decision.importance,
            source=event.source_kind,
            metadata={
                "source": event.source[:200],
                "channel": event.channel,
                "purpose": event.purpose[:200],
                "field_name": event.field_name,
                "gate_reasons": decision.reasons,
                "sensed_at": event.timestamp,
                **(event.metadata or {}),
            },
        )
    except Exception as e:
        logger.warning(f"sensory consolidation memory write failed: {e}")


def _format_memory_content(event: SensoryEvent) -> str:
    """One-line episodic memory content. Biological metaphor: the
    creature's memory of the sensing event, not the raw content."""
    verb = "saw" if event.source_kind == "opsis" else "heard"
    prefix = f"[{event.source_kind}] {verb}:"
    if event.purpose:
        prefix += f" ({event.purpose.strip()[:80]})"
    body = " ".join(event.content.split())
    return (prefix + " " + body)[:600]


def _emit_span(organism, event: SensoryEvent, decision: GateDecision) -> None:
    try:
        from ludex.core import trace as _tr
        _tr.emit_sensory_consolidation(
            organism,
            source_kind=event.source_kind,
            field_name=event.field_name,
            gate_passed=decision.pass_gate,
            wrote_memory=decision.write_memory,
            importance=decision.importance,
            reasons=decision.reasons,
        )
    except Exception:
        # trace helper may not exist in older checkouts; swallow.
        pass
