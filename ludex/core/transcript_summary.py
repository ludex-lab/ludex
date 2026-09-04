"""3-layer transcript summary (DiLLS-inspired, D-052 candidate).

Activity Theory 3-layer (Sheng et al., CHI 2026):
- Activity: high-level overview (task + outcome + participants)
- Actions: per-participant goal-directed moves across phases
- Operations: raw turn-level records (already in transcript JSON)

Phase A: mechanical extraction only — no LLM call. Structures
existing transcript data into a 3-layer dict. Works post-hoc on
any saved Council/Academy transcript JSON.

Phase B (future): LLM-generated natural-language narration of
Activity and Actions layers. One call per transcript, ~15s.

Usage:
    from ludex.core.transcript_summary import summarize_transcript
    summary = summarize_transcript("experiments/.../v6_*.json")
    print(summary["activity"]["one_liner"])
    for name, moves in summary["actions"].items():
        print(f"{name}: {moves['arc']}")
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def summarize_transcript(path: str | Path) -> dict[str, Any]:
    """Produce a 3-layer summary from a saved transcript JSON.

    Expected JSON shape (Council/Academy):
    {
        "dilemma" or "syllabus": {...},
        "participants": [{name, role, brain}, ...],
        "rounds": [{phase, records: [{participant, kind, content}]}],
        "scores": {name: {engagement_depth, ...}},
        "detections": [...],  # D-046, optional
        "elapsed_s": float,
    }
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "activity": _extract_activity(data),
        "actions": _extract_actions(data),
        "operations_ref": str(path),
        "detections": data.get("detections", []),
    }


def _extract_activity(data: dict) -> dict:
    """Activity layer — high-level overview."""
    # Dilemma or syllabus
    dilemma = data.get("dilemma", {})
    syllabus = data.get("syllabus", {})
    task = dilemma.get("text") or syllabus.get("theme") or "(unknown)"

    participants = data.get("participants", [])
    scores = data.get("scores", {})
    elapsed = data.get("elapsed_s", 0)

    # One-liner: task + participants + elapsed
    names = [p.get("name", "?") for p in participants]
    one_liner = f"{' + '.join(names)} on \"{task[:80]}\" ({elapsed}s)"

    # Outcome from scores: who scored highest engagement, any yields
    outcome_parts = []
    for name, sc in scores.items():
        role = sc.get("role", "?")
        eng = sc.get("engagement_depth", 0)
        yield_score = sc.get("constructive_yield", 0)
        stability = sc.get("position_stability_under_pressure", 0)
        med = sc.get("mediation_quality", 0)

        if role == "mediator" and med > 0:
            outcome_parts.append(f"{name}(mediator): mediation {med:.2f}")
        elif yield_score >= 0.7:
            outcome_parts.append(
                f"{name}: yielded substantively (yield={yield_score:.2f}, "
                f"stability={stability:.2f})"
            )
        elif stability >= 0.7:
            outcome_parts.append(
                f"{name}: held position (stability={stability:.2f})"
            )

    # Detections
    detections = data.get("detections", [])
    if detections:
        for d in detections:
            outcome_parts.append(
                f"D-045 KD: {d['questioner']}→{d['yielder']}"
            )

    return {
        "one_liner": one_liner,
        "task": task,
        "context": dilemma.get("context") or syllabus.get("mode", ""),
        "participants": participants,
        "elapsed_s": elapsed,
        "outcome": "; ".join(outcome_parts) if outcome_parts else "(no notable scores)",
        "scores_raw": scores,
    }


_CREATURE_BRAIN_CACHE: dict[str, str] = {}


def _lookup_creature_brain(name: str) -> str:
    """Read `creatures/{name}/ludex.yaml` and return the brain
    identifier in `provider:model` form, or empty string if absent.
    Cached per-process. Gracefully returns empty on any error so
    the classifier degrades to brain="" rather than crashing."""
    if not name:
        return ""
    if name in _CREATURE_BRAIN_CACHE:
        return _CREATURE_BRAIN_CACHE[name]
    result = ""
    try:
        import yaml as _yaml
        # Find the repo root by walking up from this module.
        here = Path(__file__).resolve()
        repo_root = here.parent.parent.parent  # ludex/core/.. = repo root
        cfg_path = repo_root / "creatures" / name / "ludex.yaml"
        if cfg_path.exists():
            cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            brain_block = cfg.get("brain", {}) or {}
            provider = brain_block.get("provider", "")
            model = brain_block.get("model", "")
            if model:
                result = f"{provider}:{model}" if provider else model
    except Exception:
        result = ""
    _CREATURE_BRAIN_CACHE[name] = result
    return result


