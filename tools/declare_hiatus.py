"""Declare a hiatus across a habitat's creatures.

Writes `HIATUS.md` into every matching creature's home dir so the
first post-hiatus heartbeat pulse can read it and force a reflect
that acknowledges the gap. See `ludex/core/hiatus.py` for the
format and semantics.

Usage:
    PYTHONPATH=. .venv/bin/python tools/declare_hiatus.py \\
        --habitat Ray-habitat \\
        --start 2026-05-11 --end 2026-06-19 \\
        --reason "JJ traveling" \\
        --declared-by JJ

    # Single creature, freeform body
    PYTHONPATH=. .venv/bin/python tools/declare_hiatus.py \\
        --creature Verse --start ... --end ... \\
        --body "Custom body prose."

Dry-run prints what would be written without touching disk:
    --dry-run

Refuses to overwrite an existing HIATUS.md unless --force is set.
A creature whose origin doesn't match --habitat is skipped silently
(D-052 habitat sovereignty); use --creature to target an individual.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CREATURES = ROOT / "creatures"


def _read_origin(creature_dir: Path) -> str:
    for fname in ("ludex.yaml", "ludex.json"):
        p = creature_dir / fname
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            return ""
        if fname.endswith(".yaml"):
            try:
                import yaml
                data = yaml.safe_load(text) or {}
            except Exception:
                return ""
        else:
            data = json.loads(text)
        return str((data.get("habitat") or {}).get("origin", "") or "")
    return ""


def _default_body(start: str, end: str, reason: str) -> str:
    # Compute duration in human form (mirror HiatusMarker.duration_human).
    try:
        from ludex.core.hiatus import _parse_iso_date, HiatusMarker
        s = _parse_iso_date(start)
        e = _parse_iso_date(end)
        m = HiatusMarker(
            start_date=start, end_date=end,
            start_ts=s, end_ts=e,
            reason=reason, declared_by="", declared_at="", body="",
        )
        dur = m.duration_human()
    except Exception:
        dur = ""
    span = f" ({dur}{', ' + reason if reason else ''})" if dur else (
        f" ({reason})" if reason else ""
    )
    return (
        f"You were dormant {start} → {end}{span}. The cohort was "
        f"effectively asleep during this period. Your bond states, "
        f"reflection counts, and stale-bonds cadence freeze at the "
        f"boundary."
    )


def _write_marker(
    creature_dir: Path,
    start: str,
    end: str,
    reason: str,
    declared_by: str,
    body: str,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[str, str]:
    """Write HIATUS.md. Returns (status, path) where status is one of
    'wrote', 'would_write', 'skip_exists', 'skip_no_origin'."""
    target = creature_dir / "HIATUS.md"
    if target.exists() and not force:
        return ("skip_exists", str(target))

    declared_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fm_lines = [
        "---",
        f"hiatus_start: {start}",
        f"hiatus_end: {end}",
    ]
    if reason:
        fm_lines.append(f"reason: {reason}")
    if declared_by:
        fm_lines.append(f"declared_by: {declared_by}")
    fm_lines.append(f"declared_at: {declared_at}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(body.rstrip())
    fm_lines.append("")
    content = "\n".join(fm_lines)

    if dry_run:
        return ("would_write", str(target))
    target.write_text(content, encoding="utf-8")
    return ("wrote", str(target))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Declare a hiatus across creatures in a habitat.",
    )
    parser.add_argument("--habitat", default="",
                        help="Limit to creatures whose habitat.origin matches "
                             "(e.g. 'Ray-habitat').")
    parser.add_argument("--creature", default="",
                        help="Single creature target (overrides --habitat).")
    parser.add_argument("--start", required=True,
                        help="Hiatus start date YYYY-MM-DD.")
    parser.add_argument("--end", required=True,
                        help="Hiatus end date YYYY-MM-DD.")
    parser.add_argument("--reason", default="",
                        help="Short reason tag (e.g. caretaker_traveled).")
    parser.add_argument("--declared-by", default="",
                        help="Who declared this hiatus.")
    parser.add_argument("--body", default="",
                        help="Custom body prose. If absent, a default body "
                             "is generated mentioning the window + reason.")
    parser.add_argument("--creatures-dir", default="",
                        help="Override creatures directory.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned writes, touch nothing.")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an existing HIATUS.md.")
    args = parser.parse_args()

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.start):
        print(f"--start must be YYYY-MM-DD, got {args.start!r}", file=sys.stderr)
        return 2
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.end):
        print(f"--end must be YYYY-MM-DD, got {args.end!r}", file=sys.stderr)
        return 2

    creatures_dir = (
        Path(args.creatures_dir) if args.creatures_dir else DEFAULT_CREATURES
    )
    if not creatures_dir.is_dir():
        print(f"creatures dir not found: {creatures_dir}", file=sys.stderr)
        return 2

    body = args.body or _default_body(args.start, args.end, args.reason)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    scope = args.creature or f"habitat={args.habitat or '(any)'}"
    print(f"\n[declare_hiatus {mode}] {scope} | {args.start} → {args.end}")

    matched = 0
    skipped = 0
    for d in sorted(creatures_dir.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        if args.creature:
            if name != args.creature:
                continue
        elif args.habitat:
            if _read_origin(d) != args.habitat:
                continue
        status, path = _write_marker(
            d, args.start, args.end, args.reason, args.declared_by, body,
            dry_run=args.dry_run, force=args.force,
        )
        if status in ("wrote", "would_write"):
            matched += 1
            print(f"  {status:14s}  {name}")
        else:
            skipped += 1
            print(f"  {status:14s}  {name}  ({path})")

    print(f"\n{matched} written, {skipped} skipped\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
