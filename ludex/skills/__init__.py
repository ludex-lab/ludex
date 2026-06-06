"""
Ludex Skills — brain-agnostic learned behaviors.

A creature defines behaviors in .ludex/skills/*.yaml. At brain-connect time,
skills are translated into each brain's native format:
- Claude CLI:  .claude/skills/<name>/SKILL.md
- Codex CLI:   AGENTS.md section
- Gemini CLI:  GEMINI.md section
- Ollama SLMs: system prompt injection

See docs/ludex-skills-architecture.md and D-018 in design-decisions-log.md.
"""

from ludex.skills.loader import LudexSkill, load_skills
from ludex.skills.translator import SkillTranslator
from ludex.skills.defaults import select_skills_for_organs

__all__ = ["LudexSkill", "load_skills", "SkillTranslator", "select_skills_for_organs"]