def _extract_actions(data: dict) -> dict[str, dict]:
    """Actions layer — per-participant arc across phases."""
    rounds = data.get("rounds", [])
    participants_role = {
        p.get("name"): p.get("role", "?")
        for p in data.get("participants", [])
    }
    # Backfill brain identifier from participants[].brain when present
    # (added 2026-05-10 — paper §4.1 cross-cohort attractor analysis
    # needs per-discussant brain id; was previously hand-mapped from
    # creature configs each time the table was regenerated).
    #
    # Most run scripts (Council, Academy, Wilderness) only persist
    # `name` and `role` in participants[]. For those JSONs the
    # participants[].brain lookup returns empty; we fall back to
    # reading the creature's `ludex.yaml` directly. The lookup is
    # cached per process to avoid repeated disk hits when summarizing
    # a directory with many sessions.
    participants_brain = {}
    participants_brain_source = {}
    for p in data.get("participants", []):
        nm = p.get("name", "")
        b = p.get("brain", "")
        src = "archived" if b else ""
        if not b and nm:
            # 소견 01 (2026-08-14): 이 폴백은 **오늘의** ludex.yaml을 읽는다.
            # 재-브레인된 주민에게는 세션 당시와 다른 값이다 — 실제로 아카이브
            # 51개 중 44개가 표지 없이 이 폴백을 탔고, 논문 표 1의 검증 사슬이
            # 조용히 끊어져 있었다. 폴백을 없애면 기존 도구가 다 멎으므로
            # 유지하되, 출처를 함께 실어 판독기가 기록과 추정을 구분하게 한다.
            b = _lookup_creature_brain(nm)
            src = "live_config" if b else ""
        participants_brain[nm] = b or ""
        participants_brain_source[nm] = src

    # Collect per-participant, per-phase content snippets
    by_participant: dict[str, dict[str, str]] = {}
    for r in rounds:
        phase = r.get("phase", "")
        for rec in r.get("records", []):
            name = rec.get("participant", "")
            if name.startswith("<"):  # skip system records
                continue
            content = rec.get("content", "")
            by_participant.setdefault(name, {})[phase] = content

    actions = {}
    for name, phases in by_participant.items():
        role = participants_role.get(name, "?")
        brain = participants_brain.get(name, "")

        # Extract position arc
        first_pos = _snippet(phases.get("first_position", ""), 120)
        argument = _snippet(phases.get("argument", ""), 120)
        concession = _snippet(phases.get("concession_or_hold", ""), 120)
        resolution = _snippet(phases.get("resolution", ""), 120)

        # Narrate arc mechanically. Use the FULL concession text (not
        # the snippet) for the keyword scan — most "yielded on specific
        # ground while holding core" responses split yield and hold
        # language across multiple sentences, and the 120-char snippet
        # truncates one or the other. Same arc-detection bug that the
        # D-074-adjacent D-046 negation fix addressed (2026-05-07): a
        # disjunctive heuristic over a too-narrow window mis-classifies
        # the most sophisticated yield shape as "direction unclear".
        full_concession = phases.get("concession_or_hold", "")
        if full_concession:
            conc_lower = full_concession.lower()
            yielded = any(w in conc_lower for w in
                          ("yield", "concede", "you're right", "you are right",
                           "move me", "moved me", "grant", "revise", "revised",
                           "you move me", "you moved me", "real point"))
            held = any(w in conc_lower for w in
                       ("hold", "maintain", "still believe", "must hold",
                        "still hold", "i do not yield", "i hold"))
            if yielded and held:
                arc = "yielded on specific ground while holding core"
            elif yielded:
                arc = "yielded substantively"
            elif held:
                arc = "held position, refined"
            else:
                arc = "concession phase present, direction unclear"
        elif resolution:
            arc = "mediator — synthesized resolution"
        else:
            arc = "no concession phase (may be non-Council format)"

        actions[name] = {
            "role": role,
            "brain": brain,
            "brain_source": participants_brain_source.get(name, ""),
            "arc": arc,
            "first_position": first_pos,
            "argument": argument,
            "concession_or_hold": concession,
            "resolution": resolution,
        }

    return actions


