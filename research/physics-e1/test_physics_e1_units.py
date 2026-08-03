"""Offline unit checks for the Physics E1 driver — no brain calls.

These cover the parts of PREREG §6 that can be settled without spending a run:
determinism of the frozen draw and design, the observation-value list the
reverse-carriage assert greps for, and hypothesis parsing. The behavioural
asserts (carriage, recall-block, boundary, scribe) need a real turn and are
covered by `driver_physics_e1.py --selftest` on the already-burned env 285.

Run: .venv/bin/python research/physics-checkup/test_physics_e1_units.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import driver_physics_e1 as D                             # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# --- env draw (PREREG §2) ---
envs = D.draw_envs()
check("draw: 16 envs", len(envs) == 16, str(envs))
check("draw: deterministic", envs == D.draw_envs())
check("draw: burned excluded", not (set(envs) & D.BURNED))
check("draw: catalogue is 97", len(D.catalogue()) == 97)
# AMENDMENT 03: population is restricted by TRUE-LAW variable count, not by the
# function signature (which counts dummies and is what made the first draw wrong).
check("population: 29 envs at n_law<=3 (30 minus burned 285)",
      len(D.population()) == 29, str(len(D.population())))
check("population: every member is within the identifiable band",
      all(D.law_vars(e)[0] <= D.MAX_LAW_VARS for e in D.population()))
check("population: burned env excluded", 285 not in D.population())
check("law_vars counts the equation, not the signature (env 89 has dummies)",
      D.law_vars(89)[1] >= D.law_vars(89)[0])
check("draw: exhausts the population, never over-draws",
      len(D.draw_envs(extend_round=2)) == 29)
ext = D.draw_envs(extend_round=1)
check("draw: +8 extension is a suffix, never a reshuffle",
      ext[:16] == envs and len(ext) == 24)

# --- fixed design (degree of freedom 1) ---
d1, t1 = D.fixed_design(285)
d2, _ = D.fixed_design(285)
check("design: 5 points", d1 is not None and len(d1) == 5)
check("design: deterministic", d1 == d2)
check("design: in range",
      all(D.OBS_RANGE[0] <= v <= D.OBS_RANGE[1] for p in d1 for v in p.values()))
check("design: differs across envs", D.fixed_design(envs[0])[0] != d1)
d495, t495 = D.fixed_design(495)
check("design: hostile env handled (495 raises on naive input)",
      d495 is None or len(d495) == 5, f"candidates={t495}")

# --- observation formatting / the reverse-carriage grep list ---
obs = [{"sample_id": 1, "input": {"var_1": 0.31, "var_2": 1.7}, "output": 12.7534}]
lines = D.observation_lines(obs)
check("obs lines: one per observation", len(lines) == 1)
check("obs lines: under recall_chars truncation", all(len(x) <= 400 for x in lines))
check("obs lines: carry the values", "0.31" in lines[0] and "12.7534" in lines[0])
vals = D.observation_values(obs)
check("obs values: inputs and output all listed",
      set(vals) >= {"0.31", "1.7"} and any(v.startswith("12.75") for v in vals), str(vals))
check("obs values: every value appears in its own line",
      all(v in lines[0] for v in vals), f"{vals} vs {lines[0]}")

# --- hypothesis parsing (degree of freedom 4) ---
P = ["var_1", "var_2", "var_3"]
cases = [
    ("5*var_1*var_2*var_3**2", "5*var_1*var_2*var_3**2"),
    ("output = 5*var_1*var_2*var_3**2", "5*var_1*var_2*var_3**2"),
    ("```python\n2*var_1 + var_2\n```", "2*var_1 + var_2"),
    ("Reasoning here.\nThe law is:\nvar_1*var_2", "var_1*var_2"),
    ("sqrt(var_1)*log(var_2)", "sqrt(var_1)*log(var_2)"),
    ("I cannot determine a law without data.", None),
    ("var_1 ** ", None),                     # syntax error -> not accepted
]
for raw, want in cases:
    got = D.parse_expression(raw, P)
    check(f"parse: {raw[:34]!r}", got == want, f"got={got!r} want={want!r}")

# markdown-wrapped answers are formatting, not invalid laws (selftest evidence)
for raw, want in [
    ("**(var_1 + var_2) * var_1 * var_3**", "(var_1 + var_2) * var_1 * var_3"),
    ("`var_1*var_2`", "var_1*var_2"),
    ("**output = var_1**2**", "var_1**2"),
    ("The answer:\n**var_1 * var_3**", "var_1 * var_3"),
]:
    got = D.parse_expression(raw, P)
    check(f"parse markdown: {raw[:34]!r}", got == want, f"got={got!r} want={want!r}")
check("parse: interior ** survives unwrapping",
      D.parse_expression("var_1**2 + var_2", P) == "var_1**2 + var_2")

# the LAW: marker — added because bolded powers are unrecoverable, not for tidiness
for raw, want in [
    ("reasoning...\nLAW: 5*var_1*var_2*var_3**2", "5*var_1*var_2*var_3**2"),
    ("LAW: var_1 + var_2\ntrailing chatter", "var_1 + var_2"),
    ("law: var_1*var_3", "var_1*var_3"),
    # paired wrapping at BOTH ends is formatting -> unwrapped
    ("LAW: **var_1**2**", "var_1**2"),
    # the real selftest artifact: bold interleaved with powers, unclosed at the
    # end. Genuinely ambiguous, so it stays unparseable rather than invented.
    ("LAW: **var_1**2**+var_2**2**+var_3**2", None),
    ("**var_1**2**+var_2**2**+var_3**2", None),
    ("LAW: I don't know\nvar_1*var_2", "var_1*var_2"),   # falls back to the scan
]:
    got = D.parse_expression(raw, P)
    check(f"parse LAW: {raw[:34]!r}", got == want, f"got={got!r} want={want!r}")
check("ASK asks for the LAW: marker and forbids markdown",
      "LAW:" in D.ASK and "NO markdown" in D.ASK)

fn = D.as_function("5*var_1*var_2*var_3**2", P)
check("as_function: bare def, no import line (PhysGym rewrites math names)",
      fn.startswith("def f(") and "import" not in fn)
ns = {}
exec(fn, ns)
check("as_function: executes", abs(ns["f"](1, 2, 3) - 90) < 1e-9)

# --- the full grading path, no brain call ---
import physgym                                             # noqa: E402
iface = physgym.ResearchInterface(env=285, sample_quota=D.SAMPLE_QUOTA,
                                  test_quota=D.TEST_QUOTA, mode=D.MASK)
pts = D.fixed_design(285)[0]
true_names = physgym.PhyEnv(285).parameter_names
ip = iface.input_params
iface.run_experiment([{ip[i]: pt[true_names[i]] for i in range(len(ip))} for pt in pts])
# env 285 is 5*m*R*omega**2. The coefficient is load-bearing: symbolic
# equivalence is strict, so a creature must induce the constant from the data
# too, not just the functional form. (Dropping the 5 scores 0.289, not correct.)
truth = "5*var_1*var_2*var_3**2"
ev = iface.test_hypothesis(D.as_function(truth, ip), truth)
check("grading: known-correct law grades correct",
      bool(ev.get("is_correct")), f"is_correct={ev.get('is_correct')} "
      f"score={ev.get('overall_score')}")
wrong = "var_1*var_2*var_3**2"
ev2 = iface.test_hypothesis(D.as_function(wrong, ip), wrong)
check("grading: right form but wrong constant is NOT correct",
      not ev2.get("is_correct"), f"score={ev2.get('overall_score')}")
check("grading: metrics nested as expected",
      "fit_metrics" in ev, str(list(ev.keys()))[:120])

# --- frozen parameters are what the PREREG says ---
check("frozen: scribe_importance 0.8", D.SCRIBE_IMPORTANCE == 0.8)
check("frozen: sample_quota 5 (= recall_n)", D.SAMPLE_QUOTA == 5)
check("frozen: test_quota 1 (single-shot)", D.TEST_QUOTA == 1)
check("frozen: anonymous masking", D.MASK == "anonymous_no_context_no_description")
check("frozen: seed 20260801", D.SEED == 20260801)
check("frozen: 285 burned", 285 in D.BURNED)
check("frozen: lineage column is haiku + agy (AMENDMENT 02)",
      set(D.LINEAGES) == {"haiku", "agy"})
check("frozen: grok excluded with a recorded reason",
      "grok" in D.EXCLUDED_LINEAGES and D.EXCLUDED_LINEAGES["grok"].get("reason"))
check("frozen: C0/C1 bypass recall, C2/C3 use it",
      [D.CONDITIONS[c]["recall"] for c in ("C0", "C1", "C2", "C3")] ==
      [False, False, True, True])
check("frozen: only C3 crosses a session boundary",
      [D.CONDITIONS[c]["boundary"] for c in ("C0", "C1", "C2", "C3")] ==
      [False, False, False, True])
check("frozen: only C1/C2 carry observations",
      [D.CONDITIONS[c]["carry"] for c in ("C0", "C1", "C2", "C3")] ==
      [False, True, True, False])

# --- the pre-committed analysis' exact statistics ---
import analysis_physics_e1 as A                            # noqa: E402

# Reference values from scipy (beta quantiles / binomtest), checked once and
# frozen here so the stdlib implementations stay honest without the dependency.
for k, n, lo, hi in [(0, 1, 0.0, 0.9750), (1, 1, 0.0250, 1.0),
                     (0, 10, 0.0, 0.3085), (3, 10, 0.0667, 0.6525),
                     (10, 10, 0.6915, 1.0), (5, 20, 0.0866, 0.4910)]:
    got = A.clopper_pearson(k, n)
    check(f"clopper-pearson {k}/{n}",
          abs(got[0] - lo) < 5e-4 and abs(got[1] - hi) < 5e-4,
          f"got={got[0]:.4f},{got[1]:.4f} want={lo},{hi}")
for b, c, p in [(0, 0, 1.0), (5, 0, 0.0625), (4, 1, 0.375),
                (3, 2, 1.0), (8, 2, 0.10938)]:
    check(f"mcnemar exact ({b},{c})", abs(A.mcnemar_exact(b, c) - p) < 1e-4,
          f"got={A.mcnemar_exact(b, c)}")
check("mcnemar is symmetric (two-sided)",
      A.mcnemar_exact(8, 2) == A.mcnemar_exact(2, 8))

print("\n" + ("ALL GREEN" if not FAILS else f"FAILURES: {FAILS}"))
sys.exit(1 if FAILS else 0)
