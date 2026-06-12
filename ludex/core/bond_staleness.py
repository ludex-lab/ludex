"""Bond staleness — observability + creature-internal substrate.

Two-layer design (D-073 follow-up, 2026-05-01):
- **Caretaker layer** — `bond_staleness_report(creature_dir)` returns
  per-bond `(name, days_since_update)` rows for `ludex inspect` and
  `ludex cohort` to surface.
- **Creature layer (removed 2026-06-12, D-024/F1)** — staleness
  memories were observability written into the experience store; the
  creature now receives staleness through the heartbeat reflect
  trigger string instead ("heartbeat:stale_bonds=[...]"), which enters
  the reflection prompt header.

Bond mtime is the source of truth — a refreshed bond resets the
staleness clock automatically.

Q1 framing (observation, not bug): we make absence visible, we do
not auto-resolve it.
Q2 framing (mixed layers): caretaker sees aggregate; creature
notices personally. Same bond mtime drives both.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable

# 7 days mirrors heartbeat.BOND_STALE_DAYS — kept duplicated so this
# module doesn't import from heartbeat (which would create a cycle).
DEFAULT_STALE_THRESHOLD_DAYS = 7


def list_bonds(creature_dir: Path) -> list[tuple[str, float]]:
    """Return `(bond_name, days_since_update)` for every bond file
    in `bonds/`, sorted oldest-first. Empty when no bonds dir."""
    bonds_dir = creature_dir / "bonds"
    if not bonds_dir.is_dir():
        return []
    now = time.time()
    rows: list[tuple[str, float]] = []
    for f in bonds_dir.glob("*.md"):
        days = max((now - f.stat().st_mtime) / 86400.0, 0.0)
        rows.append((f.stem, days))
    rows.sort(key=lambda r: r[1], reverse=True)  # oldest first
    return rows


def stale_bonds(
    creature_dir: Path,
    threshold_days: float = DEFAULT_STALE_THRESHOLD_DAYS,
) -> list[tuple[str, float]]:
    """Return `(bond_name, days_since_update)` for stale bonds only."""
    return [(n, d) for n, d in list_bonds(creature_dir) if d > threshold_days]
