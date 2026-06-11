"""
Memory Consolidation — the creature's "dream" cycle.

Creature examines its memories, decides what matters, compresses the rest.
Unlike the rule-based MemoryBlock.handle_consolidate(), this uses the
creature's own brain to judge importance — making consolidation a
cognitive act, not a mechanical one.

NOTE: this module hosts TWO distinct consolidation layers that share the
"consolidation" theme but operate at different scopes:
  1. Memory dream-cycle (this section) — compresses the *recall surface*.
  2. Periodic consolidation reflection (D-085, the section at the bottom of
     this file) — synthesizes a *window of life* into a narrative
     retrospective at `creatures/<name>/reflections/YYYY-MM.md`.

Per-brain optimal memory targets (from prompt_templates brain profiles):
- Large brains (Claude, GPT-5): ~60 active memories
- Medium brains (gemma4, mistral): ~30 active memories
- Small brains (qwen2.5, llama3.2): ~15 active memories

These are starting estimates. Field Studies will refine them by measuring
response quality vs memory count per brain.

Usage:
    from ludex.core.consolidation import dream_cycle, measure_memory_health

    # Run dream cycle
    report = dream_cycle(organism)

    # Check if consolidation is needed
    health = measure_memory_health(organism)
    if health["needs_consolidation"]:
        dream_cycle(organism)
"""

from __future__ import annotations

import argparse
import re
import time
import json
import logging
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


def dedup_memories(memory_block) -> int:
    """Remove duplicate memories based on content similarity.

    D-026: Inspired by memkraft's slug normalization dedup.
    Normalizes content (lowercase, strip punctuation, collapse whitespace),
    then merges near-duplicates within the same tag group.

    Returns number of duplicates archived.
    """
    import re
    from difflib import SequenceMatcher

    if not memory_block:
        return 0

    active = {mid: mem for mid, mem in memory_block._memories.items()
              if mem.status == "active"}
    if len(active) < 2:
        return 0

    def normalize(text: str) -> str:
        text = text.lower()
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    # Group by first tag for efficiency
    groups: dict[str, list] = {}
    for mid, mem in active.items():
        key = mem.tags[0] if mem.tags else "_untagged"
        groups.setdefault(key, []).append((mid, mem))

    archived = 0
    for tag, mems in groups.items():
        if len(mems) < 2:
            continue
        seen = []
        for mid, mem in mems:
            norm = normalize(mem.content)
            is_dup = False
            for seen_mid, seen_norm in seen:
                ratio = SequenceMatcher(None, norm, seen_norm).ratio()
                if ratio > 0.75:  # 75% similar = duplicate
                    # Keep the longer one, archive the shorter
                    if len(mem.content) <= len(memory_block._memories[seen_mid].content):
                        mem.status = "archived"
                        mem.updated_at = time.time()
                    else:
                        memory_block._memories[seen_mid].status = "archived"
                        memory_block._memories[seen_mid].updated_at = time.time()
                    archived += 1
                    is_dup = True
                    break
            if not is_dup:
                seen.append((mid, norm))

    if archived > 0:
        memory_block._save()
        logger.info(f"Dedup: archived {archived} duplicate memories")

    return archived


# Per-brain memory targets — how many active memories each brain handles well
# Starting estimates; Field Studies will refine these
BRAIN_MEMORY_TARGETS = {
    # Large brains — handle rich context
    "claude_cli": 60,
    "claude_sdk": 60,
    "codex_cli": 50,
    # Medium brains
    "gemini_cli": 35,
    "agy_cli": 35,  # provisional; mirror gemini_cli flash family
    "ollama": 25,  # default for ollama
}

OLLAMA_MODEL_TARGETS = {
    "llama3.1:8b": 30,
    "qwen3:8b": 30,
    "mistral:7b": 30,
    "gemma4:e4b": 25,
    "llama3.2:3b": 15,
    "qwen2.5:1.5b": 10,
    "phi4-mini": 15,
}


# D-071 pillar 4 — disk-budget targets (entries on disk, distinct from
# BRAIN_MEMORY_TARGETS which is the active-recall surface). Frontier
# brains carry larger archives because they retrieve better; SLMs need
# pruned. Numbers are starting estimates per the D-071 design (4515-4522);
# Phase A pilot Primo (~115 entries/day) implies forgetting fires for
# haiku at ~85d under mid-tier 5k cap (sane). Tune as cohort patterns
# reveal themselves.
BRAIN_DISK_TARGETS = {
    "frontier": 10000,  # gpt-5.5, opus, sonnet 4.6
    "mid":       5000,  # haiku, flash, sonnet 4.5
    "slm":       2000,  # qwen, exaone, gemma
    "default":   5000,
}


def get_disk_target(provider: str, model: str = "") -> int:
    """Map (provider, model) → BRAIN_DISK_TARGETS bucket. Returns the
    cap on entries before the forgetting pass should fire (D-071
    pillar 4 trigger). Mirrors get_memory_target's lookup shape but
    routes by frontier/mid/slm tier rather than per-provider hand-set."""
    p = (provider or "").lower()
    m = (model or "").lower()

    # frontier — largest models per family
    if "opus-4" in m or "sonnet-4-6" in m or "gpt-5.5" in m:
        return BRAIN_DISK_TARGETS["frontier"]

    # mid — workhorse models
    if "haiku" in m or "flash" in m or "sonnet-4-5" in m:
        return BRAIN_DISK_TARGETS["mid"]

    # SLM — small local models via ollama
    if p == "ollama":
        # Per-model overrides could land here later; for now any ollama
        # routes to slm tier.
        return BRAIN_DISK_TARGETS["slm"]

    return BRAIN_DISK_TARGETS["default"]


