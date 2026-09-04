"""Island terrain engine — the map as the ecosystem's geographic autobiography.

Generates the village island (docs/village-terrain-design.md): a 32×32
organic landmass whose PLACEMENT comes from real ecosystem data —
bonds make neighbors (R1), born_at makes rings of seniority from the
plaza (R2), dormant creatures settle by the forest (R3), facility usage
sets road proximity (R4). Deterministic: same inputs → same island
(seed = cohort hash). The land registry is append-only: a plot, once
assigned, never moves; new creatures clear new lots (개간 events).

Usage:
    from ludex.village.terrain import build_map
    python -m ludex.village.terrain --habitat Mac-habitat [--render out.png]
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

from ludex.village.bus import REPO_ROOT, scan_state, build_scenes

GRID = 96                    # 대개간 v2 (2026-07-18): the island triples; the
V = 32                       # old 32-grid village core sits centered (+V resurvey)
CX = CY = (GRID - 1) / 2


def _registry_path(habitat: str) -> Path:
    tag = habitat.lower().replace(" ", "-") or "all"
    return REPO_ROOT / "creatures" / ".village" / f"land-registry-{tag}.json"

# tile codes → skin ground slots (renderer maps missing slots to fallbacks)
# W water_deep / w water / s sand / g grass / f grass_flowers / b grass_b
# p path / P plaza

FACILITY_RING = ["council_hall", "academy", "agora"]  # usage-ranked onto ring anchors
RING_ANCHORS = [(15 + V, 9 + V), (22 + V, 15 + V), (15 + V, 22 + V),
                (9 + V, 15 + V), (21 + V, 10 + V), (10 + V, 21 + V)]  # N E S W NE SW
FIXED_SPOTS = {
    "mayor_office": (14 + V, 11 + V),
    "notice_board": (18 + V, 12 + V),
    "forum_square": (17 + V, 19 + V),
    "wilderness_grove": (8 + V, 9 + V),
    # arena_harbor placed on the SE coast at generation time
}
# 개청 (2026-08-11): the seven desks get buildings. Spots chosen off the
# facility loop with chebyshev>=2 from every plot in the live registry —
# adding a plot is an event (개청), never a reshuffle. The scouts watch
# from the forest edge; the registrar sits by the agora.
DESK_SPOTS = {
    "chronicle_hall": (10 + V, 7 + V),
    "editors_desk": (17 + V, 8 + V),
    "research_institute": (24 + V, 12 + V),
    "counsel_office": (25 + V, 19 + V),
    "registry_office": (17 + V, 23 + V),
    "scouts_tower": (9 + V, 14 + V),
}
FOREST_BOX = (3 + V, 3 + V, 11 + V, 12 + V)     # col0, row0, col1, row1
HARBOR_SECTOR = (20 + V, 18 + V)   # search start for the SE coast


def _seed_of(states: list[dict]) -> int:
    key = "|".join(f"{s['name']}:{s.get('born_at', 0)}" for s in sorted(
        states, key=lambda s: s["name"]))
    return int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)


def _wobble_fn(seed: int):
    p1, p2, p3 = (seed % 7) * 0.9, (seed % 11) * 0.7, (seed % 13) * 0.5
    def wob(c: float, r: float) -> float:
        return (1.5 * math.sin(c * 0.55 + p1) + 1.2 * math.cos(r * 0.62 + p2)
                + 0.8 * math.sin((c + r) * 0.38 + p3))
    return wob


def _dist(c, r, c2=CX, r2=CY):
    return math.hypot(c - c2, r - r2)


class Island:
    def __init__(self, states: list[dict], usage: dict[str, int]):
        self.states = states
        self.usage = usage
        self.seed = _seed_of(states)
        wob = _wobble_fn(self.seed)
        self.land = [[_dist(c, r) < 38.0 + wob(c, r) * 1.9 for c in range(GRID)]
                     for r in range(GRID)]
        # plaza + ring must be land
        for r in range(9 + V, 23 + V):
            for c in range(9 + V, 23 + V):
                if _dist(c, r) < 8.5:
                    self.land[r][c] = True
        self.tiles = [["W"] * GRID for _ in range(GRID)]
        self.plots: dict[str, dict] = {}
        self.props: list[dict] = []
        self.events: list[dict] = []

    # ---- terrain ----
    def paint_base(self):
        for r in range(GRID):
            for c in range(GRID):
                if not self.land[r][c]:
                    near_land = any(
                        0 <= r + dr < GRID and 0 <= c + dc < GRID and self.land[r + dr][c + dc]
                        for dr in (-1, 0, 1) for dc in (-1, 0, 1))
                    self.tiles[r][c] = "w" if near_land else "W"
                else:
                    near_water = any(
                        not (0 <= r + dr < GRID and 0 <= c + dc < GRID) or not self.land[r + dr][c + dc]
                        for dr in (-1, 0, 1) for dc in (-1, 0, 1))
                    if near_water:
                        self.tiles[r][c] = "s"
                    else:
                        h = (c * 7 + r * 13 + self.seed) % 100
                        self.tiles[r][c] = "f" if h < 7 else ("b" if h < 30 else "g")

    def paint_paths(self):
        for r in range(14 + V, 18 + V):
            for c in range(14 + V, 18 + V):
                self.tiles[r][c] = "P"
        # facility loop: square ring radius 6
        for c in range(10 + V, 22 + V):
            for r in (10 + V, 21 + V):
                if self.land[r][c]:
                    self.tiles[r][c] = "p"
        for r in range(10 + V, 22 + V):
            for c in (10 + V, 21 + V):
                if self.land[r][c]:
                    self.tiles[r][c] = "p"
        # plaza gates to the loop
        for r in range(11 + V, 14 + V):
            self.tiles[r][15 + V] = "p"
        for r in range(18 + V, 21 + V):
            self.tiles[r][16 + V] = "p"
        for c in range(11 + V, 14 + V):
            self.tiles[15 + V][c] = "p"
        for c in range(18 + V, 21 + V):
            self.tiles[16 + V][c] = "p"

    def spur(self, c0: int, r0: int):
        """Straight col-then-row path from (c0,r0) to the nearest loop tile."""
        tc = min(max(c0, 10 + V), 21 + V)
        tr = min(max(r0, 10 + V), 21 + V)
        step = 1 if tc > c0 else -1
        for c in range(c0, tc + step, step):
            if self.tiles[r0][c] in ("g", "b", "f", "s"):
                self.tiles[r0][c] = "p"
        step = 1 if tr > r0 else -1
        for r in range(r0, tr + step, step):
            if self.tiles[r][tc] in ("g", "b", "f", "s"):
                self.tiles[r][tc] = "p"

    # ---- placement ----
    def place_harbor(self):
        # SE coast harbor: walk SE until the last land tile
        c, r = HARBOR_SECTOR
        while c + 1 < GRID and r + 1 < GRID and self.land[r + 1][c + 1]:
            c, r = c + 1, r + 1
        self.plots["facility:arena_harbor"] = {"col": c, "row": r}

    def place_facilities(self):
        for fid, (c, r) in FIXED_SPOTS.items():
            self.plots[f"facility:{fid}"] = {"col": c, "row": r}
        self.place_harbor()
        ranked = sorted(FACILITY_RING, key=lambda f: -self.usage.get(f, 0))
        for i, fid in enumerate(ranked):
            c, r = RING_ANCHORS[i]
            self.plots[f"facility:{fid}"] = {"col": c, "row": r}
        for fid, (c, r) in DESK_SPOTS.items():
            self.plots[f"facility:{fid}"] = {"col": c, "row": r}

    def _house_candidates(self) -> list[tuple[int, int]]:
        out = []
        for r in range(GRID):
            for c in range(GRID):
                if not self.land[r][c] or self.tiles[r][c] in ("p", "P", "s"):
                    continue
                d = _dist(c, r)
                if not (7.2 <= d <= 11.5):
                    continue
                if FOREST_BOX[0] <= c <= FOREST_BOX[2] and FOREST_BOX[1] <= r <= FOREST_BOX[3]:
                    continue
                if abs(c - self.plots["facility:arena_harbor"]["col"]) <= 2 and \
                   abs(r - self.plots["facility:arena_harbor"]["row"]) <= 2:
                    continue
                out.append((c, r))
        return out

    def _free(self, cands, taken):
        return [p for p in cands if all(
            max(abs(p[0] - t[0]), abs(p[1] - t[1])) >= 3 for t in taken)]

    def place_houses(self, registry: dict):
        cands = self._house_candidates()
        taken = [(v["col"], v["row"]) for v in self.plots.values()]
        # honor the registry first — plots never move
        for name, plot in registry.get("houses", {}).items():
            self.plots[f"house:{name}"] = {"col": plot["col"], "row": plot["row"]}
            taken.append((plot["col"], plot["row"]))
        bonds = {s["name"].lower(): set(s.get("bonds", [])) for s in self.states}
        placed = {k.split(":", 1)[1].lower(): (v["col"], v["row"])
                  for k, v in self.plots.items() if k.startswith("house:")}
        order = sorted(self.states, key=lambda s: (
            s.get("substrate_status") == "dormant", s.get("born_at", 0)))
        forest_c = ((FOREST_BOX[0] + FOREST_BOX[2]) / 2, (FOREST_BOX[1] + FOREST_BOX[3]) / 2 + 2)
        for s in order:
            name = s["name"]
            if f"house:{name}" in self.plots:
                continue
            free = self._free(cands, taken)
            if not free:
                continue
            dormant = s.get("substrate_status") == "dormant"
            partners = [placed[b] for b in bonds.get(name.lower(), ())
                        if b in placed]
            if dormant:                      # R3: the quiet forest edge
                pick = min(free, key=lambda p: _dist(p[0], p[1], *forest_c))
            elif partners:                   # R1: bonds make neighbors
                pick = min(free, key=lambda p: min(
                    _dist(p[0], p[1], *q) for q in partners) + _dist(p[0], p[1]) * 0.2)
            else:                            # R2: seniority toward the plaza
                pick = min(free, key=lambda p: _dist(p[0], p[1]))
            self.plots[f"house:{name}"] = {"col": pick[0], "row": pick[1]}
            placed[name.lower()] = pick
            taken.append(pick)
            self.events.append({"t": time.time(), "type": "개간",
                                "who": name, "col": pick[0], "row": pick[1]})

    def place_props(self):
        for r in range(FOREST_BOX[1], FOREST_BOX[3] + 1):
            for c in range(FOREST_BOX[0], FOREST_BOX[2] + 1):
                if self.tiles[r][c] in ("g", "b") and (c * 5 + r * 11 + self.seed) % 10 < 6:
                    kind = "tree_a" if (c + r) % 3 else "tree_b"
                    self.props.append({"type": kind, "col": c, "row": r})
        for r in range(GRID):
            for c in range(GRID):
                if self.tiles[r][c] in ("g", "b") and (c * 17 + r * 23 + self.seed) % 100 < 3:
                    if all(max(abs(c - v["col"]), abs(r - v["row"])) >= 2
                           for v in self.plots.values()):
                        self.props.append({"type": "tree_a" if (c + r) % 2 else "rock",
                                           "col": c, "row": r})

    def carve_spurs(self):
        for key, v in self.plots.items():
            if key.startswith("house:") or key == "facility:arena_harbor":
                self.spur(v["col"], v["row"])


def build_map(habitat: str = "") -> dict:
    states = scan_state(habitat=habitat)
    usage: dict[str, int] = {}
    for s in build_scenes(habitat=habitat):
        if s["kind"] == "field":
            usage[s["where"]] = usage.get(s["where"], 0) + 1

    reg_path = _registry_path(habitat)
    registry = {"houses": {}, "facilities": {}}
    if reg_path.exists():
        registry = json.loads(reg_path.read_text(encoding="utf-8"))
    # 대개간 v2 migration: the old 32-grid plots resurvey +V into the new
    # island center — relative layout unchanged (the registry principle is
    # "no reshuffling", and a documented one-time survey is an event, not a
    # reshuffle). Coastal facilities re-place on the NEW coast.
    old_grid = registry.get("grid", 32)
    if old_grid < GRID:
        off = (GRID - old_grid) // 2
        for d in (registry.get("houses", {}), registry.get("facilities", {})):
            for v in d.values():
                v["col"] += off
                v["row"] += off
        coastal = registry.get("facilities", {}).pop("arena_harbor", None)
        registry.setdefault("events", []).append({
            "t": time.time(), "type": "대개간",
            "note": f"grid {old_grid}→{GRID}: 전 필지 +{off} 재측량 (상대 배치 불변); "
                    f"arena_harbor는 새 해안으로 재배치"})
        registry["grid"] = GRID

    isl = Island(states, usage)
    isl.paint_base()
    isl.paint_paths()
    if registry.get("facilities"):
        for fid, plot in registry["facilities"].items():
            isl.plots[f"facility:{fid}"] = dict(plot)
        if "facility:arena_harbor" not in isl.plots:
            isl.place_harbor()          # coastal facility follows the coast
    else:
        isl.place_facilities()
    # 개청: fixed facilities added AFTER a registry already exists join it
    # as new plots (an event), leaving every existing plot untouched.
    for fid, (c, r) in {**FIXED_SPOTS, **DESK_SPOTS}.items():
        key = f"facility:{fid}"
        if key not in isl.plots:
            isl.plots[key] = {"col": c, "row": r}
            isl.events.append({"t": time.time(), "type": "개청",
                               "who": fid, "col": c, "row": r})
    isl.place_houses(registry)
    isl.carve_spurs()
    isl.place_props()

    # persist the registry (append-only; first write fixes facilities)
    new_reg = {
        "comment": "land registry — append-only; plots never move (개간 only)",
        "grid": GRID,
        "facilities": {k.split(":", 1)[1]: v for k, v in isl.plots.items()
                       if k.startswith("facility:")},
        "houses": {k.split(":", 1)[1]: v for k, v in isl.plots.items()
                   if k.startswith("house:")},
        "events": (registry.get("events") or []) + isl.events,
    }
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text(json.dumps(new_reg, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    return {
        "grid": GRID, "seed": isl.seed,
        "tiles": ["".join(row) for row in isl.tiles],
        "plots": isl.plots,
        "props": isl.props,
        "layout_events": new_reg["events"][-20:],
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Generate the village island map.")
    parser.add_argument("--habitat", default="")
    parser.add_argument("--render", default="", help="write a PIL preview PNG")
    parser.add_argument("--out", default="", help="write map.json here")
    args = parser.parse_args()
    m = build_map(args.habitat)
    counts: dict[str, int] = {}
    for row in m["tiles"]:
        for ch in row:
            counts[ch] = counts.get(ch, 0) + 1
    land = sum(v for k, v in counts.items() if k not in "Ww")
    print(f"seed={m['seed']} tiles={counts} land={land} "
          f"plots={len(m['plots'])} props={len(m['props'])} events={len(m['layout_events'])}")
    for k, v in sorted(m["plots"].items()):
        print(f"  {k:28} ({v['col']},{v['row']})")
    if args.out:
        Path(args.out).write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    if args.render:
        _render(m, args.render)
        print("render →", args.render)
    return 0


def _render(m: dict, out: str):
    from PIL import Image
    SKIN = REPO_ROOT / "web" / "static" / "village" / "skins" / "sunny-suburb-bloom"
    TW, TH = 64, 32
    colors = {"W": (47, 95, 134), "w": (74, 127, 168), "s": (216, 200, 160),
              "g": (127, 176, 105), "f": (150, 190, 120), "b": (117, 166, 98),
              "p": (196, 176, 150), "P": (208, 196, 178)}
    tile_img = {}
    for code, slot in {"g": "grass", "f": "grass_flowers", "p": "path",
                       "w": "water", "W": "water", "P": "path", "b": "grass"}.items():
        f = SKIN / "ground" / f"{slot}.png"
        if f.exists():
            tile_img[code] = Image.open(f).convert("RGBA").resize((TW, TH))
    W = GRID * TW + 100
    H = GRID * TH + 300
    img = Image.new("RGB", (W, H), (13, 17, 23))
    ox = W // 2
    def pos(c, r): return (ox + (c - r) * TW // 2, 40 + (c + r) * TH // 2)
    for r in range(GRID):
        for c in range(GRID):
            code = m["tiles"][r][c]
            x, y = pos(c, r)
            if code in tile_img:
                img.paste(tile_img[code], (x - TW // 2, y - TH // 2), tile_img[code])
            else:
                t = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
                from PIL import ImageDraw
                d = ImageDraw.Draw(t)
                d.polygon([(TW // 2, 0), (TW, TH // 2), (TW // 2, TH), (0, TH // 2)],
                          fill=colors[code])
                img.paste(t, (x - TW // 2, y - TH // 2), t)
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    for p in m["props"]:
        x, y = pos(p["col"], p["row"])
        d.ellipse([x - 5, y - 14, x + 5, y - 2],
                  fill=(46, 90, 60) if "tree" in p["type"] else (120, 120, 124))
    for k, v in m["plots"].items():
        x, y = pos(v["col"], v["row"])
        fac = k.startswith("facility:")
        d.rectangle([x - 9, y - 16, x + 9, y], fill=(180, 150, 120) if fac else (222, 196, 168),
                    outline=(90, 70, 50))
        d.text((x - 8, y + 2), k.split(":", 1)[1][:8], fill=(200, 208, 216))
    img.save(out)


if __name__ == "__main__":
    raise SystemExit(main())
