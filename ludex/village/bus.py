"""Village event bus — normalized scene timeline over repo artifacts.

Layer 2 of the village architecture (repo → bus → render). Scenes are
derived, payload-by-reference: the bus never re-authors narrative data,
it points at it. Scene shape (per the design draft):

    {t, kind: wake|field|reflect|heartbeat|report, actors, where, payload_ref}

P0 builds the timeline on demand from a full scan (cheap: ~2k-line span
files). The JSONL dump (`python -m ludex.village.bus --out …`) is the
durable research asset; live append arrives with P2.

Usage:
    from ludex.village.bus import scan_state, build_scenes
    python -m ludex.village.bus --habitat Mac-habitat --out creatures/.village/scenes.jsonl
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import yaml

from ludex.core.heartbeat import SKIP_DIRS

REPO_ROOT = Path(__file__).resolve().parents[2]

# field_name prefix → village facility (render hint only)
_FACILITY_RULES = (
    ("council", "council_hall"),
    ("forum", "forum_square"),
    ("wilderness", "wilderness_grove"),
    ("academy", "academy"),
    ("agora", "agora"),
    ("meet_", "agora"),
    ("lxm", "arena_harbor"),
    ("arena", "arena_harbor"),
)

# Scenes of the same field within this window merge into one gathering.
_FIELD_MERGE_S = 30 * 60
# Heartbeat pulses across creatures within this window = one run.
_HEARTBEAT_MERGE_S = 15 * 60


def _facility_for(field_name: str) -> str:
    low = field_name.lower()
    for prefix, facility in _FACILITY_RULES:
        if prefix in low:
            return facility
    return "forum_square"


def creature_dirs(base: Path, habitat: str = "") -> list[Path]:
    """Real creature dirs under `base`, optionally filtered by habitat.origin."""
    out = []
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name in SKIP_DIRS:
            continue
        ypath = d / "ludex.yaml"
        if not ypath.exists():
            continue
        if habitat:
            try:
                cfg = yaml.safe_load(ypath.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if (cfg.get("habitat") or {}).get("origin", "") != habitat:
                continue
        out.append(d)
    return out


def _iter_spans(cdir: Path):
    spans = cdir / "store" / "spans.jsonl"
    if not spans.exists():
        return
    for line in spans.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def scan_state(base: Path | None = None, habitat: str = "") -> list[dict]:
    """Current per-creature state — every field traces to an artifact."""
    base = base or (REPO_ROOT / "creatures")
    out = []
    for cdir in creature_dirs(base, habitat):
        try:
            cfg = yaml.safe_load((cdir / "ludex.yaml").read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        brain = cfg.get("brain") or {}
        st = {
            "name": cdir.name,
            "provider": brain.get("provider", ""),
            "model": brain.get("model", ""),
            "auth": brain.get("auth", ""),
            "substrate_status": brain.get("substrate_status", "live"),
            "session_count": cfg.get("session_count", 0),
            "born_at": cfg.get("born_at", 0),
            "last_field": None,       # {t, name}
            "last_reflect": None,     # {t, trigger}
            "last_heartbeat": None,   # {t, grade, outcome}
            "last_brain_call": None,  # t
            "bonds": [],
        }
        for span in _iter_spans(cdir):
            kind = span.get("kind", "")
            t = span.get("timestamp", 0)
            attrs = span.get("attributes") or {}
            if kind == "topos_sensed" and attrs.get("field_name"):
                st["last_field"] = {"t": t, "name": attrs["field_name"]}
            elif kind == "reflect":
                st["last_reflect"] = {"t": t, "trigger": attrs.get("trigger", "")}
            elif kind == "heartbeat_pulse":
                st["last_heartbeat"] = {
                    "t": t,
                    "grade": attrs.get("health_grade", ""),
                    "outcome": attrs.get("outcome", ""),
                }
            elif kind == "brain_call":
                st["last_brain_call"] = t
        bonds_dir = cdir / "bonds"
        if bonds_dir.is_dir():
            st["bonds"] = sorted(
                p.stem for p in bonds_dir.glob("*.md"))
        # emotion — projection of the organ's own baseline artifact (absent → None,
        # and the view draws nothing: no simulated feelings)
        try:
            bp = cdir / "emotion" / "baseline.json"
            if bp.exists():
                b = json.loads(bp.read_text(encoding="utf-8"))
                freq = b.get("dominant_emotions_freq") or {}
                st["emotion"] = {
                    "valence": b.get("avg_valence"),
                    "arousal": b.get("avg_arousal"),
                    "calm": b.get("avg_calm"),
                    "dominant": max(freq, key=freq.get) if freq else None,
                }
            else:
                st["emotion"] = None
        except Exception:
            st["emotion"] = None
        out.append(st)
    return out


def _transcript_index() -> dict[str, str]:
    """field_name → session-transcript JSON under experiments/ (repo-relative).
    Runners save '<field_name>_<ts>.json'; match by filename containment."""
    idx: dict[str, str] = {}
    exp = REPO_ROOT / "experiments"
    if not exp.is_dir():
        return idx
    for f in exp.glob("*/*.json"):
        idx[f.stem] = str(f.relative_to(REPO_ROOT))
    return idx


def _find_transcript(idx: dict[str, str], field_name: str) -> str | None:
    for stem, rel in idx.items():
        if field_name and field_name in stem:
            return rel
    return None


def _scan_field_and_reflect_scenes(base: Path, habitat: str) -> list[dict]:
    field_groups: dict[str, dict] = {}  # key: field_name|bucket
    scenes: list[dict] = []
    for cdir in creature_dirs(base, habitat):
        payload = f"creatures/{cdir.name}/store/spans.jsonl"
        for span in _iter_spans(cdir):
            kind = span.get("kind", "")
            t = span.get("timestamp", 0)
            attrs = span.get("attributes") or {}
            # Any span stamped with a field_name marks field presence —
            # topos_sensed is canonical, but pre-topos history stamped
            # brain_call/translation spans instead.
            if kind != "reflect" and attrs.get("field_name"):
                fname = attrs["field_name"]
                key = f"{fname}|{int(t // _FIELD_MERGE_S)}"
                g = field_groups.setdefault(key, {
                    "t": t, "kind": "field", "actors": [],
                    "where": _facility_for(fname),
                    "field_name": fname, "payload_ref": [],
                })
                g["t"] = min(g["t"], t)
                if cdir.name not in g["actors"]:
                    g["actors"].append(cdir.name)
                if payload not in g["payload_ref"]:
                    g["payload_ref"].append(payload)
            elif kind == "reflect":
                scenes.append({
                    "t": t, "kind": "reflect", "actors": [cdir.name],
                    "where": f"house:{cdir.name}",
                    "trigger": attrs.get("trigger", ""),
                    "payload_ref": [f"creatures/{cdir.name}/SELF.md"],
                })
            elif kind == "village_arrival":
                # a newborn's arrival — the mayor walks to welcome the new house
                scenes.append({
                    "t": span.get("t") or t, "kind": "arrival",
                    "actors": [span.get("who", cdir.name)],
                    "where": f"house:{cdir.name}",
                    "note": span.get("note", ""),
                    "payload_ref": [payload],
                })
    idx = _transcript_index()
    for g in field_groups.values():
        t = _find_transcript(idx, g.get("field_name", ""))
        if t:
            g["transcript_ref"] = t
    scenes.extend(field_groups.values())
    return scenes


def _scan_heartbeat_scenes(base: Path, habitat: str) -> list[dict]:
    pulses: list[tuple[float, str, str]] = []  # (t, creature, grade)
    for cdir in creature_dirs(base, habitat):
        for span in _iter_spans(cdir):
            if span.get("kind") == "heartbeat_pulse":
                attrs = span.get("attributes") or {}
                pulses.append((span.get("timestamp", 0), cdir.name,
                               attrs.get("health_grade", "")))
    pulses.sort()
    scenes = []
    run: list[tuple[float, str, str]] = []
    for p in pulses:
        if run and p[0] - run[-1][0] > _HEARTBEAT_MERGE_S:
            scenes.append(_heartbeat_run_scene(run))
            run = []
        run.append(p)
    if run:
        scenes.append(_heartbeat_run_scene(run))
    return scenes


def _heartbeat_run_scene(run: list[tuple[float, str, str]]) -> dict:
    return {
        "t": run[0][0], "kind": "heartbeat",
        "actors": [c for _, c, _ in run],
        "where": "village",
        "grades": {c: g for _, c, g in run if g},
        "payload_ref": ["creatures/.heartbeat.log"],
    }


_JOURNAL_DATE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})")


def _scan_report_scenes() -> list[dict]:
    scenes = []
    caretakers = REPO_ROOT / "caretakers"
    if not caretakers.is_dir():
        return scenes
    for who in sorted(p for p in caretakers.iterdir() if p.is_dir()):
        for jf in sorted((who / "journal").glob("*.md")):
            for line in jf.read_text(encoding="utf-8").splitlines():
                m = _JOURNAL_DATE.match(line)
                if not m:
                    continue
                t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d"))
                scenes.append({
                    "t": t, "kind": "report", "actors": [who.name],
                    "where": "mayor_office",
                    "title": line.lstrip("# ").strip(),
                    "payload_ref": [str(jf.relative_to(REPO_ROOT))],
                })
    return scenes


def build_scenes(base: Path | None = None, habitat: str = "") -> list[dict]:
    """Full normalized scene timeline, oldest first."""
    base = base or (REPO_ROOT / "creatures")
    scenes = (_scan_field_and_reflect_scenes(base, habitat)
              + _scan_heartbeat_scenes(base, habitat)
              + _scan_report_scenes())
    scenes.sort(key=lambda s: s["t"])
    return scenes


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Dump the village scene timeline.")
    parser.add_argument("--habitat", default="", help="filter by habitat.origin")
    parser.add_argument("--out", default="", help="write JSONL here (default: stdout summary)")
    args = parser.parse_args()

    scenes = build_scenes(habitat=args.habitat)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for s in scenes:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print(f"wrote {len(scenes)} scenes → {out}")
    else:
        kinds: dict[str, int] = {}
        for s in scenes:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        print(f"{len(scenes)} scenes: {kinds}")
        for s in scenes[-5:]:
            print(f"  {time.strftime('%m-%d %H:%M', time.localtime(s['t']))} "
                  f"{s['kind']:<9} {','.join(s['actors'])[:60]} @ {s['where']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
