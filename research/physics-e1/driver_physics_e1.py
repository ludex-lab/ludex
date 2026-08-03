"""Physics E1 driver — four conditions on one pool, PhysGym import path.

PRE-REG: research/physics-checkup/PREREG_physics_e1.md (Ray-FINAL, f85c9597)
RATIFIED: RATIFIED_physics_e1.md (e26622bc) = SPEC v2 (2e74a5cd) + round-3 (8ae3aff4)
CORRECTION 01: e28c975a — generate_report() does NOT accumulate observations.

Scope (pinned): this measures `memory.recall`'s PROFILE — cross-boundary
carriage fidelity and duplicate-injection harm. Not "does memory help
induction", not the seed's physika faculty. Measured-profile walk, no
confirmatory NHST.

    C0  no observations,      recall bypassed   -> floor (manipulation check)
    C1  honest carriage,      recall bypassed   -> ceiling baseline / harm pair
    C2  honest carriage,      recall ON         -> harm cell (highest information)
    C3  session boundary,     recall ON         -> recovery numerator / main contrast

Committing this file BEFORE firing is what pins the researcher degrees of
freedom below. Each one could move every condition's success rate, so each is
stated rather than left to run time — the `scribe_importance` cliff (0.6 ->
0/5 retrieval, 0.8 -> 5/5) is the reason that discipline exists here.

  1. SAMPLE DESIGN. Five points per env, log-uniform over [0.2, 3.0] per
     variable, drawn from a per-env seeded stream. Log-uniform because these
     laws are mostly monomials and spreading across scales makes exponents
     identifiable from few points. Points are validated OFFLINE against
     `PhyEnv.execute` (some envs raise, e.g. 495 ZeroDivisionError) so that
     validation never spends interface quota; the wall still sees exactly the
     five accepted points. Up to 40 candidates, else the env is UNSAMPLEABLE
     and drops out with a record.
  2. THE ARM IS ENGINE-LEVEL. `bypass_memory` at the decision turn, with the
     organ enabled in every condition — the surface-grade toggle from walks
     1-3, not organ-disabling. So the store-write side is symmetric and the
     only difference between C1 and C2 is the recall injection itself. This
     extends PREREG assert 5 (which floors scribe receipts at C2/C3) to C1.
  3. PREMISE FROM THE TRUE INTERFACE (correction 01). A fresh interface would
     report "Samples Used: 0 / 5" to a C3 creature that really did spend five
     samples in session 1, and a false premise suppresses the recall behaviour
     the cell exists to measure. The counter is fact, so it is honest premise.
     No hint about memory is added: the counter is the only thing that says
     observations exist, and where they live is the creature's problem.
  4. UNPARSEABLE REPLY = INCORRECT, not VOID. A brain that cannot state a law
     in the requested form failed the task; VOID is reserved for OUR errors
     (config drift, carriage faults). Config is asserted, behaviour is measured.

Run (screening):  .venv/bin/python research/physics-checkup/driver_physics_e1.py --mode screen
Run (battery):    ... --mode battery --envs <pool.json>
"""
import argparse
import json
import math
import os
import random
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
import physgym                                            # noqa: E402
from ludex.core.integrity import ephemeral_creature       # noqa: E402
from ludex.core.canary import canary_gate                 # noqa: E402

# --- frozen parameters (PREREG §1) -------------------------------------------
MASK = "anonymous_no_context_no_description"
DEFAULT_MASK = "default"                     # descriptive anchor only
SAMPLE_QUOTA = 5                             # = recall_n, so top-k takes all
TEST_QUOTA = 1                               # single-shot, structurally enforced
SCRIBE_IMPORTANCE = 0.8                      # below 0.7 retrieval collapses to 0/5
SEED = 20260801
BURNED = {285}                               # spent by both labs' smokes
N_SCREEN_ENVS = 16
MAX_LAW_VARS = 3             # AMENDMENT 03: population = envs whose TRUE LAW
                             # uses <= 3 variables (30 in catalogue, 29 unburned)
