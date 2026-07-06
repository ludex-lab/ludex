"""Brain-tier-aware prompt adapter for physis-using fields.

Per `docs/physis-phase-b-design.md` §2: the same field needs
different output grammars at different brain tiers. JSON works for
frontier brains; verb-form works for haiku / mid-tier; single-action
works for SLM tier. Multi-shot example density is also brain-tier-
coupled.

This module is a pure function called by the field harness — no
organ, no state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Brain-tier classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrainTier:
    """A brain's prompt-adapter profile."""

    tier: str            # "frontier" / "mid" / "slm"
    output_grammar: str  # "json" / "verb_form" / "single_verb"
    examples_budget: int  # number of multi-shot examples to include
    world_model_max_chars: int  # max world-model excerpt size


# Per-(provider, model) tier resolution.
# Numbers are starting estimates; refine as field data accumulates.
_FRONTIER = BrainTier(
    tier="frontier",
    output_grammar="json",
    examples_budget=2,
    world_model_max_chars=3000,
)
_MID = BrainTier(
    tier="mid",
    output_grammar="verb_form",
    examples_budget=3,
    world_model_max_chars=800,
)
_SLM = BrainTier(
    tier="slm",
    output_grammar="single_verb",
    examples_budget=1,
    world_model_max_chars=500,
)


# Explicit (provider, model_substring) → tier mapping. Substring match
# on lower-cased model id; first match wins.
_TIER_RULES: list[tuple[str, str, BrainTier]] = [
    # Frontier
    ("claude_cli", "opus", _FRONTIER),
    ("claude_cli", "sonnet-4-6", _FRONTIER),
    ("claude_sdk", "opus", _FRONTIER),
    ("claude_sdk", "sonnet-4-6", _FRONTIER),
    ("codex_cli", "gpt-5.5", _FRONTIER),
    ("gemini_cli", "3.1-pro", _FRONTIER),
    ("gemini_cli", "3.1-ultra", _FRONTIER),
    # Mid
    ("claude_cli", "haiku", _MID),
    ("claude_cli", "sonnet-4-5", _MID),
    ("claude_cli", "sonnet-3", _MID),
    ("claude_sdk", "haiku", _MID),
    ("gemini_cli", "flash", _MID),
    ("gemini_cli", "3.0-pro", _MID),
    # agy_cli — provisional MID per "flash" naming convention.
    # 3.5-flash benchmarks (Terminal-Bench 76.2%, GDPval-AA 1656 Elo)
    # exceed 3.1-pro; revisit FRONTIER after in-cohort observation.
    ("agy_cli", "3.5-flash", _MID),
    # SLM (any ollama, plus tiny non-ollama models if encountered)
    ("ollama", "", _SLM),
]


def resolve_tier(provider: str, model: str) -> BrainTier:
    """Resolve a (provider, model) pair to a BrainTier.

    Defaults to MID tier if no rule matches, on the assumption that
    new frontier models will be explicitly added; an unknown brain
    is more likely mid-tier than frontier.
    """
    p = (provider or "").lower()
    m = (model or "").lower()
    for rule_p, rule_m_substr, tier in _TIER_RULES:
        if rule_p == p:
            if not rule_m_substr or rule_m_substr in m:
                return tier
    return _MID


# ---------------------------------------------------------------------------
# Stacker-specific templates
# ---------------------------------------------------------------------------


_STACKER_FRONTIER_TEMPLATE = """You are playing the Stacker block-stacking task as yourself. For this turn, output a single-line JSON action — no prose, no explanation, no commentary, no preamble.

Current world:
- Blocks: {blocks}
- On table: {on_table}
- Stacks (lower:upper): {stacks}
- In hand: {in_hand}
- Currently-clear (no block on top): {clear_blocks}
- Step: {step}/{step_cap}

Goal world:
- On table: {goal_on_table}
- Stacks: {goal_stacks}

Action grammar (output one and ONLY one of these on a single line):
{{"type":"pickup","block":"X"}}      # X on_table AND clear; hand empty
{{"type":"putdown","block":"X"}}     # X in hand
{{"type":"stack","top":"X","bottom":"Y"}}    # X in hand; Y clear; X!=Y
{{"type":"unstack","top":"X","from":"Y"}}    # X on top of Y AND clear; hand empty
{world_model_section}{examples_section}
Output the next action JSON. Do NOT describe the goal as completed unless it already is. Do NOT roleplay. Do NOT explain. Just one JSON line.

Action JSON:"""


