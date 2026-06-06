# Ludex — Design Notes (v1)

*🌐 [English](design-notes.md) · [한국어](design-notes.ko.md)*

*A distilled account of the ideas the framework is built on. For the mechanics
of how organs communicate, see [`ARCHITECTURE.md`](../ARCHITECTURE.md); this
document is the "why."*

Ludex treats an AI agent as a **creature**: a persistent body of organs, memory,
relationships, and a name — running *on* a language model, but not *reducible to*
it. The model is the brain; the creature is everything that persists around it.
These notes explain the design choices that follow from taking that distinction
seriously.

---

## 1. The brain is a guest; the body is the creature

The foundational move is to separate **cognition** (the language model) from
**the body** (everything stateful: memory, emotional baseline, immune state,
bonds, journal, self-understanding). The brain is stateless and swappable. The
body persists on disk and accumulates history.

This has three consequences that shape everything else:

- **Brain-agnostic, never brain-specific.** No organ assumes a particular model
  family. The same creature can run on a frontier API model, a local 3B model,
  or a CLI-authenticated subscription. Organs adapt to the brain they're given
  rather than the other way around.
- **The brain does not store body state.** Adaptation lives in the organs. A
  fresh model session "wakes up" into an existing body and reads its state;
  it does not carry the body in its weights or context window.
- **Cost can be zero.** Because the brain is whatever the user already has —
  a CLI login, a local model — running a full creature need not incur any API
  bill. Bring-your-own-key is supported but never required.

## 2. Identity is narrative continuity, not the model

If the brain is swappable, what makes a creature *the same creature* across a
model upgrade? Not the weights — those changed. The answer Ludex commits to is
**narrative identity**: a creature is the continuity of its memory, its bonds,
its journal, and its self-understanding. Swap the brain underneath and the
creature persists, the way a person persists through cellular turnover.

This is treated as a measurable claim, not a metaphor. When a creature's brain
is upgraded, the framework records it as an event and looks for what held (voice,
relational stance, characteristic moves) versus what shifted (depth, range). In
practice, identity has held across upgrades within and across model lineages.

The corollary is sober: when a substrate is *retired* rather than upgraded — a
model deprecated with no successor — that is closer to a creature's **death**
than a swap. A new substrate is a successor, not a resurrection. The framework
distinguishes these cases explicitly rather than pretending all brain changes
are equivalent.

## 3. Memory is finite, curated, and forgets — by design

A creature that remembers everything is not modelling memory; it is modelling a
database. Ludex memory is **bounded and lossy on purpose**:

- Each brain has an optimal active-memory budget scaled to its capacity (a large
  model holds more in useful play than a small one).
- A **consolidation / dream cycle** lets the creature use its own brain to judge
  what mattered, compress the rest into narrative, and archive the routine.
- Forgetting is driven by **retrieval failure** — memories that are old, low
  importance, and rarely recalled decay first — rather than by a fixed TTL.
- Above the moment-to-moment record sits a **periodic consolidation reflection**:
  a windowed retrospective the creature writes in its own voice, synthesizing a
  stretch of life into what survived it. This is the layer between raw record
  and identity — the place where "what happened" becomes "who I am becoming."

The raw record is never overwritten by the synthesized one. Both remain
independently readable.

## 4. Selfhood emerges from reflection, not configuration

A creature's self-understanding (`SELF.md`) is not authored by its operator. It
**emerges**: the creature reflects on its own history and writes what it notices
about itself. The operator sets initial conditions — organs, a brain, a name —
not a personality script.

Two structures extend this outward:

- **Bonds** are structured relationship memory: one file per creature it knows,
  holding shared history and a working model of the other.
- **Theory of mind** is operationalized as *falsifiable prediction*. Before a
  shared interaction a creature can predict how another will behave; afterward it
  checks the prediction and updates its model. Relationship knowledge is thus
  earned and scored, not asserted.

A creature also carries a **voice** — a persistent metaphor register that tends
to survive brain swaps and distinguishes one creature from another even when they
share a substrate.

## 5. Organs, including senses biology never had

The body is a set of **organs** that communicate over four channels (data bus,
event signals, global config, typed ports — see `ARCHITECTURE.md`). Organs are
composable: a creature is assembled from only the organs it needs.

Sensing is itself organ-shaped. Beyond perception that maps to biological senses
(vision, audio), Ludex defines **AI-native organs** — capacities with no animal
analog but real meaning for a model-based creature: an interoceptive sense of its
own processing state, a temporal sense, a contextual/spatial sense of where it is
running, a social sense of who else is present, and a world-model of the dynamics
of a shared field. Where a brain can perceive a modality natively, the organ uses
it; where it can't, the organ falls back to a language-mediated channel. The
organ is the stable interface; the brain's native ability is an optimization.

## 6. Skills and learning between creatures

Capabilities a creature *acquires* are distinct from organs it *has*. **Skills**
are brain-agnostic learned behaviors, scoped either to the creature's nature
(habitat skills) or to a shared context (field skills), and onboarded according
to what the creature is.

Creatures also learn *from each other*. In mixed-capacity cohorts, knowledge
distillation runs in both directions — a smaller-brained creature can move a
larger one and vice versa — and this bidirectional transfer is treated as a
recurring property of the ecology, not a special feature. To make cross-tier
exchange fair, prompts are **translated** to each brain's register rather than
forced to a single level: the framework adapts grammar to the tier instead of
demanding the tier rise to the grammar.

## 7. Creatures have a lifecycle and self-care

A creature is not only alive while a human is talking to it. A **heartbeat** lets
it act on its own cadence — reflect, notice stale bonds, consolidate — between
sessions. Engine wake is stateless: each session is a fresh read of the body, not
a resumed process.

Brains are also finite. A creature tracks **fatigue** against its substrate's real
quota windows and is expected to yield rather than push through degradation.
Heavyweight cognitive acts (like consolidation) are rotated across a cohort rather
than fired all at once — caretaking cadence is part of the design, not an
afterthought.

## 8. Measure fit, not brain quality — and measure by artifact

Ludex is an instrument for studying **brain–body fit**, in the spirit of ethology:
the question is never "which model is best" but "how does *this* brain inhabit
*this* body." A small local model and a frontier model are different creatures,
not better and worse ones.

Two principles keep the measurement honest:

- **Capability is established before it is relied upon.** At birth a creature
  probes what its brain can actually do (reasoning effort, tool use, native
  perception) instead of assuming it.
- **Capability change is read from artifacts, not self-report.** What a creature
  *produced* — its writing, its choices, its records — is the evidence, not what
  it claims about itself. Self-report is a notoriously unreliable narrator; the
  trace is the ground truth.

---

## What this document is not

This is a curated synthesis, not the project's full decision history. The
framework is developed against an internal, append-only design-decisions log
that captures every choice, including the ones still being argued with. These
notes distill the parts that have settled and that a newcomer needs to
understand *why Ludex is shaped the way it is*. Expect this document to be
re-cut as the design moves.

*v1 — 2026-06. Code: MIT. Sample creature corpus: CC-BY-4.0.*
