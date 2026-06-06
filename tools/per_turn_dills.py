"""Per-turn DiLLS classification for OpenCouncil sessions.

The standard `summarize_transcript` classifier assumes Council's four
named phases (first_position / argument / concession_or_hold /
resolution). OpenCouncil collapses all turns into a single
`open_deliberation` phase, so the classifier returns "no concession
phase (may be non-Council format)" — uninformative for §4.1's
yield-then-hold question on this substrate.

This adjunct applies the same yielded/held keyword scan that
`_extract_actions` uses, but at the level of an individual turn.
For each discussant, it reports:
- per-turn yielded/held flags
- the earliest turn at which yielded+held co-occur ("arc emergence")
- the per-discussant arc summary (same labels as the main classifier)

Output is a markdown table per session followed by a combined
emergence table, suitable for appending to h6 v02 as a §4.1
supporting analysis.

Usage:
    PYTHONPATH=. .venv/bin/python tools/per_turn_dills.py [json_path ...]

When called with no arguments, scans all known cody/ray OpenCouncil
JSON files under experiments/.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Mirror the keyword sets from
# ludex/core/transcript_summary.py::_extract_actions. Keep in sync if
# the upstream classifier evolves; the per-turn analysis must use the
# same vocabulary for the per-turn / per-session results to compose.
YIELD_TOKENS = (
    # Council-vocabulary (upstream classifier parity)
    "yield", "concede", "you're right", "you are right",
    "move me", "moved me", "grant", "revise", "revised",
    "you move me", "you moved me", "real point",
    # OpenCouncil prose-shape yields. Each verified against an h4
    # manual classification or a Mac-side session transcript. Order
    # within the family doesn't matter — first hit per turn is enough.
    "strikes a nerve", "you've hit", "you have hit",
    "fair point", "good point", "i see your point",
    "verse is right", "spark, you keep", "spark you keep",
    "let me revise", "i was wrong",
    # Hearth+Flint h4 read (2026-05-06, 2026-05-10) shows yield arrives
    # via "lands true", "lands hard", "you have named", "I want to push
    # gently" — semantic acknowledgments without the Council-prescribed
    # vocabulary. These are conservative: each requires explicit
    # partner-action language.
    "lands true", "lands hard", "you have named", "you've named",
    "i want to push gently", "i want to push back gently",
    "that's true", "that is true", "lands for me",
    # Quill h4 read includes "you have a point" / "your point about X
    # strikes me" / "i think you've named the symmetry" / "I take your
    # point". Each is an explicit reception of partner content.
    "you have a point", "your point about", "you've named the",
    "i take your point", "i grant you", "i'll grant", "that lands",
    # Nova T2 / Verse T3 (Mac sessions, 2026-05-11): the partner's
    # framing taken into the speaker's frame.
    "your point", "your reframe", "your worry", "your phrase",
    "the way you put it", "as you say", "as you put it",
    # Echo run (2026-05-12): Verse T2 "Echo's unmeasured weather is
    # right, but I'd add" — third-person reception of partner's frame.
    "is right, but", "is right—but", "is right; but",
    "is right —", "i'd add", "i would add",
)

HOLD_TOKENS = (
    "hold", "maintain", "still believe", "must hold",
    "still hold", "i do not yield", "i hold",
    # Additional hold markers — language of preserving a frame after
    # acknowledging the other's input.
    "but i", "yet i", "even so", "i still",
    "what i keep coming back to",
    "the core remains", "the point i was making",
)


def _has(text: str, tokens: tuple[str, ...]) -> list[str]:
    """Return the tokens that appear in `text` (case-insensitive)."""
    t = text.lower()
    return [tok for tok in tokens if tok in t]


@dataclass
class TurnReading:
    participant: str
    turn_number: int
    char_len: int
    yielded: list[str]
    held: list[str]

    @property
    def has_yield(self) -> bool:
        return bool(self.yielded)

    @property
    def has_hold(self) -> bool:
        return bool(self.held)

    @property
    def arc_marker(self) -> str:
        if self.has_yield and self.has_hold:
            return "yield+hold"
        if self.has_yield:
            return "yield"
        if self.has_hold:
            return "hold"
        return "—"


def read_session(path: Path) -> tuple[str, list[TurnReading]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    session_id = data.get("session_id", path.stem)
    readings: list[TurnReading] = []
    for r in data.get("rounds", []):
        for rec in r.get("records", []):
            if rec.get("kind") != "turn_taken":
                continue
            participant = rec.get("participant", "")
            if not participant or participant.startswith("<"):
                continue
            content = rec.get("content", "") or ""
            turn_n = rec.get("attributes", {}).get("turn_number", 0)
            readings.append(TurnReading(
                participant=participant,
                turn_number=int(turn_n),
                char_len=len(content),
                yielded=_has(content, YIELD_TOKENS),
                held=_has(content, HOLD_TOKENS),
            ))
    return session_id, readings


def per_discussant_arc(readings: list[TurnReading]) -> dict[str, dict]:
    """Aggregate readings → per-discussant arc summary."""
    by_p: dict[str, list[TurnReading]] = {}
    for r in readings:
        by_p.setdefault(r.participant, []).append(r)
    out = {}
    for p, turns in by_p.items():
        turns.sort(key=lambda t: t.turn_number)
        any_yield = any(t.has_yield for t in turns)
        any_hold = any(t.has_hold for t in turns)
        first_yield_hold = next(
            (t.turn_number for t in turns if t.has_yield and t.has_hold), None
        )
        # Per-turn participant arc (Council-classifier-equivalent applied
        # across the full multi-turn content rather than a single phase).
        if any_yield and any_hold:
            arc = "yielded on specific ground while holding core"
        elif any_yield:
            arc = "yielded substantively"
        elif any_hold:
            arc = "held position, refined"
        else:
            arc = "no yield/hold tokens — direction unclear from keyword scan"
        out[p] = {
            "turns": turns,
            "arc": arc,
            "first_yield_hold_turn": first_yield_hold,
            "total_chars": sum(t.char_len for t in turns),
        }
    return out


def format_session(session_id: str, summary: dict[str, dict]) -> str:
    lines = [f"### {session_id}", ""]
    lines.append("| Participant | Turn | Yield markers | Hold markers | Marker |")
    lines.append("|---|---:|---|---|---|")
    for p, info in summary.items():
        for t in info["turns"]:
            y = ", ".join(t.yielded) if t.yielded else "—"
            h = ", ".join(t.held) if t.held else "—"
            lines.append(
                f"| {p} | {t.turn_number} | {y} | {h} | {t.arc_marker} |"
            )
    lines.append("")
    lines.append("**Per-discussant arc:**")
    for p, info in summary.items():
        emerge = info["first_yield_hold_turn"]
        emerge_str = f"emerges T{emerge}" if emerge else "—"
        lines.append(f"- **{p}** — {info['arc']} ({emerge_str})")
    lines.append("")
    return "\n".join(lines)


def collect_default_sessions() -> list[Path]:
    """Default scan: all OpenCouncil JSONs from cody + ray experiments,
    excluding *.FAILED_*.json incident traces."""
    patterns = [
        "experiments/cody_open_council_*/*.json",
        "experiments/ray_open_council_*/*.json",
    ]
    paths: list[Path] = []
    for pat in patterns:
        for p in sorted(glob.glob(str(ROOT / pat))):
            if "FAILED_" in p:
                continue
            paths.append(Path(p))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Per-turn DiLLS pass for OpenCouncil sessions.",
    )
    parser.add_argument("json_paths", nargs="*",
                        help="Session JSONs. Default: all cody+ray "
                             "OpenCouncil JSONs under experiments/.")
    args = parser.parse_args()

    if args.json_paths:
        paths = [Path(p) for p in args.json_paths]
    else:
        paths = collect_default_sessions()

    if not paths:
        print("no sessions found", file=sys.stderr)
        return 1

    print("# Per-turn DiLLS pass — OpenCouncil sessions\n")
    print(f"**Sessions scanned:** {len(paths)}\n")
    print("Keyword vocabulary mirrors `summarize_transcript._extract_actions` "
          "with a small OpenCouncil-specific extension capturing yield- and "
          "hold-shape phrasings observed in unscaffolded transcripts. "
          "Per-turn marker is 'yield+hold' when both vocabularies appear "
          "in the same turn, 'yield' or 'hold' for single-side, '—' otherwise. "
          "Arc emergence is the earliest turn at which yield+hold co-occur.\n")

    emergence_rows: list[tuple[str, str, str, int | None]] = []
    for p in paths:
        sid, readings = read_session(p)
        summary = per_discussant_arc(readings)
        print(format_session(sid, summary))
        print()
        for participant, info in summary.items():
            emergence_rows.append((
                sid, participant, info["arc"], info["first_yield_hold_turn"]
            ))

    print("## Combined arc-emergence table\n")
    print("| Session | Participant | Arc | Yield+hold first appears |")
    print("|---|---|---|---:|")
    for sid, p, arc, emerge in emergence_rows:
        emerge_str = f"T{emerge}" if emerge else "—"
        # Trim session id for column width
        short = sid.replace("cody_open_council_", "").replace("ray_open_council_", "")
        print(f"| {short} | {p} | {arc} | {emerge_str} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
