"""
LudexStore — per-creature span and reward substrate (D-028 candidate).

Phase A of the agent-lightning integration (see
`docs/agent-lightning-integration-notes.md`).

Design principles:
- Source of truth is habitat-local: one store per creature at
  `creatures/{name}/store/`. Portable with the creature.
- Append-only JSONL for grep-ability and git-friendly diffs.
- Two files: `spans.jsonl` (event stream) and `rewards.jsonl` (reward
  signals). Rewards are also spans with a reward payload, but indexed
  separately for quick reward-only queries.
- No optimization logic here — this is observability only.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Span:
    """A single observed event in a creature's life.

    Fields:
    - kind: span type (e.g. "field_start", "tick", "tom_predict",
      "reward_energy_delta"). Free-form string; conventions live in trace.py.
    - creature: creature name (the span's subject). May differ from "actor"
      when an event is *about* one creature but initiated by another.
    - timestamp: epoch seconds.
    - attributes: arbitrary JSON-serializable dict.
    - reward: optional reward payload. If present, the span is also indexed
      in rewards.jsonl.
    """
    kind: str
    creature: str
    timestamp: float = field(default_factory=time.time)
    attributes: dict[str, Any] = field(default_factory=dict)
    reward: dict[str, Any] | None = None


class LudexStore:
    """Per-creature span + reward store, habitat-local.

    Usage:
        store = LudexStore.for_creature("creatures/Primo")
        store.append(Span(kind="field_start", creature="Primo", ...))
        recent = store.spans(kind="reward_energy_delta", limit=20)
    """

    def __init__(self, habitat_dir: str):
        self.habitat_dir = Path(habitat_dir)
        self.store_dir = self.habitat_dir / "store"
        self.spans_path = self.store_dir / "spans.jsonl"
        self.rewards_path = self.store_dir / "rewards.jsonl"

    @classmethod
    def for_creature(cls, habitat_dir: str) -> LudexStore:
        store = cls(habitat_dir)
        store.store_dir.mkdir(parents=True, exist_ok=True)
        return store

    # -------- write --------

    def append(self, span: Span) -> None:
        """Append a span to spans.jsonl; if it carries a reward, also to rewards.jsonl."""
        try:
            self.store_dir.mkdir(parents=True, exist_ok=True)
            with self.spans_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(span), ensure_ascii=False) + "\n")
            if span.reward is not None:
                with self.rewards_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(span), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"LudexStore: append failed for {span.kind}: {e}")

    # -------- read --------

    def _read_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        out: list[dict] = []
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning(f"LudexStore: read failed {path}: {e}")
        return out

    def spans(
        self,
        kind: str | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Query spans by kind / timestamp. Returns dicts (JSON-native)."""
        entries = self._read_jsonl(self.spans_path)
        if kind is not None:
            entries = [e for e in entries if e.get("kind") == kind]
        if since is not None:
            entries = [e for e in entries if e.get("timestamp", 0) >= since]
        if limit is not None:
            entries = entries[-limit:]
        return entries

    def rewards(
        self,
        dimension: str | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Query rewards by dimension (reward.dimension attr)."""
        entries = self._read_jsonl(self.rewards_path)
        if dimension is not None:
            entries = [
                e for e in entries
                if (e.get("reward") or {}).get("dimension") == dimension
            ]
        if since is not None:
            entries = [e for e in entries if e.get("timestamp", 0) >= since]
        if limit is not None:
            entries = entries[-limit:]
        return entries

    def reward_values(self, dimension: str, since: float | None = None) -> list[float]:
        """Convenience: flat list of numeric reward values for one dimension."""
        vals: list[float] = []
        for r in self.rewards(dimension=dimension, since=since):
            v = (r.get("reward") or {}).get("value")
            if isinstance(v, (int, float)):
                vals.append(float(v))
        return vals

    # -------- per-episode (per-field) views --------

    def spans_by_field(self, field_name: str) -> list[dict]:
        """All spans attributed to a given field run."""
        return [
            s for s in self._read_jsonl(self.spans_path)
            if (s.get("attributes") or {}).get("field_name") == field_name
        ]

    def rewards_by_field(self, field_name: str, dimension: str | None = None) -> list[dict]:
        entries = [
            r for r in self._read_jsonl(self.rewards_path)
            if (r.get("attributes") or {}).get("field_name") == field_name
        ]
        if dimension is not None:
            entries = [
                r for r in entries
                if (r.get("reward") or {}).get("dimension") == dimension
            ]
        return entries

    def episodes(self) -> list[dict]:
        """Return one summary per (field_name) run in chronological order.

        Each entry: {field_name, started_at, ended_at, span_count, rewards: {dim: [values]}}.
        An "episode" is bracketed by field_start and field_end spans; if a run
        is interrupted mid-way, field_end may be missing and ended_at is None.
        """
        spans = self._read_jsonl(self.spans_path)
        by_field: dict[str, dict] = {}
        for s in spans:
            fn = (s.get("attributes") or {}).get("field_name")
            if not fn:
                continue
            ep = by_field.setdefault(fn, {
                "field_name": fn,
                "started_at": None,
                "ended_at": None,
                "span_count": 0,
                "rewards": {},
            })
            ep["span_count"] += 1
            ts = s.get("timestamp")
            if s.get("kind") == "field_start" and ts:
                ep["started_at"] = ts
            elif s.get("kind") == "field_end" and ts:
                ep["ended_at"] = ts
            reward = s.get("reward") or {}
            dim = reward.get("dimension")
            val = reward.get("value")
            if dim and isinstance(val, (int, float)):
                ep["rewards"].setdefault(dim, []).append(float(val))
        # Sort by started_at (None last)
        items = list(by_field.values())
        items.sort(key=lambda e: (e["started_at"] is None, e["started_at"] or 0))
        return items
