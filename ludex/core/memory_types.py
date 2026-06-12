"""Memory type classification + differential decay + token budgets.

D-024 Sprint 1 substrate. Extends the original 3-type vocabulary
(episodic / semantic / prospective) to an 8-type vocabulary with
per-type decay multipliers, informed by memkraft 0.2.0's Goal-
Weighted Reconstructive Memory concept — but re-expressed for
creature ethology, not human knowledge management.

The decay multipliers are the mechanical layer under D-044's
narrative identity principle: identity memories decay 10× slower
than routine memories, so a creature's sense-of-self persists
through consolidation naturally.

Zero runtime dependencies — stdlib only, matches the existing
memory block's policy.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ludex.core.prompt_tier import Tier


# ============================================================
# The importance ladder — the store's constitution
# ============================================================
# (Whitepaper §2, centralized 2026-06-12 per audit F5.) One scale,
# many readers. Above the SIGNIFICANCE_LINE is what the creature *is
# about*; below DEFAULT is what merely passed through. Writers pick a
# rung from this ladder, never an arbitrary float; processes that gate
# on importance read these names, never literals. Moving a rung is a
# system-wide decision — every consumer moves with it.

IMPORTANCE_CEILING = 0.95           # recall bumps stop here
IMPORTANCE_REFLECTION = 0.8         # lived-experience reflections (selfhood)
IMPORTANCE_SIGNIFICANCE_LINE = 0.7  # recency channel rides; beliefs promote here
IMPORTANCE_DEFAULT = 0.5            # unremarkable episodic
IMPORTANCE_SESSION_CHAT = 0.45      # one-per-session conversation memory
IMPORTANCE_CONVERSATIONAL = 0.35    # low-stakes conversational material
IMPORTANCE_ARCHIVE_FLOOR = 0.3      # below this + mature → dream-cycle archives
IMPORTANCE_RECALL_BUMP = 0.05       # usage adaptation per surfaced recall


# ============================================================
# The 8-type vocabulary (+ 2 legacy aliases for compatibility)
# ============================================================

# Canonical types (memkraft 0.2.0 concept re-expressed for creatures)
TYPE_IDENTITY      = "identity"       # core self-facts; cold tier; never archived
TYPE_BELIEF        = "belief"         # held convictions (e.g. Council concessions)
TYPE_PREFERENCE    = "preference"     # stable leanings
TYPE_RELATIONSHIP  = "relationship"   # bond-linked memories
TYPE_SKILL         = "skill"          # acquired capabilities
TYPE_EPISODIC      = "episodic"       # lived events
TYPE_ROUTINE       = "routine"        # baseline patterns (field ticks)
TYPE_TRANSIENT     = "transient"      # short-lived context

# Legacy types (Sprint 0 vocabulary). Accepted; mapped for decay.
TYPE_SEMANTIC      = "semantic"       # legacy — treated like belief
TYPE_PROSPECTIVE   = "prospective"    # legacy — treated like episodic

MEMORY_TYPES: frozenset[str] = frozenset({
    TYPE_IDENTITY, TYPE_BELIEF, TYPE_PREFERENCE, TYPE_RELATIONSHIP,
    TYPE_SKILL, TYPE_EPISODIC, TYPE_ROUTINE, TYPE_TRANSIENT,
    TYPE_SEMANTIC, TYPE_PROSPECTIVE,
})


# ============================================================
# Tier buckets — which types live where
# ============================================================

# COLD: identity only. Never capped, never archived by consolidation.
COLD_TYPES: frozenset[str] = frozenset({TYPE_IDENTITY})

# WARM: consolidated / trait-like. Capacity-budgeted. Phase 2
# candidate pool.
WARM_TYPES: frozenset[str] = frozenset({
    TYPE_BELIEF, TYPE_PREFERENCE, TYPE_RELATIONSHIP, TYPE_SKILL,
    TYPE_SEMANTIC,   # legacy — lives in warm
})

# HOT: events / context. Capacity-budgeted. Phase 1 candidate pool.
HOT_TYPES: frozenset[str] = frozenset({
    TYPE_EPISODIC, TYPE_ROUTINE, TYPE_TRANSIENT,
    TYPE_PROSPECTIVE,  # legacy — lives in hot
})


def tier_for_type(memory_type: str) -> str:
    """Return 'cold' / 'warm' / 'hot' for a memory type. Unknown
    types default to 'hot' (conservative — gets capped sooner)."""
    if memory_type in COLD_TYPES:
        return "cold"
    if memory_type in WARM_TYPES:
        return "warm"
    return "hot"


# ============================================================
# Differential decay multipliers
# ============================================================

# Effective age = raw_age / decay_multiplier[type].
# Higher multiplier = ages slower = stays longer before archive.
# Reference: memkraft 0.2.0 decay table, re-checked for creature use.
DECAY_MULTIPLIERS: dict[str, float] = {
    TYPE_IDENTITY:     10.0,   # 10× slower — core self
    TYPE_BELIEF:        5.0,
    TYPE_RELATIONSHIP:  4.0,
    TYPE_PREFERENCE:    3.0,
    TYPE_SKILL:         3.0,
    TYPE_EPISODIC:      1.5,
    TYPE_ROUTINE:       1.0,   # baseline
    TYPE_TRANSIENT:     0.5,   # 2× faster — ages quickly

    # Legacy aliases: pick a conservative middle so existing memories
    # don't suddenly vanish under the new cycle.
    TYPE_SEMANTIC:      2.0,
    TYPE_PROSPECTIVE:   1.5,
}


def decay_multiplier(memory_type: str) -> float:
    """Return decay multiplier for a memory type. Unknown → 1.0."""
    return DECAY_MULTIPLIERS.get(memory_type, 1.0)


def effective_age(raw_age_seconds: float, memory_type: str) -> float:
    """Raw age divided by type's decay multiplier. Identity memories
    are effectively 10× younger than a routine memory of the same
    literal age — so they survive consolidation longer.
    """
    mult = decay_multiplier(memory_type)
    if mult <= 0:
        return raw_age_seconds
    return raw_age_seconds / mult


# ============================================================
# Token counting — stdlib heuristic, not tiktoken
# ============================================================

def estimate_tokens(content: str) -> int:
    """Coarse token estimate: ~4 chars per token + 20% JSON overhead.

    Deliberately not tiktoken — the goal is ordinal comparison for
    capacity decisions, not exact context-window planning. A D-024
    refinement can swap in tiktoken if this proves insufficient.
    """
    if not content:
        return 1
    # Basic chars-per-token heuristic
    raw = max(1, len(content) // 4)
    # 20% overhead for JSON framing / metadata bundling at recall time
    return int(raw * 1.2) or 1


# ============================================================
# Per-brain capacity budgets
# ============================================================

# Token budgets per tier. Source: D-024 initial hypotheses,
# expanded to match the 5-tier Tier enum in prompt_tier.py.
# These are STARTING ESTIMATES, not constants — see D-024 for
# the research loop that refines them empirically.
_CAPACITY_TABLE: dict[str, dict[str, int]] = {
    "large":      {"hot_tokens": 16000, "warm_tokens": 8000},
    "mid":        {"hot_tokens": 12000, "warm_tokens": 6000},
    "large_slm":  {"hot_tokens":  8000, "warm_tokens": 4000},
    "mid_slm":    {"hot_tokens":  5000, "warm_tokens": 3000},
    "small_slm":  {"hot_tokens":  3000, "warm_tokens": 2000},
}

# Fallback if tier lookup fails
_DEFAULT_CAPACITY = {"hot_tokens": 8000, "warm_tokens": 4000}


def capacity_for(brain: dict | None) -> dict[str, int]:
    """Return `{"hot_tokens": N, "warm_tokens": M}` budget for a
    creature's brain. Resolves tier via prompt_tier.tier_of, then
    looks up the capacity table. Unknown brain → conservative MID
    equivalent.
    """
    # Lazy import to avoid cycles
    from ludex.core.prompt_tier import tier_of
    tier = tier_of(brain or {})
    return dict(_CAPACITY_TABLE.get(tier.value, _DEFAULT_CAPACITY))


# ============================================================
# Progressive disclosure — D-024 Sprint 2
# ============================================================

# Three levels of detail when surfacing recalled memories into a
# prompt. The level is chosen by caller based on brain tier — SLMs
# get L1 to preserve context window; LARGEs can handle L3.

DISCLOSURE_L1 = "l1"  # ~50 tokens: count + tag clusters
DISCLOSURE_L2 = "l2"  # ~200 tokens: tag headlines + one-line-per-memory
DISCLOSURE_L3 = "l3"  # full content, truncated per item


def disclosure_level_for(brain: dict | None) -> str:
    """Pick a disclosure level appropriate to the brain's tier.

    SMALL_SLM → L1 (tightest); MID_SLM / LARGE_SLM → L2;
    MID / LARGE → L3. This is the default mapping; callers can
    override if a specific context needs more/less density.
    """
    from ludex.core.prompt_tier import tier_of, Tier
    tier = tier_of(brain or {})
    if tier == Tier.SMALL_SLM:
        return DISCLOSURE_L1
    if tier in (Tier.MID_SLM, Tier.LARGE_SLM):
        return DISCLOSURE_L2
    return DISCLOSURE_L3


def disclose_memories(memories: list, level: str = DISCLOSURE_L3) -> str:
    """Format a list of Memory (or RecallResult) objects into a
    disclosure-appropriate string. Accepts either type — extracts
    `.memory` if `.relevance` present (duck-typed RecallResult).

    L1 — aggregate: count + distinct tag clusters
      "(12 memories across tags: reflection, bond/Flare, wilderness)"

    L2 — headlines: one line per tag cluster, up to 5 clusters
      "reflection: Self-reflection (wilderness_complete): I retreat..."
      "bond/Flare: First met in meet-and-greet; frequency metaphor..."

    L3 — per-memory: content truncated to 150 chars each

    Token overhead is bounded by level. Caller is responsible for
    picking the right level for their tier.
    """
    # Normalize input — extract Memory from RecallResult if needed
    items = []
    for m in memories:
        if hasattr(m, "memory") and hasattr(m, "relevance"):
            items.append(m.memory)
        else:
            items.append(m)

    if not items:
        return "(no memories)"

    if level == DISCLOSURE_L1:
        tags = set()
        for m in items:
            tags.update(getattr(m, "tags", []))
        tag_list = ", ".join(sorted(tags)[:6]) if tags else "(untagged)"
        return f"({len(items)} memories across tags: {tag_list})"

    if level == DISCLOSURE_L2:
        # Group by first tag; produce one headline per group
        groups: dict[str, list] = {}
        for m in items:
            first_tag = (getattr(m, "tags", None) or ["(untagged)"])[0]
            groups.setdefault(first_tag, []).append(m)
        lines = []
        for tag, mems in list(groups.items())[:5]:
            head = mems[0].content.replace("\n", " ")[:100]
            suffix = f" (+{len(mems) - 1} more)" if len(mems) > 1 else ""
            lines.append(f"- {tag}: {head}{suffix}")
        return "\n".join(lines)

    # L3: full content, truncated per item
    return "\n".join(
        f"- {getattr(m, 'content', '').replace(chr(10), ' ')[:150]}"
        for m in items[:15]
    )