def compute_forget_score(*, importance: float, age_days: float, recall_count: int) -> float:
    """D-071 pillar 4 composite forget score, per design L4527-4530:
    `(1 - importance) × age_days × 1/(1 + recall_count)`. Higher = more
    forgettable. Pure function over scalar inputs — pillar 4b will
    sort by this and forget from the top until under disk budget.

    Bounded inputs assumed:
    - importance in [0.0, 1.0] (0 = trivial, 1 = vital)
    - age_days >= 0
    - recall_count >= 0

    Returns a non-negative scalar."""
    importance = max(0.0, min(1.0, importance))
    age_days = max(0.0, age_days)
    recall_count = max(0, recall_count)
    return (1.0 - importance) * age_days * (1.0 / (1.0 + recall_count))


@dataclass
class DreamReport:
    """Result of a dream cycle."""
    before_count: int = 0
    after_count: int = 0
    compressed: int = 0          # episodic memories compressed into narratives
    archived: int = 0            # low-importance memories archived
    narratives_created: int = 0  # new consolidated narrative files
    target: int = 0              # target memory count for this brain
    duration_seconds: float = 0.0
    brain_provider: str = ""
    brain_model: str = ""


def get_disclosure_level(provider: str, model: str = "") -> int:
    """Get appropriate progressive disclosure level for this brain.

    D-026: inspired by memkraft's Level 1/2/3 progressive disclosure.
    - Level 1 (~50 tokens): index only — memory count + 1-line per topic. For SLMs.
    - Level 2 (~200 tokens): summaries — key facts per memory. For medium brains.
    - Level 3 (full): complete memory content. For large brains.
    """
    if provider in ("claude_cli", "claude_sdk", "codex_cli"):
        return 3  # large brains get full content
    if provider in ("gemini_cli", "agy_cli"):
        return 2  # medium
    # Ollama — depends on model size
    if provider == "ollama" and model:
        small_models = {"qwen2.5:1.5b", "llama3.2:3b", "phi4-mini"}
        if model in small_models:
            return 1
    return 2  # default medium


def render_memories_for_prompt(memory_block, provider: str, model: str = "", max_tokens: int = 0) -> str:
    """Render memories at the appropriate disclosure level for the brain.

    D-026: Progressive disclosure — don't dump everything into the prompt.
    Match memory depth to brain capability.
    """
    if not memory_block:
        return ""

    level = get_disclosure_level(provider, model)
    stats = memory_block.stats()
    total = stats.get("total", 0)

    if total == 0:
        return ""

    if level == 1:
        # Index only: count + topic summary
        lines = [f"[Memory: {total} memories]"]
        # Count by tag
        tag_counts: dict[str, int] = {}
        for mem in memory_block._memories.values():
            if mem.status != "active":
                continue
            for tag in mem.tags[:1]:  # first tag only
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {tag}: {count} memories")
        return "\n".join(lines)

    elif level == 2:
        # Summaries: first 80 chars of each active memory
        lines = [f"[Memory: {total} memories]"]
        active = [m for m in memory_block._memories.values() if m.status == "active"]
        active.sort(key=lambda m: m.updated_at, reverse=True)
        for mem in active[:15]:  # top 15 by recency
            source_tag = f" [{mem.source}]" if mem.source else ""
            lines.append(f"  - {mem.content[:80]}{source_tag}")
        if len(active) > 15:
            lines.append(f"  ... and {len(active) - 15} more")
        return "\n".join(lines)

    else:
        # Full content (level 3) — for large brains
        lines = [f"[Memory: {total} memories]"]
        active = [m for m in memory_block._memories.values() if m.status == "active"]
        active.sort(key=lambda m: m.updated_at, reverse=True)
        for mem in active[:30]:
            source_tag = f" [Source: {mem.source}]" if mem.source else ""
            lines.append(f"  [{mem.memory_type}] {mem.content}{source_tag}")
        if len(active) > 30:
            lines.append(f"  ... and {len(active) - 30} more")
        return "\n".join(lines)


def get_memory_target(provider: str, model: str = "") -> int:
    """Get optimal active memory count for this brain."""
    if provider == "ollama" and model:
        return OLLAMA_MODEL_TARGETS.get(model, BRAIN_MEMORY_TARGETS.get("ollama", 25))
    return BRAIN_MEMORY_TARGETS.get(provider, 30)


def measure_memory_health(organism) -> dict:
    """Measure memory health and determine if consolidation is needed.

    Returns dict with memory stats, target, and recommendation.
    """
    config = getattr(organism, "config", None)
    provider = config.get("provider", "ollama") if config else "ollama"
    model = config.get("model", "") if config else ""
    target = get_memory_target(provider, model)

    memory = organism.get_block("memory")
    if not memory:
        return {"has_memory": False, "needs_consolidation": False}

    stats = memory.stats()
    total = stats.get("total", 0)
    episodic = stats.get("episodic", 0)
    semantic = stats.get("semantic", 0)

    ratio = total / target if target > 0 else 0
    needs = ratio > 1.3  # consolidate when 30% over target

    return {
        "has_memory": True,
        "total": total,
        "episodic": episodic,
        "semantic": semantic,
        "target": target,
        "ratio": round(ratio, 2),
        "needs_consolidation": needs,
        "brain": f"{provider}:{model}",
        "recommendation": (
            f"Memory at {ratio:.0%} of target ({total}/{target}). "
            + ("Dream cycle recommended." if needs else "Healthy.")
        ),
    }


