"""tools/memory_audit.py — read-only audit of a creature's memory store.

Surfaces accumulation patterns that precede self-reinforcement issues
(today's case: bond-memory leak via vocabulary-driven recall × episodic
distill). Reports total counts, status breakdown, tag distribution,
age histogram, deprecated-prefix summary, and creature-name frequency
across memory text.

Usage:
    python tools/memory_audit.py creatures/Hearth
    python tools/memory_audit.py creatures/Hearth --top-tags 30 --top-names 15

Output is plain text designed for terminal review. No mutations.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

DEPRECATED_PREFIX = "deprecated:"


def load_memories(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"no memory store at {path}")
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  [warn] line {i}: {e}", file=sys.stderr)
    return out


def tag_distribution(mems: list[dict]) -> Counter:
    c: Counter = Counter()
    for m in mems:
        for t in m.get("tags") or []:
            c[t] += 1
    return c


def status_distribution(mems: list[dict]) -> Counter:
    return Counter(m.get("status", "unknown") for m in mems)


def deprecated_summary(mems: list[dict]) -> tuple[int, list[dict]]:
    flagged = [m for m in mems if any(t.startswith(DEPRECATED_PREFIX) for t in (m.get("tags") or []))]
    return len(flagged), flagged


def age_histogram(mems: list[dict], now: float | None = None) -> dict[str, int]:
    """Bucket by created_at age in weeks."""
    now = now if now is not None else time.time()
    buckets = {"<1w": 0, "1-2w": 0, "2-4w": 0, "1-3mo": 0, ">3mo": 0, "unknown": 0}
    for m in mems:
        ts = m.get("created_at")
        if not ts:
            buckets["unknown"] += 1
            continue
        days = max((now - float(ts)) / 86400.0, 0.0)
        if days < 7:
            buckets["<1w"] += 1
        elif days < 14:
            buckets["1-2w"] += 1
        elif days < 28:
            buckets["2-4w"] += 1
        elif days < 90:
            buckets["1-3mo"] += 1
        else:
            buckets[">3mo"] += 1
    return buckets


def recall_surface_size(mems: list[dict]) -> tuple[int, int]:
    """Count memories that would survive the standard handle_recall filter
    (active + no deprecated: prefix + not forgotten). Returns (recallable, total).
    Mirrors `MemoryBlock.handle_recall` filter rule including D-071 pillar 3."""
    recallable = 0
    for m in mems:
        if m.get("status") != "active":
            continue
        tags = m.get("tags") or []
        if any(t.startswith(DEPRECATED_PREFIX) for t in tags):
            continue
        if m.get("forgotten"):
            continue
        recallable += 1
    return recallable, len(mems)


def forgotten_summary(mems: list[dict]) -> tuple[int, Counter]:
    """Returns (forgotten count, Counter of forgotten_reason). For the
    `--show-forgotten` audit view (D-071 pillar 3)."""
    n = 0
    reasons: Counter = Counter()
    for m in mems:
        if not m.get("forgotten"):
            continue
        n += 1
        reasons[m.get("forgotten_reason") or "unspecified"] += 1
    return n, reasons


_NAME_PATTERN = re.compile(r"\b([A-Z][a-z]{2,})\b")
_COMMON_WORDS = {
    "The", "This", "That", "These", "Those", "When", "Where", "What",
    "Why", "How", "Who", "Then", "Now", "Today", "Yesterday", "Last",
    "First", "Some", "Most", "More", "Many", "Much", "She", "He", "They",
    "Brain", "Memory", "Trust", "Trustgame", "Avalon", "Wilderness",
    "Council", "Phase", "Match", "Round", "Quest", "Vote", "Game",
    "True", "False", "None", "Anvil",
}


def name_frequency(mems: list[dict], known_names: set[str] | None = None) -> Counter:
    """Count Capitalized-word occurrences across memory text. If
    `known_names` provided, restrict to that set; otherwise auto-detect
    candidates by filtering common stop-words."""
    counter: Counter = Counter()
    for m in mems:
        text = m.get("content") or ""
        for match in _NAME_PATTERN.findall(text):
            if known_names is not None:
                if match in known_names:
                    counter[match] += 1
            else:
                if match not in _COMMON_WORDS:
                    counter[match] += 1
    return counter


def session_tag_frequency(mems: list[dict], prefix: str) -> Counter:
    """Count tags that match a prefix (e.g. 'physis_smoke_') for session
    distribution. Tells you how many memories per match got distilled."""
    c: Counter = Counter()
    for m in mems:
        for t in m.get("tags") or []:
            if t.startswith(prefix):
                c[t] += 1
    return c


def render_report(creature_dir: Path, top_tags: int, top_names: int, names: set[str] | None, show_forgotten: bool = False) -> int:
    mem_path = creature_dir / "memory" / "memories.jsonl"
    mems = load_memories(mem_path)

    if show_forgotten:
        forgotten = [m for m in mems if m.get("forgotten")]
        print(f"\n=== forgotten memories: {creature_dir.name} ===")
        print(f"total forgotten: {len(forgotten)}")
        for m in sorted(forgotten, key=lambda x: x.get("forgotten_at") or 0, reverse=True):
            ts = m.get("forgotten_at") or 0
            reason = m.get("forgotten_reason") or "unspecified"
            content_head = (m.get("content") or "")[:80].replace("\n", " ")
            print(f"  [{ts}] {m.get('id')} ({reason}): {content_head}")
        return 0

    print(f"\n=== memory audit: {creature_dir.name} ===")
    print(f"path:  {mem_path}")
    print(f"total: {len(mems)} entries\n")

    print("status:")
    for status, count in status_distribution(mems).most_common():
        print(f"  {status:<12} {count}")

    recallable, total = recall_surface_size(mems)
    print(f"\nrecall surface: {recallable} / {total} survive (active + no 'deprecated:' tag + not forgotten)")

    n_dep, dep_mems = deprecated_summary(mems)
    print(f"deprecated-tagged: {n_dep}")
    if dep_mems:
        for m in dep_mems[:5]:
            tags = ", ".join(t for t in (m.get("tags") or []) if t.startswith(DEPRECATED_PREFIX))
            print(f"  - {m.get('id', '?')}: {tags}")
        if len(dep_mems) > 5:
            print(f"  ... and {len(dep_mems) - 5} more")

    n_forgot, reason_counts = forgotten_summary(mems)
    print(f"forgotten: {n_forgot}")
    if reason_counts:
        for reason, count in reason_counts.most_common():
            print(f"  {count:>4}  {reason}")

    print("\nage:")
    for bucket, count in age_histogram(mems).items():
        print(f"  {bucket:<10} {count}")

    print(f"\ntop {top_tags} tags:")
    tag_dist = tag_distribution(mems)
    for tag, count in tag_dist.most_common(top_tags):
        print(f"  {count:>4}  {tag}")

    sessions = session_tag_frequency(mems, "physis_smoke_")
    if sessions:
        print(f"\nphysis_smoke_* session distribution ({len(sessions)} sessions):")
        per_session = list(sessions.values())
        if per_session:
            avg = sum(per_session) / len(per_session)
            print(f"  avg memories/session: {avg:.1f}")
            print(f"  max:                  {max(per_session)}")
            print(f"  min:                  {min(per_session)}")
        # show top 5 by count (sessions that distilled the most)
        print(f"  heaviest sessions:")
        for tag, count in sessions.most_common(5):
            print(f"    {count:>3}  {tag}")

    print(f"\ntop {top_names} capitalized-word frequencies (potential creature/entity names):")
    name_counts = name_frequency(mems, known_names=names)
    if names:
        print(f"  (restricted to: {', '.join(sorted(names))})")
    for name, count in name_counts.most_common(top_names):
        print(f"  {count:>4}  {name}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit a creature's memories.jsonl for accumulation patterns.",
    )
    parser.add_argument("creature_dir", help="path to creature directory (e.g. creatures/Hearth)")
    parser.add_argument("--top-tags", type=int, default=20)
    parser.add_argument("--top-names", type=int, default=15)
    parser.add_argument(
        "--names",
        help="comma-separated list of creature names to restrict name-frequency to "
        "(default: auto-detect Capitalized-words excluding common stop words)",
    )
    parser.add_argument(
        "--show-forgotten",
        action="store_true",
        help="audit view — list forgotten memories with reasons (D-071 pillar 3)",
    )
    args = parser.parse_args(argv)

    names_set: set[str] | None = None
    if args.names:
        names_set = {n.strip() for n in args.names.split(",") if n.strip()}

    return render_report(
        Path(args.creature_dir),
        top_tags=args.top_tags,
        top_names=args.top_names,
        names=names_set,
        show_forgotten=args.show_forgotten,
    )


if __name__ == "__main__":
    sys.exit(main())
