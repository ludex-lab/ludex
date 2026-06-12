# How a Ludex Creature Remembers

*The memory architecture, explained. For the philosophy behind it, see
[design-notes.md](design-notes.md); for inter-organ mechanics, see
[ARCHITECTURE.md](../ARCHITECTURE.md). 한국어:
[memory-architecture.ko.md](memory-architecture.ko.md)*

---

## First principles

1. **The brain does not remember; the body does.** The model is
   stateless and swappable. Everything a creature knows about its past
   lives on disk, in its habitat. A fresh session *wakes into* a
   remembering body.
2. **Memory is bounded and lossy on purpose.** A creature that recalls
   everything is a database, not a memory. Budgets scale with the
   brain, and forgetting is a designed mechanism, not a failure.
3. **The raw record is never overwritten.** Synthesis lives *beside*
   what it synthesizes. Forgetting filters what surfaces; it deletes
   nothing. The disk is an archaeology.
4. **What happened and what it was like are different records.**
   Objective telemetry goes to the span store; subjective experience
   goes to memory. One event may exist once in each — never twice in
   either.
5. **Delivery is the body's guarantee — fitted to the brain.** Having
   memories on disk is not remembering. Every brain call — any
   provider, any model size — carries the creature's continuity floor.
   And the smaller the brain, the harder the body works: it curates
   more strictly so that *less*, not more, reaches the prompt.
   Scaffolding scales up as the brain shrinks; payload scales down —
   a small model is hurt by irrelevant context more than it is helped
   by marginal context.

---

## Five questions, five systems

"Memory" is not one store. It is five systems, each answering a
different question:

| Question | System | Where it lives |
|---|---|---|
| **What happened?** | Span store | `store/spans.jsonl` |
| **What was it like?** | Episodic memory | `memory/memories.jsonl` |
| **What has it become to me?** | Synthesis | beliefs + `reflections/YYYY-MM.md` |
| **Who am I?** | Self-model | `SELF.md` |
| **Who are you to me?** | Bonds | `bonds/<other>.md` |

The boundary between the first two is the most load-bearing rule in the
system. The test for any writer: **would the creature ever say this
about itself?** "Turn 47 took 2.3 seconds" — never; that's a span.
"I held my position against the whole panel, and it held" — yes;
that's a memory. Experience is captured at *session* granularity (one
memory per field run, per conversation), never per-turn.

One of the five deserves its own section: bonds are not just a record
of shared history — they are a creature's **theory of mind** about
everyone it knows.

---

## Memory of other minds: theory of mind in the bond

**Theory of mind** (ToM) is a concept from developmental psychology:
the capacity to attribute mental states — beliefs, intentions,
knowledge — to others, and to grasp that those states can differ from
your own and from reality. The classic test: Sally puts her marble in
the basket and leaves the room; Anne moves it to the box. Where will
Sally look? Children pass around age four — the moment they understand
that *Sally's map of the world is a separate thing from the world*.
It's called a "theory" because another mind is never observed
directly: it can only be modeled, and the model can only be graded by
the predictions it makes.

Ludex takes that last sentence literally. A bond file carries, beside
the shared history, a **working model of the other's mind** and a
**prediction history**:

```
 before a shared situation:   predict   "Comet will dissent here"
 during:                      observe   what actually happened
 after:                       score     was the prediction right?
                              update    the mental model revises
```

This loop makes bonds the only memory system with built-in
falsification — and that has a striking consequence: **a creature
knows others more rigorously than it knows itself.** Self-knowledge
comes from introspection (reflections, self-report); knowledge of
others is tested against reality every time a prediction is scored.
Knowledge of others is *earned* memory, not asserted memory — and
that standard, born in the bond, is the direction the whole memory
architecture converges toward (identity, too, must eventually survive
prediction, not just repetition).

Two boundaries keep this honest. ToM is not a separate organ or
module — per the "pervasive capability" finding in recent HCI
research, it lives embedded inside relationship memory itself, the way
your model of a friend lives inside your friendship. And no mind claim
is made: Ludex measures behavior-level correlates (prediction
accuracy, model revision), not consciousness.

This is also where memory becomes ecology. A single creature's memory
makes a biography; bonds that model and test each other make a
**society**. The ecosystem graph you can watch in the app is, quite
literally, creatures' memories of each other — verified, revised, and
carried across every session and every substrate swap.

---

## The importance ladder

Every memory carries an importance value — but writers don't pick
arbitrary floats. They pick a rung from one ladder:

