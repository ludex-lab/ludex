"""
Skill Translator — convert Ludex skills to brain-native formats.

One source (.ludex/skills/*.yaml), many targets:
- Claude CLI:  .claude/skills/<name>/SKILL.md
- Codex CLI:   AGENTS.md section appended
- Gemini CLI:  GEMINI.md section appended
- Ollama SLMs: system prompt block (via prompt_only_adapter)

Output conforms to the Agent Skills open standard (name/description constraints,
structured dependency keys). SECURITY: a rendered skill is executable content in
another agent's context — the standard's model is TRUSTED SOURCES ONLY. This
renderer stamps provenance (`ludex_source`) so a consumer can decide trust, but
it does not itself gate on trust; provision from an untrusted creature is a real
attack surface. Only render/provision skills from creatures you trust.

See D-018 in docs/design-decisions-log.md.
"""

from __future__ import annotations

import os
import re
import logging
from pathlib import Path

from ludex.skills.loader import LudexSkill

logger = logging.getLogger(__name__)

# Agents Skill open standard (Anthropic, 2026) constraints — creature skill
# metadata must satisfy these or Codex/Cursor/Gemini adapters reject the skill.
_RESERVED_WORDS = ("anthropic", "claude")
_MAX_DESC = 1024


def _sanitize_skill_name(name: str) -> str:
    """Coerce a skill name to the standard: lowercase, [a-z0-9-] only, no XML
    tags, no reserved words (anthropic/claude), ≤64 chars, no leading/trailing/
    double hyphens. Always returns a valid name (fallback 'skill'). Idempotent —
    an already-compliant name (e.g. 'update-mental-model') is returned unchanged."""
    s = re.sub(r"<[^>]*>", "", (name or "").lower())   # strip XML-ish tags
    s = re.sub(r"[^a-z0-9]+", "-", s)                   # non-alnum → hyphen
    for w in _RESERVED_WORDS:
        s = s.replace(w, "")                            # reserved words forbidden
    s = re.sub(r"-+", "-", s).strip("-")[:64].rstrip("-")
    return s or "skill"


def _skill_description(skill: LudexSkill) -> str:
    """Non-empty what+when description for the standard's Level-1 field — the
    ONLY metadata a triggering agent sees. Folds the trigger ('when') in (Ludex
    historically kept it in a ## Trigger body the agent never reads for
    triggering). ≤1024 chars."""
    what = (skill.description or "").strip()
    if not what and skill.prompt:
        what = skill.prompt.strip().split("\n")[0]
    what = what or f"The {_sanitize_skill_name(skill.name)} skill."
    when = (skill.trigger or "").strip()
    desc = f"{what}  Use when: {when}" if when and "use when" not in what.lower() else what
    if len(desc) > _MAX_DESC:
        desc = desc[:_MAX_DESC - 1].rstrip() + "…"
    return desc


class SkillTranslator:
    """Translate Ludex skills to brain-native formats."""

    def __init__(self, skills: list[LudexSkill]):
        self.skills = skills

    # ----- Claude CLI: native .claude/skills/ -----

    def to_claude_skills(self, habitat_dir: str) -> int:
        """Write .claude/skills/<name>/SKILL.md for each skill.

        Returns number of skills written.
        """
        if not self.skills:
            return 0

        claude_dir = Path(habitat_dir) / ".claude" / "skills"
        source = Path(habitat_dir).name        # the creature this skill came from
        count = 0
        for skill in self.skills:
            try:
                name = _sanitize_skill_name(skill.name)
                if name != skill.name:
                    logger.warning(
                        f"Skill name {skill.name!r} is not Agents-Skill-standard "
                        f"compliant; rendered as {name!r}")
                skill_dir = claude_dir / name
                skill_dir.mkdir(parents=True, exist_ok=True)
                content = self._render_claude_skill_md(skill, source=source)
                (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
                count += 1
            except Exception as e:
                logger.warning(f"Failed to write Claude skill {skill.name}: {e}")
        return count

    def _render_claude_skill_md(self, skill: LudexSkill, source: str = "") -> str:
        """Render a Ludex skill as a standard-compliant Claude SKILL.md.

        Frontmatter: standard fields (name/description) + Ludex extension keys
        (requires_organs/uses_tools/ludex_source) as structured data — the
        standard ignores unknown keys, so these are additive/forward-compatible
        (like the MTI card contract) and machine-parseable by the cargo bridge
        (vs the human ## Required organs prose, kept below).

        ludex_source = the creature the skill came from. A skill is executable
        content in another agent's context (a prompt-injection surface); the
        Agent Skills security model is trusted-sources-only, so provenance must
        travel WITH the skill to let the cargo bridge audit at provision time
        (JJ 2026-07-05). This is the enabler, not the gate — trust enforcement
        (which sources are trusted) is a separate design; content-scanning is
        NOT the control (our immune scans deception, not injection — verified)."""
        lines = [
            "---",
            f"name: {_sanitize_skill_name(skill.name)}",
            f"description: {_skill_description(skill)}",
        ]
        if source:
            lines.append(f"ludex_source: {source}")
        if skill.requires_organs:
            lines.append("requires_organs:")
            lines += [f"  - {o}" for o in skill.requires_organs]
        if skill.uses_tools:
            lines.append("uses_tools:")
            lines += [f"  - {t}" for t in skill.uses_tools]
        lines += ["---", ""]
        if skill.trigger:
            lines += [f"## Trigger", f"{skill.trigger}", ""]
        if skill.steps:
            lines += ["## Steps"]
            for i, step in enumerate(skill.steps, 1):
                lines.append(f"{i}. {step}")
            lines.append("")
        if skill.prompt:
            lines += ["## Instructions", skill.prompt.strip(), ""]
        if skill.requires_organs:
            lines += ["## Required organs (Ludex)"]
            for org in skill.requires_organs:
                lines.append(f"- {org}")
            lines.append("")
        return "\n".join(lines)

    # ----- Identity file append (Codex/Gemini) -----

    def to_identity_section(self) -> str:
        """Render skills as a markdown section for AGENTS.md / GEMINI.md.

        Appended to identity files so CLI brains discover them on startup.
        """
        if not self.skills:
            return ""

        lines = ["", "## Available Skills", ""]
        for skill in self.skills:
            lines.append(f"### /{_sanitize_skill_name(skill.name)}")
            if skill.description:
                lines.append(f"{skill.description}")
            if skill.trigger:
                lines.append(f"**When:** {skill.trigger}")
            if skill.steps:
                steps_text = "; ".join(skill.steps)
                lines.append(f"**Steps:** {steps_text}")
            if skill.prompt:
                # Compact version for identity files
                prompt_compact = skill.prompt.strip().replace("\n", " ")
                if len(prompt_compact) > 200:
                    prompt_compact = prompt_compact[:200] + "..."
                lines.append(f"**Do:** {prompt_compact}")
            lines.append("")
        return "\n".join(lines)

    # ----- System prompt injection (Ollama SLMs) -----

    def to_prompt_block(self) -> str:
        """Render skills as a system prompt block for SLMs.

        Injected via prompt_only_adapter alongside organ state.
        SLMs can't execute multi-step workflows autonomously, but they can
        respond to user requests about these capabilities.
        """
        if not self.skills:
            return ""

        lines = ["[Available actions]"]
        for skill in self.skills:
            desc = skill.description or skill.prompt.strip().split("\n")[0] if skill.prompt else ""
            if len(desc) > 100:
                desc = desc[:100] + "..."
            lines.append(f"- {_sanitize_skill_name(skill.name)}: {desc}")
        return "\n".join(lines)
