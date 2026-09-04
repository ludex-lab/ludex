"""Two event definitions, and what the difference costs.

`should_consolidate` decides when a creature reflects. It counts "meaningful
events" and excludes three span kinds — brain_resolved, translation_applied,
heartbeat_pulse — with a comment saying cadence should track genuine activity
rather than the clock. It does NOT exclude the six sense kinds, and those fire
on every heartbeat. On a sensing creature the gate therefore counts the clock,
which is the thing the comment says it avoids.

Measured 2026-08-06 (M1 R9): of the 28 events pushing Wisp toward his
consolidation threshold, 20 were sensory; Slate 8 of 14. So the sense organs
change WHEN a creature reflects — an intervention on developmental cadence that
was never treated as one, and the reason the chronos CADENCE face is a separate
surface from the nowline utilization face that already reads MEASURED.

This module computes both definitions side by side over the ledger. It is
read-only: no brain calls, no writes, no creature is touched. Extracted from
research/metabolism-m1 at Ray's request so either lab can run it.

    python -m ludex.cadence.event_definitions --sweep
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ludex.core.consolidation import (
    _NON_EVENT_KINDS, _conso_store, _meaningful_events, should_consolidate,
    ACCUMULATION_EVENTS, HARD_FLOOR_DAYS, HIATUS_TIMEOUT_DAYS,
)

# What the gate excludes, imported rather than copied — a second copy would
# drift from the decision it is supposed to mirror.
GATE_EXCLUDED = frozenset(_NON_EVENT_KINDS)

SENSE_KINDS = frozenset({"topos_sensed", "chronos_sensed", "allos_sensed",
                         "auto_sensed", "physis_sensed"})

# The lived-event set, stated in full rather than derived from GATE_EXCLUDED.
# The two definitions differ in BOTH directions and a superset would hide half
# of that: the gate drops `translation_applied`, which M1 counts as a lived
# event, while M1 drops the five sense kinds and the weight check, which the
# gate counts. Writing this as GATE_EXCLUDED | SENSE_KINDS would silently add
# translation_applied to M1's definition and put the analyser out of step with
# its own frozen pre-registration.
LIVED_EXCLUDED = SENSE_KINDS | frozenset({
    "brain_resolved", "heartbeat_pulse", "weight_check",
})

# The asymmetry itself, so a caller can report it rather than rediscover it.
GATE_ONLY = GATE_EXCLUDED - LIVED_EXCLUDED      # gate ignores, lived counts
LIVED_ONLY = LIVED_EXCLUDED - GATE_EXCLUDED     # lived ignores, gate counts


def _fires(events: int, days: float) -> bool:
    """The gate's own rule, applied to whichever event count is passed in."""
    if days < HARD_FLOOR_DAYS:
        return False
    return events >= ACCUMULATION_EVENTS or (days >= HIATUS_TIMEOUT_DAYS and events >= 1)


def counterfactual(creature_dir: str | Path) -> dict | None:
    """Actual gate state vs the same rule on lived events only.

    Returns None when the creature has no ledger here — Ray-habitat spans are
    not tracked in this repo (.gitignore excludes *.jsonl and only Mac creatures
    were ever force-added), so a caller sweeping "both habitats" gets an honest
    absence rather than a zero.
    """
    d = Path(creature_dir)
    if not (d / "store" / "spans.jsonl").exists():
        return None
    dec = should_consolidate(d)
    win = _meaningful_events(_conso_store(d).spans(), since=dec.window_start,
                             inclusive=(dec.last_consolidated_on is None))
    lived = [e for e in win if e.get("kind") not in LIVED_EXCLUDED]
    would = _fires(len(lived), dec.days_elapsed)
    return {
        "creature": d.name,
        "window_start": dec.window_start,
        "days_elapsed": round(dec.days_elapsed, 2),
        "gate_events": len(win),
        "lived_events": len(lived),
        "excluded_by_lived_definition": len(win) - len(lived),
        "gate_fires": dec.fire,
        "fires_on_lived_only": would,
        "diverges": dec.fire != would,
        "gate_reason": dec.reason,
    }


def _marker_times(creature_dir: Path) -> list[float]:
    """Every past window boundary, read the way the gate reads the latest one.

    `last_consolidated_on` anchors on the `consolidated_on_ts` frontmatter of
    reflection files and returns the max; each file carries its own, so the full
    history is recoverable by reading all of them.

    Anchoring on the consolidation SPAN instead is wrong by four events per
    window, and wrong in the direction that matters: the marker is written
    before the retrospective runs, so the span sits ~15-45s later and the gap
    contains the consolidation's OWN brain_call plus the heartbeat's sense
    pulses. Counting those into the window that produced them makes the
    reflection part of its own evidence — the sixth appearance of A1 in this
    program, and the first one inside a tool I wrote to detect it.
    """
    rdir = creature_dir / "reflections"
    if not rdir.is_dir():
        return []
    out = []
    for f in sorted(rdir.glob("*.md")):
        try:
            head = f.read_text(encoding="utf-8")[:600]
        except Exception:
            continue
        for line in head.splitlines():
            line = line.strip()
            if line.startswith("consolidated_on_ts:"):
                try:
                    out.append(float(line.split(":", 1)[1].strip()))
                except ValueError:
                    pass
    return out