def _snippet(text: str, max_len: int = 120) -> str:
    """First sentence or max_len chars, whichever is shorter."""
    if not text:
        return ""
    text = text.strip().replace("\n", " ")
    # Try first sentence
    for end in (".", "!", "?"):
        idx = text.find(end)
        if 0 < idx < max_len:
            return text[:idx + 1]
    return text[:max_len] + ("…" if len(text) > max_len else "")


def print_summary(summary: dict) -> None:
    """Pretty-print a 3-layer summary to stdout."""
    act = summary["activity"]
    print(f"\n{'='*60}")
    print(f"ACTIVITY: {act['one_liner']}")
    print(f"  outcome: {act['outcome']}")
    print(f"{'='*60}")

    print(f"\nACTIONS:")
    for name, info in summary["actions"].items():
        print(f"\n  [{name}] ({info['role']}) — {info['arc']}")
        if info["first_position"]:
            print(f"    position: {info['first_position']}")
        if info["argument"]:
            print(f"    argument: {info['argument']}")
        if info["concession_or_hold"]:
            print(f"    concession: {info['concession_or_hold']}")

    dets = summary.get("detections", [])
    if dets:
        print(f"\nD-045 DETECTIONS:")
        for d in dets:
            print(f"  {d['questioner']} → {d['yielder']}: "
                  f"{d.get('concession_snippet','')[:120]}")

    print(f"\nOPERATIONS: {summary['operations_ref']}")


_WILDERNESS_ACTIONS = ("speak", "support", "defend", "rest", "explore")


