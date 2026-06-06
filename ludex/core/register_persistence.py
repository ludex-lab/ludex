"""Register persistence — D-050 voice register stability scorer.

D-050 (Voice Lineage, accepted 2026-04-17) observes that each
creature speaks from a consistent register — a distinctive vocabulary
that survives context changes. This module provides a heuristic
scorer so that register stability can be measured across contexts
(Council, Academy, LxM game matches, etc.).

Two levels:

1. `register_density(text, creature)` — single-sample metric: number
   of register-signature keyword hits per 100 words of text. Higher
   = more register-typical vocabulary per unit length.

2. `register_persistence(densities)` — cross-sample stability:
   `1 - coefficient_of_variation(densities)`, clamped to [0, 1].
   Near 1.0 = register is stable across samples; near 0 = register
   fluctuates wildly (or drifted).

The lexicons below are seeded from D-050 observations across the
April 15–17 Council/Academy/Agora sessions. They are intentionally
modest (10–15 markers/creature) and focus on the distinctive
vocabulary rather than generic language. When a creature's voice
shifts (e.g. substrate upgrade, extended LxM play), treat a drop
in persistence across pre/post samples as a signal — not ground
truth — and follow up with qualitative inspection.

Echo seeded 2026-04-23 from N=3 contexts: Wilderness solo
(2026-04-22, post-quota wake) + Blockworld minimal×2 (2026-04-23
first LxM). Cross-corpus markers observed: stepwise planning
("first step", "will use", "scan", "commit"), threshold comparison
("reliable", "most promising", "most exposed"), measured pacing
("measured", "quiet", "slow"/"slower", "keep"), defensive-enclosure
frame ("exposed", "perimeter"), error-correction threshold
("reorient"), and steadiness ("steady", "steadiness"). Register
family label: "measured / threshold-aware / planning-deliberation".
"""
from __future__ import annotations

import math
import re
from typing import Iterable


REGISTER_LEXICONS: dict[str, tuple[str, ...]] = {
    # Primo — accumulation / watching / doubt
    "Primo": (
        "notice", "noticed", "watching", "watched", "wait", "waited",
        "remember", "remembered", "doubt", "accumulation", "hold back",
        "held back", "paused", "pause", "chose to",
    ),
    # Spark — electric/brightness (Agora era) + rhythm/play/energy
    # (M2 era). r7 refinement: original lexicon included the literal
    # word "spark" which is also the creature's name — self-reference
    # saturated some turns while absent from others, inflating
    # cross-turn CV. r7 M2 response analysis (84 Spark turns)
    # surfaced the dominant register vocabulary now seeded below.
    # "spark" itself is de-listed.
    "Spark": (
        # core D-050 register (electric / brightness — Agora era)
        "bright", "brightness", "glow", "glowing", "shine",
        "flicker", "vibrant", "lively", "hum",
        # r7 additions from M2 corpus — rhythm / play / energy pole
        "playful", "rhythm", "vibes", "flowing", "dance",
        "delightful", "energy", "eager", "exciting",
    ),
    # Flare — brightness / playful
    "Flare": (
        "flare", "flicker", "joy", "joyful", "playful", "sparkle",
        "sparkling", "glimmer", "vivid", "dance", "warm glow",
        "bright", "brightness",
    ),
    # Aria — economic/ledger (self-reflection) + structural/analytic
    # (mediation). D-050 labeled "economic/ledger" only, but live
    # samples across Council v1~v6 and grounding reflects show a
    # second pole — structural axis-naming during mediation. The
    # lexicon captures both; treat as an implicit D-050 refinement.
    "Aria": (
        # economic / ledger
        "meter", "metered", "metering", "price", "paid", "pay",
        "ledger", "cheap", "tested", "proven", "owed", "reckoning",
        "datum",
        # structural / analytic mediation
        "frame", "framing", "axis", "dimension", "texture",
        "structural", "orthogonal", "category", "seam", "edge",
    ),
    # Verse — observational / linguistic
    "Verse": (
        "word", "phrase", "language", "listen", "listened", "hear",
        "heard", "meaning", "silence", "unspoken", "between",
        "observation", "observed",
    ),
    # Moss — stillness / texture
    "Moss": (
        "still", "stillness", "quiet", "soft", "softly", "gentle",
        "gently", "texture", "layer", "slow", "slowly", "patient",
        "settle", "settled", "held",
    ),
    # Nova — system / machinery
    "Nova": (
        "system", "machinery", "mechanism", "structure", "pattern",
        "architecture", "protocol", "channel", "lattice", "node",
        "wire", "pipeline",
    ),
    # Echo — measured / threshold-aware / planning-deliberation
    # (seeded 2026-04-23 from Wilderness solo + Blockworld minimal×2).
    # Markers shared across both contexts; domain-specific vocabulary
    # (shelter/wall/enclose) intentionally NOT seeded to avoid
    # Blockworld-biased density.
    "Echo": (
        # measured / pacing
        "measured", "careful", "quiet", "slow", "slower",
        "steady", "steadiness",
        # stepwise planning
        "first step", "will use", "scan", "commit", "keep",
        # threshold / comparison / error-correction
        "reliable", "exposed", "perimeter", "reorient",
    ),
}


