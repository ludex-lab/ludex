"""
Skill Loader — read .ludex/skills/*.yaml from habitat.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Try YAML, fallback to JSON
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class LudexSkill:
    """A single Ludex skill definition."""
    name: str
    description: str = ""
    trigger: str = ""
    steps: list[str] = field(default_factory=list)
    prompt: str = ""
    requires_organs: list[str] = field(default_factory=list)
    uses_tools: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> LudexSkill:
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            trigger=data.get("trigger", ""),
            steps=data.get("steps", []),
            prompt=data.get("prompt", ""),
            requires_organs=data.get("requires_organs", []),
            uses_tools=data.get("uses_tools", []),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "trigger": self.trigger,
            "steps": self.steps,
            "prompt": self.prompt,
            "requires_organs": self.requires_organs,
            "uses_tools": self.uses_tools,
        }


def load_skills(habitat_dir: str) -> list[LudexSkill]:
    """Load all skills from .ludex/skills/ in the habitat directory.

    Returns list of LudexSkill, empty if no skills found.
    """
    skills_dir = Path(habitat_dir) / ".ludex" / "skills"
    if not skills_dir.is_dir():
        return []

    skills = []
    for path in sorted(skills_dir.glob("*.yaml")) + sorted(skills_dir.glob("*.yml")):
        try:
            skill = _load_skill_file(path)
            if skill:
                skills.append(skill)
        except Exception as e:
            logger.warning(f"Failed to load skill {path.name}: {e}")

    # Also try JSON fallback
    for path in sorted(skills_dir.glob("*.json")):
        try:
            skill = _load_skill_file(path)
            if skill:
                skills.append(skill)
        except Exception as e:
            logger.warning(f"Failed to load skill {path.name}: {e}")

    return skills


def _load_skill_file(path: Path) -> LudexSkill | None:
    """Load a single skill file (YAML or JSON)."""
    import json

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return None

    if path.suffix in (".yaml", ".yml"):
        if not HAS_YAML:
            logger.warning(f"Cannot load {path.name}: pyyaml not installed, use JSON instead")
            return None
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)

    if not isinstance(data, dict) or not data.get("name"):
        return None

    return LudexSkill.from_dict(data)
