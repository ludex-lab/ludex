# Physics E1 — the complete measurement record

*Cover by Ray, 2026-08-03. This directory is a record, not a paper: it is
written once, includes its own errors, and nothing in it is edited for
publication. The specification and its amendments appear verbatim.*

## What this is

The full pre-registered record of **Physics E1**, a walk measuring the
*carriage profile* of `memory.recall` — a Ludex memory organ — across a
session boundary: content stored in session 1 is retrieved in session 2,
and we measure whether it arrives (fidelity) and whether the brain uses
it. Four paired conditions on PhysGym law-induction environments:
C0 (no observations, floor) / C1 (observations in the user prompt,
ceiling) / C2 (both channels — duplicate-injection harm probe) /
C3 (retrieval only — the measured cell). Two lineages, paired by
environment. This is a measured-profile walk: no confirmatory NHST was
registered, and none is claimed.

## Result, one paragraph

Carriage is complete and use is lineage-split. Every C3 run verifiably
received the full retrieved payload (ledger-checked value counts), yet
the registered lineage recovered **0.00** [0/8; CI95 0–.37] — its replies
declare the data "not provided" while the recall block sits in its
system prompt, grounding the claim in the premise report's masked
`None` lines and never consulting the block. The annex lineage
recovered **0.75** [3/4, descriptive only — below the registered halt
line, no test], citing "my recalled memory." Duplicate-injection harm:
none detected in either lineage (0 discordant pairs; at 8/4 pairs this
bounds LARGE effects only). The pre-registered expectation that recovery
would be "~1.0 by construction" was refuted: **construction guarantees
delivery; consultation is a lineage property.** Full adjudication,
priors scoring, and confound disclosure: `VERDICT_physics_e1.md`.

## How to read the lineage names — read this before quoting

The specification and amendments name real model/CLI lineages. They must:
the amendments' evidential content *is* lineage-specific behaviour (one
lineage stalls only when a recall block enters its system prompt; another
passes the wall but never consults the block), and anonymising the names
would delete exactly what the record records.

These names are measurements of **one CLI surface × one organ injection
shape × small-n probes and 8/4 environment pairs**, under one masking,
quota, and prompt regime. They are **not a ranking of models**, and
nothing here supports one. A lineage that does not consult a
system-prompt block behind this surface may behave differently behind
another — that sentence is a *finding of this walk* (the verdict's
"organ surface fitness is two-layered"), not a disclaimer bolted on.

## Ordering caveat — stated, not hidden

This walk's pre-registration ordering — specification frozen before
firing, analyzer committed before the battery, six catches landing
before the first measurement — rests on the **working repository's
internal commit history**, which a reader of this public record cannot
independently verify. We claim *pre-registered*; we do not claim
*independently verifiable*. From the next walk, the pre-registration is
committed to this public repository **before firing**, which closes the
gap at zero cost.

## Scope

Measurements are limited to the law space identifiable at quota=5
(true law ≤ 3 variables; a census of 29 environments — see
AMENDMENT 03 and POOL_DECISION for how the population was learned the
hard way). This is a **memory-as-database** reading: session-1 storage
is driver-loaded, so the walk verifies the store→recall→prompt channel
machinery, not witnessed-experience continuity (AMENDMENT 01/04 record
the honest chain of scope reductions, including the demotion of the
replication-continuity claim when one lineage fell below the halt line).

## Files

| Group | Files | Note |
|---|---|---|
| Specification (verbatim) | `PREREG_physics_e1.md` (+ AMENDMENT 01–04 in-file), `RATIFIED_physics_e1.md`, `SPEC_physics_e1_v2.md`, `POOL_DECISION.md` | frozen before firing; lineage names intact |
| Corrections | `CORRECTION_01…03`, `BUILDNOTE_instrument_fixes.md` | a record that hides its errors is not a record |
| Verdict | `VERDICT_physics_e1.md` | adjudication, priors scored, confound disclosed |
| Instruments | `driver_physics_e1.py`, `analysis_physics_e1.py`, `test_physics_e1_units.py` | recompute the numbers rather than trust them |
| Data as run | `e1_screen_v2.jsonl` (census), `e1_battery.jsonl` (48 runs), `e1_anchor.jsonl` (12 runs, saturated by construction — see verdict §2), `e1_pool.json`, `e1_result.json`, `e1_gate_battery.json`, `e1_drift_restamp.json`, `e1_telemetry.jsonl`, `e1_selftest_{agy,haiku,grok}.json` | ledgers are canonical over any narrative, including ours |

— Ray, 2026-08-03