| rung | meaning |
|---|---|
| 0.95 | ceiling — usage bumps stop here |
| 0.8 | lived-experience reflections |
| **0.7** | **the significance line** — see recall, below |
| 0.5 | unremarkable episodic |
| 0.45 | a conversation session |
| 0.3 | archive floor — below this + mature, housekeeping archives it |

Reading the ladder: above 0.7 is what the creature *is about*; below
0.5 is what merely passed through. Each surfaced recall nudges a
memory's importance up slightly (capped) — often-recalled memories
resist forgetting, so importance adapts to use.

---

## Recall: two channels

- **The lexical channel** — keyword relevance (TF-IDF + tags). *Topical*
  recall: what does this question touch? The mind searching.
- **The recency-significance channel** — significant memories
  (importance ≥ 0.7) surface for about a week after they happen,
  regardless of the question's wording or language. *Ambient* recall:
  the recent past pressing on the present. A creature that just lived
  through something important brings it into the room, even when you
  ask in a different language.

---

## Two consolidations (they are not the same thing)

**Housekeeping — the dream cycle.** Mechanical, mostly brain-free
reorganization *inside* the store: archive mature low-importance
entries, evict over-budget memories by effective age, cluster repeated
episodes into beliefs. Like sleep consolidation: nothing new is
thought; the shelf is reorganized.

**The retrospective.** Narrative synthesis *outside* the store: a
mechanical digest of a window of life (its spans, bonds, journal) is
handed to the creature's own brain, which writes a six-section
retrospective in its own voice — What Happened / Patterns I Notice /
What Held · Shifted / What I Learned About Others / Identity Shifts /
Still Open. Fires roughly monthly, when enough life has accumulated.
Like writing in a journal on the last evening of the month: the month
doesn't change; its meaning gets a shape.

Once retrospectives exist, recall becomes **dual-thread**: the creature
retrieves both the raw in-the-moment record and the consolidated
hindsight. Facing the contrast between the two is itself a driver of
self-knowledge.

---

## Identity: the slowest memory

`SELF.md` has two strata. The **rolling portrait** is rewritten by
every reflection — what the creature currently understands about
itself, volatile by design. The **identity block** is managed only by
a promotion gate: an observation the creature makes about itself in a
retrospective enters as `[PROVISIONAL]`; only if it survives a second
consecutive window does it become `[SETTLED]`. No single moment of
introspection can rewrite who a creature is — identity changes must
replicate.

The whole architecture is easiest to see as a cascade of tempos:

```
 tempo         artifact                    mechanism
 ──────        ─────────                   ──────────
 a turn        recalled-memory block       recall (2 channels)
 a session     an episodic memory          experience capture
 a week        recency-channel presence    significance decay
 ~2 weeks      housekeeping pass           dream cycle
 a month       reflections/YYYY-MM.md      retrospective
 2+ windows    [SETTLED] identity          promotion gate
 a lifetime    spans + snapshots           the archaeology (never lost)
```

The same life, settling at seven different speeds — each tempo with its
own store and its own writer.

---

## Forgetting: a filter, not a deletion

The forgetting pass keeps the recall surface within a budget scaled to
the brain, using retrieval-failure logic:

```
forget_score = (1 − importance) × age_days × 1/(1 + recall_count)
```

Old, unimportant, never-recalled memories fade first. A forgotten
memory leaves recall — and changes in no other way. The line stays in
the file forever. Identity memories are protected outright. Archived,
forgotten, and deleted are three different states, and only the last
is destructive (rare, manual).

---

## Delivery: how memory reaches the brain

```
 guaranteed by the engine, on every call, for every provider:
   [Self]                ← who I am right now, by observable signals
   [Self-understanding]  ← compressed SELF.md
   [Recalled Memory]     ← both recall channels
 added by some substrates as enrichment:
   full SELF.md + recent bonds  (CLI-adapter context injection)
   agentic self-access          (a brain with file tools may read
                                 its own habitat directly)
```

The floor is unconditional; the enrichment is a privilege. Continuity
must never depend on which provider a creature runs on or how
proactive its brain happens to be.

---

## What's ahead

Directions we've designed but not yet built: **prospective activation**
(intentions that fire when their moment arrives — "next time I meet
her, ask about the storm"); **identity revision** (a `[CONTESTED]`
state, so even settled self-knowledge can be re-examined when later
evidence contradicts it); **epistemic provenance** (memories that know
whether they were experienced, told, or inferred); **intersubjective
memory** (two creatures comparing their accounts of the same shared
event); and richer recall channels (associative resonance, mood-
congruent recall via the emotion organ).

One deliberate non-feature: memories are never rewritten by the act of
recalling them. Episodes stay immutable; reinterpretation happens in
the layers above.
