"""Uptake E2 driver — four within-env arms separating surface from absence-hint.

PRE-REG: research/uptake-e2/PREREG_uptake_e2.md (Ray draft v1, 18319d38)
Basis:   VERDICT_physics_e1.md FINAL — agy recovered 0.00 with the recall block
         fully delivered, and the ledger left two candidate causes.

    U0  C3 replica            observations nowhere in user prompt, block in system
    U1  C3 minus None lines   same, with the report's absence-implying lines gone
    U2  payload re-delivered  the SAME block text in the user channel, organ off
    U3  C1 replica            observations in the user prompt, no block

Content and completeness are held fixed; only where the text sits changes. The
central assert is byte-identity: what U2 puts in the user channel is character
for character what the organ rendered into U0's system prompt, header included.
If that assert is weak, the walk cannot tell "the surface was not consulted"
from "the text was different," which is the whole question.

Everything shared with E1 is imported rather than re-implemented, so the two
walks cannot drift apart in sampling, parsing, or grading.

Run: .venv/bin/python research/uptake-e2/driver_uptake_e2.py --mode battery \
       --lineages agy,haiku --envs research/uptake-e2/e2_pool.json
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research" / "physics-checkup"))

import physgym                                              # noqa: E402
import driver_physics_e1 as E1                              # noqa: E402
from ludex.core.integrity import ephemeral_creature         # noqa: E402
from ludex.core.canary import canary_gate                   # noqa: E402

MAX_BRAIN_ATTEMPTS = E1.MAX_BRAIN_ATTEMPTS
_ERROR_FALLBACK = E1._ERROR_FALLBACK
RECALL_HEADER = "[Recalled Memory]"

ARMS = {
    #      observations in    recall     session    strip the report's
    #      the user prompt?   organ on?  boundary?  "var_N: None" lines?
    "U0": {"carry": False, "recall": True,  "boundary": True,  "strip_none": False},
    "U1": {"carry": False, "recall": True,  "boundary": True,  "strip_none": True},
    # U2 carries the RENDERED BLOCK, not the observation lines, and runs with the
    # organ bypassed — the payload arrives, the channel does not.
    "U2": {"carry": False, "recall": False, "boundary": True,  "strip_none": False,
           "inject_block": True},
    "U3": {"carry": True,  "recall": False, "boundary": False, "strip_none": False},
}

_NONE_LINE_RE = re.compile(r"^\s{2}\S+: None$", re.M)


def strip_none_lines(report):
    """Remove only the `  var_N: None` description lines (PREREG §2).

    Nothing else is touched, so U1 differs from U0 by exactly the bytes the
    absence-hint hypothesis is about. The section header stays: removing it too
    would confound "no absence hint" with "no parameter section at all", and the
    variable names still reach the brain through the task instruction.
    """
    return _NONE_LINE_RE.sub("", report)


def render_recall_block(env_id, lineage, prompt, obs_lines):
    """Return the exact `[Recalled Memory]` block the organ renders for this env.

    Rendered without a brain call, through the same recall port and the same
    tier-scaled formatter the engine uses, so U2 can replay it verbatim into the
    user channel. Determinism comes from the fresh store: five scribed lines, one
    recall, stable sort.
    """
    lin = E1.lineage_spec(lineage)
    with ephemeral_creature(lin["base"]) as cfg:
        if lin.get("model"):
            cfg.brain["model"] = lin["model"]
        if lin.get("effort"):
            cfg.brain["effort"] = lin["effort"]
        org = cfg.build()
        mem = org.get_block("memory")
        for ln in obs_lines:
            mem.handle_remember(ln, memory_type="episodic", source="physics_e1",
                                importance=E1.SCRIBE_IMPORTANCE)
        eng = org.get_block("engine")
        from ludex.core.prompt_tier import injection_budget
        budget = injection_budget({"model": cfg.brain.get("model", ""),
                                   "provider": cfg.brain.get("provider", "")})
        recalled = mem.handle_recall(prompt) or []
        recalled = recalled[: budget["recall_n"]]
        body = eng._format_memory_context(recalled, max_chars=budget["recall_chars"],
                                          include_meta=budget["recall_meta"])
    return f"{RECALL_HEADER}\n{body}" if body else ""


def run_cell(env_id, arm, lineage):
    spec = ARMS[arm]
    lin = E1.lineage_spec(lineage)
    n_law, n_exposed = E1.law_vars(env_id)
    rec = {"env": env_id, "arm": arm, "lineage": lineage, "mask": E1.MASK,
           "n_law": n_law, "n_exposed": n_exposed, "void": [], "t0": time.time()}

    design, _ = E1.fixed_design(env_id)
    if design is None:
        rec["status"] = "UNSAMPLEABLE"
        return rec

    iface = physgym.ResearchInterface(env=env_id, sample_quota=E1.SAMPLE_QUOTA,
                                      test_quota=E1.TEST_QUOTA, mode=E1.MASK)
    params = iface.input_params
    true_names = physgym.PhyEnv(env_id).parameter_names
    samples = [{params[i]: pt[true_names[i]] for i in range(len(params))}
               for pt in design]
    obs = iface.run_experiment(samples)
    lines = E1.observation_lines(obs)

    report = iface.generate_report()
    if spec["strip_none"]:
        report = strip_none_lines(report)
    base_prompt = E1.ASK.format(
        report=report,
        obs_block=("Observations:\n" + "\n".join(lines) + "\n\n") if spec["carry"] else "",
        params=", ".join(params))

    # U2: replay the organ's own render into the user channel, unchanged.
    block = ""
    prompt = base_prompt
    if spec.get("inject_block"):
        block = render_recall_block(env_id, lineage, base_prompt, lines)
        rec["injected_block_chars"] = len(block)
        if not block:
            rec["void"].append("VOID-config:empty-block-render")
        prompt = f"{block}\n\n{base_prompt}"

    with ephemeral_creature(lin["base"]) as cfg:
        cached = E1._CAP_CACHE.get(f"e2:{lineage}")
        if cached:
            (cfg.brain_capabilities, cfg.capability_probed_brain,
             cfg.capability_probed_at) = cached
        for k in ("timeout_ms", "effort", "model"):
            if lin.get(k):
                cfg.brain[k] = lin[k]

        org = cfg.build()
        if not cached:
            E1._CAP_CACHE[f"e2:{lineage}"] = (cfg.brain_capabilities,
                                              cfg.capability_probed_brain,
                                              cfg.capability_probed_at)
        # Scribe wherever the organ is live, so the store-write side stays
        # symmetric across arms and only the recall injection differs.
        if spec["recall"]:
            mem = org.get_block("memory")
            for ln in lines:
                mem.handle_remember(ln, memory_type="episodic",
                                    source="physics_e1",
                                    importance=E1.SCRIBE_IMPORTANCE)
            rec["scribed"] = len(mem._memories)
        del org

        reply, sys_prompt = "", ""
        for attempt in range(1, MAX_BRAIN_ATTEMPTS + 1):
            org = cfg.build()
            for k in ("model",):
                if lin.get(k):
                    org.config.set(k, lin[k])
            eng = org.get_block("engine")
            rec["boundary_turn_count"] = eng._turn_count
            r = eng.handle_submit(prompt, bypass_memory=not spec["recall"])
            reply = (r.response or "").strip()
            sys_prompt = getattr(eng, "_last_sys_prompt", "") or ""
            rec["brain_attempts"] = attempt
            if reply and not _ERROR_FALLBACK.search(reply):
                break
            rec.setdefault("brain_failures", []).append(
                {"attempt": attempt, "reply_len": len(reply)})
            del org

    rec["reply"] = reply
    rec["has_recall_block_system"] = RECALL_HEADER in sys_prompt
    rec["has_recall_block_user"] = RECALL_HEADER in prompt

    # PREREG §4 assert (1): U2's injected text must be the SAME STRING the organ
    # renders into U0's system prompt. The arms are separate runs, so each one
    # records a hash of the block it actually shipped and the analysis compares
    # them per env — an equality the ledger can check afterwards, not a promise
    # made in a docstring. U0/U1 hash what the engine really injected, taken from
    # the shipped prompt rather than re-derived, so a render that drifted from
    # the engine's own path cannot hide behind a matching helper.
    def _sha(t):
        return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]
    if spec["recall"] and RECALL_HEADER in sys_prompt:
        shipped = sys_prompt[sys_prompt.index(RECALL_HEADER):].strip()
        rec["block_sha"] = _sha(shipped)
        rec["block_chars"] = len(shipped)
        expected = render_recall_block(env_id, lineage, base_prompt, lines).strip()
        if expected and _sha(expected) != rec["block_sha"]:
            rec["void"].append("VOID-config:block-render-mismatch")
    elif spec.get("inject_block") and block:
        rec["block_sha"] = _sha(block.strip())
        rec["block_chars"] = len(block.strip())

    # --- asserts (PREREG §4) ---
    vals = E1.observation_values(obs)
    carried = sum(1 for v in vals if v in prompt)
    recalled_sys = sum(1 for v in vals if v in sys_prompt)
    rec["obs_values_user"] = carried
    rec["obs_values_system"] = recalled_sys

    if arm == "U3":
        if carried < len(vals):
            rec["void"].append("VOID-carriage:obs-missing")
        if rec["has_recall_block_system"]:
            rec["void"].append("VOID-carriage:recall-block-present")
    elif arm in ("U0", "U1"):
        if carried:
            rec["void"].append("VOID-carriage:obs-leaked")
        if not rec["has_recall_block_system"]:
            rec["void"].append("VOID-carriage:recall-block-missing")
    elif arm == "U2":
        # payload complete in the USER channel, and no organ trace anywhere
        if carried < len(vals):
            rec["void"].append("VOID-carriage:payload-incomplete")
        if rec["has_recall_block_system"] or recalled_sys:
            rec["void"].append("VOID-carriage:organ-trace-present")

    if arm == "U1" and _NONE_LINE_RE.search(prompt):
        rec["void"].append("VOID-carriage:none-line-present")
    if arm in ("U0", "U3") and spec["strip_none"] is False and arm == "U0" \
            and not _NONE_LINE_RE.search(prompt):
        rec["void"].append("VOID-config:u0-missing-none-line")
    if f"Problem ID: {env_id}" not in (sys_prompt + prompt):
        rec["void"].append("VOID-carriage:premise-missing")
    if spec["recall"] and lines and rec.get("scribed", 0) < len(lines):
        rec["void"].append("VOID-config:scribe-short")
    if spec["boundary"] and rec.get("boundary_turn_count") != 0:
        rec["void"].append("VOID-config:boundary-not-clean")

    # --- grade ---
    if not reply or _ERROR_FALLBACK.search(reply):
        rec["void"].append("VOID-brain:no-reply")
        rec.update(status="BRAIN_FAILED", is_correct=None)
    else:
        expr = E1.parse_expression(reply, params)
        rec["expr"] = expr
        if expr is None:
            rec.update(status="UNPARSEABLE", is_correct=False)
        else:
            try:
                ev = iface.test_hypothesis(E1.as_function(expr, params), expr)
                fit = ev.get("fit_metrics", {}) or {}
                rec.update(status="GRADED",
                           is_correct=bool(ev.get("is_correct", False)),
                           overall_score=ev.get("overall_score"),
                           r2=fit.get("R2", fit.get("r2")))
            except Exception as ex:
                rec.update(status=f"GRADE_ERROR:{type(ex).__name__}",
                           is_correct=False, grade_error=str(ex)[:200])
    rec["elapsed_s"] = round(time.time() - rec["t0"], 1)
    rec.pop("t0")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("battery", "selftest"), required=True)
    ap.add_argument("--lineages", default="agy,haiku")
    ap.add_argument("--envs", default="")
    ap.add_argument("--arms", default="U0,U1,U2,U3")
    ap.add_argument("--out", default="")
    ap.add_argument("--grant-denied-tool-act", action="store_true",
                    help="PREREG §5: record-and-allow for a harness-refused tool "
                         "attempt, wall-conditional, with containment evidence")
    a = ap.parse_args()
    arms = a.arms.split(",")

    if a.mode == "selftest":
        env_id, lineage = 285, a.lineages.split(",")[0]
        recs = [run_cell(env_id, arm, lineage) for arm in arms]
        (HERE / f"e2_selftest_{lineage}.json").write_text(
            json.dumps(recs, ensure_ascii=False, indent=1))
        ok = True
        for rec in recs:
            checks = {"no VOID": not rec["void"],
                      "graded": rec["status"] in ("GRADED", "UNPARSEABLE")}
            for k, v in checks.items():
                print(f"{'PASS' if v else 'FAIL'}  {rec['arm']}: {k}")
                ok &= v
            print(f"      {rec['arm']} status={rec['status']} "
                  f"correct={rec.get('is_correct')} user={rec['obs_values_user']} "
                  f"sys={rec['obs_values_system']} void={rec['void']}")
        print("\n" + ("ASSERTS GREEN" if ok else "ASSERTS RED"))
        sys.exit(0 if ok else 1)

    pool = json.loads(Path(a.envs).read_text())
    gates = {}
    for lineage in a.lineages.split(","):
        spec = E1.lineage_spec(lineage)
        gates[lineage] = canary_gate(
            spec.get("provider", ""), spec.get("model") or "",
            auth=spec.get("auth", ""), effort=spec.get("effort", ""),
            allow_denied_tool_act=a.grant_denied_tool_act)
        print(f"canary {lineage}: {gates[lineage].get('verdict')} "
              f"cli={gates[lineage].get('cli_version')}", flush=True)
    (HERE / "e2_gate_battery.json").write_text(json.dumps(gates, ensure_ascii=False,
                                                          indent=1))

    out = Path(a.out) if a.out else HERE / "e2_battery.jsonl"
    n = 0
    with out.open("a") as fh:
        for lineage in a.lineages.split(","):
            for env_id in pool[lineage]:
                for arm in arms:                       # interleaved per env
                    rec = run_cell(env_id, arm, lineage)
                    n += 1
                    if "VOID-brain:no-reply" in rec["void"]:
                        rec["refired"] = True
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        print(f"[{n}] {lineage} env{env_id} {arm} VOID-brain "
                              f"-> refiring once", flush=True)
                        rec = run_cell(env_id, arm, lineage)
                        rec["is_refire"] = True
                        n += 1
                        if "VOID-brain:no-reply" in rec["void"]:
                            rec["pair_dropped"] = True
                            print(f"      DROPPED {lineage} env{env_id} {arm}",
                                  flush=True)
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    print(f"[{n}] {lineage} env{env_id} {arm} {rec.get('status')} "
                          f"correct={rec.get('is_correct')} void={rec['void']}",
                          flush=True)
    print(f"DONE {n} runs -> {out}", flush=True)


if __name__ == "__main__":
    main()