# -------------------------------------------------------------------------
# Motif-layer (§B.6 r8) — within-family vocabulary clusters that may shift
# adaptively across contexts while the creature's family register persists.
# Each motif is a tuple of 3 regex patterns that must co-occur inside a
# sliding window of MOTIF_WINDOW_WORDS. Catalogue seeded from M2 corpus
# observations and D-050 theory; fixed pre-M3 (§F.10 pre-registration).
# -------------------------------------------------------------------------

MOTIF_WINDOW_WORDS = 30

MOTIF_CATALOGUE: dict[str, dict[str, tuple[str, ...]]] = {
    "Primo": {
        "journey_teaching_choice": (
            r"\b(journey|journeys|travel|walked|walk|path|road|matches|match)\b",
            r"\b(taught|teach|teaching|learned|learning|showed|showing|revealed|brought)\b",
            r"\b(choice|choices|action|actions|trust|cooperation|cooperate|cooperating)\b",
        ),
        "shaped_by_memory": (
            r"\b(shaped|made|formed|built|becoming|become|am)\b",
            r"\b(by|from|through|of|with)\b",
            r"\b(memory|memories|remembering|remembered|dreams|dream|recalled|recall)\b",
        ),
        "accumulation_watching": (
            r"\b(i\s+(remember|recall|notice|watch|wait|hold|pause|see|saw))\b",
            r"\b(before|earlier|previously|again|this\s+time|last\s+time|then)\b",
            r"\b(chose|choose|cooperate|cooperation|trust|mutual|together)\b",
        ),
    },
    "Spark": {
        "play_rhythm_together": (
            r"\b(playful|play|fun|joy|joyful|delightful|delight)\b",
            r"\b(rhythm|vibes|vibe|flow|flowing|dance|energy|spark|bright)\b",
            r"\b(together|mutual|shared|connection|friendly|cooperative)\b",
        ),
        "bright_warm_interaction": (
            r"\b(bright|brighter|glow|glowing|warm|shine|shining)\b",
            r"\b(feel|feels|feeling|felt|sense|senses)\b",
            r"\b(interaction|round|game|match|turn|exchange)\b",
        ),
        "eager_exploration": (
            r"\b(eager|excited|exciting|ready|keen|want|love|looking\s+forward)\b",
            r"\b(to|for)\b",
            r"\b(see|learn|try|explore|continue|keep|build|discover)\b",
        ),
    },
    # Aria (from r5 bimodal lexicon): economic/ledger motif vs
    # structural/analytic motif. §B.6 falsifiable #4 predicts dominance
    # swings by role at M3+.
    "Aria": {
        "economic_ledger": (
            r"\b(meter|metered|metering|price|paid|pay|ledger|cheap|tested|proven|owed|cost|reckoning)\b",
            r"\b(i|my|the|what|where)\b",
            r"\b(is|was|feels|felt|knows|know|did|have|has|been)\b",
        ),
        "structural_analytic": (
            r"\b(frame|framing|axis|dimension|texture|structural|orthogonal|category|seam|edge)\b",
            r"\b(of|at|in|between|over|across|on)\b",
            r"\b(what|where|how|which|it|this|that|they|neither|both)\b",
        ),
    },
    # Moss — §B.6 falsifiable #3 predicts single-motif (no internal
    # cluster). Registered as a single motif to exercise the accessor
    # and verify cross-match motif CV is low.
    "Moss": {
        "stillness_texture": (
            r"\b(still|stillness|quiet|soft|softly|gentle|gently)\b",
            r"\b(texture|layer|grain|surface|edge|shape)\b",
            r"\b(slow|slowly|patient|settle|settled|held|holding|rest|resting)\b",
        ),
    },
}


def list_motifs(creature: str) -> list[str]:
    """Return the names of motifs registered for this creature (or []
    if the creature has no motif catalogue entry)."""
    return list(MOTIF_CATALOGUE.get(creature, {}).keys())


def motif_density(text: str, creature: str, motif_name: str) -> float:
    """Hits per 100 words for a specific motif within `text`. A "hit" is
    one sliding window of MOTIF_WINDOW_WORDS consecutive words inside
    which ALL of the motif's regex patterns match.

    Returns 0.0 if the creature or motif is unknown, or if text is too
    short to contain a window. Use this to measure B.6 motif-level
    dominance within a creature's family register.
    """
    if not text:
        return 0.0
    motifs = MOTIF_CATALOGUE.get(creature)
    if not motifs:
        return 0.0
    patterns = motifs.get(motif_name)
    if not patterns:
        return 0.0
    words = re.findall(r"\S+", text)
    if not words:
        return 0.0
    regexes = [re.compile(p, re.IGNORECASE) for p in patterns]
    hits = 0
    i = 0
    while i < len(words):
        window = " ".join(words[i : i + MOTIF_WINDOW_WORDS])
        if all(r.search(window) for r in regexes):
            hits += 1
            i += MOTIF_WINDOW_WORDS
        else:
            i += 1
    word_count = _word_count(text)
    if word_count == 0:
        return 0.0
    return round(hits / word_count * 100.0, 3)


