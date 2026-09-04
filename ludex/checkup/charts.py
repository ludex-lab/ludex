"""Indicator charts — the caretaker's longitudinal panel (checkup view V1).

Reads the agent-agnostic `indicators/` store (P-B off-substrate: nothing
here touches creature context) and shapes it for the chart view.

Rules carried from the ratified frame, enforced here so the view cannot
drop them:
- **Epoch grammar (C4):** series are SEGMENTED by brain epoch. Points in
  different epochs are never joined into one trend line; a re-brain is a
  break, not a data point.
- **Denominators (Ray §6):** activity-dependent indicators travel with
  their coverage (fields/brain-calls/spans in window) — an empty
  indicator is "no activity", never "healthy".
- **Markers (titration):** prescriptions ride along as timeline events,
  flagged when an epoch split has crossed them (review due).
- **Mirrors:** foreign-origin creatures are git-lagged copies, labelled.
"""
from __future__ import annotations

import json
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent.parent / "indicators"
LOCAL_ORIGIN = "Mac-habitat"

# indicator: (path in row, label, kind) — kind drives rendering
SERIES = [
    (("memories",), "memories", "count"),
    (("self_md_bytes",), "SELF.md bytes", "count"),
    (("stale_bonds",), "stale bonds", "count-inverse"),
    (("session_count",), "sessions", "count"),
    (("window", "reflects"), "reflects (30d)", "count"),
    (("window", "fields"), "fields (30d)", "count"),
    (("window", "deception_detected"), "deception hits (30d)", "count"),
]


def _dig(row, path):
    cur = row
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    # session_count arrives as a yaml string; series must be numeric
    if isinstance(cur, str):
        try:
            return float(cur) if "." in cur else int(cur)
        except ValueError:
            return None
    return cur


def _segments(snaps):
    """Split the snapshot list into same-epoch segments (C4)."""
    segs, cur = [], []
    for s in snaps:
        if cur and s.get("epoch") != cur[-1].get("epoch"):
            segs.append(cur)
            cur = []
        cur.append(s)
    if cur:
        segs.append(cur)
    return segs


def creature(name: str) -> dict | None:
    p = STORE / f"{name}.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(l) for l in p.open() if l.strip()]
    snaps = [r for r in rows if r.get("type") != "marker"]
    markers = [r for r in rows if r.get("type") == "marker"]
    if not snaps:
        return None
    cur = snaps[-1]
    segs = _segments(snaps)
    series = []
    for path, label, kind in SERIES:
        seg_points = [[{"ts": s["ts"], "v": _dig(s, path)}
                       for s in seg if _dig(s, path) is not None]
                      for seg in segs]
        seg_points = [sp for sp in seg_points if sp]
        if not seg_points:
            continue
        flat = [p["v"] for sp in seg_points for p in sp]
        series.append({"label": label, "kind": kind, "segments": seg_points,
                       "min": min(flat), "max": max(flat), "last": flat[-1]})
    # rewards: latest window snapshot, with n as the denominator
    rewards = [{"dim": d, "mean": v["mean"], "n": v["n"]}
               for d, v in sorted((cur.get("window", {}).get("rewards") or {}).items())]
    for m in markers:
        m["epoch_crossed"] = m.get("epoch") != cur.get("epoch")
    return {
        "name": name,
        "epoch": cur.get("epoch"),
        "epochs_seen": len(segs),
        "substrate_status": cur.get("substrate_status"),
        "health_grade": cur.get("health_grade"),
        "origin": cur.get("origin") or "",
        "mirror": bool(cur.get("origin")) and cur.get("origin") != LOCAL_ORIGIN,
        "coverage": {
            "window_days": cur.get("window_days"),
            "fields": _dig(cur, ("window", "fields")),
            "brain_calls": _dig(cur, ("window", "brain_calls")),
            "spans_total": cur.get("spans_total"),
            "snapshots": len(snaps),
        },
        "emotion": cur.get("emotion"),
        "senses": _dig(cur, ("window", "senses")) or {},
        "stance": _dig(cur, ("window", "stance")) or {},
        "series": series,
        "rewards": rewards,
        "markers": markers,
    }


def all_creatures(include_mirrors: bool = True) -> dict:
    out = []
    for p in sorted(STORE.glob("*.jsonl")):
        c = creature(p.stem)
        if c and (include_mirrors or not c["mirror"]):
            out.append(c)
    return {"creatures": out,
            "note": ("epoch splits break series; activity-dependent "
                     "indicators carry their coverage — empty ≠ healthy")}