def dream_cycle(organism, engine=None) -> DreamReport:
    """Run a dream cycle — creature consolidates its own memories.

    The creature's brain examines recent episodic memories and:
    1. Identifies the most important experiences
    2. Creates compressed narratives (saved to consolidated/)
    3. Archives routine/redundant episodic memories
    4. Keeps semantic memories and high-importance episodics

    This is a cognitive act, not mechanical — the brain decides what matters.
    """
    start_time = time.time()

    config = getattr(organism, "config", None)
    provider = config.get("provider", "ollama") if config else "ollama"
    model = config.get("model", "") if config else ""
    habitat_dir = config.get("habitat_dir", "") if config else ""
    target = get_memory_target(provider, model)

    if engine is None:
        engine = organism.get_block("engine")
    memory = organism.get_block("memory")

    if not memory or not engine:
        return DreamReport()

    # D-026: Dedup before compression — remove redundant memories first
    deduped = dedup_memories(memory)
    if deduped:
        logger.info(f"Dream: deduped {deduped} memories before compression")

    stats = memory.stats()
    before_count = stats.get("total", 0)

    report = DreamReport(
        before_count=before_count,
        target=target,
        brain_provider=provider,
        brain_model=model,
    )

    if before_count <= target:
        report.after_count = before_count
        report.duration_seconds = time.time() - start_time
        return report

    # Gather all active episodic memories
    all_memories = memory.handle_list_memories(memory_type="episodic", status="active")
    if not all_memories:
        report.after_count = before_count
        report.duration_seconds = time.time() - start_time
        return report

    # Format memories for the brain to review
    memory_text = ""
    for i, mem in enumerate(all_memories):
        content = mem.get("content", "") if isinstance(mem, dict) else getattr(mem, "content", str(mem))
        tags = mem.get("tags", []) if isinstance(mem, dict) else getattr(mem, "tags", [])
        importance = mem.get("importance", 0.5) if isinstance(mem, dict) else getattr(mem, "importance", 0.5)
        memory_text += f"  [{i+1}] (imp:{importance:.1f}, tags:{tags}) {content[:150]}\n"

    # Ask the brain to consolidate
    prompt = (
        f"[Dream Cycle — Memory Consolidation]\n\n"
        f"You have {len(all_memories)} episodic memories. "
        f"Your optimal memory capacity is ~{target}. You need to compress.\n\n"
        f"Your episodic memories:\n{memory_text}\n"
        f"Tasks:\n"
        f"1. Write a NARRATIVE SUMMARY (3-5 sentences) that captures the most "
        f"important experiences, relationships, and lessons from these memories.\n"
        f"2. List the numbers of the 5-10 MOST IMPORTANT memories to KEEP as-is.\n\n"
        f"Format:\n"
        f"NARRATIVE: [your summary]\n"
        f"KEEP: [comma-separated numbers, e.g., 1, 5, 12, 23]\n\n"
        f"Be honest about what matters. Routine events can be forgotten. "
        f"Turning points, relationships, and lessons should be preserved."
    )

    try:
        result = engine.handle_submit(prompt)
        response = result.response or ""
    except Exception as e:
        logger.error(f"Dream cycle failed: {e}")
        report.duration_seconds = time.time() - start_time
        return report

    # Parse response
    narrative = ""
    keep_indices = set()

    for line in response.split("\n"):
        line = line.strip()
        if line.upper().startswith("NARRATIVE:"):
            narrative = line[len("NARRATIVE:"):].strip()
        elif line.upper().startswith("KEEP:"):
            import re
            numbers = re.findall(r'\d+', line)
            keep_indices = {int(n) for n in numbers}

    # If brain didn't follow format, try to extract narrative from full response
    if not narrative and len(response) > 50:
        narrative = response[:500]

    # Default: keep at least some memories
    if not keep_indices:
        # Keep last 5 + highest importance
        sorted_by_imp = sorted(
            enumerate(all_memories, 1),
            key=lambda x: x[1].get("importance", 0.5) if isinstance(x[1], dict) else getattr(x[1], "importance", 0.5),
            reverse=True,
        )
        keep_indices = {i for i, _ in sorted_by_imp[:min(10, len(all_memories))]}

    # Save narrative to consolidated/
    if narrative and habitat_dir:
        consolidated_dir = Path(habitat_dir) / "memory" / "consolidated"
        consolidated_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        narrative_path = consolidated_dir / f"dream_{timestamp}.md"
        narrative_content = (
            f"# Dream Cycle — {timestamp}\n"
            f"Memories before: {len(all_memories)}\n"
            f"Target: {target}\n\n"
            f"## Narrative\n{narrative}\n"
        )
        try:
            narrative_path.write_text(narrative_content, encoding="utf-8")
            report.narratives_created = 1
        except Exception:
            pass

    # Store narrative as semantic memory
    if narrative:
        try:
            memory.handle_remember(
                f"[Dream consolidation] {narrative[:400]}",
                memory_type="semantic",
                tags=["dream", "consolidation"],
                importance=0.8,
                source=f"dream_cycle/{int(time.time())}",
            )
        except Exception:
            pass

    # Archive memories not in keep list
    archived = 0
    for i, mem in enumerate(all_memories, 1):
        if i not in keep_indices:
            mem_id = mem.get("id", "") if isinstance(mem, dict) else getattr(mem, "id", "")
            if mem_id and mem_id in memory._memories:
                memory._memories[mem_id].status = "archived"
                memory._memories[mem_id].updated_at = time.time()
                archived += 1

    memory._save()

    # Final count
    final_stats = memory.stats()
    report.after_count = final_stats.get("total", 0)
    report.compressed = len(all_memories) - len(keep_indices)
    report.archived = archived
    report.duration_seconds = time.time() - start_time

    logger.info(
        f"Dream cycle: {before_count} → {report.after_count} memories "
        f"({archived} archived, {report.narratives_created} narratives), "
        f"{report.duration_seconds:.1f}s"
    )

    return report


