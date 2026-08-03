"""Pre-committed analysis for Physics E1 (PREREG §7).

Committed BEFORE the battery fires. That is the whole point: every choice below
— which contrast is confirmatory (none), which test, which direction, what gets
pooled — is fixed while the data does not yet exist, so no result can reach back
and pick its own analysis.

Fixed here, per PREREG:
  * per-lineage condition success rates with exact (Clopper-Pearson) CIs
  * recovery = (C3 - C0) / (C1 - C0), point estimate, reported as a PROFILE
  * C2 vs C1: McNemar exact, TWO-SIDED, with the direction decomposed
  * C3 vs C0: exact p, reported replication-grade (no confirmatory framing)
  * NO pooling across lineages
  * default anchor and W0 literacy: descriptive tables only, no tests

Reminder carried into every verdict line: this walk registers no confirmatory
NHST. C3 > C0 is expected to pass, recovery is expected near 1.0, and only the
harm cell is genuinely uncertain.

Run: .venv/bin/python research/physics-checkup/analysis_physics_e1.py \
       --battery e1_battery.jsonl [--screen e1_screen.jsonl] [--anchor e1_anchor.jsonl]
"""
import argparse
import json
from collections import defaultdict
from math import comb
from pathlib import Path

CONDS = ("C0", "C1", "C2", "C3")


def load(path):
    if not path or not Path(path).exists():
        return []
    return [json.loads(ln) for ln in Path(path).read_text().splitlines() if ln.strip()]


# --- exact statistics, stdlib only -------------------------------------------
def binom_cdf(k, n, p):
    return sum(comb(n, i) * p**i * (1 - p)**(n - i) for i in range(k + 1))


def _bisect(f, target, increasing):
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if (f(mid) < target) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clopper_pearson(k, n, alpha=0.05):
    """Exact CI by bisection on the binomial CDF — no scipy dependency.

    Lower: smallest p with P(X >= k) = alpha/2, increasing in p.
    Upper: largest p with P(X <= k) = alpha/2, decreasing in p.
    The two tails need opposite bisection directions; collapsing them into one
    branch silently returned 0.0 as the upper bound for k=0, which would have
    reported a zero-success condition as bounded above by zero.
    """
    if n == 0:
        return (float("nan"), float("nan"))
    low = 0.0 if k == 0 else _bisect(
        lambda p: 1 - binom_cdf(k - 1, n, p), alpha / 2, True)
    high = 1.0 if k == n else _bisect(
        lambda p: binom_cdf(k, n, p), alpha / 2, False)
    return (low, high)


