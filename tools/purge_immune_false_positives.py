"""One-time D-071 pillar 3 cleanup of immune false-positives.

Marks every memory tagged `signal:identity_confusion` (and tagged
`immune` + `text_threat` to confirm it's an immune-emitted entry,
not a creature talking *about* identity confusion) as forgotten with
reason `false_positive_purge`. Disk record stays — `handle_recall`
just stops surfacing them, per D-071 design.

Idempotent: already-forgotten entries are skipped. Targets only
narrative/structured creatures (foreign-host stubs have no local
memory store).

Background: 2026-05-01 immune `_scan_text_threat` pattern tightening
removed bare `i cannot` / `i'm not able to` / `i don't actually`
matches that were generating identity_confusion false-positives on
narrative-brain creatures. New patterns won't add more, but 14
historical entries (Wick: 11, Hearth: 2, Quill: 1) sit on disk
inflating recall noise. This pass clears them.

Usage:
    python tools/purge_immune_false_positives.py
    python tools/purge_immune_false_positives.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REASON = "false_positive_purge"
TARGET_SIGNAL = "signal:identity_confusion"


def purge(creature_dir: Path, dry_run: bool = False) -> tuple[int, int]:
    """Returns (purged_now, already_forgotten)."""
    from ludex.core.organism_config import OrganismConfig

    if not (creature_dir / "ludex.yaml").exists():
        return 0, 0
    try:
        cfg = OrganismConfig.load(str(creature_dir))
    except Exception as e:
        print(f"  [{creature_dir.name}] load failed: {e}")
        return 0, 0
    # Silently skip foreign-host stubs — they have no local memory store.
    ok, _ = cfg.check_canonical_host()
    if not ok:
        return 0, 0
    try:
        organism = cfg.build()
    except Exception as e:
        print(f"  [{creature_dir.name}] build failed: {e}")
        return 0, 0

    mem = organism.get_block("memory")
    if mem is None:
        return 0, 0

    purged = 0
    already = 0
    for m in list(mem._memories.values()):
        tags = set(m.tags or [])
        if TARGET_SIGNAL not in tags or "immune" not in tags or "text_threat" not in tags:
            continue
        if m.forgotten:
            already += 1
            continue
        if dry_run:
            purged += 1
            continue
        if mem.handle_mark_forgotten(m.id, REASON):
            purged += 1
    return purged, already


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dir", default="creatures")
    args = parser.parse_args()

    base = Path(args.dir)
    if not base.exists():
        print(f"no {base}/")
        return 1

    print(f"\n{'creature':<14} {'purged':>7} {'already':>9}")
    print("-" * 35)
    total_purged = 0
    total_already = 0
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        purged, already = purge(d, dry_run=args.dry_run)
        if purged or already:
            print(f"{d.name:<14} {purged:>7} {already:>9}")
        total_purged += purged
        total_already += already

    print("-" * 35)
    label = "would purge" if args.dry_run else "purged"
    print(f"{label}: {total_purged}  (already forgotten: {total_already})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
