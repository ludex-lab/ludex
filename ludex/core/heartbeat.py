"""Heartbeat — creature lifecycle autonomic pulse (D-051 Phase A).

Each pulse: load creature → health_check → consolidate if stale →
bond staleness → emit span. No LLM call unless reflect triggers
(meaningful change detected). Designed for cron invocation:

    # Every 6 hours, pulse all creatures
    0 */6 * * * cd /path/to/ludex && .venv/bin/python -m ludex.core.heartbeat

    # Pulse one creature
    .venv/bin/python -m ludex.core.heartbeat --creature Primo

    # Dry-run (report only, no writes)
    .venv/bin/python -m ludex.core.heartbeat --dry-run

    # Also check brain-model currency (cached 24h; records model_currency
    # spans + triggers a one-time reflect on new drift)
    .venv/bin/python -m ludex.core.heartbeat --check-currency

Pure Python, no Claude Code dependency. Creature brains are
called only when reflect triggers — cost is 0 LLM calls for
a healthy creature, 1 for a creature that needs reflection.

If a brain call fails (quota, network, model offline), the pulse
records a brain_unavailable span. On next successful pulse where
reflect triggers, the creature can read this span and narrate
its own downtime ("I was asleep for 3 days because my brain was
exhausted"). This is D-044 narrative identity applied to
lifecycle.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default creatures directory
DEFAULT_CREATURES_DIR = Path(__file__).resolve().parents[2] / "creatures"

# Skip these directories (test fixtures, not real creatures)
SKIP_DIRS = {"ClaudeCLI1", "ClaudeCode1", "Claude_Spike", "FC_Test",
             "PersistTest", "onboarding"}

# When health grade drops to this or below, trigger reflect
REFLECT_GRADE_THRESHOLD = "C"

# Bond staleness threshold (days)
BOND_STALE_DAYS = 7


def _grade_at_or_below(grade: str, threshold: str) -> bool:
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    return order.get(grade, 3) >= order.get(threshold, 2)


def pulse_creature(
    creature_dir: Path,
    dry_run: bool = False,
    available_models: Optional[dict] = None,
) -> dict:
    """Run one heartbeat pulse for a creature. Returns a summary dict.

    Steps:
    1. Load creature (lightweight — OrganismConfig.load + build)
    2. health_check
    3. consolidate if consolidation_fresh fails
    4. bond staleness check
    5. Determine if reflect should trigger
    6. If trigger + not dry_run: reflect
    7. Emit heartbeat_pulse span
    """
    from ludex.core.organism_config import OrganismConfig

    name = creature_dir.name
    # OrganismConfig.load accepts either ludex.yaml or ludex.json; the
    # heartbeat used to only check .json and so missed creatures born
    # on systems where HAS_YAML=True wrote only .yaml.
    if not any((creature_dir / f).exists() for f in ("ludex.yaml", "ludex.json")):
        return {"name": name, "skip": True, "reason": "no ludex.yaml/json"}

    try:
        cfg = OrganismConfig.load(str(creature_dir))
        organism = cfg.build()
    except Exception as e:
        return {"name": name, "skip": True, "reason": f"build failed: {e}"}

    mem = organism.get_block("memory")
    resilience = organism.get_block("resilience")
    brain = cfg.brain or {}
    provider = brain.get("provider", "")
    model = brain.get("model", "")
    result = {
        "name": name,
        "brain": model or "?",
        "timestamp": time.time(),
    }
    # Caretaker-declared substrate lifecycle label (substrate_change_policy:
    # live / cost-watch / wind-down / retiring / dormant). Display-only —
    # the heartbeat never acts on it.
    if brain.get("substrate_status"):
        result["substrate_status"] = brain["substrate_status"]

    # 0. D-068 Brain fatigue check — if creature is in cooldown, surface
    # "resting" outcome immediately and skip brain calls entirely.
    if resilience is not None:
        try:
            fatigue = resilience.handle_fatigue_state()
            if fatigue.get("state") == "fatigued":
                result["fatigue"] = fatigue
                result["outcome"] = "resting"
                if not dry_run:
                    _emit_heartbeat_span(organism, result)
                return result
        except Exception as e:
            result["fatigue_check_error"] = str(e)

    # 0b. Hiatus check — if the caretaker has declared a dormancy
    # window and we are currently inside it (pre-wake), short-circuit
    # before any reflect-triggering work runs. Mid-hiatus pulses must
    # remain true noops: no memory mutation, no bond-staleness memory
    # write, no reflect, no quota burn. The post-hiatus wake itself
    # is handled later via find_active_hiatus(). Protects the
    # longitudinal-research promise that the first post-window
    # reflection is uncontaminated by accidental mid-hiatus activity.
    from ludex.core.hiatus import find_in_hiatus
    in_hiatus_marker = find_in_hiatus(creature_dir)
    if in_hiatus_marker is not None:
        result["in_hiatus"] = {
            "window": f"{in_hiatus_marker.start_date} → {in_hiatus_marker.end_date}",
            "duration": in_hiatus_marker.duration_human(),
            "reason": in_hiatus_marker.reason,
        }
        result["outcome"] = "in_hiatus"
        if not dry_run:
            _emit_heartbeat_span(organism, result)
        return result

    # 1. Health check
    health = None
    if mem:
        try:
            health = mem.handle_health_check()
            result["health_grade"] = health.grade
            result["health_pass_count"] = health.pass_count
            result["health_notes"] = health.notes
        except Exception as e:
            result["health_error"] = str(e)

    # 2. Consolidate if stale
    consolidated = False
    if health and not health.consolidation_fresh and not dry_run:
        try:
            report = mem.handle_consolidate()
            consolidated = True
            result["consolidated"] = {
                "promoted": report.promoted,
                "archived": report.archived,
                "remaining": report.total_remaining,
            }
        except Exception as e:
            result["consolidate_error"] = str(e)

    # 2b. D-071 pillar 4c — forgetting pass cadence floor. Cheap (no
    # LLM, just compute_forget_score sort + mark). Idempotent and a
    # no-op when recall surface is under disk budget. Brains route to
    # frontier/mid/slm tier via BRAIN_DISK_TARGETS. With current cohort
    # (all under 250 entries vs slm 2k / mid 5k targets) this fires
    # nothing today; it's the trigger that takes effect once
    # accumulation crosses the threshold months out.
    if mem and not dry_run:
        try:
            forget_report = mem.run_forgetting_pass(
                "score_threshold", provider=provider, model=model,
            )
            if forget_report["forgotten_now"] > 0:
                result["forgetting_pass"] = {
                    "target": forget_report["target"],
                    "forgotten": forget_report["forgotten_now"],
                    "recallable_after": forget_report["recallable_after"],
                }
        except Exception as e:
            result["forgetting_error"] = str(e)

    # 3. Bond staleness
    stale_bonds = _check_bond_staleness(creature_dir)
    result["stale_bonds"] = stale_bonds

    # 3c. Hiatus detection — if caretaker declared a dormancy window
    # and the post-hiatus wake has begun, force a reflect with hiatus
    # context and mark the marker consumed on success. Pre-hiatus
    # pulses (window declared but not yet reached) are noops.
    from ludex.core.hiatus import find_active_hiatus
    hiatus_marker = find_active_hiatus(creature_dir)
    if hiatus_marker is not None:
        result["hiatus"] = {
            "window": f"{hiatus_marker.start_date} → {hiatus_marker.end_date}",
            "duration": hiatus_marker.duration_human(),
            "reason": hiatus_marker.reason,
        }

    # 3b. D-073 follow-up: write one staleness memory per stale period
    # per bond. Cheap (no LLM), idempotent (re-pulse won't re-write),
    # creature-internal substrate. Caretaker layer (ludex inspect /
    # cohort) reads bond mtime directly via list_bonds.
    if mem and stale_bonds and not dry_run:
        try:
            from ludex.core.bond_staleness import maybe_record_bond_staleness
            recorded = maybe_record_bond_staleness(mem, creature_dir, stale_bonds)
            if recorded:
                result["bond_staleness_recorded"] = recorded
        except Exception as e:
            result["bond_staleness_error"] = str(e)

    # 3d. Model-currency check (D-051 phase 2; model_currency_cadence).
    # Reuses the classifier from the manual model_check CLI. The live
    # /models query is the quota-touching part, so it is NOT run here per
    # pulse — pulse_all resolves it once per run (disk-cached, 24h TTL) and
    # passes the per-source lists in via available_models. A bare
    # pulse_creature() call leaves available_models=None → check skipped, so
    # existing callers and the default heartbeat are unchanged. A newly
    # detected drift (SUPERSEDED/DEPRECATED, first time for this
    # status+model) records a model_currency span AND triggers a reflect, so
    # the creature narrates the change once — mirroring the stale-bonds
    # sibling above. ollama is excluded (its "available" = locally-pulled
    # models, not provider drift).
    currency_changed = False
    if available_models is not None:
        from ludex.cadence.model_check import (
            classify, record_currency_span, _VERIFY_VIA,
            SUPERSEDED, DEPRECATED,
        )
        from ludex.core.store import LudexStore
        source = _VERIFY_VIA.get(provider)
        avail = available_models.get(source) if source else None
        res = classify(model, avail)
        cinfo = {"status": res.status, "source": source}
        if res.successor:
            cinfo["successor"] = res.successor
        if res.status in (SUPERSEDED, DEPRECATED) and source != "ollama":
            try:
                store = LudexStore(str(creature_dir))
                if dry_run:
                    # Don't write; just report whether a write *would* fire.
                    prior = store.spans(kind="model_currency", limit=1)
                    a = prior[-1].get("attributes", {}) if prior else {}
                    currency_changed = not (
                        a.get("status") == res.status and a.get("model") == model
                    )
                else:
                    currency_changed = record_currency_span(
                        store, name, res.status, model, provider,
                        res.successor, source,
                    )
                cinfo["recorded"] = currency_changed
            except Exception as e:
                result["model_currency_error"] = str(e)
        result["model_currency"] = cinfo

    # 4. Determine if reflect should trigger
    should_reflect = False
    reflect_reasons = []

    if health and _grade_at_or_below(health.grade, REFLECT_GRADE_THRESHOLD):
        should_reflect = True
        reflect_reasons.append(f"health_grade={health.grade}")

    if consolidated:
        should_reflect = True
        reflect_reasons.append("just_consolidated")

    if stale_bonds:
        should_reflect = True
        reflect_reasons.append(f"stale_bonds={stale_bonds}")

    if hiatus_marker is not None:
        should_reflect = True
        reflect_reasons.append("post_hiatus")

    if currency_changed:
        should_reflect = True
        reflect_reasons.append("model_currency_changed")

    result["should_reflect"] = should_reflect
    result["reflect_reasons"] = reflect_reasons

    # 5. Reflect if triggered
    outcome = "healthy"
    if should_reflect and not dry_run:
        try:
            from ludex.core import selfhood
            text = selfhood.reflect(
                organism,
                trigger=f"heartbeat:{','.join(reflect_reasons)}",
                hiatus_marker=hiatus_marker,
            )
            result["reflected"] = bool(text)
            result["reflect_len"] = len(text or "")
            # Distinguish "reflect ran and produced text" from "reflect
            # called but returned empty" (engine error / slow ollama /
            # brain that silently fell through). Both are not-degraded
            # — the engine didn't raise — but the caretaker view should
            # see that no SELF.md update happened. Surfaced 2026-05-14
            # after Moss reflect returned empty after 184s via ollama.
            outcome = "maintenance_ran" if text else "reflect_empty"
            # Consume the hiatus marker only on a successful reflect.
            # If reflect failed (quota / engine error), the marker
            # stays active so the next pulse can try again.
            if hiatus_marker is not None and text:
                try:
                    from ludex.core.hiatus import mark_consumed
                    consumed_path = mark_consumed(creature_dir / "HIATUS.md")
                    result["hiatus_consumed"] = str(consumed_path.name)
                except Exception as e:
                    logger.warning(
                        f"hiatus: mark_consumed failed for {name}: {e}"
                    )
        except Exception as e:
            err_str = str(e).lower()
            if any(kw in err_str for kw in ("quota", "rate", "429", "limit")):
                outcome = "brain_unavailable"
                result["brain_unavailable"] = True
                result["brain_unavailable_reason"] = str(e)[:200]
            else:
                outcome = "degraded"
                result["reflect_error"] = str(e)[:200]
    elif should_reflect and dry_run:
        outcome = "would_reflect"

    result["outcome"] = outcome

    # 6. Emit span
    if not dry_run:
        try:
            from ludex.core import trace as _tr
            _tr.emit_heartbeat_pulse(
                organism,
                outcome=outcome,
                health_grade=health.grade if health else "?",
                stale_bonds=stale_bonds,
                consolidated=consolidated,
                reflected=result.get("reflected", False),
            )
        except Exception:
            pass

    return result


def _emit_heartbeat_span(organism, result: dict) -> None:
    """Emit a heartbeat span for the resting-creature short path. Best
    effort — silent on failure so heartbeat reporting never blocks on
    instrumentation."""
    try:
        from ludex.core import trace as _tr
        _tr.emit_heartbeat_pulse(
            organism,
            outcome=result.get("outcome", "?"),
            health_grade="?",
            stale_bonds=[],
            consolidated=False,
            reflected=False,
        )
    except Exception:
        pass


def _check_bond_staleness(creature_dir: Path) -> list[str]:
    """Return names of bonds not updated in >BOND_STALE_DAYS days."""
    bonds_dir = creature_dir / "bonds"
    if not bonds_dir.is_dir():
        return []

    stale = []
    threshold = time.time() - (BOND_STALE_DAYS * 86400)
    for bond_file in bonds_dir.glob("*.md"):
        if bond_file.stat().st_mtime < threshold:
            stale.append(bond_file.stem)
    return stale


def _creature_habitat_origin(creature_dir: Path) -> str:
    """Read habitat.origin from a creature's config without full load/build.

    Reads yaml/json directly to avoid paying organism-build cost just to
    filter. Returns empty string if origin field is absent or unreadable.
    D-052: each creature is bound to exactly one habitat; `origin` is the
    canonical field.
    """
    for fname in ("ludex.yaml", "ludex.json"):
        p = creature_dir / fname
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8")
            if fname.endswith(".yaml"):
                try:
                    import yaml
                    data = yaml.safe_load(text) or {}
                except Exception:
                    return ""
            else:
                data = json.loads(text)
            return str((data.get("habitat") or {}).get("origin", "") or "")
        except Exception:
            return ""
    return ""


def pulse_all(
    creatures_dir: Path = DEFAULT_CREATURES_DIR,
    dry_run: bool = False,
    creature_name: Optional[str] = None,
    habitat: str = "",
    check_currency: bool = False,
) -> list[dict]:
    """Pulse all (or one) creature(s). Returns list of result dicts.

    If `habitat` is set (e.g. "Ray-habitat"), only creatures whose
    `habitat.origin` field matches are pulsed — per D-052 habitat
    sovereignty, a machine should not pulse creatures that belong to a
    different habitat even when their configs are tracked in the shared
    repo. Creatures without an origin field are treated as unscoped and
    skipped when a habitat filter is active.

    If `check_currency` is set, the live /models lists are resolved ONCE
    here (disk-cached, 24h TTL) and passed into every pulse, so each
    creature's brain model is classified against its provider's current
    offerings and a model_currency span is recorded on new drift. Off by
    default to keep the pulse quota-free unless the caretaker asks.
    """
    available_models = None
    if check_currency:
        from ludex.cadence.model_check import available_for_sources
        available_models = available_for_sources()

    results = []
    for d in sorted(creatures_dir.iterdir()):
        if not d.is_dir() or d.name in SKIP_DIRS:
            continue
        if creature_name and d.name != creature_name:
            continue
        if habitat:
            if _creature_habitat_origin(d) != habitat:
                continue
        r = pulse_creature(d, dry_run=dry_run, available_models=available_models)
        results.append(r)
    return results


def main():
    # BYO keys from .env (existing env always wins). Without this, a
    # launchd/cron pulse that triggers a reflect on an API-key-auth brain
    # (e.g. gemini paid key post-2026-06-18) subprocesses the CLI with no
    # key in env. Mirrors model_check.py's pattern.
    from ludex.core.dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Heartbeat - creature lifecycle pulse (D-051)",
    )
    parser.add_argument("--creature", default="",
                        help="Pulse one creature by name (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only, no writes or brain calls")
    parser.add_argument("--creatures-dir", default="",
                        help="Override creatures directory")
    parser.add_argument("--habitat", default="",
                        help="Limit to creatures whose habitat.origin matches "
                             "(e.g. 'Ray-habitat'). D-052 habitat sovereignty.")
    parser.add_argument("--check-currency", action="store_true",
                        help="Classify each creature's brain model against its "
                             "provider's live /models (cached 24h) and record a "
                             "model_currency span on new drift. Off by default "
                             "to keep the pulse quota-free.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    creatures_dir = Path(args.creatures_dir) if args.creatures_dir else DEFAULT_CREATURES_DIR

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    scope = f" habitat={args.habitat}" if args.habitat else ""
    print(f"\n[heartbeat {mode}{scope}] {creatures_dir}")

    results = pulse_all(
        creatures_dir=creatures_dir,
        dry_run=args.dry_run,
        creature_name=args.creature or None,
        habitat=args.habitat,
        check_currency=args.check_currency,
    )

    print(f"\n{'name':<10} {'brain':<20} {'health':>6} {'outcome':<20} notes")
    print("-" * 85)
    for r in results:
        if r.get("skip"):
            continue
        notes_parts = []
        if r.get("substrate_status") and r["substrate_status"] != "live":
            notes_parts.append(f"substrate:{r['substrate_status']}")
        if r.get("consolidated"):
            c = r["consolidated"]
            notes_parts.append(f"consolidated(+{c['promoted']}/-{c['archived']})")
        if r.get("stale_bonds"):
            notes_parts.append(f"stale:{','.join(r['stale_bonds'])}")
        if r.get("reflected"):
            notes_parts.append(f"reflected({r.get('reflect_len',0)}ch)")
        if r.get("forgetting_pass"):
            fp = r["forgetting_pass"]
            notes_parts.append(f"forgot({fp['forgotten']}, {fp['recallable_after']}/{fp['target']})")
        if r.get("brain_unavailable"):
            notes_parts.append("brain_unavailable")
        # Phase 2: surface a flagged brain model (recorded drift gets a *).
        mc = r.get("model_currency")
        if mc and mc.get("status") in ("SUPERSEDED", "DEPRECATED"):
            tag = f"currency:{mc['status'].lower()}"
            if mc.get("successor"):
                tag += f"→{mc['successor']}"
            if mc.get("recorded"):
                tag += "*"
            notes_parts.append(tag)
        # D-068: surface brain fatigue with cause + recovery window.
        if r.get("fatigue") and r["fatigue"].get("state") == "fatigued":
            f = r["fatigue"]
            notes_parts.append(
                f"fatigue:{f.get('cause','?')} (recovers in {f.get('remaining_human','?')})"
            )
        notes = "; ".join(notes_parts) if notes_parts else "-"
        # When resting, the health grade column is meaningless — show "—".
        health_col = "—" if r.get("outcome") == "resting" else r.get("health_grade", "?")
        print(f"{r['name']:<10} {r.get('brain','?'):<20} "
              f"{health_col:>6} {r.get('outcome','?'):<20} {notes}")

    print()


if __name__ == "__main__":
    main()
