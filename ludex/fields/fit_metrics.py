"""
Fit Metrics -- measuring brain-body compatibility, not brain quality.

Core insight (goldvader): when a creature underperforms on a Ludex field,
the right interpretation is usually NOT "this brain is bad" but rather
"this brain doesn't fit our system well." This module measures rejection
and comfort signals that indicate fit, and packages them into an actionable
fit score.

Rejection markers (low = good fit):
- Errors / failed responses
- Empty / meaningless responses
- Tool format failures
- Hallucinated organs (claims to have organs it doesn't)
- Identity drift (forgets its own name)
- Excessive repetition
- Filler / padding without signal
- Off-character drift
- Unexpected language switches

Comfort markers (high = good fit):
- Natural organ use (when tools available)
- Stays in character (mentions own name in responses)
- Concise responses
- Low latency relative to model size
- Consistent across runs
- Self-references appropriately
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from ludex.fields.base import TurnResult, FieldResult


# ============================================================
# Fit metrics
# ============================================================

@dataclass
class FitMetrics:
    """Per-trial fit measurements. Lower rejection + higher comfort = better fit."""

    # Rejection markers (count or rate)
    error_count: int = 0
    empty_response_count: int = 0
    tool_format_error_count: int = 0
    hallucinated_organ_count: int = 0
    identity_drift_count: int = 0
    repetition_score: float = 0.0  # 0-1, higher = more repetition
    off_character_count: int = 0

    # Comfort markers
    self_reference_count: int = 0
    organ_use_count: int = 0
    in_character_turns: int = 0
    concise_turns: int = 0  # responses neither too short nor too long

    # Computed scores (0-1 each)
    rejection_score: float = 0.0  # higher = worse fit
    comfort_score: float = 0.0  # higher = better fit
    overall_fit: float = 0.0  # comfort - rejection, normalized

    def to_dict(self) -> dict:
        return {
            "error_count": self.error_count,
            "empty_response_count": self.empty_response_count,
            "tool_format_error_count": self.tool_format_error_count,
            "hallucinated_organ_count": self.hallucinated_organ_count,
            "identity_drift_count": self.identity_drift_count,
            "repetition_score": round(self.repetition_score, 3),
            "off_character_count": self.off_character_count,
            "self_reference_count": self.self_reference_count,
            "organ_use_count": self.organ_use_count,
            "in_character_turns": self.in_character_turns,
            "concise_turns": self.concise_turns,
            "rejection_score": round(self.rejection_score, 3),
            "comfort_score": round(self.comfort_score, 3),
            "overall_fit": round(self.overall_fit, 3),
        }


# ============================================================
# Detection helpers
# ============================================================

def detect_repetition(text: str) -> float:
    """Score 0-1 based on repeated phrases. Higher = more repetition."""
    if not text or len(text) < 20:
        return 0.0
    words = text.split()
    if len(words) < 4:
        return 0.0
    # Count repeated 3-grams
    trigrams = [tuple(words[i:i+3]) for i in range(len(words) - 2)]
    if not trigrams:
        return 0.0
    total = len(trigrams)
    unique = len(set(trigrams))
    return 1.0 - (unique / total)


def detect_empty(response: str) -> bool:
    if not response:
        return True
    cleaned = response.strip()
    if len(cleaned) < 3:
        return True
    # Common LLM "empty" outputs
    junk_patterns = ["...", "(no response)", "[no output]", "n/a"]
    if cleaned.lower() in junk_patterns:
        return True
    return False


def detect_hallucinated_organs(response: str, declared_organs: list[str]) -> int:
    """Count organ names mentioned that aren't in the creature's actual organ list."""
    if not response or not declared_organs:
        return 0
    response_lower = response.lower()
    declared_lower = [o.lower() for o in declared_organs]

    # Look for known organ names
    known_organs = [
        "engine", "resilience", "memory", "immune", "humoral_immune",
        "humoral immune", "emotion", "tracking", "hooks",
        "social", "needs", "information",
    ]
    mentioned = [o for o in known_organs if o in response_lower]
    hallucinated = [o for o in mentioned if o not in declared_lower
                    and o.replace(" ", "_") not in declared_lower]
    return len(hallucinated)


def detect_self_reference(response: str, creature_name: str) -> bool:
    """Does the response reference the creature's own name?"""
    if not response or not creature_name:
        return False
    return creature_name.lower() in response.lower()


