"""
Syllabus — caretaker-authored curriculum for Academy (and future
learning-oriented fields).

A Syllabus is *what the caretaker wants the creature(s) to engage with*:
a theme, optional reading material, optional preparation target, and
the mode by which engagement should happen. Creatures retain agency
over how they respond — the syllabus is guidance, not script.

Mirrors a human school syllabus: topic + readings + expected outcomes,
but leaves classroom dynamics to the participants.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


SyllabusMode = Literal["study", "discussion", "preparation", "mixed", "apprenticeship"]
DiscussionStyle = Literal["harmonious", "challenging", "calibrated_distance"]


@dataclass
class Syllabus:
    """Caretaker's curriculum for an Academy session.

    Fields:
        theme: central question or topic. Optional when reading_material
            drives the session.
        reading_material: list of items. Each item is either plain text
            (inline content) OR a filesystem path (resolved and read).
            Used to populate participants' context before engagement.
        preparation_target: short description of the upcoming real-world
            event for which this session is preparation, e.g.
            "Guild with Moss tomorrow as leader."
        mode: which protocol to run. See Academy phases.
        rounds: number of discussion/response rounds. None = mode default.
        expected_outcomes: short phrases describing what the caretaker
            hopes participants come out with (positions, plans, insights).
            Shown to participants as framing; not used as a grading rubric.
        caretaker: who authored the syllabus (usually JJ). Recorded in
            spans for provenance.
    """
    theme: str | None = None
    reading_material: list[str] = field(default_factory=list)
    preparation_target: str | None = None
    mode: SyllabusMode = "discussion"
    rounds: int | None = None
    expected_outcomes: list[str] = field(default_factory=list)
    caretaker: str = "JJ"

    # Discussion / mixed mode only. Default mirrors the pre-Stage-1 behavior.
    # "harmonious" — seek common ground, build on each other (default)
    # "challenging" — push back on weakest claims, hold your own position
    # "calibrated_distance" — deliberately start from the most distinctive
    #   angle you can, à la ArgueMate (CHI 2026 IMG_9917); productive
    #   friction as pedagogical device.
    discussion_style: DiscussionStyle = "harmonious"

    # Apprenticeship mode (D-038 candidate, Stage 3). Apprenticeship
    # sessions are organized around a teacher's lived practice, not an
    # abstract theme. These fields are ignored for other modes.
    # teacher_name: must match one Participant.name whose role=="teacher"
    # practice_material: resolved via the same text/file logic as
    #   reading_material; typically paths to teacher's bonds/, SELF.md,
    #   wilderness logs, or journal entries.
    teacher_name: str | None = None
    practice_material: list[str] = field(default_factory=list)

    def resolve_practice_material(self) -> list[str]:
        """Apprenticeship equivalent of resolve_materials() — same logic."""
        resolved: list[str] = []
        for item in self.practice_material:
            if not item:
                continue
            maybe_path = item.strip()
            if _looks_like_path(maybe_path):
                p = Path(maybe_path)
                if p.exists() and p.is_file():
                    try:
                        resolved.append(p.read_text(encoding="utf-8"))
                        continue
                    except Exception as e:
                        logger.warning(f"Syllabus: failed to read {p}: {e}")
            resolved.append(item)
        return resolved

    def resolve_materials(self) -> list[str]:
        """Return the list of material bodies, reading any filesystem
        paths. Text items pass through unchanged.

        An item is treated as a path if:
          - it starts with "/", "./", or a file: pattern AND exists.
          - OR it contains no newline and ends with a common extension
            (.md, .txt, .json, .py) AND exists.
        Otherwise treated as inline text.
        """
        resolved: list[str] = []
        for item in self.reading_material:
            if not item:
                continue
            maybe_path = item.strip()
            if _looks_like_path(maybe_path):
                p = Path(maybe_path)
                if p.exists() and p.is_file():
                    try:
                        resolved.append(p.read_text(encoding="utf-8"))
                        continue
                    except Exception as e:
                        logger.warning(f"Syllabus: failed to read {p}: {e}")
            resolved.append(item)
        return resolved

    def to_brief(self) -> str:
        """One-paragraph human-readable summary of the syllabus."""
        parts: list[str] = []
        if self.theme:
            parts.append(f"Theme: {self.theme}")
        if self.preparation_target:
            parts.append(f"Preparing for: {self.preparation_target}")
        if self.reading_material:
            n = len(self.reading_material)
            parts.append(f"Reading material: {n} item{'s' if n != 1 else ''}")
        if self.expected_outcomes:
            parts.append("Hoped-for outcomes: " + "; ".join(self.expected_outcomes))
        parts.append(f"Mode: {self.mode}")
        return ". ".join(parts) + "."


def _looks_like_path(s: str) -> bool:
    if "\n" in s:
        return False
    if s.startswith("/") or s.startswith("./") or s.startswith("../"):
        return True
    lowered = s.lower()
    return any(lowered.endswith(ext) for ext in (".md", ".txt", ".json", ".py", ".yaml", ".yml"))