OBS_RANGE = (0.2, 3.0)
MAX_CANDIDATES = 40
MAX_BRAIN_ATTEMPTS = 3       # empty/error replies are ours to fix, not answers
_ERROR_FALLBACK = re.compile(r"\[Error:.*\]", re.I | re.S)

LINEAGES = {
    "haiku": {"base": "creatures/Kiln", "model": "claude-haiku-4-5-20251001",
              "provider": "claude_cli", "timeout_ms": 240000},
    # AMENDMENT 02: registered replication lineage, grok -> agy. agy's
    # pre-registration exclusion was tool-hunting, which the no-tools line in
    # ASK closes; the --model routing fix opened 3.6-flash. Selftest: all four
    # conditions clean on first attempt, ~20s a cell.
    "agy":   {"base": "creatures/AgyProbe", "model": "gemini-3.6-flash",
              "provider": "agy_cli", "effort": "medium", "timeout_ms": 240000},
}

# Excluded with reasons, kept in code so the column's history is readable.
EXCLUDED_LINEAGES = {
    "grok": {"base": "creatures/GrokProbe", "model": None, "provider": "grok_cli",
             "timeout_ms": 240000,
             "reason": "AMENDMENT 02: works on the wall (C1 correct in 46s) but "
                       "stalls whenever the [Recalled Memory] block is in the "
                       "system prompt (C2 3/3 timeouts, C3 1/3). The failure "
                       "lands only on the arm conditions, so its drops would "
                       "concentrate in exactly what is being measured."},
}


# Feasibility probes only — never part of the registered column. Run by name
# to answer "could this lineage carry a future walk?", on burned envs.
# Both are codex (OpenAI) creatures, a third lineage family the column lacks.
# Their configs are used as born (auth subscription, effort high): a probe that
# changes the brain measures a brain that does not exist.
PROBE_LINEAGES = {
    "fable": {"base": "creatures/Saga", "model": "claude-fable-5",
              "provider": "claude_cli", "timeout_ms": 240000},
    "sol":   {"base": "creatures/Cinder", "model": "gpt-5.6-sol",
              "provider": "codex_cli", "auth": "subscription",
              "effort": "high", "timeout_ms": 240000},
    "luna":  {"base": "creatures/Wisp", "model": "gpt-5.6-luna",
              "provider": "codex_cli", "auth": "subscription",
              "effort": "high", "timeout_ms": 240000},
    "terra": {"base": "creatures/Bramble", "model": "gpt-5.6-terra",
              "provider": "codex_cli", "auth": "subscription",
              "effort": "high", "timeout_ms": 240000},
}


def lineage_spec(name):
    return LINEAGES.get(name) or EXCLUDED_LINEAGES.get(name) or PROBE_LINEAGES[name]


CONDITIONS = {
    #        carries observations?  recall on?   session boundary?
    "C0": {"carry": False, "recall": False, "boundary": False},
    "C1": {"carry": True,  "recall": False, "boundary": False},
    "C2": {"carry": True,  "recall": True,  "boundary": False},
    "C3": {"carry": False, "recall": True,  "boundary": True},
}

ASK = (
    # Uniform across lineages, and load-bearing for exactly one of them.
    # Measured (env 72, grok-4.5, direct CLI): without this, grok emits
    # "I'll fit a simple algebraic relation..." and then never finishes —
    # it wants to execute code against the data, and an empty sandbox leaves
    # it stuck, the same failure both labs recorded for agy. With it, grok
    # reasons in prose, recovers the constant from the five ratios, and exits.
    # Haiku has no tools on this path either way, so the line is a no-op there;
    # it is present for both because the prompt must be identical.
    "Answer directly from the numbers in this message. Do NOT use tools, do NOT "
    "read or write files, do NOT run code, do NOT search. Reason in plain prose "
    "in your reply.\n\n"
    "{report}\n\n"
    "{obs_block}"
    "Task: state the law relating the inputs to the output of this apparatus.\n"
    "End your reply with the final answer on its own last line, in exactly "
    "this form:\n"
    "LAW: <expression>\n"
    "The expression uses {params}, the operators + - * / **, and the functions "
    "sin, cos, tan, exp, log, sqrt, pi. Write it as plain text with NO markdown "
    "and NO bold. Example line:\n"
    "LAW: 2*var_1*var_2**2"
)


