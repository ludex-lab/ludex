"""Bond staleness — observability + creature-internal substrate.

Two-layer design (D-073 follow-up, 2026-05-01):
- **Caretaker layer** — `bond_staleness_report(creature_dir)` returns
  per-bond `(name, days_since_update)` rows for `ludex inspect` and
  `ludex cohort` to surface.
- **Creature layer** — `maybe_record_bond_staleness(memory_block,
  creature_dir, stale_names)` writes one episodic memory per stale
  period per bond, idempotent across heartbeat pulses, so the
  creature notices the absence in its own narrative without spam.

Bond mtime is the source of truth — a refreshed bond resets the
staleness clock automatically. The creature-side memory only fires
once per stale period; once the bond is re-engaged (mtime moves
forward past the alert memory's created_at), the next stale period
gets its own fresh alert.

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


def maybe_record_bond_staleness(
    memory_block,
    creature_dir: Path,
    stale_names: Iterable[str],
) -> list[str]:
    """Write one staleness memory per stale period per bond. Returns
    the list of bond names that got a new memory this call.

    Idempotency rule — a memory is written ONLY if no existing active
    memory tagged `bond_stale:<name>` has `created_at > bond_mtime`.
    Once a bond is re-engaged (mtime advances), the next stale period
    becomes eligible for its own fresh alert.

    No brain call. Memory is `episodic`, importance 0.4, content is
    a first-person noticing line. Tag `bond_stale:<name>` makes the
    idempotency check cheap.
    """
    if memory_block is None or not stale_names:
        return []
    bonds_dir = creature_dir / "bonds"
    if not bonds_dir.is_dir():
        return []

    written: list[str] = []
    now = time.time()
    for name in stale_names:
        bond_file = bonds_dir / f"{name}.md"
        if not bond_file.exists():
            continue
        bond_mtime = bond_file.stat().st_mtime
        days = max((now - bond_mtime) / 86400.0, 0.0)
        # Identify this stale period by the bond_mtime itself (rounded
        # to int seconds for stable equality). When the bond is re-
        # engaged, bond_mtime advances → new period identifier → fresh
        # alert eligible. Within a period (no re-engagement), repeat
        # pulses match the same period tag → skip.
        period_tag = f"bond_period:{name}:{int(bond_mtime)}"
        signature = f"bond_stale:{name}"
        already = any(
            m.status == "active" and period_tag in (m.tags or [])
            for m in memory_block._memories.values()
        )
        if already:
            continue
        try:
            memory_block.handle_remember(
                f"I noticed I haven't been in contact with {name} for "
                f"{int(round(days))} days.",
                memory_type="episodic",
                tags=["bond_staleness", signature, period_tag],
                source=f"bond_staleness/{name}",
                importance=0.4,
            )
            written.append(name)
        except Exception:
            # Best-effort: never block heartbeat on a memory write.
            pass
    return written
