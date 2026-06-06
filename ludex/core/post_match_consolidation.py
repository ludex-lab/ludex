"""Post-match consolidation pipeline — §F.11 Ludex-side (Phase 1).

Reads LxM match artifacts (log.json / state.json / result.json),
extracts per-pair summaries via game adapters, and writes `game_frame:*`
bond events on each participating creature's bond files via
`selfhood.update_bond()`. Honors §G.0.4 N-4 by routing through the
existing isolated "Role-play events" section — this module does not
touch the `update_bond()` schema.

Design doc: `docs/consolidation-pipeline-design.md` (decisions resolved
with Mac Ludex-Cody on 2026-04-22).

Key operational properties:
- **Dry-run by default.** `--commit` required to write.
- **Idempotent.** `consolidation_index.json` per creature records
  processed match_ids with a timestamp; `--force` overrides.
- **Habitat-scoped.** `--habitat-root <origin>` skips creatures whose
  `habitat.origin` doesn't match (D-052).
- **Pre-match verification.** Before emitting for a match, assert the
  log's agent_id set matches the creature set we're about to process.
  Mismatch → raise, no writes, report.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DEFAULT_CREATURES_DIR = Path(__file__).resolve().parents[2] / "creatures"

# Same skip-list used by heartbeat — test fixtures, not real creatures.
SKIP_DIRS = {"ClaudeCLI1", "ClaudeCode1", "Claude_Spike", "FC_Test",
             "PersistTest", "onboarding", "tier_test"}


@dataclass(frozen=True)
class ConsolidationReport:
    """Result of processing one match for one creature."""
    creature: str
    match_id: str
    action: str           # "skipped_already" | "skipped_filter" | "dry_run" | "committed" | "error"
    pair_count: int       # number of (other_creature) summaries produced
    detail: str = ""


# -----------------------------------------------------------------------------
# consolidation_index.json — read / write
# -----------------------------------------------------------------------------

def _index_path(creature_dir: Path) -> Path:
    return creature_dir / "consolidation_index.json"


def load_index(creature_dir: Path) -> dict[str, Any]:
    """Return the creature's consolidation index (empty dict if missing)."""
    p = _index_path(creature_dir)
    if not p.exists():
        return {"processed": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"{p}: failed to load index ({e}); starting fresh")
        return {"processed": {}}


def write_index(creature_dir: Path, index: dict[str, Any]) -> None:
    p = _index_path(creature_dir)
    p.write_text(json.dumps(index, indent=2, sort_keys=True),
                 encoding="utf-8")


def is_processed(index: dict[str, Any], match_id: str) -> bool:
    return match_id in (index.get("processed") or {})


def mark_processed(index: dict[str, Any], match_id: str,
                   pair_count: int) -> None:
    index.setdefault("processed", {})[match_id] = {
        "ts": int(time.time()),
        "pair_count": pair_count,
    }


# -----------------------------------------------------------------------------
# Match artifact loading
# -----------------------------------------------------------------------------