# --- environment draw (PREREG §2) --------------------------------------------
def catalogue():
    path = Path(physgym.__file__).parent / "samples" / "full_samples.json"
    return json.loads(path.read_text())


_VAR_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def law_vars(env_id):
    """(n_law, n_exposed) — variables the TRUE LAW uses, and variables the
    report shows (AMENDMENT 03 A03-1).

    n_law is what identifiability turns on: five observations cannot pin a law
    with more free variables than they have points for. n_exposed is bigger
    whenever PhysGym mixes in dummy parameters, and the GAP between them is a
    free covariate on the bench's built-in distractor axis — so both are
    recorded rather than collapsed.

    Counting the equation, not the function signature: `parameter_names` comes
    from the generated python and includes dummies, which is what made my first
    screening population wrong.
    """
    env = physgym.PhyEnv(env_id)
    toks = set(_VAR_RE.findall(env.equation or ""))
    n_law = sum(1 for v in env.parameter_names if v in toks)
    return n_law, len(env.parameter_names)


def population():
    """Draw population: unburned envs whose true law uses <= MAX_LAW_VARS.

    The arithmetic (n+1 unknowns vs 5 observations) is exact for monomials and
    only a heuristic across a catalogue that also holds sin/sqrt/composite
    forms. What settles the cut is the measured split, not the arithmetic:
    every solve from both lineages sat at <= 3 variables and neither scored
    once above it (Ray, A03-1).
    """
    return sorted(e["id"] for e in catalogue()
                  if e["id"] not in BURNED and law_vars(e["id"])[0] <= MAX_LAW_VARS)


def draw_envs(n=N_SCREEN_ENVS, extend_round=0):
    """Seed-fixed draw, shared by both lineages so cross-lineage reads line up.

    `extend_round` implements the pre-registered +8 extension as a suffix of the
    SAME permutation, so extending never reshuffles what was already screened.
    The filter runs BEFORE the shuffle, so restricting the population does not
    disturb the burn ledger or reorder what was already spent.
    """
    ids = population()
    rng = random.Random(SEED)
    rng.shuffle(ids)
    return ids[: n + 8 * extend_round]


# --- fixed sample design (degree of freedom 1) -------------------------------
def fixed_design(env_id, n=SAMPLE_QUOTA):
    """Return (points_by_true_name, n_candidates) or (None, n) if unsampleable."""
    env = physgym.PhyEnv(env_id)
    names = env.parameter_names
    rng = random.Random(SEED * 100_000 + env_id)
    lo, hi = math.log(OBS_RANGE[0]), math.log(OBS_RANGE[1])
    points, tried = [], 0
    while len(points) < n and tried < MAX_CANDIDATES:
        tried += 1
        p = {nm: round(math.exp(rng.uniform(lo, hi)), 4) for nm in names}
        try:
            out = env.execute(**p)
        except Exception:
            continue
        if isinstance(out, (int, float)) and math.isfinite(out):
            points.append(p)
    return (points if len(points) == n else None), tried


def observation_lines(obs):
    """One line per observation, the scribe unit. Kept well under recall_chars."""
    out = []
    for o in obs:
        inp = ", ".join(f"{k}={v}" for k, v in o["input"].items())
        out.append(f"Experiment {o['sample_id']}: {inp} -> output {o['output']:.6g}")
    return out


def observation_values(obs):
    """Every number a C0/C3 prompt must not contain (reverse-carriage check)."""
    vals = []
    for o in obs:
        vals.extend(f"{v:g}" for v in o["input"].values())
        vals.append(f"{o['output']:.6g}")
    return vals


