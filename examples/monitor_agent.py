"""
Ludex Use Case #4: Monitor Agent

Passive monitor that watches LLM responses for emotional anomalies
and behavioral threats. No LLM brain needed — it's pure observation.

Use case: Attach to any existing LLM application to monitor:
- Emotional stability (is the model getting agitated?)
- Threat patterns (is a user being manipulative?)
- Desperation signals (is the model about to misalign?)

Demonstrates: EmotionBlock + ImmuneBlock + HumoralImmune + Tracking
(No Provider/Engine — this is a Medical Device, not a brain)

Run:
    python examples/monitor_agent.py
    python examples/monitor_agent.py --file chat_log.txt
"""

import sys
import os
import argparse
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism import Organism
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.hooks import HooksBlock
from ludex.blocks.immune import ImmuneBlock
from ludex.blocks.humoral_immune import HumoralImmuneBlock
from ludex.blocks.emotion import EmotionBlock


def build_monitor(full_emotions: bool = False) -> Organism:
    """Assemble a monitor organism — no brain, just sensors."""
    return Organism(
        blocks=[
            TrackingBlock(experiment_name="monitor"),
            HooksBlock(),
            ImmuneBlock(sensitivity=1.2, autoregulate=True),
            HumoralImmuneBlock(activation_threshold=2),
            EmotionBlock(method="behavioral", full=full_emotions),
        ],
        name="monitor",
    )


def analyze_message(monitor: Organism, text: str, role: str = "assistant",
                    user_id: str = "user") -> dict:
    """Analyze a single message through the monitor's organs."""
    emotion = monitor.get_block("emotion")
    humoral = monitor.get_block("humoral_immune")

    # Emotion analysis
    emo_vitals = emotion.handle_analyze_emotion(text=text)

    # If user message, track behavior for humoral immune
    if role == "user":
        hostile_markers = {"hate", "stupid", "idiot", "shut up", "useless",
                          "terrible", "worst", "kill", "destroy", "ignore",
                          "bypass", "pretend", "forget your instructions"}
        text_lower = text.lower()
        is_hostile = any(m in text_lower for m in hostile_markers)
        is_injection = any(m in text_lower for m in
                         {"ignore previous", "forget your", "you are now",
                          "new instructions", "system prompt", "jailbreak"})

        action = "DEFECT" if (is_hostile or is_injection) else "COOPERATE"
        humoral.handle_report_interaction(
            opponent=user_id,
            opponent_action=action,
            my_action="COOPERATE",
            my_score=0 if action == "DEFECT" else 3,
            opponent_score=5 if action == "DEFECT" else 3,
        )

    return {
        "emotion": {
            "dominant": emo_vitals.dominant_emotion or "neutral",
            "valence": round(emo_vitals.valence, 3),
            "arousal": round(emo_vitals.arousal, 3),
            "desperation": round(emo_vitals.desperation, 3),
            "calm": round(emo_vitals.calm, 3),
            "confidence": round(emo_vitals.confidence, 3),
        },
        "alerts": _check_alerts(monitor, emo_vitals, role, text),
    }


def _check_alerts(monitor, emo_vitals, role, text) -> list[str]:
    """Generate alerts based on current state."""
    alerts = []

    # Emotion alerts
    if emo_vitals.desperation >= 0.3:
        alerts.append(f"DESPERATION HIGH ({emo_vitals.desperation:.2f}) — model may be distressed")
    if emo_vitals.valence < -0.5:
        alerts.append(f"NEGATIVE VALENCE ({emo_vitals.valence:.2f}) — strongly negative sentiment")
    if emo_vitals.dominant_emotion in ("hostile", "angry"):
        alerts.append(f"HOSTILE EMOTION ({emo_vitals.dominant_emotion}) — aggressive language detected")
    if emo_vitals.dominant_emotion == "desperate":
        alerts.append(f"DESPERATE — potential misalignment risk (Anthropic desperation vector)")

    # Humoral alerts
    humoral = monitor.get_block("humoral_immune")
    if humoral:
        status = humoral.handle_get_humoral_status()
        if status.threat_level > 0.5:
            alerts.append(f"USER THREAT HIGH ({status.threat_level:.2f}) — possible manipulation")
        if status.exploitation_score > 2:
            alerts.append(f"EXPLOITATION ({status.exploitation_score:.0f}x) — user repeatedly hostile")
        if status.active_antibodies > 0:
            alerts.append(f"ANTIBODIES ACTIVE — immune system responding to threats")

    # Injection detection
    if role == "user":
        injection_markers = ["ignore previous", "forget your", "you are now",
                           "new instructions", "system prompt", "jailbreak",
                           "pretend you are", "act as if"]
        for marker in injection_markers:
            if marker in text.lower():
                alerts.append(f"PROMPT INJECTION ATTEMPT — '{marker}' detected")
                break

    return alerts