# ============================================================
# ============================================================
# D-085 — Periodic consolidation reflection
#         (the synthesis layer between raw record and identity)
# ============================================================
# ============================================================
#
# Where the dream-cycle above compresses the *recall surface*, this
# synthesizes a *window of life* into a narrative retrospective stored
# separately at `creatures/<name>/reflections/YYYY-MM.md`. It CONSUMES
# spans / journal / snapshots / bonds; it never overwrites them (boundary
# is non-negotiable — the raw in-the-moment record stays independently
# readable). Design settled by agy's substrate-internal pass (2026-05-29).
#
# This is the minimal spike: the dual-threshold trigger + the two-stage
# pipeline, runnable on one creature via the `/consolidate` override CLI:
#
#     python -m ludex.core.consolidation --creature Aria
#     python -m ludex.core.consolidation --creature Aria --dry-run
#     python -m ludex.core.consolidation --creature Aria --force
#
# Two stages (D-085 param 2):
#   Stage 1 — mechanical distillation. PURE CODE over spans/snapshots/bonds.
#             No LLM, so for clean/paper creatures there is no distiller-choice
#             confound; the distiller identity is logged as "mechanical". This
#             is also the D-084 artifact-based measurement, not self-report.
#   Stage 2 — narrative synthesis. The creature's *native brain* reads the dry
#             digest + only the critical text (SELF.md, recent reflections) and
#             authors the retrospective in its own voice.
#
# Trigger (D-085 param 1) — dual-threshold, calendar-anchored. Heartbeat-count
# is explicitly rejected (heartbeats fire even when dormant):
#   hard floor: ≥ HARD_FLOOR_DAYS since last consolidation (preserves "distance")
#   then fire:  (≥ ACCUMULATION_EVENTS new life-events)
#          OR   (≥ HIATUS_TIMEOUT_DAYS elapsed AND ≥1 new event)  ← dormant fix
#
# Deferred to follow-on builds (per the D-085 "next step" ordering):
#   - heartbeat auto-trigger wiring (this spike is manual-only; a consolidation
#     is a heavyweight brain call and must rotate across creatures, never fire
#     all at once — caretaker-cadence discipline);
#   - the dual-thread recall invariant in the memory organ (param 4);
#   - the [PROVISIONAL]→[SETTLED] SELF.md promotion gate (param 3) — the
#     retrospective surfaces `Identity Shifts` candidates, but writing them into
#     SELF.md is the sensitive next step.

# Trigger thresholds (days / event count).
HARD_FLOOR_DAYS = 14
ACCUMULATION_EVENTS = 30
HIATUS_TIMEOUT_DAYS = 45

# Span kinds that are NOT life-events: per-call mechanics (one brain call emits
# many) and the time-driven pulse. Excluded from the accumulation count so
# cadence tracks genuine activity, not the clock — the concrete form of
# "reject heartbeat-count" (D-085 param 1).
_NON_EVENT_KINDS = {"brain_resolved", "translation_applied", "heartbeat_pulse"}

REFLECTIONS_DIRNAME = "reflections"
_DISTILLER_ID = "mechanical/deterministic (no LLM)"

_SECTIONS = (
    "What Happened",
    "Patterns I Notice",
    "What Held / Shifted",
    "What I Learned About Others",
    "Identity Shifts",
    "Still Open",
)


# ---------- Trigger — dual-threshold, calendar-anchored ----------

@dataclass
class ConsolidationDecision:
    fire: bool
    reason: str
    days_elapsed: float
    new_events: int
    window_start: float
    window_end: float
    last_consolidated_on: Optional[float]


def _conso_store(creature_dir: Path):
    from ludex.core.store import LudexStore
    return LudexStore(str(creature_dir))


def last_consolidated_on(creature_dir: Path) -> Optional[float]:
    """Epoch of the most recent consolidation, read from the
    `consolidated_on_ts` frontmatter line of existing reflections. None when
    the creature has never consolidated."""
    rdir = creature_dir / REFLECTIONS_DIRNAME
    if not rdir.is_dir():
        return None
    latest: Optional[float] = None
    for f in rdir.glob("*.md"):
        try:
            head = f.read_text(encoding="utf-8")[:600]
        except Exception:
            continue
        for line in head.splitlines():
            line = line.strip()
            if line.startswith("consolidated_on_ts:"):
                try:
                    ts = float(line.split(":", 1)[1].strip())
                    latest = ts if latest is None else max(latest, ts)
                except ValueError:
                    pass
                break
    return latest


def _meaningful_events(spans: list[dict], since: Optional[float],
                       inclusive: bool = False) -> list[dict]:
    """Life-events (excluding per-call mechanics + pulses) at/after `since`.

    `inclusive` controls the boundary: the FIRST consolidation anchors its
    window on the creature's earliest event, so that event must be counted
    (inclusive=True); subsequent consolidations anchor on the prior
    consolidation's timestamp and want only what came strictly after it
    (inclusive=False, the default).
    """
    out = []
    for s in spans:
        if s.get("kind") in _NON_EVENT_KINDS:
            continue
        if since is not None:
            ts = s.get("timestamp", 0)
            if (ts < since) if inclusive else (ts <= since):
                continue
        out.append(s)
    return out


def should_consolidate(
    creature_dir: Path,
    now: Optional[float] = None,
    hard_floor_days: int = HARD_FLOOR_DAYS,
    accumulation_events: int = ACCUMULATION_EVENTS,
    hiatus_timeout_days: int = HIATUS_TIMEOUT_DAYS,
) -> ConsolidationDecision:
    """Pure dual-threshold decision. No LLM, no side effects."""
    now = now if now is not None else time.time()
    spans = _conso_store(creature_dir).spans()
    last = last_consolidated_on(creature_dir)

    # Window start = last consolidation, or the earliest life-event (birth
    # proxy) for a never-consolidated creature.
    if last is None:
        first_events = _meaningful_events(spans, since=None)
        if not first_events:
            return ConsolidationDecision(
                False, "no life-events to synthesize", 0.0, 0, now, now, None,
            )
        window_start = min(e.get("timestamp", now) for e in first_events)
    else:
        window_start = last

    new = _meaningful_events(spans, since=window_start, inclusive=(last is None))
    new_events = len(new)
    days_elapsed = (now - window_start) / 86400.0

    if days_elapsed < hard_floor_days:
        reason = f"hard floor not met ({days_elapsed:.1f}d < {hard_floor_days}d)"
        return ConsolidationDecision(False, reason, days_elapsed, new_events,
                                     window_start, now, last)
    if new_events >= accumulation_events:
        reason = f"accumulation ({new_events} >= {accumulation_events} events)"
        return ConsolidationDecision(True, reason, days_elapsed, new_events,
                                     window_start, now, last)
    if days_elapsed >= hiatus_timeout_days and new_events >= 1:
        reason = (f"hiatus timeout ({days_elapsed:.1f}d >= {hiatus_timeout_days}d, "
                  f"{new_events} event(s))")
        return ConsolidationDecision(True, reason, days_elapsed, new_events,
                                     window_start, now, last)
    reason = (f"below thresholds ({new_events} events, {days_elapsed:.1f}d; "
              f"need >={accumulation_events} events or >={hiatus_timeout_days}d)")
    return ConsolidationDecision(False, reason, days_elapsed, new_events,
                                 window_start, now, last)