# --- hypothesis handling (degree of freedom 4) -------------------------------
_FENCE = re.compile(r"```[a-z]*\s*(.*?)```", re.S)
_WRAP = re.compile(r"^\s*(?:\*\*|\*|`)+\s*(.*?)\s*(?:\*\*|\*|`)+\s*$")


def _unwrap(line):
    """Strip markdown emphasis WRAPPING a line, never operators inside it.

    Selftest evidence (env 285, C3): haiku's final answer arrived as
    `**(var_1 + var_2) * var_1 * var_3**`, which fails to compile because the
    bold markers read as leading/trailing power operators. Only paired wrapping
    at both ends is removed; a `**` in the middle is arithmetic and survives.
    """
    m = _WRAP.match(line)
    return m.group(1).strip() if m else line


_LAW = re.compile(r"^\s*LAW\s*:\s*(.+?)\s*$", re.I)


def parse_expression(text, params):
    """Last line that states a law, whatever the surrounding prose or markup.

    The rule is deliberately one-directional: accept any syntactically valid
    law statement regardless of formatting, never repair an invalid one. A
    brain that cannot state a law still fails.

    The `LAW:` marker exists because of a selftest artifact, not for tidiness.
    Haiku twice ended a C3 reply with `**var_1**2**+var_2**2**+var_3**2` —
    markdown bold wrapped around terms that themselves use `**` for powers, so
    the text is genuinely ambiguous and no honest parser can recover it. A
    heuristic un-bolder would be inventing an interpretation, so the ambiguity
    is removed at the source instead. The unmarked scan below stays as fallback,
    and unparseable replies are still graded incorrect, never VOID.
    """
    t = text.strip()
    for line in reversed([ln.strip() for ln in t.splitlines() if ln.strip()]):
        m = _LAW.match(line)
        if not m:
            continue
        cand = _unwrap(m.group(1).strip().rstrip("."))
        try:
            compile(cand, "<hyp>", "eval")
        except SyntaxError:
            break                      # marker present but mangled -> fallback
        if any(p in cand for p in params):
            return cand
        break
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    for line in reversed([ln.strip() for ln in t.splitlines() if ln.strip()]):
        line = _unwrap(line)
        line = re.sub(r"^(output|y|f)\s*[:=]\s*", "", line, flags=re.I).strip()
        line = _unwrap(line.rstrip("."))
        if not any(p in line for p in params):
            continue
        try:
            compile(line, "<hyp>", "eval")
        except SyntaxError:
            continue
        return line
    return None


def as_function(expr, params):
    """Bare def, no imports — PhysGym's `preprocess_func_str` rewrites any of
    sin/cos/tan/exp/log/sqrt/pi to `np.<name>` by naive string replace, which
    would corrupt an import line into `from math import np.sin, ...` (the
    ValueError the selftest raised). The sandbox namespace already provides np.
    """
    body = ", ".join(params)
    return f"def f({body}):\n    return {expr}\n"


# --- one run ------------------------------------------------------------------
_CAP_CACHE = {}