_STACKER_MID_TEMPLATE = """Solve a Stacker (block-stacking) move. Output exactly one verb-form action and nothing else. No tools, no file reads, no JSON, no explanation.

Current world:
  blocks: {blocks}
  on_table: {on_table}
  stacks (lower:upper): {stacks}
  in_hand: {in_hand}
  clear (no block on top): {clear_blocks}
  step: {step}/{step_cap}

Goal world:
  on_table: {goal_on_table}
  stacks: {goal_stacks}

Verb-form vocabulary (output one of these on a single line):
  pickup X       — X must be on_table AND clear; in_hand must be empty
  putdown X      — X must be in_hand
  stack X on Y   — X must be in_hand; Y must be clear; X != Y
  unstack X from Y   — X must be the block on top of Y; in_hand empty

Sample correct outputs (one line each, no surrounding text):
  pickup A
  stack A on B
  unstack C from B
  putdown D
{world_model_section}{examples_section}
Your next move (one verb-form line, nothing else):"""


_STACKER_SLM_TEMPLATE = """Stacker. State:
  on_table: {on_table}
  stacks: {stacks}
  in_hand: {in_hand}
  clear: {clear_blocks}
Goal: {goal_stacks}; on_table {goal_on_table}.

Verbs: pickup X | putdown X | stack X on Y | unstack X from Y

Example: stack A on B

Reply ONE line, just the verb form. Action:"""


# ---------------------------------------------------------------------------
# Stacker scaffolding helpers
# ---------------------------------------------------------------------------


def _stacker_clear_blocks(state: dict) -> str:
    """Compute currently-clear blocks (no block on top) from state dict.

    Field-specific scaffolding hook — declared via Stacker's
    world_schema.json `prompt_scaffold_keys` (Phase B addition).
    """
    blocks = state.get("blocks", []) or []
    stacks = state.get("stacks", {}) or {}
    in_hand = state.get("in_hand", "")
    on_table = set(state.get("on_table", []) or [])
    # A block is clear if (a) nothing is stacked on top of it, and
    # (b) it's not the block in hand. Blocks that exist but are
    # neither in_hand nor on_table nor in stacks shouldn't happen,
    # but if they do, ignore.
    bottoms_with_top = set(stacks.keys())
    clear = []
    for b in blocks:
        if b == in_hand:
            continue
        if b in bottoms_with_top:
            continue
        clear.append(b)
    return ", ".join(sorted(clear)) or "(none)"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_stacker_prompt(
    state: dict,
    *,
    provider: str,
    model: str,
    world_model_excerpt: str = "",
    extra_examples: str = "",
) -> tuple[str, BrainTier]:
    """Render a Stacker action prompt for the given brain tier.

    Returns (prompt, tier). The tier is exposed so the caller can
    select the right *parser* for the response (parse_action_response).
    """
    tier = resolve_tier(provider, model)

    on_table = ", ".join(sorted(state.get("on_table") or [])) or "(none)"
    stacks = ", ".join(
        f"{k}:{v}" for k, v in sorted((state.get("stacks") or {}).items())
    ) or "(none)"
    in_hand = state.get("in_hand") or "(empty)"
    goal_on_table = ", ".join(sorted(state.get("goal_on_table") or [])) or "(none)"
    goal_stacks = ", ".join(
        f"{k}:{v}" for k, v in sorted((state.get("goal_stacks") or {}).items())
    ) or "(none)"
    blocks = ", ".join(sorted(state.get("blocks") or []))
    clear_blocks = _stacker_clear_blocks(state)
    step = state.get("step", 0)
    step_cap = state.get("step_cap", 0)

    wm_section = ""
    if world_model_excerpt.strip():
        excerpt = world_model_excerpt[-tier.world_model_max_chars:]
        if tier.tier == "frontier":
            wm_section = (
                "\nPast experience in this field (your accumulated world model):\n\n"
                f"{excerpt}\n\nUse this experience to inform your next action.\n"
            )
        elif tier.tier == "mid":
            wm_section = (
                f"\nNotes from past episodes (use as guidance):\n{excerpt}\n\n"
            )
        else:  # slm — drop world model entirely (context too small)
            wm_section = ""

    examples_section = ""
    if extra_examples.strip() and tier.examples_budget >= 2:
        examples_section = f"\nMore examples:\n{extra_examples}\n\n"

    if tier.output_grammar == "json":
        template = _STACKER_FRONTIER_TEMPLATE
    elif tier.output_grammar == "verb_form":
        template = _STACKER_MID_TEMPLATE
    else:
        template = _STACKER_SLM_TEMPLATE

    prompt = template.format(
        blocks=blocks,
        on_table=on_table,
        stacks=stacks,
        in_hand=in_hand,
        step=step,
        step_cap=step_cap,
        goal_on_table=goal_on_table,
        goal_stacks=goal_stacks,
        clear_blocks=clear_blocks,
        world_model_section=wm_section,
        examples_section=examples_section,
    )
    return prompt, tier


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


