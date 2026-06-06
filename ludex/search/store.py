"""SQLite FTS5 wrapper for the Ludex doc/journal/spec search index.

Schema (two tables — content + FTS5 virtual index linked by rowid):

    documents
        id INTEGER PRIMARY KEY
        source TEXT          -- 'design_log', 'journal', 'joint_spec',
                             --   'sub_system', 'review'
        path TEXT            -- relative repo path, e.g. docs/design-
                             --   decisions-log.md
        anchor TEXT          -- stable identifier within the file
                             --   (heading slug, line range, etc.)
        title TEXT           -- human-readable title
        lineno INTEGER       -- line number of the section start
        UNIQUE(source, path, anchor)

    documents_fts  (virtual, fts5)
        title, body          -- searchable columns
        source UNINDEXED     -- kept for filter without sacrificing
                             --   tokenization
        path UNINDEXED
        anchor UNINDEXED

Use `SearchStore(path)` as a context manager. All writes are batched
in a single transaction per `bulk_replace()` call for reindex speed.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Hit:
    """One search result."""
    source: str
    path: str
    anchor: str
    title: str
    lineno: int
    snippet: str
    score: float


@dataclass(frozen=True)
class IndexStats:
    rows_inserted: int
    sources: dict[str, int]
    index_bytes: int


class SearchStore:
    """Thin wrapper around a sqlite3 connection holding the FTS5 index."""

    def __init__(self, db_path: Path | str):
        self.path = Path(db_path)
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> "SearchStore":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()
        return self

    def __exit__(self, *exc) -> None:
        if self.conn is not None:
            self.conn.commit()
            self.conn.close()
            self.conn = None

    # ------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------

    def _ensure_schema(self) -> None:
        assert self.conn is not None
        c = self.conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                path TEXT NOT NULL,
                anchor TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                lineno INTEGER NOT NULL DEFAULT 0,
                UNIQUE(source, path, anchor)
            )
        """)
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                title, body,
                source UNINDEXED, path UNINDEXED, anchor UNINDEXED,
                tokenize='unicode61 remove_diacritics 2'
            )
        """)
        c.commit()

    # ------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------

    def bulk_replace(
        self,
        rows: list[dict],
        delete_source: str | None = None,
    ) -> IndexStats:
        """Replace rows in one transaction.

        If `delete_source` is given, all existing rows of that source
        are deleted before insert. Otherwise the inserts replace on the
        (source, path, anchor) UNIQUE constraint.

        Each row dict must have: source, path, anchor, title, lineno,
        body.
        """
        assert self.conn is not None
        c = self.conn
        counts: dict[str, int] = {}
        with c:
            if delete_source is not None:
                # Delete content rows first, FTS rows mirror them
                old_ids = [
                    r[0] for r in c.execute(
                        "SELECT id FROM documents WHERE source = ?",
                        (delete_source,),
                    )
                ]
                if old_ids:
                    q = ",".join("?" * len(old_ids))
                    c.execute(f"DELETE FROM documents_fts WHERE rowid IN ({q})",
                              old_ids)
                    c.execute(f"DELETE FROM documents WHERE id IN ({q})",
                              old_ids)
            for r in rows:
                cur = c.execute(
                    """
                    INSERT INTO documents(source, path, anchor, title, lineno)
                    VALUES (:source, :path, :anchor, :title, :lineno)
                    ON CONFLICT(source, path, anchor) DO UPDATE SET
                        title = excluded.title,
                        lineno = excluded.lineno
                    RETURNING id
                    """,
                    r,
                )
                row_id = cur.fetchone()[0]
                # Upsert into FTS by rowid
                c.execute(
                    "DELETE FROM documents_fts WHERE rowid = ?", (row_id,)
                )
                c.execute(
                    """
                    INSERT INTO documents_fts(
                        rowid, title, body, source, path, anchor
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_id, r["title"], r["body"],
                        r["source"], r["path"], r["anchor"],
                    ),
                )
                counts[r["source"]] = counts.get(r["source"], 0) + 1

        size = self.path.stat().st_size if self.path.exists() else 0
        return IndexStats(
            rows_inserted=sum(counts.values()),
            sources=counts,
            index_bytes=size,
        )

    # ------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        source: str | None = None,
        limit: int = 10,
        snippet_cols: int = 1,       # 1 = body column
        snippet_tokens: int = 16,
    ) -> list[Hit]:
        """FTS5 MATCH query with bm25 ordering.

        `query` follows FTS5 syntax. Phrase: "exact phrase". AND/OR/NOT
        uppercase. Column filter: `title:bond`. Prefix: `heart*`.
        """
        assert self.conn is not None
        sql = """
            SELECT
                d.source, d.path, d.anchor, d.title, d.lineno,
                snippet(documents_fts, ?, '<<', '>>', ' ... ', ?) AS snip,
                bm25(documents_fts) AS score
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?
        """
        params: list = [snippet_cols, snippet_tokens, query]
        if source is not None:
            sql += " AND d.source = ?"
            params.append(source)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        hits: list[Hit] = []
        for row in self.conn.execute(sql, params):
            hits.append(Hit(
                source=row["source"],
                path=row["path"],
                anchor=row["anchor"],
                title=row["title"],
                lineno=row["lineno"] or 0,
                snippet=row["snip"],
                score=float(row["score"]),
            ))
        return hits

    def list_sources(self) -> dict[str, int]:
        """Return {source: row_count}."""
        assert self.conn is not None
        return {
            r[0]: r[1] for r in self.conn.execute(
                "SELECT source, COUNT(*) FROM documents GROUP BY source"
            )
        }


@contextmanager
def open_store(db_path: Path | str) -> Iterator[SearchStore]:
    """Context manager helper."""
    store = SearchStore(db_path)
    with store:
        yield store