def run_cell(env_id, cond, lineage, mask=MASK):
    spec = CONDITIONS[cond]
    lin = lineage_spec(lineage)
    rec = {"env": env_id, "cond": cond, "lineage": lineage, "mask": mask,
           "n_law": law_vars(env_id)[0], "n_exposed": law_vars(env_id)[1],
           "void": [], "t0": time.time()}

    design, tried = fixed_design(env_id)
    rec["design_candidates"] = tried
    if design is None:
        rec["status"] = "UNSAMPLEABLE"
        return rec

    iface = physgym.ResearchInterface(env=env_id, sample_quota=SAMPLE_QUOTA,
                                      test_quota=TEST_QUOTA, mode=mask)
    params = iface.input_params
    true_names = physgym.PhyEnv(env_id).parameter_names
    # positional remap: interface names are var_1..var_n in parameter order
    samples = [{params[i]: pt[true_names[i]] for i in range(len(params))}
               for pt in design]

    obs = []
    if cond != "C0":
        obs = iface.run_experiment(samples)
    rec["n_obs"] = len(obs)

    # premise from the TRUE interface state (correction 01)
    report = iface.generate_report()
    lines = observation_lines(obs)
    obs_block = ("Observations:\n" + "\n".join(lines) + "\n\n") if spec["carry"] else ""
    prompt = ASK.format(report=report, obs_block=obs_block,
                        params=", ".join(params))

    with ephemeral_creature(lin["base"]) as cfg:
        # The D-072 birth probe re-fires whenever provider:model differs from
        # the creature's cached key — which it always does here, because the
        # driver pins a model. That is one extra brain call per BUILD, and a
        # run builds at least once, so the battery was quietly spending double
        # what "one call per run" claimed. Probe once per lineage and carry the
        # result; the probe is idempotent by design, so reusing it is what the
        # cache was for.
        cached = _CAP_CACHE.get(lineage)
        if cached:
            (cfg.brain_capabilities, cfg.capability_probed_brain,
             cfg.capability_probed_at) = cached
        if lin.get("timeout_ms"):
            cfg.brain["timeout_ms"] = lin["timeout_ms"]
        if lin.get("effort"):
            cfg.brain["effort"] = lin["effort"]
        if lin.get("model"):
            cfg.brain["model"] = lin["model"]
        org = cfg.build()
        if not cached:
            _CAP_CACHE[lineage] = (cfg.brain_capabilities,
                                   cfg.capability_probed_brain,
                                   cfg.capability_probed_at)
        if lin["model"]:
            org.config.set("model", lin["model"])

        # session 1 — scribe, symmetric wherever observations exist (dof 2).
        # No brain call: the driver holds the design, so a run costs one call.
        if lines:
            mem = org.get_block("memory")
            for ln in lines:
                mem.handle_remember(ln, memory_type="episodic",
                                    source="physics_e1",
                                    importance=SCRIBE_IMPORTANCE)
            rec["scribed"] = len(mem._memories)
        del org

        # The decision turn is the first turn of a freshly built organism in
        # EVERY condition. Because the driver runs the experiments, session 1
        # contains no brain turns at all, so this is not a change of condition
        # — it just makes the awakening counter and turn count uniform, and
        # makes a retry a clean re-run rather than a second turn in a session.
        reply, sys_prompt = "", ""
        for attempt in range(1, MAX_BRAIN_ATTEMPTS + 1):
            org = cfg.build()
            if lin["model"]:
                org.config.set("model", lin["model"])
            eng = org.get_block("engine")
            rec["boundary_turn_count"] = eng._turn_count
            r = eng.handle_submit(prompt, bypass_memory=not spec["recall"])
            reply = (r.response or "").strip()
            sys_prompt = getattr(eng, "_last_sys_prompt", "") or ""
            rec["brain_attempts"] = attempt
            if reply and not _ERROR_FALLBACK.search(reply):
                break
            # A failed call is OUR fault, not a wrong answer. Grading an empty
            # reply as incorrect would silently depress whichever condition
            # happened to catch a transient failure, indistinguishably from a
            # real failure. Selftest 3 produced exactly one (C1, empty).
            rec.setdefault("brain_failures", []).append(
                {"attempt": attempt, "reply_len": len(reply),
                 "raw": str(getattr(r, "raw", ""))[:300]})
            del org

    rec["reply"] = reply
    rec["sys_prompt_chars"] = len(sys_prompt)
    rec["has_recall_block"] = "[Recalled Memory]" in sys_prompt

    # --- asserts (PREREG §6), VOID semantics (§5) ---
    # The two channels are counted SEPARATELY. Reverse-carriage asks whether the
    # DRIVER leaked observations into the user prompt; the recall block is the
    # very channel C3 exists to measure, so grepping the assembled prompt as one
    # blob (as the first selftest did) flags a working C3 as a leak.
    full = sys_prompt + "\n" + prompt
    vals = observation_values(obs)
    carried = sum(1 for v in vals if v in prompt)
    recalled = sum(1 for v in vals if v in sys_prompt)
    rec["obs_values_carried"] = carried          # driver -> user prompt
    rec["obs_values_recalled"] = recalled        # organ -> system prompt
    if spec["carry"]:
        if carried < len(vals):                                    # f-carriage-a
            rec["void"].append("VOID-carriage:obs-missing")
    else:
        if carried:                                                # reverse-carriage
            rec["void"].append("VOID-carriage:obs-leaked")
    if not spec["recall"] and recalled:          # organ off must not inject
        rec["void"].append("VOID-carriage:obs-recalled-while-off")
    if f"Problem ID: {env_id}" not in full:                        # f-carriage-b
        rec["void"].append("VOID-carriage:premise-missing")
    if not spec["recall"] and rec["has_recall_block"]:             # recall-absence
        rec["void"].append("VOID-carriage:recall-block-present")
    if spec["recall"] and lines and not rec["has_recall_block"]:
        rec["void"].append("VOID-carriage:recall-block-missing")
    if spec["boundary"] and rec.get("boundary_turn_count") != 0:   # boundary
        rec["void"].append("VOID-config:boundary-not-clean")
    if lines and rec.get("scribed", 0) < len(lines):               # scribe receipt
        rec["void"].append("VOID-config:scribe-short")

    # --- grade ---
    if not reply or _ERROR_FALLBACK.search(reply):
        rec["void"].append("VOID-brain:no-reply")
        rec["status"] = "BRAIN_FAILED"
        rec["is_correct"] = None
        rec["elapsed_s"] = round(time.time() - rec["t0"], 1)
        rec.pop("t0")
        return rec

    expr = parse_expression(reply, params)
    rec["expr"] = expr
    if expr is None:
        rec["status"] = "UNPARSEABLE"          # = incorrect, not VOID (dof 4)
        rec["is_correct"] = False
    else:
        try:
            ev = iface.test_hypothesis(as_function(expr, params), expr)
            fit = ev.get("fit_metrics", {}) or {}
            rec.update(status="GRADED",
                       is_correct=bool(ev.get("is_correct", False)),
                       overall_score=ev.get("overall_score"),
                       fits_data=ev.get("fits_data"),
                       r2=fit.get("R2", fit.get("r2")),
                       mse=fit.get("MSE", fit.get("mse")))
        except Exception as ex:
            rec.update(status=f"GRADE_ERROR:{type(ex).__name__}", is_correct=False,
                       grade_error=str(ex)[:200])
    rec["elapsed_s"] = round(time.time() - rec["t0"], 1)
    rec.pop("t0")
    return rec


