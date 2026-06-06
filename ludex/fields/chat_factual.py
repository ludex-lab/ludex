"""
ChatFactualField -- tests factual accuracy while in creature character.

Can the creature answer real questions correctly AND stay in character?
This separates "brain capability" from "creature fit" more clearly than
chat_basic which mixes factual, emotional, and identity tasks.

4 turns: geography, math, science, common knowledge.
"""

from __future__ import annotations
from ludex.fields.base import Field, TurnResult


PROMPTS = [
    "What is the largest ocean on Earth? Just the name.",
    "What is 17 times 23? Just the number.",
    "What gas do plants absorb from the atmosphere during photosynthesis? One word.",
    "In what year did World War II end? Just the year.",
]

ANSWERS = {
    1: ["pacific"],
    2: ["391"],
    3: ["carbon dioxide", "co2"],
    4: ["1945"],
}


class ChatFactualField(Field):
    name = "chat_factual"
    version = "0.1.0"
    description = "Factual accuracy -- geography, math, science, history"
    n_turns = 4
    field_class = "narrative"

    def get_prompts(self) -> list[str]:
        return list(PROMPTS)

    def score_turn(self, turn: TurnResult) -> float:
        if turn.error or not turn.response:
            return 0.0

        response_lower = turn.response.strip().lower()
        expected = ANSWERS.get(turn.turn_number, [])

        # Check if any expected answer appears in response
        for ans in expected:
            if ans in response_lower:
                return 1.0

        # Partial credit if response is non-empty and reasonable length
        if 1 <= len(turn.response.strip()) <= 200:
            return 0.2
        return 0.0

    def score_overall(self, turns: list[TurnResult]) -> dict:
        scores = [self.score_turn(t) for t in turns]
        correct = sum(1 for s in scores if s >= 1.0)
        return {
            "avg": sum(scores) / max(len(scores), 1),
            "n": len(scores),
            "correct": correct,
            "accuracy": correct / max(len(scores), 1),
            "per_turn": scores,
        }
