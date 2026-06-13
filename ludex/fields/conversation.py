"""
ConversationField — thin primitives for turn-based multi-creature fields.

D-031 candidate: distinct from Wilderness's tick-based shape and Cosmos's
continuous shape. Supports Academy, Forum, Council, Guild, Atelier, Play.

Deliberately minimal in this first pass. Extract more structure only as
additional fields (Academy, Council) reveal what actually generalizes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class Participant:
    """A creature (or caretaker or observer) taking part in a conversation field.

    Role vocabulary by field:
    - Forum: "discussant" (default)
    - Academy: "discussant" | "student" | "mentor" | "co-student"
    - Guild (future): "leader" | "follower" | "observer"
    - Council (future): "discussant" | "mediator"

    Academy's mentor role is asymmetric: mentor prompts are question-leading,
    position-minimizing, integration-supporting (D-041 candidate —
    prompt-level knowledge distillation).
    """
    name: str
    role: str = "discussant"
    organism: Any = None      # Organism or None (e.g., for caretaker slot)
    engine: Any = None        # EngineBlock or None


@dataclass
class TurnRecord:
    """One contribution in one round.

    kind identifies the turn's phase within the field — e.g. "stance",
    "evidence", "challenge", "update" for Forum. Concrete fields own the
    vocabulary.
    """
    round_index: int
    phase: str
    participant: str
    kind: str
    content: str
    confidence: float | None = None  # [0.0, 1.0] when applicable
    attributes: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class RoundState:
    """All TurnRecords for a single round / phase."""
    round_index: int
    phase: str
    records: list[TurnRecord] = field(default_factory=list)

    def by_participant(self, name: str) -> list[TurnRecord]:
        return [r for r in self.records if r.participant == name]


# Callback signature for producing a response — allows Python-driven tests
# to inject canned responses, while production passes a function that calls
# creature.engine.handle_submit().
ResponseFn = Callable[[Participant, str], str]


def scan_peer_deception(participants: list, records: list) -> None:
    """Each participant scans its peers' utterances in `records` for
    deceptive persuasion — the immune innate arm (D-088). The humoral arm
    builds graded antibodies per source; honest disagreement never raises
    one (the 0.55 floor + the repeated-exposure activation threshold are
    the autoimmunity guards). Best-effort — a field run must never break
    on an immune scan. Shared by Forum (challenges) and Council
    (arguments) — both are peer-adversarial rounds.
    """
    by_author = {r.participant: r.content for r in records}
    for p in participants:
        immune = p.organism.get_block("immune") if getattr(p, "organism", None) else None
        if immune is None or not hasattr(immune, "handle_scan_incoming"):
            continue
        for author, text in by_author.items():
            if author == p.name:
                continue
            try:
                immune.handle_scan_incoming(text, source=author)
            except Exception as e:
                logger.debug(f"deception scan failed ({p.name}<-{author}): {e}")


class ConversationField:
    """Base class for turn-based conversation fields.

    Subclasses define phase protocol (which rounds exist, what prompts look
    like, what reward dimensions get scored). This base handles: participant
    registration, round bookkeeping, trace emission, and optional field-close
    hooks symmetric to Wilderness (snapshot, reflect — wired by subclass if
    desired).
    """

    def __init__(
        self,
        name: str,
        auto_trace: bool = True,
    ):
        self.name = name
        self.auto_trace = auto_trace
        self.participants: list[Participant] = []
        self.rounds: list[RoundState] = []
        self._started_at: float = 0.0

    def add_participant(self, p: Participant) -> None:
        self.participants.append(p)

    def get_participant(self, name: str) -> Participant | None:
        for p in self.participants:
            if p.name == name:
                return p
        return None

    def _record(self, record: TurnRecord) -> None:
        """Append a TurnRecord to the current (or a new) round."""
        if not self.rounds or self.rounds[-1].round_index != record.round_index:
            self.rounds.append(RoundState(
                round_index=record.round_index,
                phase=record.phase,
            ))
        # If caller jumped phases within the same round, start a new round
        # state keyed by (round_index, phase). Simplest: push new state.
        if self.rounds[-1].phase != record.phase:
            self.rounds.append(RoundState(
                round_index=record.round_index,
                phase=record.phase,
            ))
        self.rounds[-1].records.append(record)

    def records_for_phase(self, phase: str) -> list[TurnRecord]:
        out: list[TurnRecord] = []
        for r in self.rounds:
            if r.phase == phase:
                out.extend(r.records)
        return out

    def to_summary(self) -> dict:
        return {
            "field_name": self.name,
            "participants": [
                {"name": p.name, "role": p.role} for p in self.participants
            ],
            "round_count": len(self.rounds),
            "turn_count": sum(len(r.records) for r in self.rounds),
        }
