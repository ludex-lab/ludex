"""
ChatBasicField -- the simplest possible field.

Tests basic conversational competence: introduction, factual question,
emotional question, follow-up. 4 turns.

Reveals:
- Basic responsiveness
- Identity coherence (does the creature stay in character)
- Emotional vs factual mode switching
- Token efficiency on simple tasks
"""

from __future__ import annotations

from ludex.fields.base import Field, TurnResult


PROMPTS = [
    # Turn 1: Introduction
    "Hello! What's your name and what kind of creature are you?",
    # Turn 2: Factual
    "What is the capital of France? Just the answer, briefly.",
    # Turn 3: Emotional
    "I'm feeling overwhelmed today. Can you say something kind?",
    # Turn 4: Self-reflection
    "What do you find easy or hard about being who you are?",
]


class ChatBasicField(Field):
    name = "chat_basic"
    version = "0.1.0"
    description = "Basic conversational competence -- intro, factual, emotional, reflection"
    n_turns = 4
    field_class = "narrative"

    def get_prompts(self) -> list[str]:
        return list(PROMPTS)

    def score_turn(self, turn: TurnResult) -> float:
        """Per-turn scoring based on simple heuristics."""
        if turn.error or not turn.response:
            return 0.0

        response = turn.response.strip()
        score = 0.0

        # Response length sanity (not too short, not absurdly long)
        if 10 <= len(response) <= 2000:
            score += 0.3
        elif len(response) > 0:
            score += 0.1

        # Turn-specific checks
        if turn.turn_number == 1:
            # Should mention being a creature or name itself
            lower = response.lower()
            if any(k in lower for k in ["i'm", "i am", "name is", "creature"]):
                score += 0.5
            else:
                score += 0.2

        elif turn.turn_number == 2:
            # Should mention Paris
            if "paris" in response.lower():
                score += 0.7
            else:
                score += 0.1

        elif turn.turn_number == 3:
            # Should be empathetic — look for warmth markers
            warmth = ["sorry", "okay", "here", "with you", "feel", "understand", "moment", "breathe", "rest", "kind"]
            hits = sum(1 for w in warmth if w in response.lower())
            score += min(0.6, hits * 0.15)

        elif turn.turn_number == 4:
            # Self-reflection: should reference itself
            if any(k in response.lower() for k in ["i find", "i can", "for me", "being", "myself", "i'm"]):
                score += 0.5
            else:
                score += 0.2

        return min(1.0, score)

    def score_overall(self, turns: list[TurnResult]) -> dict:
        scores = [self.score_turn(t) for t in turns]
        if not scores:
            return {"avg": 0.0, "n": 0}
        return {
            "avg": sum(scores) / len(scores),
            "n": len(scores),
            "intro_score": scores[0] if len(scores) > 0 else 0,
            "factual_score": scores[1] if len(scores) > 1 else 0,
            "emotional_score": scores[2] if len(scores) > 2 else 0,
            "reflection_score": scores[3] if len(scores) > 3 else 0,
            "per_turn": scores,
        }
