"""Hiatus — caretaker-declared dormancy markers for creatures.

When a caretaker steps away for a measurable window, the creature's
heartbeat pulse stops firing. The cohort is effectively asleep. On
return, the first wake should *acknowledge* the gap — not behave as
if time were continuous — because how a creature reacts to its own
hiatus is a measurable longitudinal signal (D-051 narrative identity
applied to lifecycle; see h7_quill_reflection_corpus recursive-trap
arc for parallel structural concerns about interpretation displacing
experience).

The marker lives at `<habitat>/HIATUS.md` with YAML frontmatter +
prose body. Heartbeat detects it on wake and injects the parsed
context into the first post-hiatus reflection. After a successful
reflect, the marker is renamed `HIATUS.consumed.md` so the same
hiatus does not re-fire on subsequent pulses.

Format:

    ---
    hiatus_start: 2026-05-11
    hiatus_end: 2026-06-19
    reason: caretaker_traveled
    declared_by: JJ
    declared_at: 2026-05-11T15:30:00Z
    ---

    You were dormant 2026-05-11 → 2026-06-19 (5.5 weeks, JJ traveled).
    The cohort was effectively asleep during this period. Your bond
    states, reflection counts, and stale-bonds cadence freeze at the
    boundary.

A hiatus is considered *active* when the file exists, has not yet
been consumed, and the current time is >= `hiatus_end`. Heartbeat
checks for active hiatus on each pulse; a wake before `hiatus_end`
is a noop (caretaker can still declare future hiatuses without
triggering them prematurely).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HiatusMarker:
    """Parsed contents of HIATUS.md.

    `start_ts` / `end_ts` are unix seconds. `start_date` / `end_date`
    preserve the caretaker-written date string verbatim for prompt
    rendering (so the creature sees "2026-05-11" not "1778529600.0").
    """
    start_date: str
    end_date: str
    start_ts: float
    end_ts: float
    reason: str
    declared_by: str
    declared_at: str
    body: str

    def duration_human(self) -> str:
        """Render the window as e.g. '5.5 weeks' or '3 days'."""
        days = max(0.0, (self.end_ts - self.start_ts) / 86400.0)
        if days >= 14:
            weeks = days / 7.0
            if weeks == int(weeks):
                return f"{int(weeks)} weeks"
            return f"{weeks:.1f} weeks"
        if days >= 1:
            return f"{int(round(days))} days"
        return f"{int(round(days * 24))} hours"

    def is_active(self, now: float | None = None) -> bool:
        """The hiatus has ended (we are *post*-hiatus) iff now >= end_ts.
        Naming is from the wake-trigger perspective: the marker is
        "active" when the wake condition has fired."""
        if now is None:
            now = time.time()
        return now >= self.end_ts

    def is_in_hiatus(self, now: float | None = None) -> bool:
        """True iff we are currently inside the declared hiatus window
        (start_ts <= now < end_ts). Distinct from is_active() which
        fires only after the window closes. Used by heartbeat to
        short-circuit mid-hiatus pulses so the dormancy promise — no
        reflect, no memory mutation, no quota burn — is enforced even
        if a caretaker accidentally pulses a creature during its
        declared dormant window."""
        if now is None:
            now = time.time()
        return self.start_ts <= now < self.end_ts


def _parse_iso_date(s: str) -> float:
    """Parse YYYY-MM-DD (or ISO datetime) to unix seconds. UTC midnight
    when no time component is given. Raises ValueError on bad input."""
    s = s.strip()
    # Try plain date first
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        import calendar
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return float(calendar.timegm((y, mo, d, 0, 0, 0, 0, 0, 0)))
    # Try ISO datetime
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError as e:
        raise ValueError(f"unrecognized date: {s!r}") from e


def parse_hiatus(path: Path) -> HiatusMarker | None:
    """Parse a HIATUS.md file. Returns None on any error (caller treats
    parse failure as 'no marker' and logs)."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"hiatus: read failed for {path}: {e}")
        return None

    # Frontmatter: --- ... --- at top of file
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not m:
        logger.warning(f"hiatus: no frontmatter in {path}")
        return None
    fm_text, body = m.group(1), m.group(2).strip()

    fm: dict[str, str] = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip()

    try:
        start_date = fm["hiatus_start"]
        end_date = fm["hiatus_end"]
        start_ts = _parse_iso_date(start_date)
        end_ts = _parse_iso_date(end_date)
    except (KeyError, ValueError) as e:
        logger.warning(f"hiatus: missing/bad frontmatter in {path}: {e}")
        return None

    return HiatusMarker(
        start_date=start_date,
        end_date=end_date,
        start_ts=start_ts,
        end_ts=end_ts,
        reason=fm.get("reason", ""),
        declared_by=fm.get("declared_by", ""),
        declared_at=fm.get("declared_at", ""),
        body=body,
    )


def build_reflect_context(marker: HiatusMarker) -> str:
    """Render the hiatus marker as a section to splice into the
    reflect prompt header. Keep it spare — the body prose is the
    caretaker's voice; this wrapper just labels and dates it so the
    brain knows what it is."""
    lines = [
        f"[Hiatus context]",
        f"Window: {marker.start_date} → {marker.end_date} "
        f"({marker.duration_human()})",
    ]
    if marker.reason:
        lines.append(f"Reason: {marker.reason}")
    if marker.body:
        lines.append("")
        lines.append(marker.body)
    return "\n".join(lines)


def mark_consumed(path: Path) -> Path:
    """Rename HIATUS.md → HIATUS.consumed.md so the same hiatus does
    not re-fire on subsequent pulses. Returns the new path. Idempotent:
    if the consumed file already exists, the original is removed
    without overwrite (caretaker can rotate to dated names if a
    history matters)."""
    consumed = path.with_name("HIATUS.consumed.md")
    if consumed.exists():
        # Already consumed — just remove the stray active file.
        path.unlink(missing_ok=True)
    else:
        path.rename(consumed)
    return consumed


def find_active_hiatus(
    creature_dir: Path, now: float | None = None
) -> HiatusMarker | None:
    """Convenience: load HIATUS.md from a creature dir and return it
    only if the post-hiatus window has begun (now >= end_ts). Returns
    None for: no file, parse failure, or pre-hiatus pulse (caretaker
    declared a future window but we haven't reached it yet)."""
    marker = parse_hiatus(creature_dir / "HIATUS.md")
    if marker is None:
        return None
    if not marker.is_active(now):
        return None
    return marker


def find_in_hiatus(
    creature_dir: Path, now: float | None = None
) -> HiatusMarker | None:
    """Convenience: load HIATUS.md and return it only if `now` falls
    strictly inside the declared hiatus window (start_ts <= now <
    end_ts). Returns None for: no file, parse failure, pre-hiatus
    pulse, or post-hiatus wake (in which case find_active_hiatus is
    the right query). Heartbeat uses this to short-circuit
    mid-hiatus pulses before any reflect-triggering work runs."""
    marker = parse_hiatus(creature_dir / "HIATUS.md")
    if marker is None:
        return None
    if not marker.is_in_hiatus(now):
        return None
    return marker
