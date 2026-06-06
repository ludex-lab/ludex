"""
Organ Trace Logging — the foundation of AI Ethology field studies.

Records every brain-initiated organ call (and a few other interesting events)
to habitat/traces/trace.jsonl with capacity-aware rotation.

Trace events feed three downstream uses:
1. Field study analysis — what does this brain × organ combination actually do?
2. Comfort/ease/agency metrics — derived from call patterns
3. Future Neural Computer training data — structured I/O traces

Schema:
    {
        "ts": float (unix seconds),
        "creature": str (organism name),
        "kind": str ("organ_call", "session_start", "session_end", "user_message", "creature_message"),
        "source": str ("brain", "system", "internal"),
        "tool": str (tool name, e.g., "ludex_immune_assess"),
        "args": dict (sanitized args),
        "result_summary": str (truncated result),
        "duration_ms": float,
        "context": dict (turn, task, brain, etc.),
    }
"""

from __future__ import annotations

import os
import json
import time
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Default per-creature trace budget (small fraction of habitat budget)
DEFAULT_MAX_TRACE_LINES = 50000  # roughly 5-10 MB
DEFAULT_KEEP_AFTER_ROTATE = 25000


class TraceLogger:
    """
    Per-creature trace logger. One instance per creature.

    Backed by an append-only JSONL file at <habitat>/traces/trace.jsonl.
    When the file exceeds max_lines, the oldest half is dropped (rotate).

    Usage:
        logger = TraceLogger(habitat_dir="./creatures/Primo", creature="Primo")
        logger.record_organ_call(
            tool="ludex_immune_assess",
            args={"context": "user message"},
            result_summary="threat=0.0, calm=1.0",
            duration_ms=12.5,
            source="brain",
            context={"turn": 5},
        )
    """

    def __init__(
        self,
        habitat_dir: str,
        creature: str,
        max_lines: int = DEFAULT_MAX_TRACE_LINES,
        enabled: bool = True,
    ):
        self.habitat_dir = habitat_dir
        self.creature = creature
        self.max_lines = max_lines
        self.enabled = enabled and bool(habitat_dir)
        self._line_count = 0

        if self.enabled:
            self._path = Path(habitat_dir) / "traces" / "trace.jsonl"
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                if self._path.exists():
                    self._line_count = self._count_lines()
            except Exception as e:
                logger.warning(f"TraceLogger: cannot init at {self._path}: {e}")
                self.enabled = False

    def _count_lines(self) -> int:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    def _rotate_if_needed(self):
        if self._line_count <= self.max_lines:
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            keep = lines[-DEFAULT_KEEP_AFTER_ROTATE:]
            with open(self._path, "w", encoding="utf-8") as f:
                f.writelines(keep)
            self._line_count = len(keep)
            logger.debug(f"TraceLogger rotated {self._path}: kept {len(keep)} of {len(lines)}")
        except Exception as e:
            logger.warning(f"TraceLogger rotate failed: {e}")

    def _write(self, event: dict):
        if not self.enabled:
            return
        # Capture builtins for shutdown safety
        _open = open
        _json = json
        try:
            with _open(self._path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(event, ensure_ascii=False, default=str) + "\n")
            self._line_count += 1
            if self._line_count > self.max_lines:
                self._rotate_if_needed()
        except Exception as e:
            try:
                logger.debug(f"TraceLogger write failed: {e}")
            except Exception:
                pass

    # ============================================================
    # Recording API
    # ============================================================

    def record_organ_call(
        self,
        tool: str,
        args: dict | None = None,
        result_summary: str = "",
        duration_ms: float = 0.0,
        source: str = "brain",
        context: dict | None = None,
    ):
        """Record an organ tool call."""
        event = {
            "ts": time.time(),
            "creature": self.creature,
            "kind": "organ_call",
            "source": source,
            "tool": tool,
            "args": _sanitize_args(args or {}),
            "result_summary": _truncate(result_summary, 500),
            "duration_ms": round(duration_ms, 2),
            "context": context or {},
        }
        self._write(event)

    def record_user_message(self, text: str, context: dict | None = None):
        """Record a user message arriving."""
        event = {
            "ts": time.time(),
            "creature": self.creature,
            "kind": "user_message",
            "source": "user",
            "text": _truncate(text, 500),
            "context": context or {},
        }
        self._write(event)

    def record_creature_message(self, text: str, context: dict | None = None):
        """Record a creature response."""
        event = {
            "ts": time.time(),
            "creature": self.creature,
            "kind": "creature_message",
            "source": "creature",
            "text": _truncate(text, 500),
            "context": context or {},
        }
        self._write(event)

    def record_session_event(self, kind: str, info: dict | None = None):
        """Record session start/end or other lifecycle events."""
        event = {
            "ts": time.time(),
            "creature": self.creature,
            "kind": kind,
            "source": "system",
            "info": info or {},
        }
        self._write(event)

    # ============================================================
    # Read API
    # ============================================================

    def load_all(self) -> list[dict]:
        """Load all trace events. For analysis."""
        if not self.enabled or not self._path.exists():
            return []
        events = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning(f"TraceLogger load_all failed: {e}")
        return events

    def stats(self) -> dict:
        """Quick summary of trace contents."""
        events = self.load_all()
        if not events:
            return {"total": 0}

        kinds: dict[str, int] = {}
        tools: dict[str, int] = {}
        for e in events:
            k = e.get("kind", "unknown")
            kinds[k] = kinds.get(k, 0) + 1
            if k == "organ_call":
                t = e.get("tool", "unknown")
                tools[t] = tools.get(t, 0) + 1

        return {
            "total": len(events),
            "kinds": kinds,
            "tools": tools,
            "first_ts": events[0].get("ts"),
            "last_ts": events[-1].get("ts"),
            "lines_in_file": self._line_count,
            "max_lines": self.max_lines,
        }


# ============================================================
# Helpers
# ============================================================

def _truncate(text: Any, max_len: int) -> str:
    s = str(text) if text is not None else ""
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."


def _sanitize_args(args: dict) -> dict:
    """Best-effort safe copy of args (truncate strings, drop weird types)."""
    result = {}
    for k, v in args.items():
        if isinstance(v, str):
            result[k] = _truncate(v, 200)
        elif isinstance(v, (int, float, bool, type(None))):
            result[k] = v
        elif isinstance(v, (list, dict)):
            try:
                # Best effort serialize
                json.dumps(v)
                result[k] = v
            except (TypeError, ValueError):
                result[k] = str(v)[:200]
        else:
            result[k] = str(v)[:200]
    return result


# ============================================================
# Global registry — lookup by creature name
# ============================================================

_GLOBAL_LOGGERS: dict[str, TraceLogger] = {}


def get_or_create_logger(habitat_dir: str, creature: str) -> TraceLogger:
    """Get a TraceLogger for a creature, creating one if needed."""
    if creature not in _GLOBAL_LOGGERS:
        _GLOBAL_LOGGERS[creature] = TraceLogger(habitat_dir=habitat_dir, creature=creature)
    return _GLOBAL_LOGGERS[creature]


def get_logger(creature: str) -> TraceLogger | None:
    """Get an existing logger by creature name, or None."""
    return _GLOBAL_LOGGERS.get(creature)


def clear_logger(creature: str):
    """Remove a logger from the registry (e.g., on creature destruction)."""
    _GLOBAL_LOGGERS.pop(creature, None)
