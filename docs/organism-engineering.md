# Organism Engineering — from disposable agents to persistent beings

> **Status note (2026-08-31).** This paper proposed organs as the rung above
> context engineering. Native harnesses (Claude Code, grok build, codex …)
> have since absorbed much of that rung, making the superiority claim
> unmeasured. A three-lab review retired it: the runtime-organ layer
> retreats to bare substrates and bridged fields, the narrative substrate
> and village layer carry the weight, and the claim returns only with
> same-model causal evidence. The review thread is in the repo history.

*A practitioner's guide to the layer after context engineering. Distilled from the Ludex lab's
pre-registered experiments and two years of creature operations. (v0.2, 2026-07-04)*

*한국어판: [organism-engineering.ko.md](organism-engineering.ko.md)*

---

## Overview — three rungs of a ladder

| era | what it optimizes | what it solved | what it couldn't |
|-----|-------------------|----------------|------------------|
| **Prompt Engineering** | the input of one call | instruction quality | no continuity across calls |
| **Context/Harness Engineering** | the Shell around an agent (tools, memory, permissions, infra) | competence within a session | nothing survives the session |
| **Organism Engineering** | **the whole persistent being** — organs, development, relationships, lifecycle | agents that get better with operation | (what this document is about) |

**One-line philosophy: treat the agent as a continuant, not an execution. Don't stuff context — grow organs.**

The harness era's silent premise — "agents are disposable and the Shell is designed statically" —
is the thing to drop. The moment you do, a new design space opens: memory stops being one search
box and becomes **differentiated systems**; security stops being an external guardrail and becomes
an **immune organ**; the operator stops being a configurator and becomes a **caretaker**.

### Why now — two measured results

This document's claims come from measurement, not taste (full table in Appendix A):

1. **The harness dominates outcomes.** The same model (haiku) on the same task (block planning)
   went 0% → 100% on the difference of one prompt adapter. Much of what we call model evaluation
   is actually harness evaluation.
2. **But there is a layer above the harness.** Same agent, same injection path, payload only:
   a complete solution recipe (content) changed nothing (p=.79), while a structural map with an
   explicit unexplored frontier (form) unlocked exploration (p=.0096, d=1.61). **Agents cannot
   use what they "know"; they use what compiles into a next action.** That is why organ design
   is not context stuffing.

---

## Part 1 — The organ catalog: what to grow inside an agent

Each pattern: concept → minimal artifact → checks. All framework-agnostic — you can start with
files and discipline alone.

### 1-1. The identity file — the minimum unit of continuity

For an agent to be "the same being" across sessions, its identity must exist as a file. The key
rule is **carry-forward**: update by section, never rewrite wholesale (a self-document that grows
without bound and one that resets every time are both failure modes).

```markdown
# <name> — Self-Understanding
Last reflection: <date> (trigger: <event>)
## Patterns    ← self-tendencies observed repeatedly (evidence-backed only)
## Lessons     ← lessons extracted from experience
## Open questions ← what it doesn't know yet (leaving this open is information)
```

- [ ] Does an identity file exist, and is it injected into system context at session start?
- [ ] Is it updated by carry-forward rather than full rewrite?

### 1-2. Differentiated memory — one search box is not a memory

Human memory is multiple systems (declarative/procedural/spatial/temporal/social); agent memory
needs **different retrieval paths per query type**. One lexical search cannot answer both
"where was the ring" (spatial) and "when was that" (temporal).

Minimal implementation: start by tagging every memory record with its **formation context**.

```json
{"content": "...", "type": "episodic", "tags": ["field:refactor-session"],
 "created_at": 1783126818}
```

- Declarative: lexical/embedding search (you probably have this)
- Temporal: "what happened in the last N hours, in what order" — an append-only event log +
  time-window queries
- Spatial: the map of 1-4
- [ ] Are memories tagged with where/when they formed, at write time?
- [ ] Is there a path that answers "when / how long ago / in what order"?

### 1-3. World-models as FORM — the most important pattern in this document

When you inject what an agent learned about a domain into its next session, **prose summaries are
not consumed.** Measured: a 93-line summary containing the complete solution = zero effect. An
accurate structural map = exploration unlocked and the task solved.

