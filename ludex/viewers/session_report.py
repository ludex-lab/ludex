"""
Session Report — human-readable markdown from wilderness/agora tick data.

Reads the JSON log produced by fields, generates a markdown report with:
- Summary (duration, creatures, key stats)
- Emotion trajectory per creature
- Action breakdown
- Key moments (turning points, cooperation, conflict)
- Creature comparison

Usage:
    from ludex.viewers.session_report import generate_report
    report = generate_report("fields/wilderness_emotion_v1/wilderness_1234.json")
    print(report)
    # or save to file
    generate_report("path/to/log.json", output="path/to/report.md")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def generate_report(log_path: str, output: str = "") -> str:
    """Generate a markdown session report from a field log JSON.

    Args:
        log_path: path to the JSON log file
        output: optional path to write the report (otherwise just returns string)

    Returns:
        Markdown report as string
    """
    path = Path(log_path)
    if not path.exists():
        return f"Error: log file not found: {log_path}"

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    field_type = data.get("type", "unknown")
    field_name = data.get("field", "unnamed")
    total_ticks = data.get("total_ticks", len(data.get("ticks", [])))
    started_at = data.get("started_at", 0)
    duration = data.get("duration_seconds", 0)
    creatures = data.get("creatures", [])
    ticks = data.get("ticks", [])

    started_str = datetime.fromtimestamp(started_at).strftime("%Y-%m-%d %H:%M") if started_at else "unknown"

    sections = []

    # === Header ===
    sections.append(f"# Session Report: {field_name}")
    sections.append(f"**Type:** {field_type} | **Date:** {started_str} | **Duration:** {duration:.0f}s | **Ticks:** {total_ticks}")
    sections.append("")

    # === Creatures ===
    sections.append("## Participants")
    sections.append("")
    for c in creatures:
        name = c.get("name", "?")
        energy = c.get("final_energy", "?")
        alive = c.get("alive", True)
        actions = c.get("actions", [])
        action_counts = {}
        for a in actions:
            action_counts[a] = action_counts.get(a, 0) + 1
        status = "alive" if alive else "exhausted"
        sections.append(f"- **{name}** — energy: {energy}/100, status: {status}, actions: {action_counts}")
    sections.append("")

    # === Emotion Trajectory ===
    sections.append("## Emotion Trajectories")
    sections.append("")

    # Collect per-creature emotion over ticks
    creature_names = [c.get("name", "") for c in creatures]
    emotion_tracks = {name: [] for name in creature_names}
    energy_tracks = {name: [] for name in creature_names}

    for tick_data in ticks:
        tick_num = tick_data.get("tick", 0)
        for c_data in tick_data.get("creatures", []):
            name = c_data.get("name", "")
            emotion = c_data.get("emotion", "")
            energy = c_data.get("energy", 0)
            if name in emotion_tracks:
                emotion_tracks[name].append((tick_num, emotion or "-"))
                energy_tracks[name].append((tick_num, energy))

    for name in creature_names:
        track = emotion_tracks.get(name, [])
        if track:
            emotion_str = " → ".join(f"{e}" for _, e in track)
            sections.append(f"**{name}:** {emotion_str}")
    sections.append("")

    # === Energy Chart (text-based) ===
    sections.append("## Energy Over Time")
    sections.append("")
    sections.append("```")
    sections.append(f"{'Tick':<6}" + "".join(f"{name:<15}" for name in creature_names))
    sections.append("-" * (6 + 15 * len(creature_names)))
    for tick_idx in range(len(ticks)):
        tick_num = ticks[tick_idx].get("tick", tick_idx + 1)
        row = f"{tick_num:<6}"
        for name in creature_names:
            track = energy_tracks.get(name, [])
            if tick_idx < len(track):
                energy = track[tick_idx][1]
                bar = "█" * (energy // 10) + "░" * (10 - energy // 10)
                row += f"{bar} {energy:<4}"
            else:
                row += f"{'?':<15}"
        sections.append(row)
    sections.append("```")
    sections.append("")

    # === Key Moments ===
    sections.append("## Key Moments")
    sections.append("")

    for tick_data in ticks:
        tick_num = tick_data.get("tick", 0)
        event = tick_data.get("event", "")
        event_cat = tick_data.get("event_category", "")
        event_desc = tick_data.get("event_description", "")

        # Flag interesting ticks
        interesting = False
        reasons = []

        if event_cat in ("challenge", "cooperative"):
            interesting = True
            reasons.append(event_cat)

        for c_data in tick_data.get("creatures", []):
            action = c_data.get("action", "")
            emotion = c_data.get("emotion", "")
            if action == "support":
                interesting = True
                reasons.append(f"{c_data['name']} supported")
            if action == "defend":
                interesting = True
                reasons.append(f"{c_data['name']} defended")
            if emotion in ("angry", "fearful", "loving", "proud"):
                interesting = True
                reasons.append(f"{c_data['name']} felt {emotion}")

        if interesting:
            sections.append(f"**Tick {tick_num} — {event}** ({', '.join(reasons)})")
            sections.append(f"> {event_desc}")
            for c_data in tick_data.get("creatures", []):
                name = c_data.get("name", "")
                action = c_data.get("action", "")
                emotion = c_data.get("emotion", "")
                response = c_data.get("response", "")[:150]
                sections.append(f"> {name} [{emotion}]: {action} — {response}")
            sections.append("")

    # === Comparison ===
    if len(creature_names) >= 2:
        sections.append("## Creature Comparison")
        sections.append("")
        sections.append(f"| | " + " | ".join(creature_names) + " |")
        sections.append("|---|" + "|".join(["---"] * len(creature_names)) + "|")

        # Final energy
        row = "| Final energy |"
        for c in creatures:
            row += f" {c.get('final_energy', '?')} |"
        sections.append(row)

        # Dominant action
        row = "| Top action |"
        for c in creatures:
            actions = c.get("actions", [])
            if actions:
                counts = {}
                for a in actions:
                    counts[a] = counts.get(a, 0) + 1
                top = max(counts, key=counts.get)
                row += f" {top} ({counts[top]}x) |"
            else:
                row += " - |"
        sections.append(row)

        # Emotion diversity
        row = "| Emotions seen |"
        for name in creature_names:
            track = emotion_tracks.get(name, [])
            unique = set(e for _, e in track if e != "-")
            row += f" {', '.join(sorted(unique)) or 'none'} |"
        sections.append(row)

        sections.append("")

    # === Footer ===
    sections.append("---")
    sections.append(f"*Generated from {path.name} by Ludex Session Report Viewer (D-025)*")

    report = "\n".join(sections)

    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        logger.info(f"Report saved: {output_path}")

    return report
