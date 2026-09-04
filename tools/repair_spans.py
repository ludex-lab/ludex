"""Repair span records written by older writers — derivations only, never inventions.

A span ledger is a record. Making a validator pass by filling in a field we do
not have would be the same error class as an instrument that reports a number it
did not measure, so this tool does exactly three things and refuses the rest:

  t        -> timestamp     rename; the value is already there
  who      -> creature      rename, and ONLY when it matches the creature whose
                            store the line lives in — a mismatch means something
                            stranger than a schema drift and is left alone
  loose    -> attributes    domain fields that predate the attributes envelope
                            are moved into it, not copied, so nothing duplicates

Anything else missing (a `reward` that was never written, a `provider` on an old
substrate_transition) stays missing and is reported as declined.

The shapes come from three writers that coexisted in the history: the current
Span writer, an earlier one using `t`/`who` with domain fields at top level
(birth, village_arrival), and topos_sensed lines that simply lack creature.

Why it matters beyond tidiness: substrate_transition's idempotence check reads
`prior[-1].get("attributes", {})`, so a record without that envelope can never
match and a re-run silently writes a duplicate event.

Run:  .venv/bin/python tools/repair_spans.py                 # dry run, reports
      .venv/bin/python tools/repair_spans.py --apply         # writes, keeps .bak
"""
import argparse
import glob
import json
import shutil
import time
from pathlib import Path

CORE = ("kind", "timestamp", "creature", "attributes")


def defects(path):
    out = []
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            out.append((i, None, ["UNPARSEABLE"]))
            continue
        missing = [k for k in CORE if k not in d]
        if missing:
            out.append((i, d, missing))
    return out


def repair(d, creature):
    """Return (repaired, fixed, declined). Never fabricates a value."""
    fixed, declined = [], []
    if "timestamp" not in d and "t" in d:
        d["timestamp"] = d.pop("t")
        fixed.append("t->timestamp")
    if "creature" not in d:
        who = d.get("who")
        if who == creature:
            d.pop("who")
            d["creature"] = creature
            fixed.append("who->creature")
        elif who is None:
            d["creature"] = creature
            fixed.append("creature from path")
        else:
            declined.append(f"creature: who={who!r} disagrees with store owner "
                            f"{creature!r} — not resolved by this tool")
    if "attributes" not in d:
        loose = {k: v for k, v in d.items() if k not in CORE}
        if loose:
            for k in loose:
                d.pop(k)
            d["attributes"] = loose
            fixed.append(f"enveloped {sorted(loose)}")
        else:
            declined.append("attributes: nothing to envelope")
    if "timestamp" not in d:
        declined.append("timestamp: no value present, cannot be derived")
    return d, fixed, declined


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--glob", default="creatures/*/store/spans.jsonl")
    a = ap.parse_args()

    total_fixed = total_declined = 0
    for path in sorted(glob.glob(a.glob)):
        found = defects(path)
        if not found:
            continue
        creature = Path(path).parts[1]
        print(f"\n{path}  ({len(found)} defective)")
        lines = Path(path).read_text().splitlines()
        for ln, d, missing in found:
            if d is None:
                print(f"  line {ln:>5}  UNPARSEABLE — left untouched, needs a human")
                continue
            d2, fixed, declined = repair(dict(d), creature)
            print(f"  line {ln:>5}  missing {'+'.join(missing)}")
            for f in fixed:
                print(f"          fixed    {f}")
            for c in declined:
                print(f"          declined {c}")
            total_fixed += len(fixed)
            total_declined += len(declined)
            if a.apply and fixed:
                lines[ln-1] = json.dumps(d2, ensure_ascii=False)
        if a.apply:
            shutil.copy2(path, f"{path}.bak-{int(time.time())}")
            Path(path).write_text("\n".join(lines) + "\n")

    print(f"\n{total_fixed} derivations applied, {total_declined} declined"
          if a.apply else
          f"\n{total_fixed} derivations available, {total_declined} would be declined"
          "\n(dry run — pass --apply to write; .bak kept)")


if __name__ == "__main__":
    main()
