"""Field class taxonomy — narrative / structured / hybrid / unknown.

Companion to `brain_class.py` (2026-05-01). Brains and fields share
the same operational class vocabulary; the gate at field engagement
warns when the pairing is incompatible.

Surfaced from Wick × Stacker investigation: Wick (narrative gemini-
cli brain) running Stacker (structured PlanBench-style block-
stacking) produced cascading parse_failures because the brain class
mismatched what the field demanded. The static labels here let the
field runner detect the mismatch *before* the run starts and warn,
or in the future skip outright.

Classes:
- `narrative` — prose-first, no strict turn budget. Slow brains
  with rich elaboration thrive. Examples: wilderness, council,
  agora, forum, conversation, chat_*.
- `structured` — strict JSON action / tight turn clock. Function-
  call-tuned brains thrive; narrative brains drown in timeouts.
  Examples: stacker, avalon, trustgame.
- `hybrid` — works with either class. Examples: defense, academy.
- `unknown` — no rule matched; treat as no-opinion.

Compatibility matrix — `is_compatible(brain_class, field_class)`:
- narrative × narrative   ✓
- narrative × hybrid      ✓
- narrative × structured  ✗  (Wick × Stacker case — drives parse_failure)
- structured × *          ✓  (function-call brains can do narrative; overkill but fine)
- hybrid × *              ✓  (frontier brains do everything)
- unknown × *             ✓  (no opinion either way)
"""

from __future__ import annotations

NARRATIVE = "narrative"
STRUCTURED = "structured"
HYBRID = "hybrid"
UNKNOWN = "unknown"

FIELD_CLASSES = (NARRATIVE, STRUCTURED, HYBRID, UNKNOWN)


# Direct map of canonical field name → class. Slash-namespaced names
# (e.g. `academy/stacker`, `lxm/avalon`) resolve via leaf lookup.
FIELD_CLASS_REGISTRY: dict[str, str] = {
    # Narrative
    "wilderness": NARRATIVE,
    "council": NARRATIVE,
    "agora": NARRATIVE,
    "forum": NARRATIVE,
    "conversation": NARRATIVE,
    "chat_basic": NARRATIVE,
    "chat_factual": NARRATIVE,

    # Structured
    "stacker": STRUCTURED,
    "avalon": STRUCTURED,
    "trustgame": STRUCTURED,

    # Hybrid
    "defense": HYBRID,
    "academy": HYBRID,  # academy is a parent namespace; per-leaf registration takes precedence
}


def classify_field(field) -> str:
    """Resolve a field to its class. Accepts either a string name or
    a Field instance (or anything with `.field_class` and/or `.name`).

    Resolution order:
    1. Instance attribute `field_class` (declarative override on
       Field subclass — wins over the registry)
    2. Direct registry lookup by `name`
    3. Leaf-of-namespace registry lookup (`academy/stacker` → stacker)
    4. UNKNOWN

    The instance-attribute path lets new fields ship with their class
    declared inline, so the central registry stays a fallback rather
    than a required edit per new field."""
    if field is None:
        return UNKNOWN

    # If the field carries a declarative override, trust it.
    explicit = getattr(field, "field_class", "") or ""
    if explicit:
        return str(explicit)

    # Resolve to a name string for registry lookup.
    name = field if isinstance(field, str) else getattr(field, "name", "")
    if not name:
        return UNKNOWN
    name = str(name).lower()

    if name in FIELD_CLASS_REGISTRY:
        return FIELD_CLASS_REGISTRY[name]
    if "/" in name:
        leaf = name.rsplit("/", 1)[-1]
        if leaf in FIELD_CLASS_REGISTRY:
            return FIELD_CLASS_REGISTRY[leaf]
    return UNKNOWN


def is_compatible(brain_class: str, field_class: str) -> bool:
    """True if a brain of `brain_class` can run a field of `field_class`
    without operational mismatch (timeout cascade, parse failures, etc).

    Coarse rule: only the (narrative brain × structured field) pair is
    incompatible. Everything else is at-worst overkill, never broken.
    Unknowns abstain (return True — no opinion)."""
    if brain_class == NARRATIVE and field_class == STRUCTURED:
        return False
    return True


def mismatch_reason(brain_class: str, field_class: str) -> str:
    """Human-readable explanation of why a pair is incompatible.
    Returns empty string when compatible."""
    if is_compatible(brain_class, field_class):
        return ""
    if brain_class == NARRATIVE and field_class == STRUCTURED:
        return (
            f"narrative-class brain on structured-class field — high "
            f"latency + prose-first emission cascade into timeouts and "
            f"parse_failure. Reassign to a structured/hybrid brain or "
            f"a narrative/hybrid field."
        )
    return f"brain={brain_class} field={field_class} mismatch"