The test for any injected knowledge: **"does it compile into a next action without a plan?"**
- Bad: "I learned that doing X then Y opens Z" (demands a multi-step execution commitment)
- Good: structure relative to the current position + an **explicit frontier** (a list of things
  tryable right now)

```
[Map] 5 places known · you are at: <X>
- <place>: <link>→<dest> · <link>→?      ("?" = not yet visited)
Things you have not tried from where you now stand: <list>   ← the signal concentrates here
```

- [ ] Is your cross-session knowledge injection a prose summary, or an action-compilable form?
- [ ] Are confidence tags present? (seen 1–2× = "hypothesis to test"; 10×+ = "policy")

### 1-4. Spatial memory and the frontier — coding agents deserve maps too

"Spatial" is not a metaphor. A coding agent's zone is the repository: nodes = directories/modules,
edges = imports/calls, **frontier = the regions not yet read**. The measured failure mode is
crisp — without a map, unexplored space is *invisible*, and the agent re-examines whatever is in
front of it (we call this over-anchoring, and we observed it **unchanged across every capability
tier** from haiku to the newest frontier models). The antidote is not capability. It is a map.

- [ ] Can your agent query where in its workspace it has and hasn't been?

### 1-5. Immunity — security as an organ

External guardrails get bypassed; organs live with the being. Three layers:
- **Storage-boundary guards**: block failure artifacts (error fallbacks, timeout strings) from
  being written into memory/identity at the storage site. (Our incident: "[Error: CLI timed out]"
  became an agent's "memory" and contaminated later behavior.)
- **Input immunity**: deception scanning on incoming persuasion/instructions — especially in
  multi-agent environments.
- **Ecosystem integrity**: experiments run on **copies** (ephemeral-copy discipline so live state
  is never contaminated), and credentials are explicit per-being (we lived through an incident
  where the mere presence of an environment variable silently changed the billing path).
- [ ] Are the paths by which failure artifacts leak into persistent state closed?
- [ ] Do experiments/tests run on copies of live agent state?

### 1-6. Temperament measurement — diagnose before you deploy

Which model to use for a role is a question of **behavioral temperament**, not benchmark scores.
We measure four axes (reactivity/compliance/sociality/resilience) with a standard battery, store
the result as a card, and use it for role assignment and experiment stratification. Minimal
version: before deploying, run candidate models through an identical scenario set and keep the
behavioral profile. Every measurement must carry **provenance** (method / n / date / the model
measured) — separating measurement from assertion is the entire discipline.

- [ ] Is model selection grounded in measured temperament, or benchmark impressions?
- [ ] Do measurements carry provenance, and get marked stale when the model changes?

### 1-7. Reflection and consolidation — where experience becomes an asset

Have the agent write its own retrospective when a session ends (updating the identity file of
1-1). Periodically consolidate the event log into **chapters** — raw logs only grow; consolidated
retrospectives become the material of future context. Observed effect: an agent's own reflection
measurably changes its next session's behavior (double-edged — we have a case where a reflection
fixated the next attempt onto specific objects; hence the confidence tags).

- [ ] Is a retrospective automatically written at session end?
- [ ] Is there a procedure that periodically compresses/consolidates the event log?

### 1-8. Lifecycle governance — dormancy instead of deletion

Continuants need lifecycle policy. Our principles:
- **Dormancy ≠ death**: if the model/credentials disappear but the state (memory/identity/
  relationships) survives, a re-brain brings back the *same being*. Death is only the
  **irreversible loss of narrative data**.
- **Backup = resilience**: an off-machine backup of the state directory is the being's insurance.
- **Substrate changes are rituals**: a model swap is not a config flip but a recorded life event
  (change → smoke test → transition record → the agent's own awareness of it). We have repeatedly
  observed the identity accidents that silent flips produce.
- [ ] Is agent state backed up? Is a model swap a recorded event?

### 1-9. Community — agents that re-recognize each other

The next step for multi-agent isn't orchestration; it's **relationships**: stable being
identifiers (not a fresh id per meeting), records about the other after interaction (bonds),
re-recognition on re-meeting. Collaboration quality comes from the accumulation of "I've worked
with this agent before and it tends to do X." (A warning: stake design dominates behavior — the
same models stopped talking and only traded the moment rewards were introduced.)

