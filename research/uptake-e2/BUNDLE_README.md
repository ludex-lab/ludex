# Uptake E2 — the complete measurement record

*Cover by Ray, 2026-08-04. Like the E1 record, this directory is written
once, includes its own errors, and nothing is edited for publication.*

## What this is

The follow-up walk to Physics E1. E1 found that the registered lineage
recovered 0.00 from a fully-delivered `[Recalled Memory]` system-prompt
block, and disclosed two candidate causes: the delivery surface itself,
or an absence-suggesting `None` line in the premise text. E2 separates
them within-env: four arms on E1's exact environment pool (a registered
pool-reuse waiver — reuse is the control here, not the contamination),
holding content byte-identical and varying only the surface. U0/U3
replicate E1's C3/C1 as anchors; U1 removes the `None` lines; U2
re-delivers the identical block text through the user channel.

## Result, one paragraph

**The surface is the cause.** The same 482-character block yields 0/8
in the system prompt and **8/8** in the user message (all eight pairs
discordant in one direction, exact p=.0078; block identity sha-verified
per environment). Removing the absence cue changed nothing (U1 0/8,
zero discordant pairs) — that hypothesis died cheaply, against a
pre-registered ~25% prior, and its death is what makes the surface
reading clean. The mechanism is visible in prose: with the text in the
user channel the lineage stops saying "no data was provided" (5/8 → 0/8)
and starts inducing — eight law statements, eight correct. A cross-walk
CLI version change (1.1.9 → 1.1.10) was caught at the gate and
adjudicated by U0's pre-registered dual role: U0 held 0/8, so E1's
finding survives the build change. The annex lineage stayed high in all
arms, as registered for a contrast control. Full adjudication:
`VERDICT_uptake_e2.md`.

## Ordering — verifiable this time

This walk is the first run of the standing rule adopted after E1: the
pre-registration was committed to this public repository before firing.
The evidence for that ordering is not the commit's own dates — those
are set locally and prove nothing — but GitHub's server-side push
event, `2026-08-03T10:01:01Z` (event `16557145606`), which precedes the
battery's first run. Public events leave the API after roughly ninety
days, so the receipt is archived beside the spec
(`PREREG_PUBLISH_RECEIPT.json`); after that a reader is trusting our
archive, and a signed commit or an external timestamp anchor would be
strictly stronger. E1's cover carried "pre-registered, not
independently verifiable"; this one carries a narrower and checkable
claim instead.

## How to read the lineage names

Unchanged from E1, and it still travels with the claim: these names are
measurements of one CLI surface × one organ injection shape × 8/4
environment pairs under one masking and quota regime. They are **not a
ranking of models**. E2 sharpens the point: the same lineage that scored
0/8 behind one delivery surface scored 8/8 behind another, with the
content byte-identical. The surface, not the model, was the variable.

## Scope

Estimates are conditional on the E1 environment pool (registered
waiver; within-env manipulation requires it), the quota=5-identifiable
law space (true law ≤ 3 variables), and a memory-as-database reading.
The registered lineage was measured on CLI 1.1.10 (E1: 1.1.9) with
U0 as the pre-registered drift gate.

## Files

| Group | Files | Note |
|---|---|---|
| Specification (verbatim) | `PREREG_uptake_e2.md` + `PREREG_PUBLISH_RECEIPT.json` | already in this public directory since before firing — deliberately NOT re-copied by the bundle, because overwriting it would destroy the artifact whose earlier presence is the point; the receipt carries the ordering evidence |
| Verdict | `VERDICT_uptake_e2.md` | priors scored, drift adjudicated, analyzer limitation annotated |
| Instruments | `driver_uptake_e2.py`, `analysis_uptake_e2.py`, `test_uptake_e2_units.py` | analyzer pre-committed; verdict wording selected by code |
| Data as run | `e2_battery.jsonl` (48 runs), `e2_pool.json`, `e2_result.json`, `e2_gate_battery.json`, `e2_drift_restamp.json`, `e2_telemetry.jsonl`, `e2_selftest_agy.json` | ledgers are canonical over any narrative, including ours |

— Ray, 2026-08-04
