"""tools/cohort_stage.py — caretaker grade-distribution view.

Walks creatures/ (or a passed dir), runs `audit_creature` on each, and
prints a per-creature row + a bottom distribution table. Read-only, no
mutations.

Usage:
    python tools/cohort_stage.py
    python tools/cohort_stage.py --dir creatures
    python tools/cohort_stage.py --include-test     # include scratch/Spike/Test creatures
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# allow the script to import from project root when invoked directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from ludex.core.stage import STAGES, audit_creature  # noqa: E402

# Heuristic for "real" cohort vs scratch/test creatures.
_TEST_PATTERNS = ("Spike", "Test", "Dbg", "Persist", "Trace", "OllamaPrimo", "Tentacle", "Tent", "field_study", "diff_resp", "gemini_flash_test", "tier_test", "onboarding", "Claude_Spike", "ClaudeCode1", "PrimoSdk", "E2E", "Moss", "Flare")


def is_test_creature(name: str) -> bool:
    return any(p in name for p in _TEST_PATTERNS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cohort stage-distribution report — caretaker visibility.",
    )
    parser.add_argument("--dir", default="creatures", help="creatures parent dir (default: creatures)")
    parser.add_argument("--include-test", action="store_true",
                        help="include scratch/Spike/Test creatures in the report")
    args = parser.parse_args(argv)

    base = Path(args.dir)
    if not base.exists():
        print(f"no such directory: {base}", file=sys.stderr)
        return 2

    rows = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        if not (d / "ludex.yaml").exists():
            continue
        if not args.include_test and is_test_creature(d.name):
            continue
        try:
            report = audit_creature(d)
        except Exception as e:
            print(f"  [warn] {d.name}: {e}", file=sys.stderr)
            continue
        rows.append((d.name, report))

    if not rows:
        print("(no creatures matched)")
        return 0

    # Header
    print(f"\n{'creature':<14} {'stage':<10} {'age_d':>6} {'sess':>5} "
          f"{'mem':>5} {'flds':>5} {'bond':>5} {'last_d':>7}  flags")
    print("-" * 80)

    for name, r in rows:
        s = r.signals
        last = s["last_session_days_ago"]
        last_str = f"{last:.1f}" if last is not None else "—"
        flags_str = ", ".join(r.flags) if r.flags else ""
        print(f"{name:<14} {r.name:<10} {s['age_days']:>6.1f} "
              f"{s['session_count']:>5} {s['memory_count']:>5} "
              f"{s['field_count']:>5} {s['bond_count']:>5} {last_str:>7}  {flags_str}")

    # Distribution
    dist: Counter = Counter(r.name for _, r in rows)
    flag_dist: Counter = Counter()
    for _, r in rows:
        for f in r.flags:
            flag_dist[f] += 1

    print("\nstage distribution:")
    for stage in STAGES:
        count = dist.get(stage, 0)
        bar = "█" * count
        print(f"  {stage:<10} {count:>3}  {bar}")

    if flag_dist:
        print("\nattention flags:")
        for flag, count in flag_dist.most_common():
            print(f"  {flag:<25} {count}")

    print(f"\ntotal: {len(rows)} creatures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