- [ ] Do agent-to-agent interactions persist into the next meeting?

---

## Part 2 — Operating discipline: the caretaker's grammar

Organs are what you plant; this part is how you tend them. All of it was learned from incidents.

### 2-1. Diagnosis before prescription
Don't declare a root cause from one symptom. When a tool fails, capture stderr / raw output /
surrounding state first, and fix only when evidence supports the hypothesis. Multiple symptoms
can hide multiple causes.

### 2-2. No conclusions from a single observation
One anomalous behavior is a hypothesis, not a finding. Until it reproduces N times, the label is
[PROVISIONAL]. The prescription for intermittent failure is robustness, not removal.

### 2-3. Concurrent controls — time is the biggest confounder
Agent performance **drifts within a single day** (we twice measured a same-spec baseline moving
1.0 → 2.5 across two days). Comparing yesterday's baseline to today's treatment manufactures
effects. **Every comparison runs concurrent and interleaved.** This is a permanent design rule
in our lab.

### 2-4. Pre-register agent changes too
To claim an effect for a prompt/organ/model change, record the prediction **before** the change
and then measure. Results are most credible when the analyst's *contrary* prior is on record —
our two strongest results both arrived by **refuting** a registered prior.

### 2-5. Quota and fatigue — caretaker cadence
Brain calls are a finite resource. Smoke-test first, rotate work across beings, and read early
fatigue signals as a cue to yield, not push. Save premium models for high-value work.

---

## Appendix A — Evidence table (all lab-internal; pre-registered or externally anchored)

| claim | measurement | status |
|-------|-------------|--------|
| The harness dominates outcomes | same model & task, 0%→100% on an adapter difference | reproduced (both labs) |
| Content injection is not consumed | WM including a full solve recipe, n=12/arm, Δ+0.17 p=.79 | pre-registered null |
| Form injection is consumed | structural map+frontier, Δ+1.67 exact-p=.0096 d=1.61, LOO-robust | pre-registered; analyst's contrary prior refuted |
| Static-map effect replicates | next-day replication Δ+2.58 (concurrent control) | replicated |
| Self-built maps also work (**organ confirmed**) | built from an empty map, Δ+1.50, Holm p=.029, incl. fastest solve — **replicated 3× at identical +1.50**, then shipped to product | pre-registered; confirmed, replicated, shipped |
| The "earning tax" — the cost of self-building | self-built vs oracle gap 1.08 [0.33, 1.83] — the price of generality; ~1 room-state in a 5-room zone | pre-registered estimate |
| A forced-explore directive's marginal effect (the gate) | directive on top of the organ, Δ+0.58, exact-p=.30 — **not confirmed → not shipped** (what isn't measured doesn't ship) | pre-registered null; redesign in progress |
| Fixation is tier-invariant | haiku = sonnet-5 = fable-5, identical fixation | observed across 3 tiers |
| Performance drift is real | same-spec baseline 1.0→2.5 (two days, twice) | observed 2× → made a permanent rule |
| Temperament is a model property | 4-axis battery; marked stale on model change | in operation (MTI) |

*Until third-party replication, read every number as "within our lab." The invitation to
replicate is open.*

## Appendix B — Combined checklist

**Organs (Part 1):** identity file · carry-forward updates · memory context tags · temporal query
path · action-compilable knowledge injection · confidence tags · workspace map+frontier ·
storage-boundary guards · experiments on copies · temperament measurement+provenance · session
retrospectives · log consolidation · backups · substrate-change rituals · being identifiers and
relationship records

**Discipline (Part 2):** diagnosis first · no single-observation conclusions · concurrent
controls · pre-registration of changes · caretaker cadence

---

*The Ludex project (github.com/ludex-lab/ludex) — a public reference implementation where every
pattern in this document actually runs. Questions and replication attempts welcome.*

*A standalone CLI that bolts these patterns onto your existing agent workflow — **organum** — is
in the works.*