def history(creature_dir: str | Path) -> list[dict]:
    """Every PAST consolidation, recomputed under both definitions.

    The current-window function answers "would it fire now"; this answers "did
    the two definitions ever disagree", which is the cell's actual question and
    is already answerable. The gate's constants and exclusion set have not
    changed since 2026-06-02 (a1f6c20b, the D-085 spike) and the earliest fire
    in the ledger is 06-03, so today's rule applied to past windows is not an
    anachronism — every recorded fire happened under exactly this rule.

    A window where the gate fired but the lived-only count would not have is the
    finding: the creature reflected on sensor volume rather than on lived
    events.
    """
    d = Path(creature_dir)
    if not (d / "store" / "spans.jsonl").exists():
        return []
    spans = sorted(_conso_store(d).spans(), key=lambda e: e.get("timestamp", 0))
    fires = sorted(_marker_times(d))
    if not fires:
        return []
    out = []
    prev = min((e.get("timestamp", 0) for e in spans), default=0)
    for i, t in enumerate(fires):
        win = [e for e in spans
               if prev < e.get("timestamp", 0) <= t
               and e.get("kind") not in GATE_EXCLUDED]
        lived = [e for e in win if e.get("kind") not in LIVED_EXCLUDED]
        days = (t - prev) / 86400.0
        out.append({
            "creature": d.name, "n": i + 1, "at": t,
            "days_elapsed": round(days, 2),
            "gate_events": len(win), "lived_events": len(lived),
            "excluded_by_lived_definition": len(win) - len(lived),
            "fires_on_lived_only": _fires(len(lived), days),
            # the gate DID fire — that is why this window is here
            "diverges": not _fires(len(lived), days),
        })
        prev = t
    return out


def history_sweep(creatures_root: str | Path = "creatures") -> list[dict]:
    root = Path(creatures_root)
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "ludex.yaml").exists():
            out += history(d)
    return out


def sweep(creatures_root: str | Path = "creatures") -> dict:
    root = Path(creatures_root)
    rows, missing = [], []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not (d / "ludex.yaml").exists():
            continue
        r = counterfactual(d)
        (rows if r else missing).append(r or d.name)
    return {"rows": rows, "no_ledger_here": missing}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creature")
    ap.add_argument("--root", default="creatures")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--history", action="store_true",
                    help="recompute every PAST consolidation under both definitions")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.creature and not a.sweep:
        r = counterfactual(Path(a.root) / a.creature)
        print(json.dumps(r, ensure_ascii=False, indent=1) if r
              else f"no ledger for {a.creature} in this repo")
        return 0

    if a.history:
        rows = history_sweep(a.root)
        div = [r for r in rows if r["diverges"]]
        print(f"{'creature':10} {'#':>2} {'gate':>5} {'lived':>6} {'excl':>5} "
              f"{'days':>7}  on-lived")
        for r in rows:
            print(f"{r['creature']:10} {r['n']:>2} {r['gate_events']:>5} "
                  f"{r['lived_events']:>6} {r['excluded_by_lived_definition']:>5} "
                  f"{r['days_elapsed']:>7}  {r['fires_on_lived_only']!s:>5}"
                  + ("   <- DIVERGES" if r["diverges"] else ""))
        print(f"\n{len(rows)} past consolidations, {len(div)} diverge "
              f"({100*len(div)/len(rows):.0f}%)" if rows else "no history")
        return 0

    out = sweep(a.root)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    print(f"{'creature':10} {'gate':>5} {'lived':>6} {'excl':>5} {'days':>7}  "
          f"{'fires':>6} {'on-lived':>9}")
    for r in sorted(out["rows"], key=lambda x: -x["excluded_by_lived_definition"]):
        print(f"{r['creature']:10} {r['gate_events']:>5} {r['lived_events']:>6} "
              f"{r['excluded_by_lived_definition']:>5} {r['days_elapsed']:>7} "
              f"{r['gate_fires']!s:>6} {r['fires_on_lived_only']!s:>9}"
              + ("   <- DIVERGES" if r["diverges"] else ""))
    div = [r for r in out["rows"] if r["diverges"]]
    print(f"\n{len(out['rows'])} creatures read, {len(div)} diverge")
    if out["no_ledger_here"]:
        print(f"no ledger in this repo ({len(out['no_ledger_here'])}): "
              f"{out['no_ledger_here']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
