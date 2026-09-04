"""Village onboarding — a newly born creature reports to the mayor and appears.

The gap this closes (2026-07-20): Forge/CLI birth created a creature but never
wired it into the village, so it stayed invisible — the state scan filters by
`habitat.origin`, houses are only assigned on a map build, and no event
announced the arrival. `onboard_creature()` is the single call both paths use.

What onboarding does (each step idempotent):
  1. origin marker  — set brain-independent `habitat.origin` so the village
     state scan includes the creature (the visibility gate).
  2. house          — trigger a map build; the land registry assigns a plot
     (append-only 개간 event; existing plots never move).
  3. outfit         — assign a wardrobe garment (caretaker-editable outfits.json;
     kept if already present).
  4. arrival event  — append a village `arrival` scene so the mayor's walk
     visits the new house (head is automatic: lineage face from the brain).

Head/face and lineage color are automatic in the renderer (brain → face_*),
so onboarding does not touch them.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ludex.village.bus import REPO_ROOT

WARDROBE = ["body_tunic", "body_robe", "body_apron", "body_cloak", "body_vest", "body_scarf"]


def _creature_dir(name: str) -> Path:
    return REPO_ROOT / "creatures" / name


def _set_origin(name: str, origin: str) -> bool:
    """Set habitat.origin in the creature's ludex.yaml (visibility gate).
    Returns True if changed."""
    import yaml
    p = _creature_dir(name) / "ludex.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    hab = cfg.setdefault("habitat", {})
    if hab.get("origin") == origin:
        return False
    hab["origin"] = origin
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return True


def _assign_outfit(name: str) -> str:
    """Assign a wardrobe garment if the creature has none. Deterministic by
    name so re-runs are stable; caretaker can edit outfits.json afterward."""
    p = REPO_ROOT / "creatures" / ".village" / "outfits.json"
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    if name in data and data[name]:
        return data[name]
    h = 7
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    garment = WARDROBE[h % len(WARDROBE)]
    data[name] = garment
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return garment


def _arrival_event(name: str, habitat: str, plot: dict) -> bool:
    """Append an arrival scene so the bus surfaces it and the mayor walks to
    welcome the new house. Returns True when a span was written.

    View principle: this is a real event (a birth into the habitat), not a
    simulation — and a real event happens once. This wrote a hand-rolled dict
    with `t`/`who` instead of the canonical Span, so every arrival line failed
    the structural validator on creature/timestamp/attributes, and re-running
    onboarding appended the same arrival again: the house and the outfit were
    idempotent, the ledger was not (이음, 2026-08-26, five Studio residents).

    Legacy lines are NOT rewritten — the ledger is append-only, and a past
    record in an old shape is still evidence of what happened.
    """
    from ludex.core.store import LudexStore, Span

    store = LudexStore.for_creature(str(_creature_dir(name)))
    col, row = plot.get("col"), plot.get("row")
    for prior in store.spans(kind="village_arrival"):
        a = prior.get("attributes") or {}
        # legacy lines carry the fields at top level; read both shapes
        p_hab = a.get("habitat", prior.get("habitat"))
        p_col = a.get("col", prior.get("col"))
        p_row = a.get("row", prior.get("row"))
        if (p_hab, p_col, p_row) == (habitat, col, row):
            return False
    store.append(Span(
        kind="village_arrival",
        creature=name,
        attributes={
            "habitat": habitat, "col": col, "row": row,
            "note": f"{name} arrived in the village — house cleared at "
                    f"({col},{row}).",
        },
    ))
    return True


def onboard_creature(name: str, habitat: str = "Mac-habitat") -> dict:
    """Wire a newly-born creature into the village. Idempotent. Returns a
    report dict: {name, house, outfit, origin_set, appeared}."""
    from ludex.village.terrain import build_map

    origin_set = _set_origin(name, habitat)
    # build the map — place_houses assigns a plot to any creature without one
    # (append-only registry, 개간 event). This is the mayor "giving land".
    m = build_map(habitat)
    plot = m.get("plots", {}).get(f"house:{name}")
    outfit = _assign_outfit(name)
    if plot:
        _arrival_event(name, habitat, plot)
    return {
        "name": name,
        "origin_set": origin_set,
        "house": plot,
        "outfit": outfit,
        "appeared": plot is not None,
    }


if __name__ == "__main__":
    import sys
    for nm in sys.argv[1:]:
        print(json.dumps(onboard_creature(nm), ensure_ascii=False))
