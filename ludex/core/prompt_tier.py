"""
Prompt Tier Translator — universal per-tier prompt adaptation (D-043).

See `docs/prompt-tier-translator-design.md` for the architectural design
and the coevolutionary-ecology-north-star for the philosophical basis:
translation is a routing tool, not a sorting tool. Smaller brains get
the same activity expressed at a density they can actually engage with.

Phase A scope:
- 5-tier enum defined; rule sets implemented for LARGE / MID / MID_SLM
- Bracketed structured markers ([essential], [task], [elaboration],
  [constraint-negative], [length], [frame]) with repeat allowed and
  unknown tags passing through
- Rule-based Translator only; LLMTranslator interface reserved
- Pure `translate()` returning `TranslationResult`; Ludex wrapper
  `translate_and_emit()` opts into trace emission
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


# ============================================================
# Tier definitions
# ============================================================

class Tier(Enum):
    LARGE = "large"
    MID = "mid"
    LARGE_SLM = "large_slm"
    MID_SLM = "mid_slm"
    SMALL_SLM = "small_slm"


# Brain model patterns for tier lookup. Order matters — first match wins.
# Patterns are regex searched against lowercased model string.
_TIER_PATTERNS: list[tuple[Tier, list[str]]] = [
    (Tier.LARGE, [
        r"opus", r"gemini-3?.*pro", r"gpt-5-pro", r"ultra",
    ]),
    (Tier.MID, [
        r"sonnet", r"haiku", r"flash", r"gpt-5$", r"gpt-4",
    ]),
    (Tier.LARGE_SLM, [
        r"llama.*3\.1.*8b", r"gemma.*(?:10b|9b)", r"qwen.*7b", r"mistral.*7b",
    ]),
    (Tier.MID_SLM, [
        r"gemma4:e4b", r"gemma.*(?:4b|5b|6b)", r"llama.*3\.2.*3b",
        r"phi-?3\.5", r"qwen.*3b",
    ]),
    (Tier.SMALL_SLM, [
        r"smollm", r"phi-?3-mini", r"gemma.*(?:2b|1b)", r"llama.*1b",
        r"tinyllama",
    ]),
]


def tier_of(brain: dict | None) -> Tier:
    """Map a brain dict (provider/model) to a Tier.

    Defaults to MID for unknown brains (conservative — under-simplifying
    is less harmful than over-simplifying for an unknown creature).
    """
    if not brain or not isinstance(brain, dict):
        return Tier.MID
    model = (brain.get("model") or "").lower()
    if not model:
        return Tier.MID
    for tier, patterns in _TIER_PATTERNS:
        for pat in patterns:
            if re.search(pat, model):
                return tier
    return Tier.MID


# ============================================================
# Structured prompt representation
# ============================================================

# Known tags — preserved, counted, and transformed per tier.
# Repeatable tags can appear multiple times (e.g., [constraint-negative]).
TAG_ESSENTIAL = "essential"
TAG_TASK = "task"
TAG_ELABORATION = "elaboration"
TAG_CONSTRAINT_NEGATIVE = "constraint-negative"
TAG_LENGTH = "length"
TAG_FRAME = "frame"
KNOWN_TAGS = {
    TAG_ESSENTIAL, TAG_TASK, TAG_ELABORATION,
    TAG_CONSTRAINT_NEGATIVE, TAG_LENGTH, TAG_FRAME,
}
REPEATABLE_TAGS = {TAG_CONSTRAINT_NEGATIVE}


@dataclass
class PromptSegment:
    tag: str
    content: str


def parse_structured(prompt: str) -> list[PromptSegment]:
    """Parse a marker-tagged prompt into ordered segments.

    A marker is a line (after optional leading whitespace) whose first
    token is `[tag]`. Content extends to the next marker line or EOF.
    Lines before any marker become an initial `""` (no-tag) segment so
    flat prompts don't lose content.
    """
    lines = prompt.splitlines()
    segments: list[PromptSegment] = []
    current_tag = ""
    current_lines: list[str] = []

    marker_re = re.compile(r"^\s*\[([a-zA-Z0-9_-]+)\]\s?(.*)$")

    for line in lines:
        m = marker_re.match(line)
        if m:
            if current_lines or current_tag:
                segments.append(PromptSegment(
                    tag=current_tag,
                    content="\n".join(current_lines).rstrip(),
                ))
            current_tag = m.group(1).lower()
            current_lines = [m.group(2)] if m.group(2) else []
        else:
            current_lines.append(line)

    if current_lines or current_tag:
        segments.append(PromptSegment(
            tag=current_tag,
            content="\n".join(current_lines).rstrip(),
        ))

    # Strip empty segments
    return [s for s in segments if s.tag or s.content.strip()]


def render_segments(segments: list[PromptSegment]) -> str:
    """Render segments back to a prompt string. Preserves marker format."""
    parts: list[str] = []
    for s in segments:
        if s.tag:
            header = f"[{s.tag}]"
            content = s.content.strip()
            if content and "\n" in content:
                parts.append(f"{header}\n{content}")
            elif content:
                parts.append(f"{header} {content}")
            else:
                parts.append(header)
        else:
            if s.content.strip():
                parts.append(s.content.strip())
    return "\n\n".join(parts)


# ============================================================
# Result type
# ============================================================

@dataclass
class TranslationResult:
    prompt: str
    source_length: int
    target_length: int
    target_tier: Tier
    transformations: list[str] = field(default_factory=list)
    translator: str = "rule-based"


# ============================================================
# Translator protocol
# ============================================================

class Translator(Protocol):
    def translate(self, prompt: str, target: Tier) -> TranslationResult: ...


# ============================================================
# Rule-based translator
# ============================================================

class RuleBasedTranslator:
    """Rule-based Phase A translator.

    Transformations available (applied per tier):
    - drop_elaboration: remove all [elaboration] segments
    - shorten_elaboration: truncate [elaboration] segments to char budget
    - cap_negatives: keep only first N [constraint-negative] segments
    - collapse_steps: reduce numbered-list task content to 1-2 items
    - strip_parentheticals: remove (...) aside clauses in [task]/[frame]
    """

    name = "rule-based"

    def translate(self, prompt: str, target: Tier) -> TranslationResult:
        source_len = len(prompt)
        segments = parse_structured(prompt)
        transformations: list[str] = []

        if target == Tier.LARGE:
            # pass-through
            pass
        elif target == Tier.MID:
            # pass-through (default authoring tier)
            pass
        elif target == Tier.LARGE_SLM:
            segments, t = _shorten_elaboration(segments, max_chars=300)
            transformations.extend(t)
            segments, t = _cap_negatives(segments, max_count=2)
            transformations.extend(t)
        elif target == Tier.MID_SLM:
            segments, t = _drop_elaboration(segments)
            transformations.extend(t)
            segments, t = _cap_negatives(segments, max_count=1)
            transformations.extend(t)
            segments, t = _collapse_task_steps(segments, max_steps=2)
            transformations.extend(t)
        elif target == Tier.SMALL_SLM:
            segments, t = _drop_elaboration(segments)
            transformations.extend(t)
            segments, t = _cap_negatives(segments, max_count=0)
            transformations.extend(t)
            segments, t = _collapse_task_steps(segments, max_steps=1)
            transformations.extend(t)
            segments, t = _strip_parentheticals(segments)
            transformations.extend(t)

        out = render_segments(segments)
        return TranslationResult(
            prompt=out,
            source_length=source_len,
            target_length=len(out),
            target_tier=target,
            transformations=transformations,
            translator=self.name,
        )


# ============================================================
# Transformation helpers
# ============================================================

def _drop_elaboration(segments: list[PromptSegment]) -> tuple[list[PromptSegment], list[str]]:
    before = len(segments)
    kept = [s for s in segments if s.tag != TAG_ELABORATION]
    removed = before - len(kept)
    return kept, ([f"dropped_{removed}_elaborations"] if removed else [])


def _shorten_elaboration(segments: list[PromptSegment], max_chars: int) -> tuple[list[PromptSegment], list[str]]:
    out: list[PromptSegment] = []
    shortened = 0
    for s in segments:
        if s.tag == TAG_ELABORATION and len(s.content) > max_chars:
            new_content = s.content[:max_chars].rstrip() + "…"
            out.append(PromptSegment(tag=s.tag, content=new_content))
            shortened += 1
        else:
            out.append(s)
    return out, ([f"shortened_{shortened}_elaborations"] if shortened else [])


def _cap_negatives(segments: list[PromptSegment], max_count: int) -> tuple[list[PromptSegment], list[str]]:
    out: list[PromptSegment] = []
    kept = 0
    dropped = 0
    for s in segments:
        if s.tag == TAG_CONSTRAINT_NEGATIVE:
            if kept < max_count:
                out.append(s)
                kept += 1
            else:
                dropped += 1
        else:
            out.append(s)
    return out, ([f"dropped_{dropped}_negatives"] if dropped else [])


def _collapse_task_steps(segments: list[PromptSegment], max_steps: int) -> tuple[list[PromptSegment], list[str]]:
    """Collapse numbered steps in [task] segments to at most max_steps."""
    out: list[PromptSegment] = []
    collapsed = 0
    step_re = re.compile(r"^\s*(\d+)\.\s", re.MULTILINE)
    for s in segments:
        if s.tag != TAG_TASK:
            out.append(s)
            continue
        step_matches = list(step_re.finditer(s.content))
        if len(step_matches) <= max_steps:
            out.append(s)
            continue
        # Cut at the start of the (max_steps + 1)-th step (step_matches is
        # 0-indexed, so step_matches[max_steps] is that step's start).
        if max_steps == 0:
            # remove all numbered content — leave pre-first-step text only
            end = step_matches[0].start()
            new_content = s.content[:end].rstrip()
        elif max_steps < len(step_matches):
            cut_pos = step_matches[max_steps].start()
            new_content = s.content[:cut_pos].rstrip()
        else:
            # fewer steps than max — no cut needed
            new_content = s.content.rstrip()
        out.append(PromptSegment(tag=s.tag, content=new_content))
        collapsed += 1
    return out, ([f"collapsed_{collapsed}_task_steps"] if collapsed else [])


def _strip_parentheticals(segments: list[PromptSegment]) -> tuple[list[PromptSegment], list[str]]:
    out: list[PromptSegment] = []
    stripped = 0
    paren_re = re.compile(r"\s*\([^)]*\)\s*")
    for s in segments:
        if s.tag in (TAG_TASK, TAG_FRAME):
            new_content, n = paren_re.subn(" ", s.content)
            if n:
                stripped += n
                out.append(PromptSegment(tag=s.tag, content=new_content.strip()))
                continue
        out.append(s)
    return out, ([f"stripped_{stripped}_parentheticals"] if stripped else [])


# ============================================================
# LLM-based translator — interface reserved for Phase A.5
# ============================================================

class LLMTranslator:
    """Interface placeholder. Implementation deferred to Phase A.5.

    When activated, will take a helper engine (mid-tier brain) and use it
    to rewrite a source prompt for a target tier. Reserved for empirical
    A/B comparison against rule-based translation per creature per tier.
    """

    name = "llm-based"

    def __init__(self, helper_engine=None):
        self.helper_engine = helper_engine

    def translate(self, prompt: str, target: Tier) -> TranslationResult:
        raise NotImplementedError("LLMTranslator Phase A.5 — not yet implemented")


# ============================================================
# Convenience API
# ============================================================

_default_translator = RuleBasedTranslator()


def translate(prompt: str, target: Tier, translator: Translator | None = None) -> TranslationResult:
    """Translate a prompt for a target tier. Pure function, no side effects."""
    t = translator or _default_translator
    return t.translate(prompt, target)


def translate_for(prompt: str, brain_or_creature, translator: Translator | None = None) -> TranslationResult:
    """Convenience: derive tier from brain/creature, then translate."""
    if isinstance(brain_or_creature, dict):
        brain = brain_or_creature
    elif hasattr(brain_or_creature, "brain"):
        brain = brain_or_creature.brain
    elif hasattr(brain_or_creature, "config"):
        cfg = brain_or_creature.config
        brain = cfg.get("brain", {}) if hasattr(cfg, "get") else {}
    else:
        brain = {}
    return translate(prompt, tier_of(brain), translator=translator)


def translate_and_emit(prompt: str, participant_or_organism, translator: Translator | None = None) -> str:
    """Translate for a participant's brain tier AND emit a trace span.

    Convenience wrapper used inside Ludex fields. Library users of
    `translate()` bypass the trace dependency. Returns the adapted
    prompt string only; full TranslationResult available via
    translate_for() if needed.
    """
    # Resolve brain + organism from input
    organism = None
    brain: dict = {}
    if hasattr(participant_or_organism, "organism") and hasattr(participant_or_organism, "role"):
        # Participant
        organism = participant_or_organism.organism
        if organism and hasattr(organism, "config"):
            cfg = organism.config
            brain = cfg.get("brain", {}) if hasattr(cfg, "get") else {}
    elif hasattr(participant_or_organism, "config"):
        # Organism
        organism = participant_or_organism
        cfg = organism.config
        brain = cfg.get("brain", {}) if hasattr(cfg, "get") else {}
    elif isinstance(participant_or_organism, dict):
        brain = participant_or_organism

    result = translate(prompt, tier_of(brain), translator=translator)

    if organism is not None:
        try:
            from ludex.core import trace as _tr
            _tr.emit_translation_applied(organism, result)
        except Exception:
            pass

    return result.prompt


def build_tiered(
    *,
    essential: str = "",
    task: str = "",
    elaboration: str = "",
    constraints_negative: list[str] | None = None,
    length: str = "",
    frame: str = "",
    target: Tier = Tier.MID,
    translator: Translator | None = None,
) -> TranslationResult:
    """Compose a structured prompt from parts and translate to target tier.

    Use this when authoring new prompts — authors classify content into
    tag-appropriate slots rather than writing free prose.
    """
    parts: list[str] = []
    if essential:
        parts.append(f"[essential] {essential}")
    if task:
        parts.append(f"[task] {task}")
    if elaboration:
        parts.append(f"[elaboration] {elaboration}")
    for neg in (constraints_negative or []):
        parts.append(f"[constraint-negative] {neg}")
    if length:
        parts.append(f"[length] {length}")
    if frame:
        parts.append(f"[frame] {frame}")
    prompt = "\n\n".join(parts)
    return translate(prompt, target, translator=translator)
