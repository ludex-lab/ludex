"""D-076 — unified compatibility rejection.

Phase B exit-criteria Test 3 (`docs/phase-b-exit-criteria.md`)
requires that the framework catch documented incompatibility
patterns at config-load / field-entry time and emit a structured
error pointing to the right design-decisions-log entry.

Pre-D-076, two compatibility gates existed independently and only
in warn-mode:
- D-073 brain × field-class compatibility — `FieldRunner.run`
  logs WARNING + tags `result.notes` when narrative-class brain
  meets structured-class field. Caretakers can still force the
  run.
- D-072 brain capability probing — birth-time capability snapshot
  is recorded but no FieldRunner-level gate consumes it (the
  LxM side has its own gate).

D-076 unifies these into a `RejectionError` class with structured
fields (pattern_id, layer, reason, suggestion, decisions_log_ref)
that callers can either log+continue (warn-mode, default) or
raise hard (strict-mode). Adds a new pattern: required-capability
gate that consumes the D-072 probe data.

This module intentionally has no dependencies on field code — it
is a leaf utility that the field layer (and future organ-
assembly checkers) import from.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RejectionError(Exception):
    """Structured compatibility rejection.

    Use the helper constructors in this module rather than raising
    raw `RejectionError(...)` so the pattern_id and decisions_log_ref
    stay in sync with the design-decisions-log.

    `layer` is one of "config-load" / "field-runner" / "brain-call",
    documenting where the gate fires. New gates pick the most
    upstream layer at which the check is decidable.
    """
    pattern_id: str       # e.g. "D-072", "D-073", "D-074"
    layer: str            # config-load | field-runner | brain-call
    reason: str           # short human-readable diagnosis
    suggestion: str       # one-line action to resolve
    decisions_log_ref: str  # path / anchor for full context

    def __str__(self) -> str:  # type: ignore[override]
        lines = [
            f"[{self.pattern_id}] {self.reason}",
            f"  → suggestion: {self.suggestion}",
            f"  see {self.decisions_log_ref}",
        ]
        return "\n".join(lines)


# Helper constructors — keep pattern_id ↔ decisions_log_ref bound.

def brain_field_class_mismatch(
    brain_class: str,
    field_class: str,
    reason_extra: str = "",
) -> RejectionError:
    """D-073 — brain × field-class mismatch (e.g. narrative brain
    on structured field). Pre-D-076 this was warn-only. Strict
    mode raises this instance.
    """
    reason = (
        f"brain_class={brain_class!r} is not compatible with "
        f"field_class={field_class!r}"
    )
    if reason_extra:
        reason += f" — {reason_extra}"
    return RejectionError(
        pattern_id="D-073",
        layer="field-runner",
        reason=reason,
        suggestion=(
            "Pick a brain whose class is compatible with the "
            "field, or run in warn-mode for limit-testing"
        ),
        decisions_log_ref="docs/design-decisions-log.md § D-073",
    )


def required_capability_missing(
    capability: str,
    declared_capabilities: list[str],
    field_name: str,
) -> RejectionError:
    """D-072-derived — field declares `requires_capabilities` and
    the organism's probed `brain_capabilities` does not include
    one of them. New pattern in D-076.
    """
    return RejectionError(
        pattern_id="D-072",
        layer="field-runner",
        reason=(
            f"field {field_name!r} requires capability "
            f"{capability!r}, but brain declared "
            f"{declared_capabilities!r}"
        ),
        suggestion=(
            f"Either swap the brain for one with {capability!r}, "
            "remove the requirement from the field, or re-probe "
            "if the brain's capability snapshot is stale"
        ),
        decisions_log_ref=(
            "docs/design-decisions-log.md § D-072 (capability probing) + "
            "§ D-076 (rejection unification)"
        ),
    )


def field_required_organ_missing(
    organ_name: str,
    organism_name: str,
    field_name: str,
) -> RejectionError:
    """D-076 new pattern — field declares `requires_organs` and
    the organism does not have the named organ enabled.

    Currently fields rarely declare hard organ requirements
    (Council assumes engine + memory; Wilderness assumes engine +
    emotion + immune). This gate gives them a place to enforce
    those assumptions explicitly rather than crashing mid-run.
    """
    return RejectionError(
        pattern_id="D-076",
        layer="config-load",
        reason=(
            f"field {field_name!r} requires organ "
            f"{organ_name!r}, but organism {organism_name!r} "
            "does not have it enabled"
        ),
        suggestion=(
            f"Enable {organ_name!r} in the organism's ludex.yaml, "
            "or run a different field that does not require it"
        ),
        decisions_log_ref="docs/design-decisions-log.md § D-076",
    )
