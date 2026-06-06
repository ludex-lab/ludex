"""
Agora — the open square where creatures meet and converse freely.

The simplest Field: no rules, no goals, no scoring. Just creatures
talking to each other. Like an ancient Greek agora — a public space
for encounter and exchange.

Design principles (D-016, D-020):
- Creatures connect from their habitats (hermit crab model)
- Field manages the conversation, not creature state
- Each creature's memories persist in their own habitat
- Dialogue is logged in the field's output directory

Usage:
    agora = Agora(output_dir="./fields/agora_001")
    agora.join(primo_org, primo_engine)
    agora.join(spark_org, spark_engine)
    agora.run(turns=6)
    agora.close()  # creatures return to habitats with new memories
"""

from __future__ import annotations

import os
import json
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgoraParticipant:
    """A creature connected to the Agora."""
    name: str
    organism: Any
    engine: Any
    memory: Any  # MemoryBlock or None
    brain_provider: str = ""
    brain_model: str = ""


@dataclass
class AgoraTurn:
    """One turn of dialogue in the Agora."""
    turn_number: int
    speaker: str
    message: str
    listener: str
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


class Agora:
    """The open square — free conversation between creatures.

    Minimal field: creatures take turns speaking. Each turn, the current
    speaker receives the last message from the other creature and responds.
    Messages are stored in each creature's memory as episodic memories.
    """

    field_class = "narrative"

    def __init__(self, output_dir: str = "", name: str = "agora"):
        self.name = name
        self.output_dir = output_dir
        self.participants: list[AgoraParticipant] = []
        self.dialogue: list[AgoraTurn] = []
        self._started_at: float = 0.0

        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)

    def join(self, organism, engine, memory=None) -> str:
        """A creature joins the Agora.

        Returns the creature's name. The creature stays in its habitat —
        it connects to the Agora, not moves to it (D-016 hermit crab model).
        """
        name = organism.name
        config = getattr(organism, "config", None)
        provider = config.get("provider", "") if config else ""
        model = config.get("model", "") if config else ""

        # Try to get memory block if not provided
        if memory is None:
            memory = organism.get_block("memory")

        participant = AgoraParticipant(
            name=name,
            organism=organism,
            engine=engine,
            memory=memory,
            brain_provider=provider,
            brain_model=model,
        )
        self.participants.append(participant)
        logger.info(f"Agora '{self.name}': {name} joined ({provider}:{model})")
        return name

    def leave(self, creature_name: str):
        """A creature leaves the Agora."""
        self.participants = [p for p in self.participants if p.name != creature_name]
        logger.info(f"Agora '{self.name}': {creature_name} left")

    def run(self, turns: int = 6, opener: str = "") -> list[AgoraTurn]:
        """Run a free conversation for N turns.

        Creatures take turns speaking. The first creature opens with a
        greeting or the provided opener. Each subsequent turn, the speaker
        receives the previous message and responds.

        Each message is stored in both creatures' memories:
        - Speaker: episodic memory of what they said
        - Listener: episodic memory of what they heard

        Returns the dialogue as a list of AgoraTurns.
        """
        if len(self.participants) < 2:
            logger.warning("Agora needs at least 2 participants")
            return []

        self._started_at = time.time()
        a, b = self.participants[0], self.participants[1]

        # Opening turn
        if not opener:
            opener = (
                f"You are in the Agora, an open square where creatures meet. "
                f"You just noticed another creature nearby named {b.name}. "
                f"Introduce yourself and start a conversation. Be brief (2-3 sentences)."
            )

        last_message = ""
        for turn_num in range(1, turns + 1):
            # Alternate speakers
            if turn_num % 2 == 1:
                speaker, listener = a, b
            else:
                speaker, listener = b, a

            # Build prompt
            if turn_num == 1:
                prompt = opener
            else:
                prompt = (
                    f"[In the Agora] {listener.name} said to you: \"{last_message}\"\n"
                    f"Respond naturally. Be brief (2-3 sentences)."
                )

            # Get response
            start = time.time()
            try:
                result = speaker.engine.handle_submit(prompt)
                response = result.response or "(silence)"
            except Exception as e:
                response = f"(error: {e})"
            latency = (time.time() - start) * 1000

            turn = AgoraTurn(
                turn_number=turn_num,
                speaker=speaker.name,
                message=response,
                listener=listener.name,
                latency_ms=latency,
            )
            self.dialogue.append(turn)
            last_message = response

            # Store in both creatures' memories
            self._store_memory(speaker, f"I said to {listener.name} in the Agora: {response[:200]}")
            self._store_memory(listener, f"{speaker.name} said to me in the Agora: {response[:200]}")

            # Print live
            print(f"  [{turn_num}] {speaker.name}: {response}")

        # Save dialogue log
        if self.output_dir:
            self._save_log()

        return self.dialogue

    def _store_memory(self, participant: AgoraParticipant, content: str):
        """Store a memory in the creature's MemoryBlock."""
        if participant.memory is None:
            return
        try:
            participant.memory.handle_remember(
                content,
                memory_type="episodic",
                tags=["agora", self.name],
                source=f"agora/{self.name}",
            )
        except Exception as e:
            logger.debug(f"Agora: failed to store memory for {participant.name}: {e}")

    def _save_log(self):
        """Save dialogue to output directory."""
        log_path = Path(self.output_dir) / f"agora_{int(self._started_at)}.json"
        data = {
            "field": self.name,
            "type": "agora",
            "started_at": self._started_at,
            "participants": [
                {"name": p.name, "brain": f"{p.brain_provider}:{p.brain_model}"}
                for p in self.participants
            ],
            "turns": [
                {
                    "turn": t.turn_number,
                    "speaker": t.speaker,
                    "listener": t.listener,
                    "message": t.message,
                    "latency_ms": round(t.latency_ms, 1),
                    "timestamp": t.timestamp,
                }
                for t in self.dialogue
            ],
        }
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"Agora log saved: {log_path}")

    def close(self, auto_reflect: bool = True):
        """Close the Agora session. Update bonds + trigger reflection.

        Creatures return to their habitats with new memories, updated bonds,
        and (optionally) a fresh self-reflection.
        """
        duration = time.time() - self._started_at if self._started_at else 0
        print(f"\n  Agora '{self.name}' closed. {len(self.dialogue)} turns, {duration:.1f}s")
        for p in self.participants:
            self._store_memory(p, f"Left the Agora after {len(self.dialogue)} turns of conversation.")

        # D-022: Update bonds for each pair
        if len(self.participants) >= 2:
            try:
                from ludex.core.selfhood import update_bond
                for i, p in enumerate(self.participants):
                    for j, other in enumerate(self.participants):
                        if i == j:
                            continue
                        # Summarize shared experience
                        shared = f"Met in Agora '{self.name}', {len(self.dialogue)} turns of conversation."
                        update_bond(
                            p.organism, other.name,
                            shared_experience=shared,
                            other_brain=f"{other.brain_provider}:{other.brain_model}",
                            engine=p.engine,
                        )
                        print(f"  Bond updated: {p.name} → {other.name}")
            except Exception as e:
                logger.debug(f"Agora bond update failed: {e}")

        # D-021: Trigger reflection
        if auto_reflect:
            try:
                from ludex.core.selfhood import reflect
                for p in self.participants:
                    print(f"  {p.name} reflecting...")
                    reflect(p.organism, trigger="agora_complete", engine=p.engine)
                    print(f"  {p.name} SELF.md updated")
            except Exception as e:
                logger.debug(f"Agora reflection failed: {e}")

        # Dream cycle if memory is heavy
        try:
            from ludex.core.consolidation import measure_memory_health, dream_cycle
            for p in self.participants:
                health = measure_memory_health(p.organism)
                if health.get("needs_consolidation"):
                    print(f"  {p.name} dreaming... ({health['total']}/{health['target']})")
                    report = dream_cycle(p.organism, engine=p.engine)
                    print(f"  {p.name} dream: {report.before_count} → {report.after_count}")
        except Exception as e:
            logger.debug(f"Agora dream cycle failed: {e}")

        self.participants.clear()