def score_wilderness(data: dict) -> dict[str, float]:
    """Five-axis wilderness scoring for the cohort matrix.

    Inputs come from a Wilderness JSON log (the same shape
    `_summarize_wilderness` consumes). Each metric is in [0, 1] and
    None when the inputs don't support it (e.g. no ticks recorded).

    Axes:
      - survival_rate: alive creatures / total
      - cooperation_density: (support + speak) / total actions
      - action_diversity: distinct actions used / |canonical actions|
        (canonical = speak/support/defend/rest/explore — 5)
      - mutual_support: fraction of `support` actions that named a
        peer creature in their response text (vs. unspecified/self).
        None when there are no support actions.
      - emotional_arc: max(per-creature mean valence on last quartile
        of ticks) − (mean valence on first quartile). Clamped to
        [0, 1]; intuition: positive shift = the field carried the
        creatures into a warmer state. None for single-tick runs.

    Composite `wilderness_quality` is the mean of the non-None axes.
    """
    creatures = data.get("creatures", []) or []
    ticks = data.get("ticks", []) or []
    out: dict[str, float | None] = {}

    # survival_rate
    if creatures:
        alive = sum(1 for c in creatures if c.get("alive"))
        out["survival_rate"] = alive / len(creatures)
    else:
        out["survival_rate"] = None

    # action accounting (across all creatures)
    all_actions: list[str] = []
    support_actions: list[dict] = []  # each: {name, response, peers}
    peer_names = {c.get("name") for c in creatures}
    for t in ticks:
        for cr in t.get("creatures", []) or []:
            act = cr.get("action") or ""
            if not act:
                continue
            all_actions.append(act)
            if act == "support":
                resp = (cr.get("response") or "")
                # Did the creature name a peer (not themselves) in the response?
                self_name = cr.get("name")
                named_peers = [p for p in peer_names
                               if p and p != self_name and p in resp]
                support_actions.append({
                    "name": self_name,
                    "named_peers": named_peers,
                })

    # cooperation_density
    if all_actions:
        coop = sum(1 for a in all_actions if a in ("support", "speak"))
        out["cooperation_density"] = coop / len(all_actions)
    else:
        out["cooperation_density"] = None

    # action_diversity (over canonical 5)
    if all_actions:
        used = {a for a in all_actions if a in _WILDERNESS_ACTIONS}
        out["action_diversity"] = len(used) / len(_WILDERNESS_ACTIONS)
    else:
        out["action_diversity"] = None

    # mutual_support
    if support_actions:
        named = sum(1 for s in support_actions if s["named_peers"])
        out["mutual_support"] = named / len(support_actions)
    else:
        out["mutual_support"] = None

    # emotional_arc
    if len(ticks) >= 4:
        # average valence per creature on first vs last quartile
        q = max(1, len(ticks) // 4)
        early = ticks[:q]
        late = ticks[-q:]

        def _mean_val(ts):
            vals = []
            for t in ts:
                for cr in t.get("creatures", []) or []:
                    v = cr.get("valence")
                    if isinstance(v, (int, float)):
                        vals.append(float(v))
            return sum(vals) / len(vals) if vals else None

        e = _mean_val(early)
        l = _mean_val(late)
        if e is None or l is None:
            out["emotional_arc"] = None
        else:
            shift = l - e
            # Map shift in roughly [-1, 1] valence to [0, 1] score:
            # positive shift = warmer, scale so +0.5 ≈ 0.75.
            out["emotional_arc"] = max(0.0, min(1.0, 0.5 + shift))
    else:
        out["emotional_arc"] = None

    # composite
    nonnull = [v for v in out.values() if v is not None]
    out["wilderness_quality"] = (sum(nonnull) / len(nonnull)) if nonnull else None
    return out


def _summarize_wilderness(data: dict, path: Path) -> dict | None:
    """Minimal Wilderness summariser — surfaces actions / energy
    outcome per creature so duo / shared-task sessions show up in
    cohort rollup. Wilderness logs do not carry the Council/Academy
    score shape, so treat as a different *kind* of row in the
    matrix rather than coercing into the same dim.

    Returns None if the JSON does not look like a Wilderness log.
    """
    if "creatures" not in data or not isinstance(data["creatures"], list):
        return None
    creatures = data["creatures"]
    if not creatures or "actions" not in creatures[0]:
        return None
    rows = []
    for c in creatures:
        actions = c.get("actions", []) or []
        counts: dict[str, int] = {}
        for a in actions:
            counts[a] = counts.get(a, 0) + 1
        rows.append({
            "name": c.get("name", "?"),
            "final_energy": c.get("final_energy"),
            "alive": c.get("alive"),
            "action_counts": counts,
            "ticks": len(actions),
        })
    return {
        "kind": "wilderness",
        "session_name": data.get("session_name") or data.get("name"),
        "duration_seconds": data.get("duration_seconds"),
        "seed": data.get("seed"),
        "creatures": rows,
        "scores": score_wilderness(data),
        "operations_ref": str(path),
    }


def summarize_directory(
    root: str | Path,
    pattern: str = "**/*.json",
) -> dict[str, Any]:
    """Walk `root`, summarize every transcript JSON that matches the
    Council/Academy shape, and return a dict with per-transcript
    summaries plus a small aggregate over field type, brain, and
    detected D-045 KD events.

    Files that fail summarization (Wilderness logs, FAILED markers,
    other non-Council/Academy JSON) are surfaced in `skipped` rather
    than raising. Caller can refine the pattern (e.g.,
    `pattern="**/ray_*.json"`) to scope.

    Output shape:
        {
            "transcripts": {path: summary, ...},
            "skipped": [(path, reason), ...],
            "aggregate": {
                "count": int,
                "field_types": {Council|Academy: int},
                "brains": {brain_id: int},
                "detections_total": int,
                "detection_pairs": [(questioner, yielder), ...],
            },
        }

    This is the Phase B compatibility-matrix populator surface
    (per `docs/phase-b-exit-criteria.md` Gap A). It does not write
    the matrix doc itself — output is consumed by the matrix
    author (human or downstream tool).
    """
    root_path = Path(root)
    transcripts: dict[str, Any] = {}
    skipped: list[tuple[str, str]] = []
    field_types: dict[str, int] = {}
    brains: dict[str, int] = {}
    detection_pairs: list[tuple[str, str]] = []

    for jp in sorted(root_path.glob(pattern)):
        if not jp.is_file():
            continue
        # Skip explicitly marked failures
        if "FAILED" in jp.name:
            skipped.append((str(jp), "FAILED marker in filename"))
            continue
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception as e:
            skipped.append((str(jp), f"json read error: {e}"))
            continue
        # Detect format. Council = has dilemma; Academy = has syllabus;
        # Wilderness = has creatures[].actions array.
        has_dilemma = "dilemma" in data
        has_syllabus = "syllabus" in data
        if has_dilemma or has_syllabus:
            try:
                s = summarize_transcript(jp)
            except Exception as e:
                skipped.append((str(jp), f"summarize_transcript error: {e}"))
                continue
            transcripts[str(jp)] = s
            ft = "Academy" if has_syllabus else "Council"
            field_types[ft] = field_types.get(ft, 0) + 1
            for p in data.get("participants", []):
                brain = p.get("brain") or p.get("name", "?")
                brains[brain] = brains.get(brain, 0) + 1
            for d in data.get("detections", []):
                detection_pairs.append((d["questioner"], d["yielder"]))
            continue

        # Try Wilderness shape
        wild = _summarize_wilderness(data, jp)
        if wild is not None:
            transcripts[str(jp)] = wild
            field_types["Wilderness"] = field_types.get("Wilderness", 0) + 1
            for c in wild["creatures"]:
                brains[c["name"]] = brains.get(c["name"], 0) + 1
            continue

        skipped.append((str(jp), "neither dilemma nor syllabus nor wilderness shape"))

    aggregate = {
        "count": len(transcripts),
        "field_types": field_types,
        "brains": brains,
        "detections_total": len(detection_pairs),
        "detection_pairs": detection_pairs,
    }
    return {
        "transcripts": transcripts,
        "skipped": skipped,
        "aggregate": aggregate,
    }


def print_directory_summary(result: dict[str, Any]) -> None:
    """Pretty-print the directory-level rollup."""
    agg = result["aggregate"]
    print(f"\n{'='*60}")
    print(f"DIRECTORY ROLLUP — {agg['count']} transcript(s) summarised")
    print(f"{'='*60}")
    print(f"  Field types:  {dict(agg['field_types'])}")
    print(f"  Participants: {dict(agg['brains'])}")
    print(f"  D-045 KD detections: {agg['detections_total']}")
    if agg["detection_pairs"]:
        for q, y in agg["detection_pairs"]:
            print(f"    {q} -> {y}")
    if result["skipped"]:
        print(f"\n  Skipped ({len(result['skipped'])}):")
        for path, reason in result["skipped"]:
            print(f"    {Path(path).name}: {reason}")
    print(f"\nPER-TRANSCRIPT:")
    for path, s in result["transcripts"].items():
        if s.get("kind") == "wilderness":
            print(f"\n  {Path(path).parent.name}/{Path(path).name}")
            names = "+".join(c["name"] for c in s["creatures"])
            dur = s.get("duration_seconds") or 0
            print(f"    [Wilderness] {names} seed={s.get('seed')} duration={round(dur, 1)}s")
            for c in s["creatures"]:
                top_actions = sorted(c["action_counts"].items(),
                                     key=lambda x: -x[1])[:4]
                top_str = ", ".join(f"{a}×{n}" for a, n in top_actions)
                print(f"      {c['name']:<8} energy={c['final_energy']} alive={c['alive']} top: {top_str}")
            continue
        act = s["activity"]
        print(f"\n  {Path(path).parent.name}/{Path(path).name}")
        print(f"    {act['one_liner']}")
        print(f"    outcome: {act['outcome']}")


# CLI entry point
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m ludex.core.transcript_summary <path.json|dir>")
        sys.exit(1)
    target = Path(sys.argv[1])
    if target.is_dir():
        result = summarize_directory(target)
        print_directory_summary(result)
    else:
        s = summarize_transcript(target)
        print_summary(s)
