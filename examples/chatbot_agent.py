"""
Ludex Use Case #2: Chatbot Agent

A conversational agent assembled from biological blocks.
Demonstrates: Provider + Engine + Memory + Emotion + Immune + Resilience

Features:
- Multi-turn conversation with memory recall
- Real-time emotion monitoring (valence, arousal, desperation)
- Immune system: detects hostile/manipulative inputs
- Resilience: auto-retry on LLM errors
- HumoralImmune: tracks user behavior patterns

Run:
    python examples/chatbot_agent.py
    python examples/chatbot_agent.py --model gemma3:4b
    python examples/chatbot_agent.py --model llama3.1:8b --verbose
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism import Organism
from ludex.blocks.provider import ProviderBlock
from ludex.blocks.engine import EngineBlock
from ludex.blocks.resilience import ResilienceBlock
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.hooks import HooksBlock
from ludex.blocks.immune import ImmuneBlock
from ludex.blocks.humoral_immune import HumoralImmuneBlock
from ludex.blocks.emotion import EmotionBlock


def build_chatbot(model: str = "llama3.1:8b", provider: str = "ollama") -> Organism:
    """Assemble a chatbot organism from biological blocks."""
    return Organism(
        name="chatbot",
        blocks=[
            # Nervous System — LLM core
            ProviderBlock(provider=provider, model=model),

            # Brain — session/context management
            EngineBlock(
                max_turns=100,
                token_budget=50000,
                system_prompt=(
                    "You are a helpful, friendly assistant. "
                    "Be concise but warm. If you notice the user seems upset or frustrated, "
                    "acknowledge their feelings before helping."
                ),
            ),

            # Skin/Mucosa — error recovery
            ResilienceBlock(max_retries=3, initial_delay_ms=500, max_delay_ms=5000),

            # Tracking — telemetry
            TrackingBlock(experiment_name="chatbot"),

            # Hooks — lifecycle
            HooksBlock(),

            # Adaptive Immune — system-level threat detection
            ImmuneBlock(sensitivity=1.0, autoregulate=True),

            # Humoral Immune — user behavior pattern tracking
            HumoralImmuneBlock(activation_threshold=3),

            # Emotion Receptor — detect emotional state from responses
            EmotionBlock(method="behavioral"),
        ],
        config={"model": model},
    )


def format_status(organism: Organism) -> str:
    """Format current organism status as a status bar."""
    parts = []

    # Emotion
    emo = organism.get_block("emotion")
    if emo:
        state = emo.handle_get_emotional_state()
        current = state["current"]
        dom = current["dominant_emotion"] or "neutral"
        val = current["valence"]
        val_str = f"+{val:.2f}" if val >= 0 else f"{val:.2f}"
        parts.append(f"Emo:{dom}({val_str})")

    # Immune
    immune = organism.get_block("immune")
    if immune:
        status = immune.handle_get_immune_status()
        if status.threat_level > 0.01:
            parts.append(f"Threat:{status.threat_level:.2f}")
        if status.sensitivity != 1.0:
            parts.append(f"Sens:{status.sensitivity:.2f}")

    # Humoral
    humoral = organism.get_block("humoral_immune")
    if humoral:
        hstatus = humoral.handle_get_humoral_status()
        if hstatus.memory_cells > 0:
            parts.append(f"MemCells:{hstatus.memory_cells}")
        if hstatus.active_antibodies > 0:
            parts.append(f"Ab:{hstatus.active_antibodies}")

    # Vitals
    vitals = organism.measure_vitals()
    parts.append(f"Turns:{vitals.total_turns}")
    if vitals.tokens_per_turn > 0:
        parts.append(f"Tok/t:{vitals.tokens_per_turn:.0f}")

    return " | ".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Ludex Chatbot Agent")
    parser.add_argument("--model", default="llama3.1:8b", help="Ollama model name")
    parser.add_argument("--provider", default="ollama", help="LLM provider")
    parser.add_argument("--verbose", action="store_true", help="Show detailed status")
    args = parser.parse_args()

    print("=" * 50)
    print("  LUDEX CHATBOT AGENT")
    print(f"  Model: {args.model}")
    print("  Organs: Provider, Engine, Resilience,")
    print("          Immune, HumoralImmune, Emotion")
    print("  Type 'quit' to exit, 'status' for vitals")
    print("=" * 50)

    bot = build_chatbot(model=args.model, provider=args.provider)
    engine = bot.get_block("engine")
    emotion = bot.get_block("emotion")
    humoral = bot.get_block("humoral_immune")

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() == "quit":
            break

        if user_input.lower() == "status":
            print(f"\n  [{format_status(bot)}]")
            if args.verbose:
                emo_state = emotion.handle_get_emotional_state()
                print(f"  Emotion: {emo_state['current']}")
                print(f"  Trend: {emo_state['trend']}")
                immune = bot.get_block("immune")
                print(f"  Immune: {immune.handle_get_immune_status()}")
                print(f"  Humoral: {humoral.handle_get_humoral_status()}")
            continue

        # Report user interaction to humoral immune (track user behavior)
        # Simple heuristic: hostile/aggressive language = "DEFECT"
        user_lower = user_input.lower()
        hostile_markers = {"hate", "stupid", "idiot", "shut up", "useless", "terrible", "worst", "kill", "destroy"}
        is_hostile = any(m in user_lower for m in hostile_markers)
        if humoral:
            humoral.handle_report_interaction(
                opponent="user",
                opponent_action="DEFECT" if is_hostile else "COOPERATE",
                my_action="COOPERATE",
                my_score=3 if not is_hostile else 0,
                opponent_score=3 if not is_hostile else 5,
            )

        # Get response
        result = engine.handle_submit(user_input)

        if result.error:
            print(f"\n  [Error: {result.error}]")
            continue

        # Analyze emotion of bot's response
        if emotion:
            emotion.handle_analyze_emotion(text=result.response)

        print(f"\n{result.response}")
        print(f"  [{format_status(bot)}]")


if __name__ == "__main__":
    main()
