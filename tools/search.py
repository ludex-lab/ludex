"""CLI: FTS5 search over Ludex docs, journal, and joint spec.

Examples:

    # Default: top 10 hits across all sources
    python -m tools.search "bond staleness"

    # Filter by source
    python -m tools.search "heartbeat" --source journal
    python -m tools.search "D-052" --source design_log

    # Phrase and boolean queries (FTS5 syntax)
    python -m tools.search '"register context fitness"'
    python -m tools.search "narrative identity AND heartbeat"
    python -m tools.search "voice shell NOT refusal"

    # Rebuild the index from current repo state
    python -m tools.search --rebuild

    # Show stats
    python -m tools.search --stats
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="FTS5 search over Ludex docs / journal / spec.",
    )
    parser.add_argument("query", nargs="?",
                        help="FTS5 query. Phrase: \"exact phrase\". "
                             "Boolean: AND/OR/NOT. Prefix: word*. "
                             "Column: title:bond.")
    parser.add_argument("--source", default=None,
                        choices=["design_log", "joint_spec", "journal",
                                 "sub_system", "review"],
                        help="Restrict results to one source.")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max results (default 10).")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild the index from current repo, then exit.")
    parser.add_argument("--stats", action="store_true",
                        help="Print per-source row counts, then exit.")
    parser.add_argument("--db", type=Path, default=None,
                        help="Override index DB path.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    from ludex.search.indexer import DEFAULT_DB_PATH, reindex, search
    from ludex.search.store import SearchStore

    db_path = args.db or DEFAULT_DB_PATH

    if args.rebuild:
        print(f"Rebuilding index at {db_path} …")
        stats = reindex(db_path=db_path)
        print(f"  rows_inserted: {stats.rows_inserted}")
        for src, n in sorted(stats.sources.items()):
            print(f"  {src:<12} {n} rows")
        print(f"  index size: {stats.index_bytes / 1024:.1f} KB")
        return 0

    if args.stats:
        if not db_path.exists():
            print(f"no index at {db_path} — run with --rebuild first")
            return 1
        with SearchStore(db_path) as store:
            counts = store.list_sources()
            total = sum(counts.values())
        print(f"{db_path} — {total} rows")
        for src in sorted(counts):
            print(f"  {src:<12} {counts[src]} rows")
        return 0

    if not args.query:
        parser.error("query required (or use --rebuild / --stats)")

    if not db_path.exists():
        print(f"no index at {db_path}; run with --rebuild first",
              file=sys.stderr)
        return 1

    hits = search(
        args.query,
        source=args.source,
        limit=args.limit,
        db_path=db_path,
    )

    if not hits:
        print("(no hits)")
        return 0

    for h in hits:
        loc = f"{h.path}"
        if h.lineno > 1:
            loc += f":{h.lineno}"
        # Two-line format: header + snippet with indentation.
        print(f"[{h.source}] {h.title}  —  {loc}")
        snippet = h.snippet.replace("\n", " ").strip()
        print(f"    {snippet}")
        if args.verbose:
            print(f"    score={h.score:.3f}  anchor={h.anchor}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
