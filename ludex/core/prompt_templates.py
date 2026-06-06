"""
Per-brain prompt templates — adapt system prompts to each brain's strengths.

P4 from handoff: different models expect different prompt styles.
Large brains (Claude, o3) handle rich structured prompts well.
Small brains (SLMs) need short, direct instructions.

This module transforms a creature's system prompt to fit its brain,
not by changing the content but by adjusting verbosity, structure,
and emphasis based on brain characteristics.

See docs/design-decisions-log.md D-002 (brain-agnostic).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Brain characteristics database
# Derived from Field Guide observations + onboarding results
BRAIN_PROFILES = {
    # === Large brains: rich prompts OK ===
    "claude_cli": {
        "size": "large",
        "max_system_tokens": 2000,
        "style": "structured",   # handles markdown, sections, bullet points
        "identity_strength": "strong",  # follows identity instructions well
    },
    "claude_sdk": {
        "size": "large",
        "max_system_tokens": 2000,
        "style": "structured",
        "identity_strength": "strong",
    },
    "codex_cli": {
        "size": "large",
        "max_system_tokens": 1500,
        "style": "structured",
        "identity_strength": "moderate",
    },
    "gemini_cli": {
        "size": "large",
        "max_system_tokens": 1000,
        "style": "concise",   # over-follows identity if prompt is too long
        "identity_strength": "over_adherent",  # may ignore user question
    },
    "agy_cli": {
        # Provisional: mirror gemini_cli. Gemini 3.5 Flash may not exhibit
        # the over-adherence pattern observed in 2.5-flash; revisit after
        # in-cohort observation.
        "size": "large",
        "max_system_tokens": 1000,
        "style": "concise",
        "identity_strength": "over_adherent",
    },
    # === Ollama SLMs: keep it short ===
    "ollama": {
        "size": "variable",  # depends on model
        "max_system_tokens": 500,
        "style": "direct",
        "identity_strength": "variable",
    },
}

# Model-specific overrides within ollama
OLLAMA_MODEL_HINTS = {
    # Large Ollama models — can handle more
    "llama3.1:70b": {"size": "large", "max_system_tokens": 1500},
    "qwen3:8b": {"size": "medium", "max_system_tokens": 800},
    "mistral:7b": {"size": "medium", "max_system_tokens": 800},
    "gemma4:e4b": {"size": "medium", "max_system_tokens": 600},
    # Small Ollama models — keep it minimal
    "llama3.2:3b": {"size": "small", "max_system_tokens": 300},
    "qwen2.5:1.5b": {"size": "small", "max_system_tokens": 200},
    "phi4-mini": {"size": "small", "max_system_tokens": 300},
}


def get_brain_profile(provider: str, model: str = "") -> dict:
    """Get brain characteristics for prompt adaptation."""
    profile = BRAIN_PROFILES.get(provider, BRAIN_PROFILES["ollama"]).copy()

    # Apply model-specific overrides for Ollama
    if provider == "ollama" and model:
        override = OLLAMA_MODEL_HINTS.get(model, {})
        profile.update(override)

    return profile


def adapt_system_prompt(
    prompt: str,
    provider: str,
    model: str = "",
    creature_name: str = "",
    organs: list[str] | None = None,
) -> str:
    """Adapt a system prompt to fit the target brain.

    Transforms the prompt based on brain size and style:
    - Large brains: keep full prompt, add structure
    - Medium brains: trim to essentials, moderate structure
    - Small brains: compress to minimal, direct style

    Does NOT change the meaning — only the expression.
    """
    profile = get_brain_profile(provider, model)
    size = profile.get("size", "variable")
    max_tokens = profile.get("max_system_tokens", 500)
    style = profile.get("style", "direct")

    # If prompt is already short enough, return as-is
    estimated_tokens = len(prompt) // 4
    if estimated_tokens <= max_tokens:
        return prompt

    if size == "large":
        # Large brains: keep full prompt
        return prompt

    if size == "small":
        # Small brains: compress aggressively
        return _compress_for_small(prompt, creature_name, organs, max_tokens)

    # Medium brains: moderate compression
    return _compress_for_medium(prompt, creature_name, organs, max_tokens)


def _compress_for_medium(
    prompt: str, creature_name: str, organs: list[str] | None, max_tokens: int
) -> str:
    """Medium compression: keep identity + key instructions, trim detail."""
    lines = prompt.split("\n")
    essential = []
    char_budget = max_tokens * 4  # rough token-to-char

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Always keep identity lines
        if creature_name and creature_name.lower() in stripped.lower():
            essential.append(stripped)
        # Keep short lines (likely instructions)
        elif len(stripped) < 100:
            essential.append(stripped)
        # Truncate long lines
        else:
            essential.append(stripped[:100] + "...")

        if sum(len(l) for l in essential) > char_budget:
            break

    return "\n".join(essential)


def _compress_for_small(
    prompt: str, creature_name: str, organs: list[str] | None, max_tokens: int
) -> str:
    """Aggressive compression for SLMs: identity + one-line instruction."""
    parts = []
    if creature_name:
        parts.append(f"You are {creature_name}.")
    if organs:
        parts.append(f"Organs: {', '.join(organs)}.")
    parts.append("Be brief. Stay in character.")

    result = " ".join(parts)

    # If there's budget left, add first sentence of original prompt
    char_budget = max_tokens * 4
    if len(result) < char_budget - 100:
        first_sentence = prompt.split(".")[0].strip()
        if first_sentence and first_sentence not in result:
            result += " " + first_sentence + "."

    return result[:char_budget]
