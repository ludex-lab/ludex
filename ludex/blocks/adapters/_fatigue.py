"""Shared fatigue-reset timestamp parser for CLI adapters.

claude_cli and codex_cli both emit stderr that sometimes carries an
absolute reset timestamp ("try again at May 12th, 2026 3:16 PM.",
"resets at 2026-05-12T15:16:00Z", etc.). When the parser succeeds,
the adapter returns the parsed seconds-from-now alongside its
fatigue cause/detail tuple; the resilience block then sets
_fatigue_until to that exact moment instead of falling back to a
1-hour default.

Contract:
    parse_reset_at(text: str) -> int | None

Returns seconds-from-now until the reset moment, or None when no
parseable timestamp is found. None is the safe fallback path —
the resilience block already has a 1h default for that case, and
returning a partial guess would corrupt the fatigue window the
caretaker reads from heartbeat logs.

D-077 (2026-05-09): formalized after JJ verified Codex stderr
carries "try again at May 12th, 2026 3:16 PM." with no timezone
hint, no year-when-current-year, occasional ordinal suffixes
(1st/2nd/3rd/4th). The parser is liberal in what it accepts but
conservative in what it returns — any ambiguity → None.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)


# Trigger phrases that introduce an absolute reset timestamp.
# Order matters only for the substring captured after the trigger;
# all triggers are case-insensitive.
_TRIGGER_PATTERNS = [
    re.compile(r"try\s+again\s+at\s+(.+?)(?:\.|$|\n)", re.IGNORECASE),
    re.compile(r"resets?\s+at\s+(.+?)(?:\.|$|\n)", re.IGNORECASE),
    re.compile(r"resets?\s+on\s+(.+?)(?:\.|$|\n)", re.IGNORECASE),
    re.compile(r"available\s+again\s+at\s+(.+?)(?:\.|$|\n)", re.IGNORECASE),
]

# Strip ordinal suffixes (1st, 22nd, 3rd, 4th) from month-day forms.
_ORDINAL_RE = re.compile(r"(\d+)(st|nd|rd|th)\b", re.IGNORECASE)

# Time-only forms used when only the date already passed:
# "3:16 PM", "3 PM", "15:16", "15:16:00"
_TIME_FORMATS = [
    "%I:%M %p",
    "%I:%M:%S %p",
    "%I %p",
    "%H:%M",
    "%H:%M:%S",
]

# Date-and-time forms tried after ordinal stripping. Each entry is
# a strftime format string passed to datetime.strptime.
_DATETIME_FORMATS = [
    # ISO 8601-ish
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    # "May 12 2026 3:16 PM" / "May 12, 2026 3:16 PM"
    "%B %d %Y %I:%M %p",
    "%B %d, %Y %I:%M %p",
    "%B %d %Y %I %p",
    "%B %d, %Y %I %p",
    "%b %d %Y %I:%M %p",
    "%b %d, %Y %I:%M %p",
    # 24h variants
    "%B %d %Y %H:%M",
    "%B %d, %Y %H:%M",
    "%b %d %Y %H:%M",
    "%b %d, %Y %H:%M",
    # Date-only (assume start-of-day, today's tz)
    "%B %d %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%b %d, %Y",
]


def _strip_iso_z(s: str) -> str:
    """Convert trailing 'Z' to '+00:00' so fromisoformat accepts it."""
    return s.rstrip().replace("Z", "+00:00") if s.rstrip().endswith("Z") else s


def _parse_phrase(phrase: str, now: float) -> int | None:
    """Try a battery of format strings against `phrase`. Returns the
    seconds-from-now delta clamped to >=0, or None when no format
    matched."""
    phrase = phrase.strip().rstrip(".,;:")
    if not phrase:
        return None
    phrase = _ORDINAL_RE.sub(r"\1", phrase)

    # ISO with optional timezone via fromisoformat (Py 3.11+).
    try:
        dt = datetime.fromisoformat(_strip_iso_z(phrase))
        # Treat naive datetimes as local time (matches CLI stderr in
        # practice — no TZ usually means caretaker-local).
        delta = dt.timestamp() - now
        return max(0, int(delta))
    except ValueError:
        pass

    for fmt in _DATETIME_FORMATS:
        try:
            dt = datetime.strptime(phrase, fmt)
            delta = dt.timestamp() - now
            return max(0, int(delta))
        except ValueError:
            continue

    # Time-only ("3:16 PM"). Anchor to today; if the parsed time has
    # already passed today, push it to tomorrow (caretakers reading
    # "resets at 3:16 PM" after 6 PM mean tomorrow's 3:16 PM).
    today = datetime.fromtimestamp(now).date()
    for fmt in _TIME_FORMATS:
        try:
            time_only = datetime.strptime(phrase, fmt).time()
            dt = datetime.combine(today, time_only)
            delta = dt.timestamp() - now
            if delta < 0:
                dt = datetime.combine(today, time_only)
                # Add one day
                from datetime import timedelta
                dt = dt + timedelta(days=1)
                delta = dt.timestamp() - now
            return max(0, int(delta))
        except ValueError:
            continue

    return None


def parse_reset_at(text: str) -> int | None:
    """Parse the first absolute-reset timestamp appearing in `text`
    and return seconds-from-now until that moment. None when no
    trigger phrase matches or the date portion fails every known
    format — caller falls back to the resilience block's 1h default.

    Examples that parse:
        "Codex usage limit reached. Try again at May 12th, 2026 3:16 PM."
        "Daily limit reached; resets at 2026-05-12T15:16:00Z"
        "Available again at 15:16"
    """
    if not text:
        return None
    now = time.time()
    for pat in _TRIGGER_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        phrase = m.group(1)
        # Some CLIs add a trailing detail clause after a comma — try
        # the full phrase first, then progressively shorter prefixes
        # so "May 12th, 2026 3:16 PM, please retry then" still parses.
        candidates = [phrase]
        if "," in phrase:
            # Keep one comma (for "May 12, 2026 ..." style)
            head = phrase.split(",")
            if len(head) >= 2:
                candidates.append(",".join(head[:2]).strip())
            candidates.append(head[0].strip())
        for c in candidates:
            seconds = _parse_phrase(c, now)
            if seconds is not None:
                return seconds
    return None