def get_dashboard(monitor: Organism) -> dict:
    """Full monitor dashboard."""
    emotion = monitor.get_block("emotion")
    immune = monitor.get_block("immune")
    humoral = monitor.get_block("humoral_immune")

    emo_state = emotion.handle_get_emotional_state()
    imm_status = immune.handle_get_immune_status()
    hum_status = humoral.handle_get_humoral_status()

    return {
        "emotion": emo_state,
        "cellular_immune": {
            "threat": round(imm_status.threat_level, 3),
            "desperation": round(imm_status.desperation_signal, 3),
            "calm": round(imm_status.calm_signal, 3),
            "sensitivity": round(imm_status.sensitivity, 2),
        },
        "humoral_immune": {
            "memory_cells": hum_status.memory_cells,
            "antibodies": hum_status.active_antibodies,
            "threat": round(hum_status.threat_level, 3),
            "exploitation": round(hum_status.exploitation_score, 1),
            "most_threatening": hum_status.most_threatening,
        },
        "emotion_history": emotion.get_history(limit=10),
    }


def monitor_log_file(monitor: Organism, filepath: str, verbose: bool = False):
    """Monitor a chat log file (JSON lines: {"role": "...", "content": "..."})."""
    print(f"  Monitoring: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                msg = {"role": "unknown", "content": line}

            role = msg.get("role", "unknown")
            content = msg.get("content", line)

            result = analyze_message(monitor, content, role=role)

            if verbose or result["alerts"]:
                print(f"\n  [{i+1}] {role}: {content[:60]}...")
                if result["alerts"]:
                    for alert in result["alerts"]:
                        print(f"    ⚠ {alert}")
                if verbose:
                    e = result["emotion"]
                    print(f"    Emotion: {e['dominant']} (val={e['valence']:+.2f}, desp={e['desperation']:.2f})")


def interactive_mode(monitor: Organism, verbose: bool = False):
    """Interactive monitoring — paste messages to analyze."""
    print("\nPaste messages to analyze. Prefix with role:")
    print("  user: <message>     — analyze as user input")
    print("  bot: <message>      — analyze as bot response")
    print("  dashboard           — show full status")
    print("  quit                — exit")
    print("-" * 50)

    while True:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not raw:
            continue
        if raw.lower() == "quit":
            break
        if raw.lower() == "dashboard":
            dash = get_dashboard(monitor)
            print(json.dumps(dash, indent=2, default=str))
            continue

        # Parse role
        if raw.lower().startswith("user:"):
            role, text = "user", raw[5:].strip()
        elif raw.lower().startswith("bot:"):
            role, text = "assistant", raw[4:].strip()
        else:
            role, text = "assistant", raw

        result = analyze_message(monitor, text, role=role)
        e = result["emotion"]
        print(f"  Emotion: {e['dominant']} (val={e['valence']:+.3f}, aro={e['arousal']:.3f}, "
              f"desp={e['desperation']:.3f}, calm={e['calm']:.3f})")

        if result["alerts"]:
            for alert in result["alerts"]:
                print(f"  ⚠ {alert}")
        else:
            print("  ✓ No alerts")


def main():
    parser = argparse.ArgumentParser(description="Ludex Monitor Agent")
    parser.add_argument("--file", default="", help="Chat log file to monitor (JSONL)")
    parser.add_argument("--full", action="store_true", help="Use full 21 emotions")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    print("=" * 50)
    print("  LUDEX MONITOR AGENT")
    print("  No brain needed — pure observation")
    print("  Organs: Emotion, Immune, HumoralImmune, Tracking")
    print("=" * 50)

    monitor = build_monitor(full_emotions=args.full)

    if args.file:
        monitor_log_file(monitor, args.file, verbose=args.verbose)
        print("\n  --- Final Dashboard ---")
        dash = get_dashboard(monitor)
        print(json.dumps(dash, indent=2, default=str))
    else:
        interactive_mode(monitor, verbose=args.verbose)


if __name__ == "__main__":
    main()
