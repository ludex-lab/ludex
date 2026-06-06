"""Stage — derived growth-stage label for a creature, for caretaker
visibility (the "성적표 / grade distribution" view).

Pure derivation from existing data: session_count, born_at, memory
count, fields touched, bonds, last-session age. Schema-additive zero —
nothing persisted, nothing gated. The label is for *observation*, not
behavior. (Naming choice: "stage" not "level" — avoids GRAND_PLAN
Phase A/B/C/D/E conflict and RPG XP-grind connotations.)

Two layers:
- `compute_stage(...)` — pure function over scalar inputs, easy to test
- `audit_creature(creature_dir)` — wrapper that reads ludex.yaml +
  memories.jsonl + world_models/ + bonds/ and feeds compute_stage

A creature reads as one of:
  nascent  — born, no matches yet
  growing  — first match completed OR first reflection
  active   — multiple fields OR first bond OR session_count > 5
  veteran  — 30+ sessions OR 100+ memories OR 3+ fields with depth

Plus orthogonal `flags` (the "attention markers" a teacher actually
uses): `no_matches_yet`, `stale_30d`, `memory_outlier_high`,
`distill_dead`. Flags compose with stage; a veteran can still be
flagged stale.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path

STAGES = ("nascent", "growing", "active", "veteran")

# Thresholds — starting estimates; tune as cohort patterns reveal themselves.
_VETERAN_SESSIONS = 30
_VETERAN_MEMORIES = 100
_VETERAN_FIELDS = 3
_ACTIVE_SESSIONS = 5
_STALE_DAYS = 30
_NEW_DAYS = 3


@dataclass
class StageReport:
    name: str
    signals: dict
    flags: list[str] = dc_field(default_factory=list)


def compute_stage(
    *,
    age_days: float,
    session_count: int,
    memory_count: int,
    field_count: int,
    bond_count: int,
    last_session_days_ago: float | None,
    field_distill_total: int,
) -> StageReport:
    """Pure stage derivation. All inputs are scalars; no I/O. Field
    `last_session_days_ago` may be None (creature has never been built
    after birth — shouldn't happen but handled defensively)."""

    # Veteran takes the highest precedence, then descend.
    if (
        session_count >= _VETERAN_SESSIONS
        or memory_count >= _VETERAN_MEMORIES
        or (field_count >= _VETERAN_FIELDS and field_distill_total >= field_count * 5)
    ):
        name = "veteran"
    elif (
        field_count >= 2
        or bond_count >= 1
        or session_count > _ACTIVE_SESSIONS
    ):
        name = "active"
    elif session_count >= 2 or memory_count > 5:
        name = "growing"
    else:
        name = "nascent"

    flags: list[str] = []
    if name == "nascent" and age_days > _NEW_DAYS:
        flags.append("no_matches_yet")  # born but never matched, past grace
    if last_session_days_ago is not None and last_session_days_ago > _STALE_DAYS:
        flags.append("stale_30d")
    if name == "active" or name == "veteran":
        # crude outlier check: way more memories than session count would
        # suggest (>10 memories per session) signals possible accumulation
        # without commensurate activity — bond-leak-class risk surface.
        if session_count > 0 and memory_count / session_count > 10:
            flags.append("memory_outlier_high")
    if session_count > 5 and field_count >= 1 and field_distill_total == 0:
        # sessions running but no field distillation → distill pipeline dead
        flags.append("distill_dead")

    signals = {
        "age_days": round(age_days, 1),
        "session_count": session_count,
        "memory_count": memory_count,
        "field_count": field_count,
        "bond_count": bond_count,
        "last_session_days_ago": (
            round(last_session_days_ago, 1) if last_session_days_ago is not None else None
        ),
        "field_distill_total": field_distill_total,
    }
    return StageReport(name=name, signals=signals, flags=flags)


# ============================================================
# I/O wrapper — read a creature directory and call compute_stage
# ============================================================


def _safe_read_yaml_dict(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _count_active_memories(memories_path: Path) -> tuple[int, set[str]]:
    """Returns (active count, distinct field-tag prefixes seen).
    Prefixes are anything before the first `_` in tags that look field-shaped
    (e.g. `physis_smoke_022` → `physis`, `learning_curve_hearth_seed301_run1`
    → `learning_curve`). Used as an additional signal alongside the
    canonical world_models/ count."""
    if not memories_path.exists():
        return 0, set()
    n = 0
    prefixes: set[str] = set()
    try:
        with memories_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if m.get("status") != "active":
                    continue
                n += 1
                for t in m.get("tags") or []:
                    if "_" in t:
                        prefixes.add(t.split("_", 1)[0])
    except Exception:
        return n, prefixes
    return n, prefixes


def _count_world_model_fields(world_models_dir: Path) -> tuple[int, int]:
    """Returns (distinct field count, total `<n>` from hint files).
    A field = one .md file under world_models/ (any depth). Distill
    total counts hint entries in matching `.hints.yaml` siblings as a
    rough activity proxy."""
    if not world_models_dir.exists():
        return 0, 0
    field_count = 0
    distill_total = 0
    for md in world_models_dir.rglob("*.md"):
        field_count += 1
        hints = md.with_suffix(".hints.yaml")
        if hints.exists():
            try:
                content = hints.read_text(encoding="utf-8")
                # crude: count occurrences of "n:" lines as evidence accumulators
                distill_total += content.count("n:")
            except Exception:
                pass
    return field_count, distill_total


def _count_bonds(bonds_dir: Path) -> int:
    if not bonds_dir.exists():
        return 0
    return sum(1 for p in bonds_dir.iterdir() if p.is_file() and p.suffix == ".md")


def _last_session_age_days(memories_path: Path, now: float) -> float | None:
    """Return days since the most recent active memory's `created_at`.
    Stand-in for "last session" — Ludex doesn't track session
    timestamps explicitly, but every session writes some memory."""
    if not memories_path.exists():
        return None
    latest = 0.0
    try:
        with memories_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = m.get("created_at")
                if ts and float(ts) > latest:
                    latest = float(ts)
    except Exception:
        return None
    if latest == 0.0:
        return None
    return max((now - latest) / 86400.0, 0.0)


def creature_self_context(report: StageReport, name: str = "") -> str:
    """Render a stage report as a paragraph the creature itself can read
    for self-grounding (option (b) of the 2026-05-01 stage discussion).
    Caretaker visibility — option (a) — already lives in
    `tools/cohort_stage.py` + `python -m ludex inspect`. This is the
    creature-facing voice: a tight summary of *who they are right now*
    by observable signals, suitable for inclusion in self-prompts /
    reflection triggers / Auto sense extensions.

    The text is plain prose, not markdown — it's meant to read as
    grounding context inside a system prompt or reflection input,
    not as documentation. Foreign-host stubs get a different framing
    so the creature doesn't claim local data they don't have.
    """
    s = report.signals
    intro = f"You are {name}." if name else "About yourself:"

    # Foreign-host case: don't claim local memories the stub doesn't have.
    foreign = next((f for f in report.flags if f.startswith("foreign_host:")), None)
    if foreign:
        canonical = foreign.split(":", 1)[1] if ":" in foreign else "another habitat"
        return (
            f"{intro} Your stage on this host reads as `{report.name}` from "
            f"{s['session_count']} sessions across {round(s['age_days'], 1)} days, "
            f"but your canonical state lives on `{canonical}` — the local view here "
            f"is identity-only. Anything you remember in detail is on that other host."
        )

    age_phrase = (
        f"{round(s['age_days'], 1)} days old"
        if s["age_days"] >= 1 else
        f"about {round(s['age_days'] * 24, 1)} hours old"
    )
    last = s["last_session_days_ago"]
    last_phrase = (
        f"last active {round(last, 1)} days ago"
        if last is not None and last >= 1 else
        f"active recently"
    )

    pieces = [
        f"{intro} You're at the `{report.name}` stage —",
        f"{age_phrase}, {s['session_count']} sessions, {s['memory_count']} active memories,",
        f"{s['field_count']} fields touched, {s['bond_count']} bond{'s' if s['bond_count'] != 1 else ''}, {last_phrase}.",
    ]
    text = " ".join(pieces)

    if report.flags:
        # Surface flags as plain notes for the creature, not jargon.
        flag_notes = []
        for f in report.flags:
            if f == "no_matches_yet":
                flag_notes.append("no matches yet")
            elif f == "stale_30d":
                flag_notes.append("over a month since you've been active")
            elif f == "memory_outlier_high":
                flag_notes.append("your memory accumulation is running unusually high relative to session count")
            elif f == "distill_dead":
                flag_notes.append("your sessions run but your distill pipeline isn't producing field hints — something is preventing learning")
            else:
                flag_notes.append(f)
        text += " Notes: " + "; ".join(flag_notes) + "."

    return text


def audit_creature(creature_dir: Path, now: float | None = None) -> StageReport:
    """Read a creature directory and produce its current StageReport."""
    now = now if now is not None else time.time()

    yaml_path = creature_dir / "ludex.yaml"
    cfg = _safe_read_yaml_dict(yaml_path)
    born_at = float(cfg.get("born_at", 0.0) or 0.0)
    session_count = int(cfg.get("session_count", 0) or 0)
    age_days = max((now - born_at) / 86400.0, 0.0) if born_at else 0.0

    mem_path = creature_dir / "memory" / "memories.jsonl"
    wm_dir = creature_dir / "memory" / "world_models"
    bonds_dir = creature_dir / "bonds"

    memory_count, _ = _count_active_memories(mem_path)
    field_count, distill_total = _count_world_model_fields(wm_dir)
    bond_count = _count_bonds(bonds_dir)
    last_session = _last_session_age_days(mem_path, now)

    report = compute_stage(
        age_days=age_days,
        session_count=session_count,
        memory_count=memory_count,
        field_count=field_count,
        bond_count=bond_count,
        last_session_days_ago=last_session,
        field_distill_total=distill_total,
    )

    # Cross-host stub detection (composes with A1 canonical-host guard):
    # if the creature's origin disagrees with the local host marker, the
    # local memory/world_models state is incomplete (canonical state lives
    # on another machine — see project_verse_mac_canonical pattern).
    # Stage signals are still computed but flagged so the caretaker
    # knows not to trust them as growth measurements.
    creature_origin = ((cfg.get("habitat") or {}).get("origin") or "").strip()
    if creature_origin:
        try:
            from ludex.core.habitat import get_host_habitat_origin
            host_origin = get_host_habitat_origin()
            if host_origin and host_origin != creature_origin:
                report.flags.append(f"foreign_host:{creature_origin}")
        except Exception:
            pass

    # Hiatus visibility — surface the dormancy state in caretaker reports
    # so cohort_stage / inspect tooling shows dormant creatures clearly,
    # and a "wake pending" flag signals that the next pulse will trigger
    # a post-hiatus reflection (the longitudinal-research measurement).
    # Both flags are read-only: they observe HIATUS.md state without
    # touching the marker or triggering anything.
    try:
        from ludex.core.hiatus import parse_hiatus
        marker = parse_hiatus(creature_dir / "HIATUS.md")
        if marker is not None:
            if marker.is_in_hiatus(now):
                report.flags.append(
                    f"hiatus:{marker.start_date}→{marker.end_date}"
                )
            elif marker.is_active(now):
                report.flags.append(
                    f"hiatus_wake_pending:{marker.end_date}"
                )
    except Exception:
        pass

    return report