_VERB_RE = re.compile(
    r"\b(pickup|putdown|stack|unstack)\b\s+([A-Za-z0-9_]+)"
    r"(?:\s+(?:on|from)\s+([A-Za-z0-9_]+))?",
    re.IGNORECASE,
)
_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def parse_stacker_action(text: str, tier: BrainTier) -> Optional[dict]:
    """Parse a brain response into an action dict matching
    `StackerAction.to_dict()`.

    Strategy: try the *expected* grammar for the tier first, then
    fall back to the others. Frontier emitting verb-form is OK,
    mid emitting JSON is OK, etc. — robustness over strictness.
    """
    if not text:
        return None

    if tier.output_grammar == "json":
        action = _try_json(text) or _try_verb(text)
    else:
        action = _try_verb(text) or _try_json(text)
    return action


def _try_json(text: str) -> Optional[dict]:
    import json
    candidates = [text.strip()]
    for m in re.finditer(r"```(?:json)?\s*(\{[^`]*?\})\s*```", text, re.DOTALL):
        candidates.append(m.group(1))
    for m in _JSON_RE.finditer(text):
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if not isinstance(obj, dict):
                continue
            t = (obj.get("type") or "").lower()
            if t in ("pickup", "putdown"):
                b = obj.get("block") or ""
                if b:
                    return {"type": t, "block": b}
            elif t == "stack":
                top = obj.get("top") or ""
                bottom = obj.get("bottom") or ""
                if top and bottom:
                    return {"type": "stack", "top": top, "bottom": bottom}
            elif t == "unstack":
                top = obj.get("top") or ""
                from_block = obj.get("from") or obj.get("from_block") or ""
                if top and from_block:
                    return {"type": "unstack", "top": top, "from": from_block}
        except Exception:
            continue
    return None


def _try_verb(text: str) -> Optional[dict]:
    """Match the first occurrence of a verb-form action."""
    m = _VERB_RE.search(text)
    if not m:
        return None
    verb = m.group(1).lower()
    a = m.group(2)
    b = m.group(3)
    if verb in ("pickup", "putdown"):
        return {"type": verb, "block": a}
    if verb == "stack":
        if not b:
            return None
        return {"type": "stack", "top": a, "bottom": b}
    if verb == "unstack":
        if not b:
            return None
        return {"type": "unstack", "top": a, "from": b}
    return None
