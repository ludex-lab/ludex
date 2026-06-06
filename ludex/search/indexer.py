"""Index scanners for Ludex repo markdown.

Handles four source types:

- `design_log`    — one row per `## D-NNN:` heading in
                    `docs/design-decisions-log.md`
- `joint_spec`    — one row per `### §X.Y` or `## §X.` heading in
                    `docs/joint_session_spec_v0.1.md`
- `journal`       — one row per file in `journal/*.md`
- `sub_system`    — one row per remaining `docs/*.md` file
- `review`        — per-file indexing of *.md under
                    `experiments/m3full_avalon_6creature/` if present
                    (captures M3-full analysis + classification)

Anchor strategy: for heading-split sources the anchor is the heading
slug (e.g. `d-044`, `c-4-1`). For file-level sources the anchor is
the file's basename without extension.
"""
from __future__ import annotations

import re
from pathlib import Path

from ludex.search.store import IndexStats, SearchStore

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "ludex" / ".search.db"


_HEADING_SLUG_TRIM = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    """Turn a heading title into a stable anchor slug."""
    s = text.lower().strip()
    s = _HEADING_SLUG_TRIM.sub("-", s).strip("-")
    return s[:80] or "section"


def _split_by_heading(
    path: Path,
    heading_re: re.Pattern[str],
    repo_root: Path = REPO_ROOT,
) -> list[dict]:
    """Split a markdown file into rows at matches of heading_re.

    The regex should match an entire heading line and have its captured
    group 1 be the heading title text (without the leading markers).

    Produces rows with: source (left to caller), path (relative to
    repo_root), anchor (slug of title), title, lineno (1-based), body.
    """
    rel = path.relative_to(repo_root).as_posix()
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    boundaries: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = heading_re.match(line)
        if m:
            title = m.group(1).strip()
            boundaries.append((i, title))

    rows: list[dict] = []
    if not boundaries:
        return rows
    for j, (start_line, title) in enumerate(boundaries):
        end_line = boundaries[j + 1][0] if j + 1 < len(boundaries) else len(lines)
        body = "\n".join(lines[start_line:end_line]).strip()
        rows.append({
            "path": rel,
            "anchor": _slug(title),
            "title": title,
            "lineno": start_line + 1,
            "body": body,
        })
    return rows


# -----------------------------------------------------------------------------
# Source scanners
# -----------------------------------------------------------------------------

def scan_design_log(repo_root: Path = REPO_ROOT) -> list[dict]:
    """Scan docs/design-decisions-log.md, one row per D-entry."""
    path = repo_root / "docs" / "design-decisions-log.md"
    if not path.exists():
        return []
    heading = re.compile(r"^## (D-\d+:.*)$")
    rows = _split_by_heading(path, heading, repo_root)
    for r in rows:
        r["source"] = "design_log"
    return rows


def scan_joint_spec(repo_root: Path = REPO_ROOT) -> list[dict]:
    """Scan docs/joint_session_spec_v0.1.md, one row per top- or
    sub-section (^## §X or ^### §X.Y or ^### C.Y.Z)."""
    path = repo_root / "docs" / "joint_session_spec_v0.1.md"
    if not path.exists():
        return []
    heading = re.compile(r"^#{2,3} (§?[A-Z]\..*|C\.\d+.*)$")
    rows = _split_by_heading(path, heading, repo_root)
    for r in rows:
        r["source"] = "joint_spec"
    return rows


def scan_journal(repo_root: Path = REPO_ROOT) -> list[dict]:
    """Scan journal/*.md, one row per file (whole-file body)."""
    jdir = repo_root / "journal"
    if not jdir.exists():
        return []
    rows: list[dict] = []
    for p in sorted(jdir.glob("*.md")):
        body = p.read_text(encoding="utf-8")
        first_heading = next(
            (ln.lstrip("# ").strip() for ln in body.splitlines()
             if ln.startswith("#")),
            p.stem,
        )
        rows.append({
            "source": "journal",
            "path": p.relative_to(repo_root).as_posix(),
            "anchor": p.stem,
            "title": first_heading,
            "lineno": 1,
            "body": body,
        })
    return rows


def scan_sub_system(repo_root: Path = REPO_ROOT) -> list[dict]:
    """Scan docs/*.md except design-decisions-log and joint spec (those
    have their own scanners)."""
    ddir = repo_root / "docs"
    if not ddir.exists():
        return []
    skip = {"design-decisions-log.md", "joint_session_spec_v0.1.md"}
    rows: list[dict] = []
    for p in sorted(ddir.glob("*.md")):
        if p.name in skip:
            continue
        body = p.read_text(encoding="utf-8")
        first_heading = next(
            (ln.lstrip("# ").strip() for ln in body.splitlines()
             if ln.startswith("#")),
            p.stem,
        )
        rows.append({
            "source": "sub_system",
            "path": p.relative_to(repo_root).as_posix(),
            "anchor": p.stem,
            "title": first_heading,
            "lineno": 1,
            "body": body,
        })
    return rows


def scan_review(repo_root: Path = REPO_ROOT) -> list[dict]:
    """Scan experiments/m3full_avalon_6creature/*.md (review artifacts)."""
    rdir = repo_root / "experiments" / "m3full_avalon_6creature"
    if not rdir.exists():
        return []
    rows: list[dict] = []
    for p in sorted(rdir.glob("*.md")):
        body = p.read_text(encoding="utf-8")
        first_heading = next(
            (ln.lstrip("# ").strip() for ln in body.splitlines()
             if ln.startswith("#")),
            p.stem,
        )
        rows.append({
            "source": "review",
            "path": p.relative_to(repo_root).as_posix(),
            "anchor": p.stem,
            "title": first_heading,
            "lineno": 1,
            "body": body,
        })
    return rows


# -----------------------------------------------------------------------------
# Public API — reindex + search
# -----------------------------------------------------------------------------

SCANNERS = {
    "design_log": scan_design_log,
    "joint_spec": scan_joint_spec,
    "journal": scan_journal,
    "sub_system": scan_sub_system,
    "review": scan_review,
}


def reindex(
    db_path: Path | str = DEFAULT_DB_PATH,
    repo_root: Path = REPO_ROOT,
    sources: list[str] | None = None,
) -> IndexStats:
    """Full reindex. Deletes + reinserts each scanned source.

    `sources`: if given, only those source names are rescanned (useful
    for incremental updates to one subtree).
    """
    sources = sources or list(SCANNERS.keys())
    totals: dict[str, int] = {}
    last_size = 0
    with SearchStore(db_path) as store:
        for src in sources:
            if src not in SCANNERS:
                continue
            rows = SCANNERS[src](repo_root)
            stats = store.bulk_replace(rows, delete_source=src)
            totals[src] = stats.rows_inserted
            last_size = stats.index_bytes
    return IndexStats(
        rows_inserted=sum(totals.values()),
        sources=totals,
        index_bytes=last_size,
    )


def search(
    query: str,
    *,
    source: str | None = None,
    limit: int = 10,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list:
    """One-shot search: open store, run query, close, return results."""
    with SearchStore(db_path) as store:
        return store.search(query, source=source, limit=limit)