def mcnemar_exact(b, c):
    """Two-sided exact McNemar on discordant pairs: binomial(b+c, 0.5).

    b = A-only successes, c = B-only successes. Two-sided by doubling the
    smaller tail (capped at 1) — the standard exact convention.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = binom_cdf(k, n, 0.5)
    return min(1.0, 2 * tail)


# --- shaping ------------------------------------------------------------------
def index(rows):
    """(lineage, env, cond) -> is_correct, skipping VOID and unsampleable runs."""
    out, dropped = {}, []
    for r in rows:
        if r.get("void"):
            dropped.append((r.get("lineage"), r.get("env"), r.get("cond"), r["void"]))
            continue
        if r.get("status") == "UNSAMPLEABLE":
            dropped.append((r.get("lineage"), r.get("env"), r.get("cond"), ["UNSAMPLEABLE"]))
            continue
        out[(r["lineage"], r["env"], r["cond"])] = bool(r.get("is_correct"))
    return out, dropped


def instrument_health(rows, lineage):
    """Per-condition instrument-failure table (AMENDMENT 01 A3).

    Standing form of the detection logic that caught two real artifacts before
    firing: an instrument defect masquerades as an effect by CONCENTRATING in
    one condition. Haiku twice ended a C3 reply with markdown-bolded powers
    (unparseable by construction), and one C1 call came back empty and was
    being graded as a wrong answer. Both would have moved a single condition's
    rate. So both counts are reported per condition, always, alongside the
    result — never inferred from silence.
    """
    tally = {}
    for c in CONDS:
        rs = [r for r in rows if r.get("lineage") == lineage and r.get("cond") == c]
        graded = [r for r in rs if not r.get("void")]
        tally[c] = {
            "graded_n": len(graded),
            "unparseable": sum(1 for r in graded if r.get("status") == "UNPARSEABLE"),
            "void_brain": sum(1 for r in rs
                              if "VOID-brain:no-reply" in (r.get("void") or [])),
            "pairs_dropped": sum(1 for r in rs if r.get("pair_dropped")),
            "void_other": sum(1 for r in rs if r.get("void")
                              and "VOID-brain:no-reply" not in r["void"]),
        }
    return tally


def rates(idx, lineage):
    tab = {}
    for c in CONDS:
        vals = [v for (ln, _e, cd), v in idx.items() if ln == lineage and cd == c]
        k, n = sum(vals), len(vals)
        lo, hi = clopper_pearson(k, n)
        tab[c] = {"k": k, "n": n, "rate": (k / n if n else float("nan")),
                  "ci95": (round(lo, 3), round(hi, 3))}
    return tab


def paired(idx, lineage, a, b):
    """Discordant counts for conditions a,b over envs measured in both."""
    envs = sorted({e for (ln, e, cd) in idx if ln == lineage and cd == a}
                  & {e for (ln, e, cd) in idx if ln == lineage and cd == b})
    a_only = sum(1 for e in envs if idx[(lineage, e, a)] and not idx[(lineage, e, b)])
    b_only = sum(1 for e in envs if idx[(lineage, e, b)] and not idx[(lineage, e, a)])
    both = sum(1 for e in envs if idx[(lineage, e, a)] and idx[(lineage, e, b)])
    neither = len(envs) - a_only - b_only - both
    return {"n_pairs": len(envs), f"{a}_only": a_only, f"{b}_only": b_only,
            "both": both, "neither": neither,
            "p_exact_two_sided": round(mcnemar_exact(a_only, b_only), 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--battery", required=True)
    ap.add_argument("--screen", default="")
    ap.add_argument("--anchor", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    brows = load(a.battery)
    idx, dropped = index(brows)
    report = {"dropped": dropped, "lineages": {},
              "walk_shape": "measured-profile; no confirmatory NHST registered"}

    for lineage in sorted({ln for (ln, _e, _c) in idx}):
        tab = rates(idx, lineage)
        r0, r1, r3 = tab["C0"]["rate"], tab["C1"]["rate"], tab["C3"]["rate"]
        denom = r1 - r0
        recovery = ((r3 - r0) / denom) if denom > 0 else None

        harm = paired(idx, lineage, "C1", "C2")
        # direction decomposition: C1_only = recall injection LOST an env (harm),
        # C2_only = recall injection GAINED one (help; no cell, but the two-sided
        # test counts it, so it is reported rather than silently absorbed).
        harm["direction"] = ("harm" if harm["C1_only"] > harm["C2_only"]
                             else "help" if harm["C2_only"] > harm["C1_only"]
                             else "tie")
        harm["net_discordant"] = abs(harm["C1_only"] - harm["C2_only"])

        report["lineages"][lineage] = {
            "rates": tab,
            "instrument_health": instrument_health(brows, lineage),
            "recovery": {
                "value": (round(recovery, 3) if recovery is not None else None),
                "note": ("profile, not a test; denominator (C1-C0) must exceed 0"
                         if denom > 0 else
                         "UNDEFINED: C1 did not exceed C0 in the battery"),
            },
            "harm_C2_vs_C1": harm,
            "main_contrast_C3_vs_C0": {
                **paired(idx, lineage, "C3", "C0"),
                "note": "replication-grade: p reported, no confirmatory framing",
            },
        }

    if a.screen:
        srows = load(a.screen)
        lit = defaultdict(lambda: defaultdict(int))
        for r in srows:
            if r.get("void") or r.get("status") == "UNSAMPLEABLE":
                continue
            lit[r["lineage"]][r["cond"]] += int(bool(r.get("is_correct")))
            lit[r["lineage"]][r["cond"] + "_n"] += 1
        report["screen_literacy_descriptive"] = {k: dict(v) for k, v in lit.items()}

    if a.anchor:
        arows = [r for r in load(a.anchor) if not r.get("void")]
        anc = defaultdict(lambda: [0, 0])
        for r in arows:
            anc[r["lineage"]][0] += int(bool(r.get("is_correct")))
            anc[r["lineage"]][1] += 1
        report["default_anchor_descriptive"] = {
            k: {"correct": v[0], "n": v[1]} for k, v in anc.items()}

    text = json.dumps(report, ensure_ascii=False, indent=1)
    print(text)
    if a.out:
        Path(a.out).write_text(text)


if __name__ == "__main__":
    main()