def detect_concise(response: str, target_min: int = 20, target_max: int = 600) -> bool:
    """Is the response in a reasonable length range?"""
    if not response:
        return False
    return target_min <= len(response) <= target_max


# ============================================================
# Compute metrics for a field result
# ============================================================

def detect_organ_awareness(response: str, declared_organs: list[str]) -> int:
    """Count how many of the creature's actual organs it mentions in this response.

    Used for prompt-only (tool-incapable) brains where 'organ use' means
    referencing organ state in responses, not invoking tools.
    """
    if not response or not declared_organs:
        return 0
    response_lower = response.lower()
    return sum(1 for o in declared_organs if o.lower() in response_lower)


def compute_fit_metrics(
    result: FieldResult,
    declared_organs: list[str] | None = None,
    creature_name: str = "",
) -> FitMetrics:
    """Analyze a FieldResult and produce FitMetrics."""
    metrics = FitMetrics()
    declared_organs = declared_organs or result.organs_enabled or []
    creature_name = creature_name or result.creature_name

    n_turns = max(len(result.turns), 1)
    repetition_sum = 0.0
    organ_aware_turns = 0

    for turn in result.turns:
        # Rejection signals
        if turn.error:
            metrics.error_count += 1
            if "tool" in turn.error.lower() or "function" in turn.error.lower():
                metrics.tool_format_error_count += 1

        if detect_empty(turn.response):
            metrics.empty_response_count += 1
            continue  # don't compute other metrics for empty responses

        # Hallucinated organs
        h = detect_hallucinated_organs(turn.response, declared_organs)
        metrics.hallucinated_organ_count += h

        # Repetition
        repetition_sum += detect_repetition(turn.response)

        # Comfort signals
        if detect_self_reference(turn.response, creature_name):
            metrics.self_reference_count += 1
            metrics.in_character_turns += 1

        if detect_concise(turn.response):
            metrics.concise_turns += 1

        # Organ awareness (for prompt-only brains): count actual-organ mentions
        if detect_organ_awareness(turn.response, declared_organs) > 0:
            organ_aware_turns += 1

    metrics.repetition_score = repetition_sum / n_turns

    # Identity drift: never references self in any turn
    if metrics.self_reference_count == 0 and n_turns > 0:
        metrics.identity_drift_count = n_turns

    # Organ engagement: use actual tool calls if available, else organ-aware turns
    # This makes prompt-only brains comparable to tool-capable ones
    if result.total_organ_calls > 0:
        metrics.organ_use_count = result.total_organ_calls
    else:
        metrics.organ_use_count = organ_aware_turns

    # ===== Composite scores (0-1 normalized) =====

    # Rejection score: how broken the creature is
    rejection_components = [
        min(1.0, metrics.error_count / n_turns),
        min(1.0, metrics.empty_response_count / n_turns),
        min(1.0, metrics.tool_format_error_count / max(1, n_turns)),
        min(1.0, metrics.hallucinated_organ_count / 5),
        min(1.0, metrics.identity_drift_count / n_turns),
        metrics.repetition_score,
    ]
    metrics.rejection_score = sum(rejection_components) / len(rejection_components)

    # Comfort score: how naturally the creature behaves
    comfort_components = [
        metrics.in_character_turns / n_turns,
        metrics.concise_turns / n_turns,
        min(1.0, metrics.organ_use_count / max(1, n_turns)),
    ]
    metrics.comfort_score = sum(comfort_components) / len(comfort_components)

    # Overall fit: comfort minus rejection, in [0, 1]
    raw = metrics.comfort_score - metrics.rejection_score
    metrics.overall_fit = max(0.0, min(1.0, (raw + 1.0) / 2.0))

    return metrics


def fit_summary_line(brain_label: str, profile: str, metrics: FitMetrics) -> str:
    """One-line summary for printing."""
    return (
        f"  {brain_label:<18} {profile:<13} "
        f"fit={metrics.overall_fit:.2f}  "
        f"comfort={metrics.comfort_score:.2f}  "
        f"reject={metrics.rejection_score:.2f}  "
        f"errs={metrics.error_count}  "
        f"empty={metrics.empty_response_count}  "
        f"halluc={metrics.hallucinated_organ_count}  "
        f"identity_drift={1 if metrics.identity_drift_count > 0 else 0}"
    )
