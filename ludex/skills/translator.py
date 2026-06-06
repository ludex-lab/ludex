"""
Skill Translator — convert Ludex skills to brain-native formats.

One source (.ludex/skills/*.yaml), many targets:
- Claude CLI:  .claude/skills/<name>/SKILL.md
- Codex CLI:   AGENTS.md section appended
- Gemini CLI:  GEMINI.md section appended
- Ollama SLMs: system prompt block (via prompt_only_adapter)

See D-018 in docs/design-decisions-log.md.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path

from ludex.skills.loader import LudexSkill

logger = logging.getLogger(__name__)


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
        count = 0
        for skill in self.skills:
            try:
                skill_dir = claude_dir / skill.name
                skill_dir.mkdir(parents=True, exist_ok=True)
                content = self._render_claude_skill_md(skill)
                (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
                count += 1
            except Exception as e:
                logger.warning(f"Failed to write Claude skill {skill.name}: {e}")
        return count

    def _render_claude_skill_md(self, skill: LudexSkill) -> str:
        """Render a Ludex skill as Claude Code SKILL.md format."""
        lines = [
            "---",
            f"name: {skill.name}",
            f"description: {skill.description}",
            "---",
            "",
        ]
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
            lines.append(f"### /{skill.name}")
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
            lines.append(f"- {skill.name}: {desc}")
        return "\n".join(lines)
