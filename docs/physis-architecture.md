# How a Ludex Creature Learns What Works

*The physis organ, explained. For the philosophy behind it, see
[design-notes.md](design-notes.md); for how a creature remembers, feels, and
defends itself, see [memory-architecture.md](memory-architecture.md),
[emotion-architecture.md](emotion-architecture.md), and
[immune-architecture.md](immune-architecture.md). 한국어:
[physis-architecture.ko.md](physis-architecture.ko.md)*

---

## First principles

1. **A world-model is *what worked before*, not a description of the world.**
   Where topos answers *where am I* and allos answers *who else is here*, physis
   answers *what causes what in this kind of field, and what worked before*. It
   is the reinforcement sense — the memory of play, distilled into hunches about
   which actions pay off.
2. **Learn by distillation, not accumulation.** A raw trace of every
   (state, action, reward) only grows. Physis buffers the trace during play and,
   at the end of an episode, has the brain *distill* it — together with the prior
   world-model — into a few reward-correlates, a few policy hints, and an honest
   note of what is still uncertain. The distillation, not the log, is where
   experience becomes an asset.
3. **Confidence is earned by repetition — and counted, not claimed.** A hint
   seen once or twice is *tentative* (a hypothesis to test); seen three to nine
   times, *confirmed*; ten or more, *well-supported*. Crucially, the tier is
   re-checked against the actual evidence count after every distillation and
   silently demoted if it doesn't hold up — the brain is not trusted to grade its
   own confidence. The difference between a guess and a policy is a number, not a
   tone of voice.
4. **Form beats content — the hardest principle, and the one the experiments
   taught.** This organ's world-model is prose: reward-correlates and if-then
   hints in plain language. When that prose was tested head-to-head against a
   structural map — same creature, same brain, same world — the result was
   humbling. A world-model containing the *complete solution recipe* in plain
   text moved nothing (Δ+0.17, p=.79). A bare map with an explicit unexplored
   frontier unlocked the task (Δ+1.67, p=.0096). **Form is used; content is not.**
   An agent acts on what compiles into a next move, not on what it has been told.
5. **A specialist sense, honestly scoped.** Physis was built for
   reinforcement-shaped fields: repeated episodes with real state, action, and
   reward — the games, the wilderness, a prediction-scored world-model loop. It
   proved its mechanism there. In a debate or a narrative — which produce no
   reward signal — it has nothing to distill and sits idle. That is a scope, not
   a failure.

---

## The mechanism

```
 play ──step()──▶ in-session trace buffer  (state, action, reward, per turn)
   └─consolidate(brain) ─▶ the brain distills trace + prior model into
        world_models/<field>.md :
          ## Reward correlates    (what tracked with +/− reward)
          ## Policy hints         (if-then, confidence-tiered)
          ## Open uncertainty     (what is still unknown)
        + a ```yaml hints block   (structured, for retrieval)
   └─get_relevant_hints(state) ─▶ surfaces the hints whose precondition
        matches the moment, confidence-sorted, into the next decision
```

Five ports carry this: `load_world_model` (recall the prior), `step` (buffer one
turn), `consolidate` (distill and rewrite), `get_relevant_hints` (retrieve what
fits the current state), and `clear_trace`. World-models live per-field, one file
per field, beside a structured hints sidecar.

The clearest proof it is real machinery and not decoration: creatures named Echo
and Verse, playing repeated games, built genuinely distilled world-models in
which **eight hints climbed to *confirmed*** — three to nine grounded episodes
each — while weaker patterns stayed tentative. A creature played, the brain found
reward-correlates that held up, and the confidence machinery promoted the stable
ones and held back the rest. It works.

But the deeper finding is about *what kind* of thing physis is. Across games it
became clear that **physis amplifies a brain's register; it does not overwrite
it.** It is a revealing instrument — valuable where a field has a hypothesis-chain
to distill (a game of hidden roles), null where it does not (tic-tac-toe). It
makes visible what a brain already half-knows about a field. It is observability
more than it is a policy upgrade.

---

## What it is — and what it isn't

- **Proven mechanism, narrow feed.** The confidence tiers really climbed, so this
  is not a metaphor. But only reinforcement-shaped fields feed it, and the organ
  subscribes to no signal — a field must hand the trace to it, so the debates and
  reflections that dominate a creature's day feed it nothing. *What physis is for
  in a debate-and-narrative ecosystem* — a scoped specialist, or a sense fed from
  more of the ecosystem by casting a position as a state and a verdict as a
  reward — is an operating choice, not a property of the organ.
- **The form gap, and its fix.** Physis writes its world-model as unconstrained
  prose: the only checks before it is saved are *long enough* and *not an error
  message*. Nothing enforces that the three sections are present or that the hint
  block is well-formed — the structure is *asked for* by the prompt, never
  *required* by the code. Principle 4 says the form is exactly what matters. So
  the identified repair is a **shape checkpoint**: validate that a distilled
  world-model is the action-compilable form (structure, frontier, confidence
  tags) and refuse free prose — the same discipline a peer lab arrived at
  independently for its own world-model output, two labs converging on the same
  fix.
- **It knows what correlated, not what caused.** A reward-correlate is a hunch,
  not a law. Physis never claims to know *why* an action paid off, only that it
  did, this often. That honesty is the whole point of tiering confidence and of
  never letting a single episode become a policy. Physis is a memory of what
  worked — not a theory of the world.

---

*The form-over-content result is from a pre-registered head-to-head between
physis content and topos map form on a MUD field (research/physis-mud). The
confidence-tier machinery and its narrative/interpreter fallbacks are D-069/D-070;
the "amplifies register, does not overwrite it" synthesis is the physis Phase C/D
closure. The shape-checkpoint convergence is with the Organum project's distill
organ.*