def _load_match_artifacts(
    match_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], str]:
    """Load log/state/result + infer game name. Raises FileNotFoundError
    or ValueError on malformed dirs."""
    log_path = match_dir / "log.json"
    state_path = match_dir / "state.json"
    result_path = match_dir / "result.json"
    if not log_path.exists():
        raise FileNotFoundError(f"{match_dir}: missing log.json")
    if not state_path.exists():
        raise FileNotFoundError(f"{match_dir}: missing state.json")

    log = json.loads(log_path.read_text(encoding="utf-8"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    result = (
        json.loads(result_path.read_text(encoding="utf-8"))
        if result_path.exists()
        else {}
    )

    # Game name lives in state.game.name or result.game or match_dir name hint
    game = (
        (state.get("game") or {}).get("name")
        or result.get("game")
        or ""
    ).lower()
    if not game:
        # Heuristic fallback — look at the dir name for common game tokens.
        dn = match_dir.name.lower()
        for candidate in ("avalon", "trust"):
            if candidate in dn:
                game = candidate
                break
    if not game:
        raise ValueError(
            f"{match_dir}: could not infer game name from state/result/path"
        )
    return log, state, result, game


def _agent_ids_in_log(log: Iterable[dict[str, Any]]) -> set[str]:
    """Set of all agent_ids that produced at least one log entry."""
    return {e.get("agent_id") for e in log if e.get("agent_id")}


# -----------------------------------------------------------------------------
# Creature config helpers (habitat scoping, name resolution)
# -----------------------------------------------------------------------------

def _habitat_origin_of(creature_dir: Path) -> str:
    """Read habitat.origin without a full organism build. Returns "" if
    absent."""
    for fname in ("ludex.yaml", "ludex.json"):
        p = creature_dir / fname
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8")
            if fname.endswith(".yaml"):
                try:
                    import yaml
                except Exception:
                    continue
                data = yaml.safe_load(text) or {}
            else:
                data = json.loads(text)
            origin = ((data.get("habitat") or {}).get("origin")) or ""
            return str(origin)
        except Exception:
            return ""
    return ""


def _list_habitat_creatures(
    creatures_dir: Path,
    habitat_root: str = "",
) -> dict[str, Path]:
    """Return {creature_name (lowercased): creature_dir} for creatures
    in the given habitat (or all, if habitat_root is empty)."""
    out: dict[str, Path] = {}
    for d in sorted(creatures_dir.iterdir()):
        if not d.is_dir() or d.name in SKIP_DIRS:
            continue
        if not any((d / f).exists() for f in ("ludex.yaml", "ludex.json")):
            continue
        if habitat_root:
            if _habitat_origin_of(d) != habitat_root:
                continue
        out[d.name.lower()] = d
    return out


# -----------------------------------------------------------------------------
# Pre-match verification
# -----------------------------------------------------------------------------

class VerificationError(RuntimeError):
    """Raised when log agent_ids and target creatures disagree."""


def verify_match(
    match_id: str,
    log_agent_ids: set[str],
    target_creatures: set[str],
) -> None:
    """Assert log agent_ids ⊆ target_creatures (case-insensitive).

    A match may be processed against a superset of creatures (not every
    creature participated), so the constraint is *subset*, not equality.
    Mismatch means log references a creature we don't have — suspicious
    enough to fail loud.
    """
    log_lower = {a.lower() for a in log_agent_ids}
    target_lower = {c.lower() for c in target_creatures}
    unknown = log_lower - target_lower
    if unknown:
        raise VerificationError(
            f"{match_id}: log references creatures not in target set: "
            f"{sorted(unknown)}. Target set: {sorted(target_lower)}."
        )


# -----------------------------------------------------------------------------
# Match consolidation
# -----------------------------------------------------------------------------

def consolidate_match(
    match_dir: Path,
    creatures_by_name: dict[str, Path],
    *,
    dry_run: bool,
    force: bool = False,
) -> list[ConsolidationReport]:
    """Process one match. Returns one report per (creature, match) pair
    considered. Does not re-raise on per-creature write errors; returns
    them as action="error" rows.
    """
    from ludex.core import game_adapters
    from ludex.core.organism_config import OrganismConfig
    from ludex.core import selfhood

    match_id = match_dir.name
    reports: list[ConsolidationReport] = []

    try:
        log, state, _result, game = _load_match_artifacts(match_dir)
    except (FileNotFoundError, ValueError) as e:
        reports.append(ConsolidationReport(
            creature="(n/a)", match_id=match_id, action="error",
            pair_count=0, detail=str(e),
        ))
        return reports

    try:
        adapter = game_adapters.get(game)
    except KeyError as e:
        reports.append(ConsolidationReport(
            creature="(n/a)", match_id=match_id, action="error",
            pair_count=0, detail=str(e),
        ))
        return reports

    # Pre-match verification
    log_agents = _agent_ids_in_log(log)
    try:
        verify_match(match_id, log_agents, set(creatures_by_name.keys()))
    except VerificationError as e:
        reports.append(ConsolidationReport(
            creature="(n/a)", match_id=match_id, action="error",
            pair_count=0, detail=f"verify_match: {e}",
        ))
        return reports

    # Match-participating creatures = intersection of log and target set
    participating = {a.lower() for a in log_agents} & set(creatures_by_name.keys())

    for me_name in sorted(participating):
        creature_dir = creatures_by_name[me_name]
        index = load_index(creature_dir)

        if not force and is_processed(index, match_id):
            reports.append(ConsolidationReport(
                creature=me_name, match_id=match_id, action="skipped_already",
                pair_count=0,
            ))
            continue

        others = [o for o in participating if o != me_name]
        pair_summaries = [
            adapter.extract_pair_summary(log, state, match_id, me_name, o)
            for o in others
        ]

        if dry_run:
            reports.append(ConsolidationReport(
                creature=me_name, match_id=match_id, action="dry_run",
                pair_count=len(pair_summaries),
                detail="; ".join(
                    adapter.phrase_for(s) for s in pair_summaries
                )[:600],
            ))
            continue

        # LIVE path — build organism, call update_bond per pair, write index
        try:
            cfg = OrganismConfig.load(str(creature_dir))
            organism = cfg.build()
        except Exception as e:
            reports.append(ConsolidationReport(
                creature=me_name, match_id=match_id, action="error",
                pair_count=0, detail=f"build: {e}",
            ))
            continue

        for summary in pair_summaries:
            phrase = adapter.phrase_for(summary)
            try:
                selfhood.update_bond(
                    organism,
                    other_name=summary.other_id,
                    shared_experience=phrase,
                    context=f"game_frame:{match_id}",
                )
            except Exception as e:
                reports.append(ConsolidationReport(
                    creature=me_name, match_id=match_id, action="error",
                    pair_count=0,
                    detail=f"update_bond {summary.other_id}: {e}",
                ))
                # Still try remaining pairs
                continue

        mark_processed(index, match_id, len(pair_summaries))
        write_index(creature_dir, index)
        reports.append(ConsolidationReport(
            creature=me_name, match_id=match_id, action="committed",
            pair_count=len(pair_summaries),
        ))

    return reports


# -----------------------------------------------------------------------------
# Batch over a matches root
# -----------------------------------------------------------------------------

def consolidate_matches_root(
    matches_root: Path,
    creatures_by_name: dict[str, Path],
    *,
    dry_run: bool,
    force: bool = False,
    match_filter: str = "",
) -> list[ConsolidationReport]:
    """Process every `<matches_root>/<match_dir>/` that has log.json.

    `match_filter`: if non-empty, only match_dir names containing this
    substring are processed.
    """
    reports: list[ConsolidationReport] = []
    for match_dir in sorted(matches_root.iterdir()):
        if not match_dir.is_dir():
            continue
        if not (match_dir / "log.json").exists():
            continue
        if match_filter and match_filter not in match_dir.name:
            continue
        reports.extend(consolidate_match(
            match_dir, creatures_by_name,
            dry_run=dry_run, force=force,
        ))
    return reports
