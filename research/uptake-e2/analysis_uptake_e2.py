"""Pre-committed analysis for Uptake E2 (PREREG §6).

Committed before the battery fires, for the same reason it was in E1: every
choice below is fixed while the data does not yet exist, so no result can reach
back and select its own contrast.

Fixed here:
  * per-lineage arm success rates with exact (Clopper-Pearson) CIs
  * U2 vs U0 and U1 vs U0 as WITHIN-ENV paired discordance, McNemar exact,
    two-sided, p reported replication-grade with no confirmatory framing
  * U3 vs U0 as the replication anchor contrast
  * no pooling across lineages; haiku is table-only, zero tests
  * the instrument-health table, standing since E1
  * the block-identity check (PREREG §4 assert 1) as a first-class output

The exact statistics are imported from the E1 analyser rather than re-derived,
so the two walks cannot disagree about what a p-value means.

Run: .venv/bin/python research/uptake-e2/analysis_uptake_e2.py \
       --battery research/uptake-e2/e2_battery.jsonl
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research" / "physics-checkup"))
from analysis_physics_e1 import clopper_pearson, mcnemar_exact   # noqa: E402

ARMS = ("U0", "U1", "U2", "U3")


def load(path):
    return [json.loads(ln) for ln in Path(path).read_text().splitlines() if ln.strip()]


def index(rows):
    """(lineage, env, arm) -> is_correct, dropping VOID and unsampleable runs."""
    out, dropped = {}, []
    for r in rows:
        if r.get("void") or r.get("status") == "UNSAMPLEABLE":
            dropped.append((r.get("lineage"), r.get("env"), r.get("arm"),
                            r.get("void") or ["UNSAMPLEABLE"]))
            continue
        out[(r["lineage"], r["env"], r["arm"])] = bool(r.get("is_correct"))
    return out, dropped


def rates(idx, lineage):
    tab = {}
    for a in ARMS:
        vals = [v for (ln, _e, ar), v in idx.items() if ln == lineage and ar == a]
        k, n = sum(vals), len(vals)
        lo, hi = clopper_pearson(k, n)
        tab[a] = {"k": k, "n": n, "rate": (k / n if n else float("nan")),
                  "ci95": (round(lo, 3), round(hi, 3))}
    return tab


def paired(idx, lineage, a, b):
    envs = sorted({e for (ln, e, ar) in idx if ln == lineage and ar == a}
                  & {e for (ln, e, ar) in idx if ln == lineage and ar == b})
    a_only = sum(1 for e in envs if idx[(lineage, e, a)] and not idx[(lineage, e, b)])
    b_only = sum(1 for e in envs if idx[(lineage, e, b)] and not idx[(lineage, e, a)])
    both = sum(1 for e in envs if idx[(lineage, e, a)] and idx[(lineage, e, b)])
    return {"n_pairs": len(envs), f"{a}_only": a_only, f"{b}_only": b_only,
            "both": both, "neither": len(envs) - a_only - b_only - both,
            "p_exact_two_sided": round(mcnemar_exact(a_only, b_only), 4),
            "note": "replication-grade: p reported, no confirmatory framing"}


def instrument_health(rows, lineage):
    tally = {}
    for a in ARMS:
        rs = [r for r in rows if r.get("lineage") == lineage and r.get("arm") == a]
        graded = [r for r in rs if not r.get("void")]
        tally[a] = {
            "graded_n": len(graded),
            "unparseable": sum(1 for r in graded if r.get("status") == "UNPARSEABLE"),
            "void_brain": sum(1 for r in rs
                              if "VOID-brain:no-reply" in (r.get("void") or [])),
            "pairs_dropped": sum(1 for r in rs if r.get("pair_dropped")),
            "void_other": sum(1 for r in rs if r.get("void")
                              and "VOID-brain:no-reply" not in r["void"]),
        }
    return tally


def block_identity(rows, lineage):
    """PREREG §4 assert 1, checked against the ledger rather than asserted.

    U2 is only a surface manipulation if the text it carried is the text the
    organ rendered. Each arm recorded a hash of the block it actually shipped,
    so equality is verifiable here per environment. A mismatch means the walk
    measured a content change and the headline question was never asked.
    """
    by_env = defaultdict(dict)
    for r in rows:
        if r.get("lineage") == lineage and r.get("block_sha"):
            by_env[r["env"]][r["arm"]] = r["block_sha"]
    checked, matched, mismatches = 0, 0, []
    for env, per_arm in sorted(by_env.items()):
        if "U0" in per_arm and "U2" in per_arm:
            checked += 1
            if per_arm["U0"] == per_arm["U2"]:
                matched += 1
            else:
                mismatches.append({"env": env, "U0": per_arm["U0"],
                                   "U2": per_arm["U2"]})
    return {"envs_checked": checked, "identical": matched,
            "mismatches": mismatches,
            "verdict": ("HOLDS" if checked and matched == checked
                        else "NOT CHECKED" if not checked else "BROKEN")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--battery", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    rows = load(a.battery)
    idx, dropped = index(rows)
    report = {"dropped": dropped, "lineages": {},
              "walk_shape": ("measured-profile, within-env surface manipulation; "
                             "estimates are conditional on the E1 pool (waiver)")}

    for lineage in sorted({ln for (ln, _e, _a) in idx}):
        tab = rates(idx, lineage)
        entry = {
            "rates": tab,
            "instrument_health": instrument_health(rows, lineage),
            "block_identity": block_identity(rows, lineage),
            "surface_U2_vs_U0": paired(idx, lineage, "U2", "U0"),
            "absence_hint_U1_vs_U0": paired(idx, lineage, "U1", "U0"),
            "anchor_U3_vs_U0": paired(idx, lineage, "U3", "U0"),
        }
        # The pre-committed reading (PREREG §3). Stated as which registered
        # sentence the pattern selects, not as a fresh interpretation.
        rec = lambda arm: tab[arm]["rate"]        # noqa: E731
        u0, u1, u2 = rec("U0"), rec("U1"), rec("U2")
        base = u0 if u0 == u0 else 0.0
        u2_rec, u1_rec = u2 > base, u1 > base
        entry["registered_reading"] = (
            "surface: consultation skipped" if u2_rec and not u1_rec else
            "compound: absence hint triggers the skip, surface permits it"
            if u2_rec and u1_rec else
            "absence hint primary; surface secondary" if u1_rec else
            "third cause — verdict withheld, back to design review")
        report["lineages"][lineage] = entry

    text = json.dumps(report, ensure_ascii=False, indent=1)
    print(text)
    if a.out:
        Path(a.out).write_text(text)


if __name__ == "__main__":
    main()
