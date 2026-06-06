"""Chronos — temporal sensory organ (D-059 Phase A).

Chronos is the creature's **temporal sense** — afferent read-only
aggregator that answers "when" questions from existing timestamps
(`time.time()`, `OrganismConfig.born_at`, `session_count`, recent
span timestamps). No new state.

Context: D-047 named `Chronos` as a candidate future AI-native
sensory organ ("sense of time, rhythm, duration; currently implicit
in timestamps and span sequence"). D-059 promotes it to a formal
organ because the Phase B sensory consolidation gate (perception-
action Step 1) already uses inline elapsed signals; future consumers
(heartbeat, memory recall, engine prompt context) will share the
interface.

Phase A MVP:
- `ChronosBlock.handle_sense()` → `ChronosReading`
- Fields: now, session_age_s, creature_age_s, session_count,
  last_sensory_ago_s, last_reflection_ago_s
- `summary()` produces short prose ("session 12, 2h in, 3d old")
- `KIND_CHRONOS_SENSED` trace span per call
- Default-enabled

Phase B (deferred):
- Chronological memory recall — "how long ago did X happen" over
  the creature's full span log
- Cross-creature elapsed ("we met 2 days ago")
- Rhythm detection (regular vs irregular invocation patterns)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChronosReading:
    """Temporal snapshot of the creature. All elapsed fields are
    seconds; None where the source is unavailable."""
    now: float = field(default_factory=time.time)
    session_age_s: float = 0.0           # since this session wake
    creature_age_s: float | None = None   # since born_at
    session_count: int = 0
    last_sensory_ago_s: float | None = None
    last_reflection_ago_s: float | None = None

    def summary(self) -> str:
        parts: list[str] = []
        if self.session_count:
            parts.append(f"session {self.session_count}")
        if self.session_age_s >= 60:
            parts.append(f"{int(self.session_age_s / 60)}m in")
        elif self.session_age_s:
            parts.append(f"{int(self.session_age_s)}s in")
        if self.creature_age_s is not None:
            age_days = self.creature_age_s / 86400.0
            if age_days >= 1:
                parts.append(f"{int(age_days)}d old")
            else:
                parts.append(f"{int(self.creature_age_s / 3600)}h old")
        return ", ".join(parts) if parts else "untimed"


class ChronosBlock(Block):
    """Temporal sensing organ. Aggregator; holds no state.

    provides: sense
    requires: (none; uses sibling organ reads + config defensively)

    D-059 Phase A. See module docstring.
    """

    name = "chronos"
    provides = [
        Port("sense", description="Read unified temporal state"),
    ]
    requires = []

    def __init__(self):
        super().__init__()
        # Record the wake time so session_age_s works even when the
        # organism doesn't expose one. Set fresh on attach.
        self._session_wake_at: float = 0.0

    def on_attach(self) -> None:
        self._session_wake_at = time.time()

    # --- Provides: sense ---

    def handle_sense(self) -> ChronosReading:
        now = time.time()
        session_age = max(0.0, now - self._session_wake_at) if self._session_wake_at else 0.0
        creature_age = self._creature_age(now)
        session_count = self._session_count()
        last_sensory = self._last_sensory_ago(now)
        last_reflection = self._last_reflection_ago(now)

        reading = ChronosReading(
            now=now,
            session_age_s=session_age,
            creature_age_s=creature_age,
            session_count=session_count,
            last_sensory_ago_s=last_sensory,
            last_reflection_ago_s=last_reflection,
        )

        try:
            from ludex.core import trace as _tr
            _tr.emit_chronos_sensed(
                self._organism,
                session_age_s=session_age,
                creature_age_s=creature_age,
                session_count=session_count,
                last_sensory_ago_s=last_sensory,
                last_reflection_ago_s=last_reflection,
                summary=reading.summary(),
            )
        except Exception:
            pass

        return reading

    # ------------------------------------------------------------
    # Per-source readers
    # ------------------------------------------------------------

    def _creature_age(self, now: float) -> float | None:
        cfg = getattr(self._organism, "config", None)
        if not cfg:
            return None
        try:
            born_at = cfg.get("born_at", 0.0) if hasattr(cfg, "get") else 0.0
        except Exception:
            return None
        if not born_at:
            return None
        return max(0.0, now - float(born_at))

    def _session_count(self) -> int:
        cfg = getattr(self._organism, "config", None)
        if not cfg:
            return 0
        try:
            return int(cfg.get("session_count", 0) if hasattr(cfg, "get") else 0)
        except Exception:
            return 0

    def _last_sensory_ago(self, now: float) -> float | None:
        """Look at the sensory_consolidation last-seen tracker for any
        recent Opsis/Akoué event by this creature. Defensive; returns
        None if the tracker is empty."""
        try:
            from ludex.core.sensory_consolidation import _LAST_SENSING
        except Exception:
            return None
        name = getattr(self._organism, "name", "")
        times = [
            ts for (org, _kind, _field), ts in _LAST_SENSING.items()
            if org == name
        ]
        if not times:
            return None
        return max(0.0, now - max(times))

    def _last_reflection_ago(self, now: float) -> float | None:
        """Look at SELF.md mtime for recent reflection."""
        cfg = getattr(self._organism, "config", None)
        if not cfg:
            return None
        try:
            habitat_dir = cfg.get("habitat_dir", "") if hasattr(cfg, "get") else ""
        except Exception:
            return None
        if not habitat_dir:
            return None
        try:
            from pathlib import Path
            p = Path(habitat_dir) / "SELF.md"
            if not p.exists():
                return None
            return max(0.0, now - p.stat().st_mtime)
        except Exception:
            return None
