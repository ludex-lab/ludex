"""Allos — social sensory organ (D-061 Phase A).

Allos is the creature's sense of **other creatures** — afferent
aggregator over the habitat's bond files. Answers "who do I know /
who is with me now" without requiring the creature to explicitly
read the file system.

Context: D-047 named `Allos` as a candidate future AI-native
sensory organ ("sense of other creatures' state; currently
implicit in cross-creature file reads"). D-061 promotes it formally
to support Council / trio / multi-creature fields where the
creature benefits from knowing who else is present and what shared
history exists.

Phase A MVP:
- `AllosBlock.handle_sense()` → `AllosReading`
- Fields: known_others (from bonds/ dir), per_other metadata
  (last_interaction_ago_s, event_count, role_play_events_count)
- `summary()` short prose
- `KIND_ALLOS_SENSED` trace span
- Default-enabled

Phase B (deferred):
- `present_others` — intersect known_others with Topos.participants
  once fields expose a participants list consistently.
- Live bond-updates subscription — refresh on file change rather
  than scan on every sense.
- Bond-strength heuristic beyond event count (recency-weighted).

Philosophy:
- Afferent only. No writes to bond files from Allos.
- Defensive. Missing bonds/ dir → empty reading, no errors.
- Cached per-dir-mtime so repeated handle_sense() calls don't
  re-scan the filesystem every time.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OtherMeta:
    """Per-known-other metadata summary."""
    name: str
    last_interaction_ago_s: float | None = None   # None if mtime unavailable
    event_count: int = 0           # lines in the bond file body
    role_play_event_count: int = 0  # lines under `## Role-play events`


@dataclass(frozen=True)
class AllosReading:
    """Creature's social snapshot. `per_other` keyed by lowercase
    other-name for case-insensitive lookup."""
    known_others: list[str] = field(default_factory=list)
    per_other: dict[str, OtherMeta] = field(default_factory=dict)
    bonds_dir: str = ""

    def summary(self) -> str:
        n = len(self.known_others)
        if n == 0:
            return "no bonds"
        fresh = [m for m in self.per_other.values()
                 if m.last_interaction_ago_s is not None
                 and m.last_interaction_ago_s < 86400 * 7]
        if fresh:
            return f"known {n}, {len(fresh)} within 7d"
        return f"known {n}"


class AllosBlock(Block):
    """Social sensing organ. Scans the creature's bonds/ directory
    to build a view of who is known and how recently interacted.

    provides: sense
    requires: (none; reads filesystem)

    D-061 Phase A.
    """

    name = "allos"
    provides = [
        Port("sense", description="Read known-others and bond metadata"),
    ]
    requires = []

    def __init__(self):
        super().__init__()
        self._cache_mtime: float = -1.0
        self._cache_reading: AllosReading | None = None

    def on_attach(self) -> None:
        pass

    # --- Provides: sense ---

    def handle_sense(self) -> AllosReading:
        bonds_dir = self._bonds_dir()
        if not bonds_dir or not bonds_dir.is_dir():
            reading = AllosReading(bonds_dir=str(bonds_dir or ""))
            self._emit(reading)
            return reading

        dir_mtime = bonds_dir.stat().st_mtime
        if (self._cache_reading is not None
                and dir_mtime == self._cache_mtime):
            self._emit(self._cache_reading)
            return self._cache_reading

        now = time.time()
        known: list[str] = []
        per: dict[str, OtherMeta] = {}
        for bond_path in sorted(bonds_dir.glob("*.md")):
            name = bond_path.stem
            known.append(name)
            per[name.lower()] = _read_other_meta(bond_path, now=now, name=name)

        reading = AllosReading(
            known_others=known,
            per_other=per,
            bonds_dir=str(bonds_dir),
        )
        self._cache_mtime = dir_mtime
        self._cache_reading = reading
        self._emit(reading)
        return reading

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------

    def _bonds_dir(self) -> Path | None:
        cfg = getattr(self._organism, "config", None)
        if not cfg or not hasattr(cfg, "get"):
            return None
        habitat_dir = cfg.get("habitat_dir", "")
        if not habitat_dir:
            return None
        return Path(habitat_dir) / "bonds"

    def _emit(self, reading: AllosReading) -> None:
        try:
            from ludex.core import trace as _tr
            _tr.emit_allos_sensed(
                self._organism,
                known_count=len(reading.known_others),
                bonds_dir=reading.bonds_dir,
                summary=reading.summary(),
            )
        except Exception:
            pass


def _read_other_meta(path: Path, now: float, name: str) -> OtherMeta:
    try:
        mtime = path.stat().st_mtime
        last_ago = max(0.0, now - mtime)
    except Exception:
        last_ago = None

    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = ""

    event_count = 0
    role_play_events = 0
    in_roleplay = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Role-play events"):
            in_roleplay = True
            continue
        if stripped.startswith("## ") and in_roleplay:
            in_roleplay = False
        if stripped.startswith("- "):
            event_count += 1
            if in_roleplay:
                role_play_events += 1

    return OtherMeta(
        name=name,
        last_interaction_ago_s=last_ago,
        event_count=event_count,
        role_play_event_count=role_play_events,
    )