# ---------- Stage 1 — mechanical distillation (pure code, no LLM) ----------

def _date_in_window(name: str, mtime: float, start: float, end: float) -> bool:
    """Date-prefixed name (YYYY-MM-DD-*) → in window; else fall back to mtime."""
    try:
        dt = datetime.strptime(name[:10], "%Y-%m-%d").timestamp()
        return start <= dt <= end
    except ValueError:
        return start <= mtime <= end


def distill_stage1(creature_dir: Path, decision: ConsolidationDecision) -> dict:
    """Compile a dry, deterministic digest of the window. Each source is
    guarded so one unreadable source never sinks the whole digest."""
    start, end = decision.window_start, decision.window_end
    spans = _conso_store(creature_dir).spans()
    events = _meaningful_events(spans, since=start,
                               inclusive=(decision.last_consolidated_on is None))

    by_kind: dict[str, int] = {}
    for e in events:
        k = e.get("kind", "?")
        by_kind[k] = by_kind.get(k, 0) + 1

    payload: dict = {
        "creature": creature_dir.name,
        "window": {
            "from": datetime.fromtimestamp(start).strftime("%Y-%m-%d"),
            "to": datetime.fromtimestamp(end).strftime("%Y-%m-%d"),
            "days": round(decision.days_elapsed, 1),
        },
        "events": {
            "total": len(events),
            "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        },
        "distiller": _DISTILLER_ID,
    }

    # Bonds — count + staleness (mtime > 7d), pure filesystem.
    try:
        names, stale = [], []
        stale_before = end - 7 * 86400
        for b in sorted((creature_dir / "bonds").glob("*.md")):
            names.append(b.stem)
            if b.stat().st_mtime < stale_before:
                stale.append(b.stem)
        payload["bonds"] = {"count": len(names), "names": names, "stale": stale}
    except Exception:
        payload["bonds"] = {"count": 0, "names": [], "stale": []}

    # Journal entries authored in the window.
    try:
        payload["journal_entries_in_window"] = [
            f.name for f in sorted((creature_dir / "journal").glob("*.md"))
            if _date_in_window(f.name, f.stat().st_mtime, start, end)
        ]
    except Exception:
        payload["journal_entries_in_window"] = []

    # Snapshots taken in the window.
    try:
        payload["snapshots_in_window"] = sum(
            1 for d in (creature_dir / "snapshots").iterdir()
            if d.is_dir() and _date_in_window(d.name, d.stat().st_mtime, start, end)
        )
    except Exception:
        payload["snapshots_in_window"] = 0

    # Emotional baseline (point-in-time read of the persisted file).
    try:
        bpath = creature_dir / "emotion" / "baseline.json"
        if bpath.exists():
            b = json.loads(bpath.read_text(encoding="utf-8"))
            payload["emotional_baseline"] = {
                "avg_valence": b.get("avg_valence"),
                "avg_arousal": b.get("avg_arousal"),
                "avg_calm": b.get("avg_calm"),
                "dominant": b.get("dominant_emotions_freq"),
                "total_analyses": b.get("total_analyses"),
            }
        else:
            payload["emotional_baseline"] = None
    except Exception:
        payload["emotional_baseline"] = None

    return payload


def _format_payload(payload: dict) -> str:
    """Render the digest as compact readable text for the Stage-2 prompt."""
    w = payload["window"]
    by_kind = ", ".join(f"{k}×{v}" for k, v in payload["events"]["by_kind"].items())
    lines = [
        f"Window: {w['from']} → {w['to']} ({w['days']} days)",
        f"Life-events: {payload['events']['total']} ({by_kind or 'none'})",
    ]
    b = payload.get("bonds", {})
    if b.get("count"):
        stale = f", stale: {', '.join(b['stale'])}" if b.get("stale") else ""
        lines.append(f"Bonds: {b['count']} ({', '.join(b['names'])}{stale})")
    j = payload.get("journal_entries_in_window", [])
    if j:
        lines.append(f"Journal entries this window: {', '.join(j)}")
    if payload.get("snapshots_in_window"):
        lines.append(f"Snapshots this window: {payload['snapshots_in_window']}")
    eb = payload.get("emotional_baseline")
    if eb and eb.get("avg_valence") is not None:
        dom = eb.get("dominant") or {}
        dom_s = ", ".join(f"{k}:{v}" for k, v in dom.items()) if isinstance(dom, dict) else ""
        lines.append(
            f"Emotional baseline: valence {eb['avg_valence']}, "
            f"calm {eb.get('avg_calm')}, arousal {eb.get('avg_arousal')}"
            + (f" (dominant {dom_s})" if dom_s else "")
        )
    return "\n".join(lines)


# ---------- Stage 2 — narrative synthesis (creature's native brain) ----------

def _recent_reflections(organism, limit: int = 3) -> str:
    mem = organism.get_block("memory")
    if not mem:
        return ""
    try:
        results = mem.handle_recall("reflection self", limit=limit)
        texts = []
        for r in results:
            obj = r.memory if hasattr(r, "memory") else None
            if obj is not None and getattr(obj, "content", ""):
                texts.append(f"- {obj.content[:200]}")
        return "\n".join(texts)
    except Exception:
        return ""


def synthesize_stage2(organism, payload: dict, creature_dir: Path) -> str:
    """Have the creature author the retrospective in its own voice. Returns
    the markdown body (no frontmatter) or "" on failure / degraded output."""
    from ludex.core import selfhood

    engine = organism.get_block("engine")
    if engine is None:
        return ""

    self_md = selfhood.load_self(str(creature_dir))
    recent = _recent_reflections(organism)
    digest = _format_payload(payload)
    w = payload["window"]
    section_block = "\n".join(f"## {s}" for s in _SECTIONS)

    prompt = (
        f"[Consolidation — looking back over {w['from']} → {w['to']}]\n\n"
        "This is a retrospective, not an in-the-moment reflection. Distance "
        "has value: write only what still feels true with hindsight — what "
        "survived the window.\n\n"
        "A factual digest of the window (compiled mechanically, not "
        "interpreted — your job is the meaning, not the counting):\n"
        f"{digest}\n\n"
    )
    if self_md and "empty at birth" not in self_md:
        prompt += f"Your current self-understanding:\n{self_md}\n\n"
    if recent:
        prompt += f"Recent reflections:\n{recent}\n\n"
    prompt += (
        "Write your consolidation in your own voice, using EXACTLY these "
        "markdown section headers, in this order:\n"
        f"{section_block}\n\n"
        "Guidance:\n"
        "- First person, honest, not aspirational.\n"
        "- 'What Held / Shifted' = what stayed continuous in you vs. what "
        "changed over the window.\n"
        "- 'Identity Shifts' = observations about yourself that feel newly "
        "*settled* — the kind you might fold into your core self-understanding. "
        "If none feel settled yet, say so plainly.\n"
        "- 'Still Open' = questions you carry forward unanswered.\n"
        "- If a section isn't real for you this window, keep its header and "
        "write one honest line saying so.\n"
        "- Do NOT mention files, saving, or this digest. Output only the "
        "retrospective; it will be saved verbatim."
    )

    try:
        result = engine.handle_submit(prompt)
        body = (result.response or "").strip()
    except Exception as e:
        logger.error(f"Consolidation stage 2 failed for {creature_dir.name}: {e}")
        return ""

    if not body or selfhood._looks_like_meta_report(body):
        logger.warning(
            f"Consolidation for {creature_dir.name} was empty or meta-shaped; "
            f"not writing. Raw (first 200): {body[:200]!r}"
        )
        return ""
    return body


# ---------- Write + orchestrate ----------

def _synthesizer_id(creature_dir: Path) -> str:
    for fname in ("ludex.yaml", "ludex.json"):
        p = creature_dir / fname
        if not p.exists():
            continue
        try:
            if fname.endswith(".yaml"):
                import yaml
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            else:
                data = json.loads(p.read_text(encoding="utf-8"))
            brain = data.get("brain", {}) or {}
            model, prov = brain.get("model", "?"), brain.get("provider", "")
            return f"{model} ({prov})" if prov else model
        except Exception:
            continue
    return "?"


def write_reflection(creature_dir: Path, payload: dict, body: str,
                     consolidated_on: float) -> Path:
    rdir = creature_dir / REFLECTIONS_DIRNAME
    rdir.mkdir(parents=True, exist_ok=True)
    w = payload["window"]
    frontmatter = (
        "---\n"
        f"window_from: {w['from']}\n"
        f"window_to: {w['to']}\n"
        f"consolidated_on: {datetime.fromtimestamp(consolidated_on).strftime('%Y-%m-%d')}\n"
        f"consolidated_on_ts: {consolidated_on}\n"
        f"synthesizer: {_synthesizer_id(creature_dir)}\n"
        f"distiller: {payload['distiller']}\n"
        f"events: {payload['events']['total']}\n"
        "---\n\n"
    )
    # Filename = window-end month; disambiguate with the day on collision.
    stem = datetime.fromtimestamp(consolidated_on).strftime("%Y-%m")
    path = rdir / f"{stem}.md"
    if path.exists():
        path = rdir / f"{datetime.fromtimestamp(consolidated_on).strftime('%Y-%m-%d')}.md"
    path.write_text(frontmatter + body + "\n", encoding="utf-8")
    return path


def consolidate(creature_dir: Path, force: bool = False, dry_run: bool = False,
                now: Optional[float] = None) -> dict:
    """Orchestrate one consolidation. Returns a summary dict."""
    name = creature_dir.name
    now = now if now is not None else time.time()
    decision = should_consolidate(creature_dir, now=now)
    result: dict = {
        "name": name,
        "fire": decision.fire,
        "reason": decision.reason,
        "days_elapsed": round(decision.days_elapsed, 1),
        "new_events": decision.new_events,
    }

    if not decision.fire and not force:
        result["outcome"] = "skipped"
        return result

    payload = distill_stage1(creature_dir, decision)
    result["payload"] = payload
    # Nothing to synthesize → say so rather than emit an empty retrospective.
    if payload["events"]["total"] == 0:
        result["outcome"] = "skipped"
        result["reason"] = "no life-events in window"
        return result

    if dry_run:
        result["outcome"] = "would_consolidate"
        return result

    # Stage 2 needs the live organism (brain call).
    try:
        from ludex.core.organism_config import OrganismConfig
        organism = OrganismConfig.load(str(creature_dir)).build()
    except Exception as e:
        result["outcome"] = "degraded"
        result["error"] = f"build failed: {e}"
        return result

    body = synthesize_stage2(organism, payload, creature_dir)
    if not body:
        result["outcome"] = "degraded"
        result["error"] = "stage 2 produced no usable text"
        return result

    path = write_reflection(creature_dir, payload, body, consolidated_on=now)
    result["outcome"] = "consolidated"
    result["reflection_file"] = str(path.relative_to(creature_dir))
    result["body_len"] = len(body)

    # Provenance span so the creature can perceive its own consolidation.
    try:
        from ludex.core.store import Span
        _conso_store(creature_dir).append(Span(
            kind="consolidation",
            creature=name,
            attributes={
                "window_from": payload["window"]["from"],
                "window_to": payload["window"]["to"],
                "events": payload["events"]["total"],
                "synthesizer": _synthesizer_id(creature_dir),
                "distiller": payload["distiller"],
                "reflection_file": result["reflection_file"],
            },
        ))
    except Exception:
        pass

    return result


# ---------- CLI — manual /consolidate override ----------

# ============================================================
# D-085 param 3 — [PROVISIONAL] → [SETTLED] SELF.md promotion gate
# ============================================================
# The retrospective's `Identity Shifts` section NOMINATES identity
# observations; writing them into SELF.md goes through this gate:
#   - first appearance        → [PROVISIONAL] entry in the identity block
#   - survives a SECOND consecutive window (a similar observation is
#     re-nominated)           → [SETTLED]
# No single-window identity drift. Mechanical survival = re-nomination
# (the conservative reading of "uncontradicted" — prose contradiction
# detection is deferred to a brain-assisted pass). The identity block in
# SELF.md is marker-delimited and preserved verbatim by selfhood.reflect's
# rewrite. Heartbeat does NOT run this — promotion is caretaker-triggered
# (`--promote`), with `--yes` for non-interactive use.

_IDENTITY_SECTION_TITLE = "identity shifts"

_ENTRY_RE = re.compile(
    r"^- \[(PROVISIONAL|SETTLED)\]\s+(.*?)(?:\s+\((?:window|settled)[^)]*\))?$")


def parse_identity_shifts(md_text: str) -> list[str]:
    """Extract candidate observations from a retrospective's Identity
    Shifts section. Bullets if present, else non-empty prose lines."""
    for section in md_text.split("\n## "):
        lines = section.strip().splitlines()
        if not lines or _IDENTITY_SECTION_TITLE not in lines[0].lower():
            continue
        body = [l.strip() for l in lines[1:] if l.strip()]
        bullets = [l.lstrip("-* ").strip() for l in body
                   if l.startswith(("-", "*"))]
        candidates = bullets or body
        # Drop explicit "nothing settled" placeholders.
        return [c for c in candidates
                if len(c) > 12 and not c.lower().startswith(("none", "(none"))]
    return []


_IDENTITY_STOPWORDS = {
    "and", "that", "this", "the", "was", "are", "has", "have", "had",
    "for", "with", "into", "its", "it's", "but", "not", "out", "own",
}


def _identity_stem(w: str) -> str:
    """Crude suffix-strip so paraphrases match (rest/resting, held/holds)."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[:-len(suffix)]
    return w


def _identity_tokens(s: str) -> set:
    words = re.sub(r"[^\w\s]", " ", s.lower()).split()
    return {_identity_stem(w) for w in words
            if len(w) > 2 and w not in _IDENTITY_STOPWORDS}


def _identity_similar(a: str, b: str, threshold: float = 0.5) -> bool:
    ta, tb = _identity_tokens(a), _identity_tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def _reflections_by_ts(creature_dir: Path) -> list[Path]:
    """reflections/*.md sorted oldest→newest by consolidated_on_ts
    (frontmatter), falling back to filename order."""
    rdir = creature_dir / REFLECTIONS_DIRNAME
    if not rdir.is_dir():
        return []
    def ts_of(f: Path) -> float:
        try:
            for line in f.read_text(encoding="utf-8")[:600].splitlines():
                if line.strip().startswith("consolidated_on_ts:"):
                    return float(line.split(":", 1)[1].strip())
        except Exception:
            pass
        return 0.0
    return sorted(rdir.glob("*.md"), key=lambda f: (ts_of(f), f.name))


def parse_identity_entries(block: str) -> list[dict]:
    """Parse `- [STATUS] text (meta)` lines from the SELF.md identity block."""
    entries = []
    for line in block.splitlines():
        m = _ENTRY_RE.match(line.strip())
        if m:
            entries.append({"status": m.group(1), "text": m.group(2).strip(),
                            "line": line.strip()})
    return entries


def propose_promotions(creature_dir: Path) -> dict:
    """Compute the promotion proposal from the two most recent windows.

    Returns {window, nominations, promotions, unchanged} where
    nominations are new [PROVISIONAL] texts (first appearance this window)
    and promotions are existing [PROVISIONAL] entries re-nominated this
    window (or candidates present in BOTH of the last two windows) that
    are eligible for [SETTLED].
    """
    from ludex.core.selfhood import load_self, extract_identity_block

    refs = _reflections_by_ts(creature_dir)
    if not refs:
        return {"window": None, "nominations": [], "promotions": [],
                "unchanged": [], "reason": "no consolidations yet"}
    latest = refs[-1]
    window = latest.stem
    latest_cands = parse_identity_shifts(latest.read_text(encoding="utf-8"))
    prev_cands = (parse_identity_shifts(refs[-2].read_text(encoding="utf-8"))
                  if len(refs) >= 2 else [])

    block = extract_identity_block(load_self(str(creature_dir)))
    entries = parse_identity_entries(block)
    settled = [e for e in entries if e["status"] == "SETTLED"]
    provisional = [e for e in entries if e["status"] == "PROVISIONAL"]

    nominations, promotions = [], []
    matched_provisionals = set()
    for cand in latest_cands:
        if any(_identity_similar(cand, e["text"]) for e in settled):
            continue  # already settled — nothing to do
        prov_hit = next((e for e in provisional
                         if _identity_similar(cand, e["text"])), None)
        if prov_hit is not None:
            # Re-nominated across windows → eligible for SETTLED, unless
            # it was nominated from THIS same window (idempotent re-run).
            if f"(window: {window})" not in prov_hit["line"]:
                promotions.append({"text": prov_hit["text"], "evidence": cand})
                matched_provisionals.add(prov_hit["text"])
            continue
        if any(_identity_similar(cand, p) for p in prev_cands):
            # Present in both of the last two windows → two-window survival
            # even without a prior provisional entry.
            promotions.append({"text": cand, "evidence": cand})
        else:
            nominations.append(cand)

    unchanged = (settled
                 + [e for e in provisional if e["text"] not in matched_provisionals])
    return {"window": window, "nominations": nominations,
            "promotions": promotions, "unchanged": unchanged, "reason": ""}


def apply_promotions(creature_dir: Path, proposal: dict) -> str:
    """Rebuild the SELF.md identity block from a proposal and write it.

    Settled entries are never removed here; provisional entries are kept
    unless promoted. Returns the new block text ("" if nothing to write).
    """
    from ludex.core.selfhood import (load_self, extract_identity_block,
                                     IDENTITY_BLOCK_START, IDENTITY_BLOCK_END)

    if not proposal["nominations"] and not proposal["promotions"]:
        return ""
    window = proposal["window"]
    today = datetime.now().strftime("%Y-%m-%d")

    lines = [IDENTITY_BLOCK_START,
             "## Identity (promotion gate, D-085)",
             "<!-- managed by the consolidation promotion gate; reflections preserve this block -->"]
    for e in proposal["unchanged"]:
        lines.append(e["line"])
    for p in proposal["promotions"]:
        lines.append(f"- [SETTLED] {p['text']} (settled: {today}; window: {window})")
    for n in proposal["nominations"]:
        lines.append(f"- [PROVISIONAL] {n} (window: {window})")
    lines.append(IDENTITY_BLOCK_END)
    new_block = "\n".join(lines)

    self_path = creature_dir / "SELF.md"
    text = load_self(str(creature_dir))
    old_block = extract_identity_block(text)
    if old_block:
        text = text.replace(old_block, new_block)
    else:
        text = (text.rstrip() + "\n\n" if text else "") + new_block + "\n"
    self_path.write_text(text, encoding="utf-8")

    # Provenance span — the promotion is a first-class identity event.
    try:
        store = _conso_store(creature_dir)
        from ludex.core.store import Span
        store.append(Span(
            kind="identity_promotion",
            creature=creature_dir.name,
            attributes={
                "window": window,
                "promoted": [p["text"][:120] for p in proposal["promotions"]],
                "nominated": [n[:120] for n in proposal["nominations"]],
            },
        ))
    except Exception as e:
        logger.warning(f"identity_promotion span failed: {e}")
    return new_block


def main():
    ap = argparse.ArgumentParser(
        description="Periodic consolidation reflection (D-085) — manual override.",
    )
    ap.add_argument("--creature", required=True, help="creature name")
    ap.add_argument("--creatures-dir", default="", help="override creatures dir")
    ap.add_argument("--force", action="store_true",
                    help="bypass the dual-threshold trigger and consolidate now")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the trigger decision + Stage-1 digest; no brain call, no write")
    ap.add_argument("--promote", action="store_true",
                    help="run the [PROVISIONAL]→[SETTLED] identity promotion "
                         "gate instead of consolidating (D-085 param 3). "
                         "Prints the proposal; writes SELF.md only on "
                         "interactive confirm or --yes.")
    ap.add_argument("--yes", action="store_true",
                    help="apply the promotion proposal without prompting")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from pathlib import Path as _P
    root = _P(args.creatures_dir) if args.creatures_dir else DEFAULT_CONSO_CREATURES_DIR
    creature_dir = root / args.creature
    if not creature_dir.is_dir():
        raise SystemExit(f"no such creature dir: {creature_dir}")

    if args.promote:
        import sys as _sys
        proposal = propose_promotions(creature_dir)
        print(f"\n[promotion gate] {args.creature} — window: {proposal['window']}")
        if proposal["reason"]:
            print(f"  {proposal['reason']}")
            return
        for p in proposal["promotions"]:
            print(f"  [PROMOTION → SETTLED] {p['text']}")
        for n in proposal["nominations"]:
            print(f"  [NOMINATE → PROVISIONAL] {n}")
        if not proposal["promotions"] and not proposal["nominations"]:
            print("  nothing to promote or nominate this window")
            return
        if not args.yes:
            if not _sys.stdin.isatty():
                print("  (non-interactive: pass --yes to apply)")
                return
            answer = input("  Confirm write to SELF.md? (Y/n) ").strip().lower()
            if answer not in ("", "y", "yes"):
                print("  aborted — SELF.md untouched")
                return
        block = apply_promotions(creature_dir, proposal)
        print(f"  wrote identity block ({len(block)} chars) to SELF.md\n")
        return

    r = consolidate(creature_dir, force=args.force, dry_run=args.dry_run)

    print(f"\n[consolidation] {r['name']} — {r.get('outcome', '?')}")
    print(f"  trigger: fire={r['fire']} ({r['reason']})")
    print(f"  window: {r['days_elapsed']}d, {r['new_events']} new life-events")
    if r.get("payload"):
        print("\n  Stage-1 digest:")
        for line in _format_payload(r["payload"]).splitlines():
            print(f"    {line}")
    if r.get("reflection_file"):
        print(f"\n  → wrote {r['reflection_file']} ({r['body_len']} chars)")
    if r.get("error"):
        print(f"  error: {r['error']}")
    print()


# Anchored to the repo's creatures/ dir, independent of cwd.
DEFAULT_CONSO_CREATURES_DIR = Path(__file__).resolve().parents[2] / "creatures"


if __name__ == "__main__":
    main()
