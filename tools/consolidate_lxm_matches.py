"""CLI: post-match consolidation of LxM match logs into Ludex bonds.

Dry-run by default. Pass `--commit` to actually write bond events.

Examples:

    # Dry-run one match
    python -m tools.consolidate_lxm_matches \\
        --match-dir /path/to/matches/m3full_avalon_E_7

    # Dry-run all unconsolidated matches under a root, Mac-habitat only
    python -m tools.consolidate_lxm_matches \\
        --matches-root /path/to/matches \\
        --habitat-root Mac-habitat \\
        --filter m3full_avalon

    # Commit (actually write bonds)
    python -m tools.consolidate_lxm_matches \\
        --match-dir /path/to/matches/m3full_avalon_E_7 --commit

See `docs/consolidation-pipeline-design.md` for decisions and scope.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Post-match consolidation — LxM match logs → Ludex bonds",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--match-dir", type=Path,
                        help="Process a single match directory.")
    source.add_argument("--matches-root", type=Path,
                        help="Process all match directories under this root.")

    parser.add_argument("--habitat-root", default="",
                        help="Scope to creatures whose habitat.origin matches "
                             "(e.g. 'Mac-habitat', 'Ray-habitat'). D-052.")
    parser.add_argument("--creatures-dir", type=Path, default=None,
                        help="Override Ludex creatures/ directory location.")
    parser.add_argument("--filter", default="",
                        help="Substring match on match_dir names when using "
                             "--matches-root.")
    parser.add_argument("--commit", action="store_true",
                        help="Actually write bond events. Without this flag, "
                             "run is dry (per consolidation-pipeline-design §safeguards).")
    parser.add_argument("--force", action="store_true",
                        help="Reprocess matches already in consolidation_index.json.")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    from ludex.core.post_match_consolidation import (
        DEFAULT_CREATURES_DIR,
        _list_habitat_creatures,
        consolidate_match,
        consolidate_matches_root,
    )

    creatures_dir = args.creatures_dir or DEFAULT_CREATURES_DIR
    creatures_by_name = _list_habitat_creatures(
        creatures_dir, habitat_root=args.habitat_root,
    )

    mode = "DRY-RUN" if not args.commit else "COMMIT"
    scope = f" habitat={args.habitat_root}" if args.habitat_root else ""
    print(f"\n[consolidate {mode}{scope}] creatures_dir={creatures_dir}")
    print(f"  target creatures: {sorted(creatures_by_name.keys()) or '(none)'}")

    if not creatures_by_name:
        print("  no creatures match the filter — nothing to do")
        return 0

    if args.match_dir:
        reports = consolidate_match(
            args.match_dir, creatures_by_name,
            dry_run=not args.commit, force=args.force,
        )
    else:
        reports = consolidate_matches_root(
            args.matches_root, creatures_by_name,
            dry_run=not args.commit, force=args.force,
            match_filter=args.filter,
        )

    # Summary
    by_action: dict[str, int] = {}
    for r in reports:
        by_action[r.action] = by_action.get(r.action, 0) + 1

    print(f"\n  {len(reports)} report rows; by action: {by_action}")

    # Per-row details (dry-run phrases, errors)
    for r in reports:
        if r.action == "error":
            print(f"  [ERR] {r.creature:<10} {r.match_id:<30} {r.detail}")
        elif r.action == "dry_run":
            print(f"  [DRY] {r.creature:<10} {r.match_id:<30} "
                  f"{r.pair_count} pairs")
            if r.detail and args.verbose:
                print(f"        {r.detail[:500]}")
        elif r.action == "committed":
            print(f"  [OK ] {r.creature:<10} {r.match_id:<30} "
                  f"{r.pair_count} pairs written")
        elif r.action == "skipped_already" and args.verbose:
            print(f"  [skip] {r.creature:<10} {r.match_id:<30} "
                  f"already processed (use --force to override)")

    errors = by_action.get("error", 0)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
