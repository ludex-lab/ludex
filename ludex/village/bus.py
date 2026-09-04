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
                # a newborn's arrival — the mayor walks to welcome the new house.
                # Two shapes live here: canonical spans (creature/timestamp/
                # attributes) and pre-2026-08-26 lines that carried t/who/note
                # at the top level. Both are real arrivals; the old ones are not
                # rewritten, so this reads either.
                scenes.append({
                    "t": t or span.get("t"), "kind": "arrival",
                    "actors": [span.get("creature") or span.get("who", cdir.name)],
                    "where": f"house:{cdir.name}",
                    "note": attrs.get("note", span.get("note", "")),
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


# ---- village institutions (2026-08 governance layer) ------------------------
# Sources are the village's own file ledgers — desk goal ledgers, the postal
# service, working-meeting records, rulings, board notices, task cards. Same
# contract as everything above: derive and point, never re-author. The
# caretaker session ledger is read ONLY for the timestamp/creature/note of
# duty sessions — its measurement columns are never loaded into a scene, so
# the viewer stays blind to them by construction.

_OFFICE_FACILITY = {
    "chronicle": "chronicle_hall", "editing": "editors_desk",
    "research": "research_institute", "counsel": "counsel_office",
    "registry": "registry_office", "scouts": "scouts_tower",
    "agora": "agora",
}
_DUTY_OFFICE = re.compile(r"goal drive:\s*(\w+)|reveille:\s*(\w+)")
_FNAME_DATE = re.compile(r"^(\d{8})")
_LETTER_NAME = re.compile(r"^(\d{8})-from-([a-z]+)")
_TASK_HIST = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2})\s*(.*)")


def _fname_t(name: str, fallback: float) -> float:
    m = _FNAME_DATE.match(name)
    if not m:
        return fallback
    return time.mktime(time.strptime(m.group(1), "%Y%m%d")) + 12 * 3600