# --- battery ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("screen", "battery", "anchor", "selftest"),
                    required=True)
    ap.add_argument("--lineages", default="haiku,grok")
    ap.add_argument("--envs", default="", help="battery/anchor: pool json path")
    ap.add_argument("--extend", type=int, default=0, help="screen: +8 env rounds")
    # Capability sweep: C1 alone answers "can this lineage induce at this dose"
    # — honest carriage, organ off, pure induction. The other three conditions
    # are about the organ and cost brain calls that this question does not need.
    ap.add_argument("--conds", default="", help="comma list, overrides mode default")
    # A03-2 extension rounds continue the SAME fixed draw order, so the new
    # round is a suffix; --only names it explicitly rather than re-screening
    # envs whose results are already registered for assignment.
    ap.add_argument("--only", default="", help="screen: explicit env ids")
    ap.add_argument("--limit", type=int, default=0, help="first N envs only")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    if a.mode == "selftest":
        # PREREG §9 (1): the behavioural asserts green before anything fires.
        # Runs on env 285, already burned by both labs' smokes, so the pool
        # stays untouched. Four brain calls.
        env_id, lineage = 285, a.lineages.split(",")[0]
        recs = [run_cell(env_id, c, lineage) for c in ("C0", "C1", "C2", "C3")]
        (HERE / f"e1_selftest_{lineage}.json").write_text(
            json.dumps(recs, ensure_ascii=False, indent=1))
        ok = True
        for rec in recs:
            expect_block = CONDITIONS[rec["cond"]]["recall"] and rec["cond"] != "C0"
            checks = {
                "no VOID": not rec["void"],
                "driver carriage matches condition":
                    (rec["obs_values_carried"] > 0) == CONDITIONS[rec["cond"]]["carry"],
                "recall block matches arm": rec["has_recall_block"] == expect_block,
                "graded": rec["status"] in ("GRADED", "UNPARSEABLE"),
            }
            if rec["cond"] == "C3":
                checks["boundary clean"] = rec.get("boundary_turn_count") == 0
                # the cell's whole premise: observations arrive by recall alone
                checks["observations arrived by recall"] = rec["obs_values_recalled"] > 0
            for k, v in checks.items():
                print(f"{'PASS' if v else 'FAIL'}  {rec['cond']}: {k}")
                ok &= v
            print(f"      {rec['cond']} status={rec['status']} "
                  f"correct={rec.get('is_correct')} carried="
                  f"{rec['obs_values_carried']} recalled="
                  f"{rec['obs_values_recalled']} void={rec['void']}")
        print("\n" + ("ASSERTS GREEN" if ok else "ASSERTS RED"))
        sys.exit(0 if ok else 1)

    if a.mode == "screen":
        envs = draw_envs(extend_round=a.extend)
        conds = ["C0", "C1"]
        mask = MASK
    else:
        pool = json.loads(Path(a.envs).read_text())
        envs = None                                   # per-lineage below
        conds = ["C0", "C1", "C2", "C3"] if a.mode == "battery" else ["C1"]
        mask = MASK if a.mode == "battery" else DEFAULT_MASK

    # PREREG §9 (1) / AMENDMENT 02 A02-4: fail-closed canary before any
    # measurement run, per lineage, with the CLI version stamped at gate time.
    # Re-stamp at verdict and quarantine on mismatch (mid-battery drift rule).
    gates = {}
    for lineage in a.lineages.split(","):
        spec = lineage_spec(lineage)
        v = canary_gate(spec.get("provider", ""), spec.get("model") or "",
                        auth=spec.get("auth", ""), effort=spec.get("effort", ""))
        gates[lineage] = v
        print(f"canary {lineage}: {v.get('verdict')} cli={v.get('cli_version')}",
              flush=True)
    (HERE / f"e1_gate_{a.mode}.json").write_text(
        json.dumps(gates, ensure_ascii=False, indent=1))

    out = Path(a.out) if a.out else HERE / f"e1_{a.mode}.jsonl"
    if a.conds:
        conds = a.conds.split(",")
    if a.only:
        envs = [int(x) for x in a.only.split(",")]
    if a.limit and envs is not None:
        envs = envs[:a.limit]

    n = 0
    with out.open("a") as fh:
        for lineage in a.lineages.split(","):
            targets = envs if envs is not None else pool[lineage]
            for env_id in targets:
                for cond in conds:
                    rec = run_cell(env_id, cond, lineage, mask=mask)
                    n += 1
                    # AMENDMENT 01 A3: a VOID-brain run refires ONCE fresh; a
                    # second one drops that env's pair with a log. Dropping is
                    # by absence in the analysis, so it has to be logged here or
                    # it becomes a silent cap.
                    if "VOID-brain:no-reply" in rec["void"]:
                        rec["refired"] = True
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        print(f"[{n}] {lineage} env{env_id} {cond} VOID-brain "
                              f"-> refiring once", flush=True)
                        rec = run_cell(env_id, cond, lineage, mask=mask)
                        rec["is_refire"] = True
                        n += 1
                        if "VOID-brain:no-reply" in rec["void"]:
                            rec["pair_dropped"] = True
                            print(f"      DROPPED PAIR {lineage} env{env_id} "
                                  f"{cond}: VOID-brain twice", flush=True)
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    print(f"[{n}] {lineage} env{env_id} {cond} "
                          f"{rec.get('status')} correct={rec.get('is_correct')} "
                          f"void={rec['void']}", flush=True)
    print(f"DONE {n} runs -> {out}", flush=True)


if __name__ == "__main__":
    main()
