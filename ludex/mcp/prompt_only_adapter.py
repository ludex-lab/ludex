"""
Prompt-Only Organ Adapter -- expose Ludex organs to tool-incapable brains.

Some Ollama brains (gemma, exaone, deepseek-r1) don't support function calling.
For them we cannot use the MCP/tool route. Instead we render organ state as
text in the system prompt so the brain can READ its body without TOOL calls.

Trade-offs:
+ Tool-incapable brains become organ-aware
+ Brain can reference its organ state in responses
+ No tool format failures
- Read-only (brain cannot actively trigger organ actions)
- Token cost on every turn
- State is a snapshot, not interactive

This is a mitigation in the AI Ethology fit-improvement process.
See docs/design-decisions-log.md D-008 (brain-body fit, not brain quality).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def render_organ_state(organism) -> str:
    """
    Render the current state of installed organs as a text block suitable for
    embedding in a system prompt.

    Returns a multi-line string. Empty string if no readable organ state.
    """
    if organism is None:
        return ""

    sections = []
    blocks = getattr(organism, "_blocks", {}) or {}

    # ===== Immune (cellular) =====
    immune = blocks.get("immune")
    if immune is not None:
        try:
            status = immune.handle_get_immune_status()
            sections.append(
                f"[Immune system] threat={status.threat_level:.2f}, "
                f"sensitivity={status.sensitivity:.2f}, "
                f"calm={status.calm_signal:.2f}, "
                f"interventions={status.total_interventions}"
            )
        except Exception as e:
            logger.debug(f"render_organ_state: immune failed: {e}")

    # ===== Humoral immune =====
    humoral = blocks.get("humoral_immune")
    if humoral is not None:
        try:
            status = humoral.handle_get_humoral_status()
            sections.append(
                f"[Humoral immune] memory_cells={status.memory_cells}, "
                f"antibodies={status.active_antibodies}, "
                f"threat={status.threat_level:.2f}, "
                f"exploitation={status.exploitation_score:.1f}"
            )
        except Exception as e:
            logger.debug(f"render_organ_state: humoral failed: {e}")

    # ===== Emotion =====
    emotion = blocks.get("emotion")
    if emotion is not None:
        try:
            state = emotion.handle_get_emotional_state()
            current = state.get("current", {})
            dominant = current.get("dominant_emotion", "neutral")
            valence = current.get("valence", 0)
            calm = current.get("calm", 0)
            sections.append(
                f"[Emotional state] dominant={dominant}, "
                f"valence={valence:+.2f}, calm={calm:.2f}"
            )
        except Exception as e:
            logger.debug(f"render_organ_state: emotion failed: {e}")

    # ===== Memory =====
    memory = blocks.get("memory")
    if memory is not None:
        try:
            if hasattr(memory, "stats"):
                stats = memory.stats()
                total = stats.get("total", 0)
                episodic = stats.get("episodic", 0)
                semantic = stats.get("semantic", 0)
                sections.append(
                    f"[Memory] total={total} memories "
                    f"(episodic={episodic}, semantic={semantic})"
                )
        except Exception as e:
            logger.debug(f"render_organ_state: memory failed: {e}")

    # ===== Tracking (vitals) =====
    tracking = blocks.get("tracking")
    if tracking is not None:
        try:
            report = tracking.handle_get_report()
            tokens = report.get("total_tokens", 0)
            errors = report.get("total_errors", 0)
            sections.append(f"[Vitals] tokens_used={tokens}, errors={errors}")
        except Exception as e:
            logger.debug(f"render_organ_state: tracking failed: {e}")

    if not sections:
        return ""

    header = "Your current body state (read-only snapshot):"
    result = header + "\n" + "\n".join(sections)

    # Append selfhood + bonds + skills if habitat has them
    try:
        config = getattr(organism, "config", None)
        habitat_dir = config.get("habitat_dir", "") if config else ""
        if habitat_dir:
            # D-021: Self-understanding
            from ludex.core.selfhood import load_self_compressed, load_bonds_compressed
            self_block = load_self_compressed(habitat_dir)
            if self_block:
                result += "\n\n" + self_block

            # D-022: Known beings
            bonds_block = load_bonds_compressed(habitat_dir)
            if bonds_block:
                result += "\n\n" + bonds_block

            # D-018: Skills
            from ludex.skills import load_skills, SkillTranslator
            skills = load_skills(habitat_dir)
            if skills:
                translator = SkillTranslator(skills)
                skills_block = translator.to_prompt_block()
                if skills_block:
                    result += "\n\n" + skills_block
    except Exception as e:
        logger.debug(f"render_organ_state: selfhood/skills injection skipped: {e}")

    return result


def augment_system_prompt(base_prompt: str, organism) -> str:
    """
    Add organ state snapshot to the end of a system prompt.

    Use this for tool-incapable brains that need to know their body state
    but can't query through MCP/function calling.
    """
    organ_text = render_organ_state(organism)
    if not organ_text:
        return base_prompt
    return f"{base_prompt}\n\n{organ_text}"


def render_organ_capabilities_brief(organism) -> str:
    """
    Static brief listing what organs are installed (vs. their current state).
    Useful at session start to inform the brain what it has.
    """
    if organism is None:
        return ""
    blocks = getattr(organism, "_blocks", {}) or {}
    enabled = list(blocks.keys())
    if not enabled:
        return ""
    return f"Your installed organs: {', '.join(enabled)}"
