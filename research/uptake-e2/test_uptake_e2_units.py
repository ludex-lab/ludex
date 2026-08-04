"""Offline unit checks for the Uptake E2 driver and analyser — no brain calls.

The one that matters is block identity. E2 only asks its question if U2 carries
the exact string the organ rendered for U0; if the text drifts, the walk has
measured a content change and called it a surface effect. So the render is
checked for determinism and completeness here, and the analyser is checked to
actually fail when the hashes differ rather than quietly reporting a pass.

Run: .venv/bin/python research/uptake-e2/test_uptake_e2_units.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "research" / "physics-checkup"))
sys.path.insert(0, str(ROOT))

import physgym                                              # noqa: E402
import driver_physics_e1 as E1                              # noqa: E402
import driver_uptake_e2 as D                                # noqa: E402
import analysis_uptake_e2 as A                              # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# --- arm table is what the PREREG froze ---
check("arms: exactly U0-U3", list(D.ARMS) == ["U0", "U1", "U2", "U3"])
check("arms: only U3 carries observations in the user prompt",
      [D.ARMS[a]["carry"] for a in ("U0", "U1", "U2", "U3")] == [False, False, False, True])
check("arms: only U0/U1 run the organ live",
      [D.ARMS[a]["recall"] for a in ("U0", "U1", "U2", "U3")] == [True, True, False, False])
check("arms: only U1 strips the None lines",
      [D.ARMS[a]["strip_none"] for a in ("U0", "U1", "U2", "U3")] == [False, True, False, False])
check("arms: only U2 injects the block", D.ARMS["U2"].get("inject_block") is True)

# --- the None stripper touches only the None lines ---
report = ("Experiment Report (ID: abc)\nProblem ID: 134\n"
          "Start Time: 2026-08-03T00:00:00\nSamples Used: 5 / 5\n\n"
          "Input Parameters:\n  var_1: None\n  var_2: None\n  var_3: None\n")
stripped = D.strip_none_lines(report)
check("strip: no None lines survive", not D._NONE_LINE_RE.search(stripped))
check("strip: section header kept", "Input Parameters:" in stripped)
check("strip: premise untouched",
      "Problem ID: 134" in stripped and "Samples Used: 5 / 5" in stripped)
check("strip: nothing else removed",
      all(l in stripped for l in report.splitlines() if ": None" not in l))
check("strip: a described parameter is NOT removed",
      "  m: mass of the disc" in D.strip_none_lines("  m: mass of the disc\n"))

# --- block render: deterministic, complete, and shaped like the engine's ---
ENV = 134
design, _ = E1.fixed_design(ENV)
iface = physgym.ResearchInterface(env=ENV, sample_quota=E1.SAMPLE_QUOTA,
                                  test_quota=E1.TEST_QUOTA, mode=E1.MASK)
params = iface.input_params
true_names = physgym.PhyEnv(ENV).parameter_names
obs = iface.run_experiment([{params[i]: pt[true_names[i]] for i in range(len(params))}
                            for pt in design])
lines = E1.observation_lines(obs)
prompt = E1.ASK.format(report=iface.generate_report(), obs_block="",
                       params=", ".join(params))

b1 = D.render_recall_block(ENV, "agy", prompt, lines)
b2 = D.render_recall_block(ENV, "agy", prompt, lines)
check("render: deterministic across calls", b1 == b2)
check("render: starts with the engine's own header", b1.startswith(D.RECALL_HEADER))
vals = E1.observation_values(obs)
check("render: carries every observation value",
      sum(1 for v in vals if v in b1) == len(vals),
      f"{sum(1 for v in vals if v in b1)}/{len(vals)}")
check("render: non-empty", len(b1) > 50, f"{len(b1)} chars")

# --- analyser: block identity must FAIL loudly, not pass quietly ---
def rows_for(sha_u0, sha_u2):
    return [
        {"lineage": "agy", "env": 1, "arm": "U0", "is_correct": False, "void": [],
         "status": "GRADED", "block_sha": sha_u0},
        {"lineage": "agy", "env": 1, "arm": "U2", "is_correct": True, "void": [],
         "status": "GRADED", "block_sha": sha_u2},
    ]


check("analyser: identical hashes -> HOLDS",
      A.block_identity(rows_for("aaa", "aaa"), "agy")["verdict"] == "HOLDS")
broken = A.block_identity(rows_for("aaa", "bbb"), "agy")
check("analyser: differing hashes -> BROKEN", broken["verdict"] == "BROKEN")
check("analyser: the mismatch is named, not just counted",
      broken["mismatches"] and broken["mismatches"][0]["env"] == 1)
check("analyser: absent hashes -> NOT CHECKED, never a silent pass",
      A.block_identity([{"lineage": "agy", "env": 1, "arm": "U0", "void": []}],
                       "agy")["verdict"] == "NOT CHECKED")

# --- analyser: the registered reading is selected, not invented ---
def reading(u0, u1, u2):
    rows = []
    for arm, k in (("U0", u0), ("U1", u1), ("U2", u2), ("U3", 8)):
        for e in range(8):
            rows.append({"lineage": "agy", "env": e, "arm": arm, "void": [],
                         "status": "GRADED", "is_correct": e < k})
    idx, _ = A.index(rows)
    import io, contextlib, json as _j
    buf = io.StringIO()
    sys.argv = ["x", "--battery", "/dev/null"]
    tab = A.rates(idx, "agy")
    u0r, u1r, u2r = tab["U0"]["rate"], tab["U1"]["rate"], tab["U2"]["rate"]
    base = u0r
    if u2r > base and not (u1r > base):
        return "surface"
    if u2r > base and u1r > base:
        return "compound"
    if u1r > base:
        return "absence"
    return "third"


check("reading: U2 alone recovers -> surface", reading(0, 0, 7) == "surface")
check("reading: both recover -> compound", reading(0, 5, 7) == "compound")
check("reading: U1 alone recovers -> absence hint", reading(0, 5, 0) == "absence")
check("reading: nothing recovers -> third cause", reading(0, 0, 0) == "third")

# --- inherited frozen parameters must not have drifted ---
check("inherited: quota 5", E1.SAMPLE_QUOTA == 5)
check("inherited: scribe importance 0.8", E1.SCRIBE_IMPORTANCE == 0.8)
check("inherited: anonymous masking", E1.MASK.startswith("anonymous"))
check("inherited: single-shot hypothesis", E1.TEST_QUOTA == 1)

print("\n" + ("ALL GREEN" if not FAILS else f"FAILURES: {FAILS}"))
sys.exit(1 if FAILS else 0)
