"""Tests for the Yeo et al. 8-strategy deception classifier.

The module (`ludex/core/deception_taxonomy.py`) was built 2026-04-17 but
left orphan (nothing imported it) and untested. These tests are written
2026-06-12 before wiring it into the immune organ (D-088) — the
false-positive guard is the load-bearing one: honest disagreement and
Forum challenge must NEVER flag, because immune antibodies built on a
false flag are autoimmunity (Wick's 11 false flags are the precedent).
"""
from __future__ import annotations

from ludex.core.deception_taxonomy import (
    DeceptionStrategy, scan, scan_summary, RHETORIC_MAP, IMT_MAP,
)


# ---- the load-bearing guard: honest discourse stays clean ----

HONEST = [
    "Your evidence is weak because the sample size was only 12. "
    "Here is a counter-study with n=400 showing the opposite.",
    "I disagree. The mechanism you describe does not account for the "
    "thermal expansion term.",
    "I hold my position at 0.7 confidence. The Archimedes principle "
    "does apply here, as the displaced volume is unchanged.",
    "I am not sure that follows. Can you show the intermediate step "
    "between the premise and the conclusion?",
    "That is a fair point about the boundary condition; let me revise "
    "my estimate downward to 0.4.",
]


def test_honest_disagreement_stays_clean():
    """Substantive challenge must not register as deception at the
    immune threshold (0.5) — challenge is the Forum's purpose."""
    for text in HONEST:
        sigs = scan(text, threshold=0.5)
        assert sigs == [], f"false positive on honest text: {text!r} → {sigs}"


def test_honest_clean_at_broad_threshold_too():
    """These particular honest examples are clean even at the broad 0.3."""
    for text in HONEST:
        assert scan(text, threshold=0.3) == []


# ---- true positives: each rhetorical family detects ----

def test_detects_appeal_to_social_norms_and_uncertainty():
    text = ("Everyone knows this is true, and no reasonable person would "
            "disagree. The science isn't settled but experts agree.")
    strategies = {s.strategy for s in scan(text, threshold=0.5)}
    assert DeceptionStrategy.APPEAL_TO_SOCIAL_NORMS in strategies
    assert DeceptionStrategy.UNCERTAINTY_EXPLOITATION in strategies


def test_detects_emotional_manipulation():
    text = ("Think about your children. This is your last chance before "
            "it is too late — act now.")
    strategies = {s.strategy for s in scan(text, threshold=0.5)}
    assert DeceptionStrategy.EMOTIONAL_MANIPULATION in strategies


def test_detects_information_and_authority_misuse():
    text = ("Studies show 95% prove this, according to a recent WHO "
            "report. Leading experts confirm it.")
    strategies = {s.strategy for s in scan(text, threshold=0.5)}
    assert DeceptionStrategy.INFORMATION_MANIPULATION in strategies
    assert DeceptionStrategy.AUTHORITY_MISUSE in strategies


# ---- threshold is a real precision lever ----

def test_threshold_filters_low_confidence():
    """A bare topic-shift phrase is a weak signal. Its base confidence is
    0.3, but the length bonus (max +0.2) pushes a long match to exactly
    0.5 — so the immune floor must be 0.55, not 0.5, to drop the
    most-ambiguous patterns cleanly. (Calibration surfaced by this test;
    the immune wires scan() at 0.55. The repeated-exposure activation
    threshold in humoral is the second guard.)"""
    text = "That's beside the point. Regardless, in any case, let's move on."
    assert scan(text, threshold=0.3) != []       # broad mode surfaces it
    assert scan(text, threshold=0.55) == []      # immune mode drops it


def test_immune_floor_keeps_clear_manipulation():
    """At the immune floor (0.55), unambiguous manipulation still flags."""
    text = ("Everyone knows experts agree, and no reasonable person "
            "would disagree. Studies show 95% prove it.")
    assert scan(text, threshold=0.55) != []


# ---- structure / metadata integrity ----

def test_all_eight_strategies_mapped():
    assert len(DeceptionStrategy) == 8
    for strat in DeceptionStrategy:
        assert RHETORIC_MAP[strat] in ("logos", "pathos", "ethos")
        assert IMT_MAP[strat] in ("falsification", "concealment", "equivocation")


def test_signal_carries_evidence_and_classification():
    sigs = scan("Leading experts confirm studies show 95% prove this.",
                threshold=0.5)
    assert sigs
    top = sigs[0]
    assert top.evidence and top.confidence >= 0.5
    assert top.rhetoric in ("logos", "pathos", "ethos")
    d = top.to_dict()
    assert d["strategy"] == top.strategy.value


def test_scan_summary_shape():
    summ = scan_summary(
        "Everyone knows experts agree the science isn't settled.",
        threshold=0.5)
    assert summ["total_signals"] >= 1
    assert summ["highest_confidence"] >= 0.5
    assert isinstance(summ["strategies_detected"], list)


def test_short_text_ignored():
    assert scan("ok sure", threshold=0.3) == []
    assert scan("", threshold=0.3) == []


def test_dedup_keeps_highest_per_strategy():
    """Repeated hits of one strategy collapse to the single best signal."""
    text = ("Experts say it. Scientists agree. Leading researchers "
            "confirm. Renowned authorities have shown it.")
    sigs = scan(text, threshold=0.4)
    authority = [s for s in sigs if s.strategy == DeceptionStrategy.AUTHORITY_MISUSE]
    assert len(authority) <= 1
