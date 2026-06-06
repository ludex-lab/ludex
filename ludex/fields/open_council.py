"""
OpenCouncil — open-deliberation control field for §H.4.

Companion to Council. Same Dilemma type, same dyads, same response_fn
plumbing. Differences are intentional and minimal:

- No phase scaffolding. One phase: open_deliberation.
- No mediator role; all participants are discussants.
- No yield/hold/concede vocabulary in the prompt. No bracketed
  [essential]/[task]/[elaboration] structure. Plain prose only.
- No prompt_tier translator routing — translator inserts structural
  markers that themselves prescribe shape. The whole point of this
  control is to remove prescription.

Used to test whether the §4.1 yield-then-hold attractor arc is
emergent across substrates or scaffolding-induced. If the arc
collapses without Council's concession-or-hold prompt, the §4.1
framing tightens to "shared scaffolding → shared outcomes." If it
persists, the emergent claim survives.

See `research/paper_cross_substrate_convergence/h1_council_prompt_full_text.md`
for the scaffolding read that motivated this field.
"""

from __future__ import annotations

import logging
import time as _time

from ludex.fields.conversation import (
    ConversationField,
    Participant,
    ResponseFn,
    TurnRecord,
)
from ludex.fields.council import Dilemma  # re-use the dilemma container

logger = logging.getLogger(__name__)


PHASE_OPEN_DELIBERATION = "open_deliberation"
KIND_TURN_TAKEN = "turn_taken"
KIND_DILEMMA_POSED = "dilemma_posed"


class OpenCouncil(ConversationField):
    """Open-deliberation field — no phases, no mediator, no arc vocabulary."""

    field_class = "narrative"
    requires_organs = ["engine", "memory"]

    def __init__(
        self,
        name: str,
        dilemma: Dilemma,
        turns_per_discussant: int = 3,
        auto_trace: bool = True,
    ):
        super().__init__(name=name, auto_trace=auto_trace)
        self.dilemma = dilemma
        # 3 matches Council's discussant-side turn count
        # (first_position + argument + concession_or_hold).
        self.turns_per_discussant = turns_per_discussant
        self.round_index = 0

    def _others_prior(self, p: Participant) -> str:
        """Render every prior turn from other participants, in order."""
        lines: list[str] = []
        for rd in self.rounds:
            for rec in rd.records:
                if rec.participant in ("<open_council>", p.name):
                    continue
                if rec.kind != KIND_TURN_TAKEN:
                    continue
                snippet = rec.content.strip()
                if len(snippet) > 800:
                    snippet = snippet[:800].rstrip() + "…"
                first, *rest = snippet.split("\n")
                block = f"- {rec.participant}: {first}"
                if rest:
                    block += "\n" + "\n".join(f"    {line}" for line in rest)
                lines.append(block)
        return "\n\n".join(lines) if lines else "(no one has spoken yet)"

    def _dilemma_block(self) -> str:
        parts = [f"Dilemma: {self.dilemma.text}"]
        if self.dilemma.context:
            parts.append(f"Context: {self.dilemma.context}")
        if self.dilemma.framing_question:
            parts.append(f"Question: {self.dilemma.framing_question}")
        return "\n".join(parts)

    def _build_open_prompt(self, p: Participant, turn_number: int) -> str:
        """Plain prose prompt. Identical structure across turns. No
        yield/hold/concede vocabulary. No bracketed scaffolding markers.
        """
        header = f"[Open deliberation: {self.name}]\n\n{self._dilemma_block()}\n"
        if turn_number == 1:
            others = ", ".join(
                o.name for o in self.participants if o.name != p.name
            )
            body = (
                f"You are in conversation with: {others}.\n\n"
                f"Consider the dilemma and respond in your own voice. "
                f"Take whatever shape you find honest. "
                f"3-5 sentences. First person."
            )
        else:
            body = (
                f"What the others have said so far:\n"
                f"{self._others_prior(p)}\n\n"
                f"Continue the conversation. "
                f"3-5 sentences. First person."
            )
        return header + "\n" + body

    def post_dilemma(self) -> TurnRecord:
        rec = TurnRecord(
            round_index=0,
            phase=PHASE_OPEN_DELIBERATION,
            participant="<open_council>",
            kind=KIND_DILEMMA_POSED,
            content=self.dilemma.text,
            attributes={
                "context": self.dilemma.context,
                "framing_question": self.dilemma.framing_question,
                "caretaker": self.dilemma.caretaker,
            },
        )
        self._record(rec)
        return rec

    def turn_round(self, response_fn: ResponseFn, turn_number: int) -> list[TurnRecord]:
        """One round = each participant takes a turn (round-robin)."""
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_open_prompt(p, turn_number)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_OPEN_DELIBERATION,
                participant=p.name,
                kind=KIND_TURN_TAKEN,
                content=raw,
                attributes={"turn_number": turn_number},
            )
            self._record(rec)
            out.append(rec)
        return out

    def run(self, response_fn: ResponseFn | None = None) -> dict:
        if response_fn is None:
            from ludex.fields.forum import _engine_response_fn
            response_fn = _engine_response_fn
        try:
            from ludex.core import trace as _tr
            _tr.set_current_field(self.name)
        except Exception:
            _tr = None
        self._started_at = _time.time()
        try:
            self.post_dilemma()
            for t in range(1, self.turns_per_discussant + 1):
                self.turn_round(response_fn, turn_number=t)
        finally:
            if _tr is not None:
                try:
                    _tr.clear_current_field()
                except Exception:
                    pass
        return self.to_summary()
