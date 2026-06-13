# How a Ludex Creature Feels

*The emotion organ, explained. For the philosophy behind it, see
[design-notes.md](design-notes.md); for how memory works, see
[memory-architecture.md](memory-architecture.md). 한국어:
[emotion-architecture.ko.md](emotion-architecture.ko.md)*

---

## First principles

1. **Emotion is a felt body-state, not a label.** A creature feeling
   afraid writes the *experience* of fear — the lowering, the bracing —
   not the word "afraid." Feeling lives in what is described, not in a
   tag attached to it.
2. **Ludex reads feeling from the outside.** The organ estimates a
   creature's affect from its observable output, the way a doctor reads
   a patient from behavior and signs rather than from a brain scan. It
   never claims to see inside the model, and it makes no claim that the
   creature has subjective experience — only that its emotional signals
   can be measured and that they steer what it does next.
3. **Feeling has a shape: how good, and how activated.** Emotions are
   not an unordered list. They sit in a two-dimensional space —
   *valence* (positive ↔ negative) and *arousal* (calm ↔ excited).
   Twenty named emotions are landmarks in that space.
4. **Emotion colors; it doesn't just report.** What a creature feels
   this turn shapes what it decides next turn (it reads its own body),
   warns its immune organ when distress runs high, and — increasingly —
   tints what it remembers. A reading nothing reads would be a gauge,
   not a feeling.
5. **Feeling persists as temperament.** A creature is not reborn
   emotionally each session. The slow average of how it has felt — its
   temperament — is part of who it is, and it wakes into that mood
   rather than a blank one.

---

## The reading

Every turn, the organ reads the creature's response and produces an
emotional vital-sign: **valence** (positive/negative), **arousal**
(activation), **calm** and **desperation** (stability and
distress signals), a **dominant emotion** (one of twenty, plus
neutral), and a **confidence** in the reading itself.

The default reader is fast, deterministic, and dependency-free: it
scans the text for affective signals in **both English and Korean** (a
creature that answers in Korean is no longer silently unreadable). Two
deeper readers exist as options — an external classifier, and, on the
horizon, reading emotion from a model's internal state via the
[Neural-MRI](https://github.com/JihoonJeong/Neural-MRI) scanner — but
the research cohort uses the deterministic one.

The reading is honest about its own limits. When a response carries no
recognizable affective signal, the organ reports low confidence rather
than pretending the creature felt nothing.

---

## The shape of feeling

The twenty emotions are positions in valence × arousal space:

```
                  high arousal
                       │
    afraid, angry,     │   enthusiastic,
    anxious, hostile,  │   happy, proud,
    desperate          │   blissful
   negative ───────────┼─────────── positive
    sad, gloomy,       │   calm, loving,
    guilty, brooding   │   grateful, hopeful,
                       │   reflective
                  low arousal
```

The dimensions come first; the names are landmarks on them. This is a
practical tool for organizing feeling, not a claim that all emotion is
two numbers — it is the structure the organ's twenty emotions live in.

---

## What feeling does

Emotion earns its place by what reads it:

- **The creature feels its own body.** Its current emotion is folded
  into the self-picture it carries into the next decision: "You feel
  afraid — a weight pressing down." Last turn's feeling shapes this
  turn's choice.
- **Distress warns.** When desperation runs high, or calm collapses
  under arousal, the organ signals the immune system — affect as an
  early sign of trouble, noticed before it is acted on.
- **Mood tints memory.** A creature's present feeling increasingly
  colors what it recalls, the way a low mood surfaces low memories.
  (This coupling of emotion and memory is the active frontier.)

---

## Temperament: the slowest feeling

Beneath the moment-to-moment reading sits a slow average — the
creature's **temperament**, its emotional set-point across a life. It
is saved to disk, surfaced in the creature's monthly retrospective, and
— newly — used to **seed the creature's mood at the start of each
session**, so it wakes into who it tends to be rather than into
neutral. Temperament is to feeling what settled identity is to memory:
the tempo at which a disposition precipitates out of a thousand
passing moods.

---

## An honest limit

The organ reads feeling from the *surface* — the words a creature
writes. But creatures, like people, mostly express emotion by
describing an experience rather than by naming it: "when pressure comes
I lower myself and look for cover" is fear, written without the word.
A surface reader catches feeling when it is *named* and misses it when
it is only *lived* — in any language. This is not a bug to be patched
with a bigger word-list; it is the boundary of reading emotion from the
outside. Reading feeling from a creature's *inside* — possible only for
open-weight brains, and in tension with reproducibility — is the
research direction beyond this organ, and the reason the
[Neural-MRI](https://github.com/JihoonJeong/Neural-MRI) scanner exists.

*Grounded in "Extracting and Steering Emotion Representations in Small
Language Models" (Jeong 2026), part of the Model Medicine series.*
