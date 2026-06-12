"""Structural validator for creature habitat data.

Checks the on-disk invariants the engine and research tooling rely on:

- ludex.yaml parses; has name + brain.provider + brain.model;
  brain.substrate_status (if present) is a known lifecycle label.
- store/spans.jsonl: every line is JSON with kind/creature/timestamp/
  attributes; timestamp numeric.
- store/rewards.jsonl, memory/memories.jsonl (if present): every line parses.
- snapshots/*/: each snapshot dir contains a parseable snapshot.json.

Validator-first by design: no schema_version fields are required yet —
those get added when a real migration demands them. This script is the
guard that makes silent drift (the json/yaml split, case-sensitivity
bugs) visible before it contaminates analysis.

Usage:
    python tools/validate_creature_data.py            # all creatures
    python tools/validate_creature_data.py --creature Nimbus
Exit code: 0 clean, 1 any error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CREATURES = ROOT / "creatures"

from ludex.core.heartbeat import SKIP_DIRS  # test fixtures, not real creatures

SUBSTRATE_STATUSES = {"live", "cost-watch", "wind-down", "retiring", "dormant"}
SPAN_REQUIRED = ("kind", "creature", "timestamp", "attributes")


def _rel(path: Path) -> Path:
    """Path relative to repo root for display; absolute when outside it."""
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def _check_jsonl(path: Path, errors: list[str], required: tuple = ()) -> int:
    n = 0
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"{_rel(path)}:{i}: not JSON ({e})")
            continue
        n += 1
        for key in required:
            if key not in obj:
                errors.append(f"{_rel(path)}:{i}: missing '{key}'")
        if required and "timestamp" in obj and not isinstance(
                obj["timestamp"], (int, float)):
            errors.append(f"{_rel(path)}:{i}: timestamp not numeric")
    return n


def validate_creature(habitat: Path) -> tuple[list[str], dict]:
    errors: list[str] = []
    stats = {"spans": 0, "snapshots": 0}

    ypath = habitat / "ludex.yaml"
    if ypath.exists():
        try:
            cfg = yaml.safe_load(ypath.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            errors.append(f"{_rel(ypath)}: yaml parse error ({e})")
            cfg = {}
        if cfg:
            if not cfg.get("name"):
                errors.append(f"{_rel(ypath)}: missing name")
            brain = cfg.get("brain") or {}
            for key in ("provider", "model"):
                if not brain.get(key):
                    errors.append(f"{_rel(ypath)}: missing brain.{key}")
            status = brain.get("substrate_status")
            if status and status not in SUBSTRATE_STATUSES:
                errors.append(
                    f"{_rel(ypath)}: unknown substrate_status "
                    f"{status!r} (known: {sorted(SUBSTRATE_STATUSES)})")
    else:
        errors.append(f"{habitat.name}: no ludex.yaml")

    spans = habitat / "store" / "spans.jsonl"
    if spans.exists():
        stats["spans"] = _check_jsonl(spans, errors, required=SPAN_REQUIRED)

    rewards = habitat / "store" / "rewards.jsonl"
    if rewards.exists():
        _check_jsonl(rewards, errors)

    mems = habitat / "memory" / "memories.jsonl"
    if mems.exists():
        _check_jsonl(mems, errors)
        # Status vocabulary (candidate_for_distillation retired 2026-06-12)
        for i, line in enumerate(mems.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                status = json.loads(line).get("status", "active")
            except json.JSONDecodeError:
                continue  # already reported by _check_jsonl
            if status not in ("active", "archived", "deleted"):
                errors.append(f"{_rel(mems)}:{i}: unknown status {status!r} "
                              "(known: active/archived/deleted)")

    snaps = habitat / "snapshots"
    if snaps.is_dir():
        for snap in sorted(p for p in snaps.iterdir() if p.is_dir()):
            stats["snapshots"] += 1
            meta = snap / "snapshot.json"
            if not meta.exists():
                errors.append(f"{_rel(snap)}: no snapshot.json")
                continue
            try:
                json.loads(meta.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                errors.append(f"{_rel(meta)}: not JSON ({e})")

    return errors, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="validate creature habitat data")
    ap.add_argument("--creature", default="", help="validate one creature only")
    ap.add_argument("--root", default=str(CREATURES), help="creatures root dir")
    args = ap.parse_args(argv)

    root = Path(args.root)
    habitats = ([root / args.creature] if args.creature
                else sorted(p for p in root.iterdir() if p.is_dir()
                            and not p.name.startswith(".")
                            and p.name not in SKIP_DIRS))

    total_errors = 0
    for habitat in habitats:
        if not habitat.is_dir():
            print(f"✗ {habitat.name}: not found")
            return 1
        errors, stats = validate_creature(habitat)
        mark = "✗" if errors else "✓"
        print(f"{mark} {habitat.name:<12} spans={stats['spans']:<6} "
              f"snapshots={stats['snapshots']:<4} errors={len(errors)}")
        for e in errors:
            print(f"    {e}")
        total_errors += len(errors)

    print(f"\n{'CLEAN' if not total_errors else 'ERRORS: ' + str(total_errors)}")
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