def _first_heading(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip()
    return path.stem


def _scan_duty_scenes() -> list[dict]:
    led = REPO_ROOT / "research" / "metabolism-m1" / "caretaker_ledger.jsonl"
    scenes: list[dict] = []
    if not led.exists():
        return scenes
    for line in led.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") != "duty_session":
            continue
        note = row.get("note", "")
        m = _DUTY_OFFICE.search(note)
        office = (m.group(1) or m.group(2)) if m else ""
        office = office if office in _OFFICE_FACILITY else ""
        scenes.append({
            "t": row.get("ts", 0), "kind": "duty",
            "actors": [row.get("creature", "?")],
            "where": _OFFICE_FACILITY.get(office, "council_hall"),
            "office": office,
            "payload_ref": [f"village/desks/{office}/goals.md" if office
                            else "village/reveille.log"],
        })
    return scenes


def _scan_letter_scenes() -> list[dict]:
    post = REPO_ROOT / "village" / "post"
    scenes: list[dict] = []
    if not post.is_dir():
        return scenes
    proper = {d.name.lower(): d.name for d in (REPO_ROOT / "creatures").iterdir()
              if d.is_dir()}
    for box in sorted(p for p in post.iterdir() if p.is_dir()):
        for f in sorted((box / "inbox").glob("**/*.md")):
            m = _LETTER_NAME.match(f.name)
            if not m:
                continue
            sender = proper.get(m.group(2), m.group(2).capitalize())
            # mtime is the true delivery moment when it agrees with the
            # filename's date (local-first); across clones it drifts, so a
            # mismatched mtime falls back to the filename date at noon.
            mt = f.stat().st_mtime
            t = mt if time.strftime("%Y%m%d", time.localtime(mt)) == m.group(1) \
                else _fname_t(f.name, mt)
            scenes.append({
                "t": t,
                "kind": "letter", "actors": [sender, box.name],
                "where": f"house:{box.name}",
                "read": f.parent.name == "read",
                "payload_ref": [str(f.relative_to(REPO_ROOT))],
            })
    return scenes


# Where session transcripts live. A runner that writes somewhere not on this
# list is invisible to the village — which is how the founding agora itself
# (five phases, twenty voices, 2026-08-25) ended up with zero scenes while a
# routine letter the same evening had one. The transcripts were filed where
# they belonged as records (village/agora/), and the scanner was still watching
# where the first ones happened to land. Adding an output location means adding
# a line here; test_village_founding_is_visible holds that door.
_SESSION_GLOBS = [
    # (root, glob, kind, where) — `where` is the 3D location the scene poses at
    ("research/village-founding", "*meeting*.json", "meeting", "council_hall"),
    ("village/agora/founding-briefs", "phase*-result.json", "agora", "agora"),
    ("village/agora", "*-result.json", "agora", "agora"),
    ("village/desks", "*/*-result.json", "meeting", "council_hall"),
]


def _scan_session_scenes() -> list[dict]:
    """Session transcripts from every registered root (see _SESSION_GLOBS)."""
    scenes: list[dict] = []
    seen: set[str] = set()
    for root, pattern, kind, where in _SESSION_GLOBS:
        d = REPO_ROOT / root
        if not d.is_dir():
            continue
        for f in sorted(d.glob(pattern)):
            rel = str(f.relative_to(REPO_ROOT))
            if rel in seen:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if data.get("outcome") != "ok" or not data.get("participants"):
                continue
            seen.add(rel)
            # A filename's leftovers are not a title: 20260826-worksession-
            # result.json reduced to "result", which is what the published
            # Day 19 caption nearly said. Strip the date and the generic
            # tail, and fall back to the desk the session belongs to.
            title = re.sub(r"^\d{8}[-_]?", "", f.stem)
            title = re.sub(r"[-_]?(result|results|transcript)$", "", title)
            title = title.replace("-", " ").replace("_", " ").strip()
            desk = f.parent.name
            if not title or title in ("meeting", "worksession", "session"):
                title = f"{desk} {title}".strip() if desk not in (root, "") else desk
            elif desk not in title and desk not in ("founding-briefs", ""):
                title = f"{desk} {title}"
            scenes.append({
                "t": data.get("started_at", f.stat().st_mtime), "kind": kind,
                "actors": data["participants"], "where": where,
                "title": title, "turns": len(data.get("turns", [])),
                "payload_ref": [rel],
            })
    return scenes


def _scan_rollcall_scenes() -> list[dict]:
    """Roll-call rounds — a list of speakers, not a meeting transcript.

    The ratification consent round of 2026-08-25 is the shape this exists for:
    twenty residents each speaking once, which is one event in the village even
    though the file is a roster. Kept separate from _scan_session_scenes rather
    than bent into it, because a roll call has no facilitator and no turns —
    flattening the two shapes would lose that.
    """
    scenes: list[dict] = []
    for f in sorted((REPO_ROOT / "village" / "agora").glob("**/*consent*.json")):
        try:
            rows = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(rows, list) or not rows:
            continue
        # A resident who spoke twice — the 08-25 abstention that was cured on
        # the spot and re-entered as consent — is still one actor in the scene.
        spoke: list[str] = []
        for r in rows:
            if isinstance(r, dict) and r.get("name") and r.get("ok") \
                    and r["name"] not in spoke:
                spoke.append(r["name"])
        if not spoke:
            continue
        ts = [r.get("ts") for r in rows if isinstance(r, dict) and r.get("ts")]
        scenes.append({
            "t": min(ts) if ts else f.stat().st_mtime,
            "kind": "agora", "actors": spoke, "where": "agora",
            "title": "ratification consent round", "turns": len(spoke),
            "payload_ref": [str(f.relative_to(REPO_ROOT))],
        })
    return scenes


def _scan_meeting_scenes() -> list[dict]:
    vf = REPO_ROOT / "research" / "village-founding"
    scenes: list[dict] = []
    if not vf.is_dir():
        return scenes
    for f in sorted(vf.glob("*meeting*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if d.get("outcome") != "ok" or not d.get("participants"):
            continue
        title = re.sub(r"^\d{8}-meeting-", "", f.stem).replace("-", " ")
        scenes.append({
            "t": d.get("started_at", f.stat().st_mtime), "kind": "meeting",
            "actors": d["participants"], "where": "council_hall",
            "title": title, "turns": len(d.get("turns", [])),
            "payload_ref": [str(f.relative_to(REPO_ROOT))],
        })
    return scenes


def _scan_board_scenes() -> list[dict]:
    scenes: list[dict] = []
    for sub, kind, where in (("decisions", "ruling", "agora"),
                             ("board", "notice", "notice_board")):
        d = REPO_ROOT / "village" / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            if f.name == "README.md":
                continue
            scenes.append({
                "t": _fname_t(f.name, f.stat().st_mtime), "kind": kind,
                "actors": [], "where": where,
                "title": _first_heading(f),
                "payload_ref": [str(f.relative_to(REPO_ROOT))],
            })
    return scenes


def _scan_task_scenes() -> list[dict]:
    tasks = REPO_ROOT / "village" / "tasks"
    scenes: list[dict] = []
    if not tasks.is_dir():
        return scenes
    for f in sorted(tasks.glob("*.md")):
        if f.name == "README.md":
            continue
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        try:
            meta = yaml.safe_load(text.split("---", 2)[1]) or {}
        except Exception:
            continue
        rel = str(f.relative_to(REPO_ROOT))
        for i, line in enumerate(text.splitlines()):
            m = _TASK_HIST.match(line.strip())
            if not m:
                continue
            t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d")) + 12 * 3600 + i
            scenes.append({
                "t": t, "kind": "task",
                "actors": [a for a in [meta.get("owner")] if a],
                "where": _OFFICE_FACILITY.get(meta.get("desk", ""), "council_hall"),
                "title": str(meta.get("title", f.stem)),
                "status": meta.get("status", ""), "event": m.group(2),
                "payload_ref": [rel],
            })
    return scenes


# Transitions on the same day are one event, not several. The 07-27 sweep moved
# four creatures across two lineages within minutes; rendering them separately
# would show four upgrades where the record shows a generation turning over.
_TRANSITION_MERGE_S = 6 * 3600


def _scan_transition_scenes(base: Path, habitat: str) -> list[dict]:
    """substrate_transition spans — the longitudinal record's first-class event.

    The village could already swap a head when a re-brain happened LIVE, but the
    sixteen transitions already in the ledger were invisible: the renderer had
    the capability and the bus never scanned for them. That matters more since
    the longitudinal reframe (DEVIATION 01) — the pre-registered event was one
    generation dying and being succeeded, and what the ledger holds instead is
    the whole substrate generation turning over with nobody dead. That event has
    to be watchable.
    """
    moves: list[tuple[float, str, dict]] = []
    for cdir in creature_dirs(base, habitat):
        # Narrations recorded separately (the 08-07 backfill, when the ritual's
        # narrate step had no home yet) are joined back by the transition they
        # describe. Appended, never written into the original span.
        late: dict[float, str] = {}
        for span in _iter_spans(cdir):
            if span.get("kind") == "substrate_transition_narration":
                a = span.get("attributes") or {}
                if a.get("of_transition_at") and a.get("narration"):
                    late[a["of_transition_at"]] = a["narration"]
        for span in _iter_spans(cdir):
            if span.get("kind") == "substrate_transition":
                attrs = dict(span.get("attributes") or {})
                t = span.get("timestamp", 0)
                if not attrs.get("narration") and t in late:
                    attrs["narration"] = late[t]
                    attrs["narration_late"] = True
                moves.append((t, cdir.name, attrs))
    moves.sort()
    scenes, run = [], []
    for m in moves:
        if run and m[0] - run[-1][0] > _TRANSITION_MERGE_S:
            scenes.append(_transition_scene(run))
            run = []
        run.append(m)
    if run:
        scenes.append(_transition_scene(run))
    return scenes


def _desc(v) -> str:
    """from/to are a string on some spans and a dict on others (Nova 07-20)."""
    if isinstance(v, dict):
        return "/".join(str(v[k]) for k in ("provider", "model", "auth") if v.get(k))
    return str(v or "")


def _transition_scene(run: list[tuple[float, str, dict]]) -> dict:
    axes = sorted({str(a.get("axis", "?"))[:1] for _, _, a in run})
    return {
        "t": run[0][0], "kind": "transition",
        "actors": [c for _, c, _ in run],
        "where": "village",
        "axes": axes,
        "cohort_sweep": len(run) >= 3,     # a turnover, not one creature moving
        "moves": [{"creature": c, "axis": a.get("axis"), "op": a.get("op"),
                   "from": _desc(a.get("from")), "to": _desc(a.get("to")),
                   # 07-27's four spans carry no reason; absent, never invented
                   "reason": a.get("reason"),
                   # the creature's own account of the move (ritual step 4).
                   # Unsaved before 2026-08-07, so older moves have none.
                   "narration": a.get("narration"),
                   "narration_late": a.get("narration_late", False)}
                  for _, c, a in run],
        "payload_ref": [f"creatures/{c}/store/spans.jsonl" for _, c, _ in run],
    }


def build_scenes(base: Path | None = None, habitat: str = "") -> list[dict]:
    """Full normalized scene timeline, oldest first."""
    base = base or (REPO_ROOT / "creatures")
    scenes = (_scan_field_and_reflect_scenes(base, habitat)
              + _scan_heartbeat_scenes(base, habitat)
              + _scan_transition_scenes(base, habitat)
              + _scan_report_scenes()
              + _scan_duty_scenes()
              + _scan_letter_scenes()
              + _scan_session_scenes()
              + _scan_rollcall_scenes()
              + _scan_board_scenes()
              + _scan_task_scenes())
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
