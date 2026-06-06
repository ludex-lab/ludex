"""FTS5-backed search over Ludex docs, journal, and joint spec.

Public API:
    from ludex.search import search, reindex
    search("bond staleness", source="journal", limit=10) -> list[Hit]
    reindex() -> IndexStats

Store lives at `ludex/.search.db` (gitignored, derived index).
Rebuild idempotently from source files. See
`docs/hermes-agent-review.md` §5 C1 for background.
"""
from __future__ import annotations

from ludex.search.store import Hit, IndexStats, SearchStore
from ludex.search.indexer import reindex, search

__all__ = ["Hit", "IndexStats", "SearchStore", "reindex", "search"]