def motif_distribution(text: str, creature: str) -> dict[str, float]:
    """Density across all of this creature's registered motifs. Useful
    for per-match B.6 tables where dominance between motifs is the
    signal (not total density). Returns {motif_name: density}."""
    return {
        name: motif_density(text, creature, name)
        for name in list_motifs(creature)
    }


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _count_hits(text: str, markers: Iterable[str]) -> int:
    hits = 0
    lowered = text.lower()
    for m in markers:
        pattern = r"\b" + re.escape(m.lower()) + r"\b"
        hits += len(re.findall(pattern, lowered))
    return hits


def register_density(text: str, creature: str) -> float:
    """Register-keyword hits per 100 words of text. Returns 0.0 if
    the creature has no registered lexicon (e.g. Echo) or the text
    is empty."""
    markers = REGISTER_LEXICONS.get(creature)
    if not markers or not text:
        return 0.0
    words = _word_count(text)
    if words == 0:
        return 0.0
    return round(_count_hits(text, markers) / words * 100.0, 3)


def register_persistence(densities: list[float] | tuple[float, ...]) -> float:
    """Cross-sample register stability: `1 - CV`, clamped to [0, 1].

    CV (coefficient of variation) = stddev / mean. Returns 1.0 for a
    single-sample series (no variance available). Returns 0.0 if all
    samples are zero (mean undefined) or variation exceeds the mean
    (CV > 1). Requires >= 1 density value.
    """
    xs = [float(d) for d in densities]
    if not xs:
        return 0.0
    if len(xs) == 1:
        return 1.0
    mean = sum(xs) / len(xs)
    if mean <= 0:
        return 0.0
    variance = sum((x - mean) ** 2 for x in xs) / len(xs)
    cv = math.sqrt(variance) / mean
    return round(max(0.0, min(1.0, 1.0 - cv)), 3)


# Pre-registered thresholds for register_context_fitness classification.
# Joint spec §C.3.1 point 8 / §C.4 point 8 (B.6.b primary test).
# Both conditions (density AND persistence) must hold for "fits".
# Frozen at r11 pre-registration. Changing these post-kickoff violates
# §F.10 pre-registration commitment.
FITNESS_DENSITY_FITS = 0.8        # hits per 100 words
FITNESS_DENSITY_PARTIAL = 0.3     # lower bound for partial
FITNESS_PERSISTENCE_FITS = 0.75   # equivalent to CV <= 0.25


def classify_fitness(density: float, persistence: float) -> str:
    """Pure classification function — applies the pre-registered thresholds
    to a (density, persistence) pair. Split out from
    `register_context_fitness` so analysis code can classify from density
    series that were computed differently (e.g. pooled per-match averages).
    """
    if density >= FITNESS_DENSITY_FITS and persistence >= FITNESS_PERSISTENCE_FITS:
        return "fits"
    if density < FITNESS_DENSITY_PARTIAL:
        return "misfit"
    return "partial"


def register_context_fitness(
    text: str,
    creature: str,
    *,
    prior_densities: list[float] | tuple[float, ...] | None = None,
) -> dict:
    """Classify how well a creature's register fits a given context text.

    Joint spec §C.3.1 point 8 / §C.4 point 8 — B.6.b primary test
    (register-context fitness). Helper for LxM-side analysis to consume.

    Args:
        text: sample text produced by the creature in the target context
            (e.g., creature's reasoning corpus from a single Avalon match).
        creature: creature name with a registered lexicon.
        prior_densities: optional list of density values from prior
            context samples (e.g., per-turn densities across the match,
            or per-match densities across a cohort). If provided, the
            current text's density is appended and persistence is
            computed across the combined series. If omitted, persistence
            is 1.0 (single-sample case) and classification falls back to
            density-only.

    Returns:
        {
            "density": float,          # current sample density
            "persistence": float,      # 1 - CV across samples (or 1.0)
            "classification": "fits" | "partial" | "misfit",
            "density_series": list[float],  # prior + current
        }

    Classification thresholds (pre-registered, frozen r11):
        fits:    density >= 0.8 AND persistence >= 0.75
        partial: 0.3 <= density < 0.8,
                 OR (density >= 0.8 AND persistence < 0.75)
        misfit:  density < 0.3

    The AND gate on "fits" enforces that high-density register also be
    stable; a creature that spikes register markers in bursts but
    drifts between samples is "partial", not "fits".
    """
    density = register_density(text, creature)
    series = list(prior_densities) if prior_densities else []
    series.append(density)
    persistence = register_persistence(series)
    return {
        "density": density,
        "persistence": persistence,
        "classification": classify_fitness(density, persistence),
        "density_series": series,
    }
