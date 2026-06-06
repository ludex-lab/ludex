"""Annotate `brain.class` on existing creature ludex.yaml files.

Reads each creature's brain (provider, model), computes the class via
`classify_brain`, and writes it back to `brain.class` in the yaml so
the value is durable. Idempotent — re-running on already-annotated
creatures changes nothing unless the brain changed.

Usage:
    python tools/annotate_brain_class.py
    python tools/annotate_brain_class.py --dry-run
    python tools/annotate_brain_class.py --creature Wick

The `brain_class` property still falls back to `classify_brain` when
the field is absent, so this is purely a persistence/discoverability
helper. Caretakers can override by editing the yaml directly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ludex.core.brain_class import classify_brain


def annotate(creature_dir: Path, dry_run: bool = False) -> tuple[bool, str, str]:
    """Returns (changed, before_class, after_class)."""
    yaml_path = creature_dir / "ludex.yaml"
    if not yaml_path.exists():
        return False, "", ""
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    brain = data.get("brain") or {}
    before = brain.get("class", "")
    after = classify_brain(brain.get("provider", ""), brain.get("model", ""))
    if before == after:
        return False, before, after
    brain["class"] = after
    data["brain"] = brain
    if not dry_run:
        with yaml_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)
    return True, before, after


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--creature", default="", help="annotate one creature only")
    parser.add_argument("--dir", default="creatures", help="creatures dir (default: creatures)")
    args = parser.parse_args()

    base = Path(args.dir)
    if not base.exists():
        print(f"no {base}/ directory")
        return 1

    print(f"\n{'creature':<14} {'before':<12} {'after':<12} action")
    print("-" * 60)
    changed = 0
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        if args.creature and d.name != args.creature:
            continue
        if not (d / "ludex.yaml").exists():
            continue
        ch, before, after = annotate(d, dry_run=args.dry_run)
        action = "—" if not ch else ("WOULD WRITE" if args.dry_run else "wrote")
        before_label = before or "(unset)"
        print(f"{d.name:<14} {before_label:<12} {after:<12} {action}")
        if ch:
            changed += 1

    print(f"\n{'would change' if args.dry_run else 'changed'}: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
