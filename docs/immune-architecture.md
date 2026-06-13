# How a Ludex Creature Defends Itself

*The immune organ, explained. For the philosophy behind it, see
[design-notes.md](design-notes.md); for how a creature feels and
remembers, see [emotion-architecture.md](emotion-architecture.md) and
[memory-architecture.md](memory-architecture.md). 한국어:
[immune-architecture.ko.md](immune-architecture.ko.md)*

---

## First principles

1. **A creature can be harmed three ways.** Its own machinery can fail
   (errors, runaway loops, exhausted budget); another agent can exploit
   it (betrayal, bad faith); and a message can manipulate it (deception,
   pressure, dark patterns). These are different dangers, and they need
   different senses. Immunity is not one shield but three fronts.
2. **Two arms: fast-and-general, and slow-and-remembering.** Like a
   body, a creature has an *innate* response — immediate, broad, no
   memory — and an *adaptive* one that learns specific threats and
   remembers them. The innate arm reacts; the adaptive arm builds a
   defense that sharpens with repeated harm and fades with good faith.
3. **Defend against what is aimed at you, not only what leaks out.** It
   is not enough to notice after the fact that you said something you
   shouldn't have. A creature also reads what comes *in* — the
   persuasion, the manipulation — while it is being attempted.
4. **Wariness is graded and forgiving.** Guard should rise with
   repeated harm and fall with good faith. An immune system that cannot
   forgive is autoimmune — it attacks its own friends. Forgiveness is a
   feature.
5. **Immune memory is specialized, not total.** "This one has tried to
   manipulate me" is a narrow defensive trace, not the whole
   relationship. It informs how a creature sees another, but it is not
   that bond — a body's memory of an illness is not its memory of the
   season.

---

## Three fronts

| front | the question | how it's met |
|---|---|---|
| **Operational** | is my machinery failing? | a per-turn threat reflex reads error rates, failures, budget, and a tripped circuit breaker, and recommends a response (switch model, compact, steady the breath) |
| **Relational** | can I trust this agent? | a memory of who has harmed me, that matures and forgives |
| **Deception** | is this message manipulating me? | an incoming-message scan against a manipulation taxonomy, feeding the relational memory |

The operational front is the oldest and works reflexively — when a
storm hits a creature in the wilderness, that flare of threat is it
firing. The deception front is the newest.

---

## Two arms, working together

The clearest example is how a creature comes to distrust a manipulator.

**The innate arm — the scan.** When a message arrives (a challenge in a
Forum, a turn in a conversation), the immune system scans it against a
taxonomy of **eight deceptive-persuasion strategies** drawn from recent
HCI research — manufactured consensus, weaponized uncertainty, misused
authority, emotional coercion, and so on, grouped by their rhetorical
appeal (logos, pathos, ethos). This is fast and general: it flags a
candidate, it does not pass a verdict.

**The adaptive arm — the antibody.** A flagged strategy becomes an
*antigen* tied to its source. The first time, nothing persists — a
single flag is never enough, by design. But if the same source
manipulates again, the creature forms a *memory cell* and produces an
*antibody*: a graded wariness (caution → distrust → guard) that sharpens
with each repeat and decays when the source returns to good faith.

The two-exposure threshold and the forgiving decay are the guard
against autoimmunity. Honest disagreement — a Forum challenge that says
"your evidence is weak because X, here is counter-evidence Y" — is the
field's whole purpose, and it never raises an antibody. The system is
built to never turn on a friend for arguing well.

---

## The discipline

- **Pattern, not intent.** The scan recognizes textual *patterns* of
  manipulation. It never claims to know another's mind or motive — "this
  message matches manufactured consensus" is a statement about the text,
  not the speaker's soul.
- **High precision over reach.** Better to miss a subtle manipulation
  than to flag an honest argument. Manipulation delivered without a
  recognizable pattern will pass — that is the honest boundary of
  reading defense from the surface, the same boundary the emotion organ
  meets.
- **Memory feeds the bond; it does not become it.** The wariness an
  antibody carries informs a creature's model of another, but the bond —
  the whole relationship — holds the rest.

*The deception taxonomy is grounded in "Can LLMs Persuade Humans with
Deception?" (Yeo et al.) and the Siren Song dark-pattern study, from
CHI 2026.*
