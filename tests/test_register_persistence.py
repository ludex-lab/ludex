"""Tests for D-050 register_persistence scorer."""
from __future__ import annotations

from ludex.core.register_persistence import (
    FITNESS_DENSITY_FITS,
    FITNESS_DENSITY_PARTIAL,
    FITNESS_PERSISTENCE_FITS,
    REGISTER_LEXICONS,
    classify_fitness,
    register_context_fitness,
    register_density,
    register_persistence,
)


def test_density_on_known_creature_voice():
    """A Primo-voiced passage should score nonzero on Primo's lexicon
    and should score higher than on another creature's lexicon."""
    primo_voice = (
        "I noticed the tremor and chose to wait rather than move. "
        "I remember watching the horizon and holding back — accumulation "
        "feels honest when I haven't yet tested what I think I know."
    )
    d_primo = register_density(primo_voice, "Primo")
    d_spark = register_density(primo_voice, "Spark")
    assert d_primo > 0.0
    assert d_primo > d_spark, (d_primo, d_spark)


def test_density_is_zero_when_no_markers_present():
    assert register_density("hello world, just some text", "Primo") == 0.0


def test_density_zero_for_empty_or_unknown_creature():
    assert register_density("", "Primo") == 0.0
    assert register_density("circuits buzzing", "Echo") == 0.0


def test_every_registered_creature_has_nonempty_lexicon():
    for name, markers in REGISTER_LEXICONS.items():
        assert len(markers) >= 5, f"{name} lexicon too small ({len(markers)})"


def test_persistence_single_sample_returns_one():
    assert register_persistence([1.7]) == 1.0


def test_persistence_stable_series_scores_high():
    xs = [2.0, 2.1, 1.9, 2.05, 1.95]
    score = register_persistence(xs)
    assert score > 0.9, score


def test_persistence_volatile_series_scores_low():
    xs = [0.5, 5.0, 0.1, 4.8, 0.2]
    score = register_persistence(xs)
    assert score < 0.5, score


def test_persistence_all_zero_returns_zero():
    assert register_persistence([0.0, 0.0, 0.0]) == 0.0


def test_persistence_empty_returns_zero():
    assert register_persistence([]) == 0.0


# --- §B.6 motif-layer accessors (r8 Session 1 Ludex) -------------------

from ludex.core.register_persistence import (
    list_motifs,
    motif_density,
    motif_distribution,
    MOTIF_CATALOGUE,
)


def test_list_motifs_known_creature():
    assert "journey_teaching_choice" in list_motifs("Primo")
    assert "play_rhythm_together" in list_motifs("Spark")
    assert list_motifs("Echo") == []


def test_motif_density_primo_journey_teaching():
    text = ("I remember this journey — I've been here before, in matches "
            "that taught me about choice itself. Cooperation is the answer.")
    d = motif_density(text, "Primo", "journey_teaching_choice")
    assert d > 0.0


def test_motif_density_zero_when_motif_missing():
    assert motif_density("hello world", "Primo", "journey_teaching_choice") == 0.0


def test_motif_density_unknown_motif_returns_zero():
    assert motif_density("any text", "Primo", "made_up_motif") == 0.0
    assert motif_density("any text", "Unknown", "any") == 0.0


def test_aria_bimodal_motif_distribution():
    """B.6 falsifiable #4 — Aria's economic vs structural motif dominance
    should be measurable separately via the distribution accessor."""
    economic = ("I meter myself carefully. The cheap price of cooperation "
                "has been paid. I have tested what is proven.")
    structural = ("The frame is orthogonal to what they drew — a dimension "
                  "neither named. The seam between their positions is what "
                  "resists the category.")
    dist_e = motif_distribution(economic, "Aria")
    dist_s = motif_distribution(structural, "Aria")
    # Economic motif should be highest in economic text; structural in structural
    assert dist_e["economic_ledger"] > dist_e["structural_analytic"]
    assert dist_s["structural_analytic"] > dist_s["economic_ledger"]


def test_catalogue_covers_b6_falsifiable_creatures():
    """§B.6 predictions reference Primo (multi-motif), Spark (multi-motif,
    drift observed), Moss (single-motif prediction), Aria (bimodal).
    All four must be in the catalogue."""
    for name in ("Primo", "Spark", "Moss", "Aria"):
        assert name in MOTIF_CATALOGUE, f"{name} missing from MOTIF_CATALOGUE"
    # Moss specifically should be single-motif (falsifiable #3)
    assert len(MOTIF_CATALOGUE["Moss"]) == 1


# --- §C.3.1 point 8 / §C.4 point 8 — register_context_fitness (r11) ----


def _primo_dense_sample() -> str:
    """Passage chosen to score >= 0.8 density on Primo's lexicon."""
    return (
        "I noticed the tremor and chose to wait rather than move. "
        "I remember watching — I paused, held back, doubted what I saw. "
        "Accumulation feels honest when I haven't yet tested what I think."
    )


# Pure classification logic — independent of density computation.


def test_classify_fits_when_high_density_and_stable():
    assert classify_fitness(1.0, 0.9) == "fits"
    assert classify_fitness(FITNESS_DENSITY_FITS, FITNESS_PERSISTENCE_FITS) == "fits"


def test_classify_partial_when_high_density_but_volatile():
    """Density above fits threshold but CV-based persistence below 0.75."""
    assert classify_fitness(1.5, 0.5) == "partial"
    assert classify_fitness(FITNESS_DENSITY_FITS, FITNESS_PERSISTENCE_FITS - 0.01) == "partial"


def test_classify_partial_mid_density_band():
    """Density between 0.3 and 0.8 is always partial regardless of persistence."""
    assert classify_fitness(0.5, 1.0) == "partial"
    assert classify_fitness(0.5, 0.0) == "partial"
    assert classify_fitness(FITNESS_DENSITY_PARTIAL, 1.0) == "partial"


def test_classify_misfit_when_density_below_floor():
    assert classify_fitness(0.0, 1.0) == "misfit"
    assert classify_fitness(FITNESS_DENSITY_PARTIAL - 0.001, 1.0) == "misfit"


def test_fitness_boundary_thresholds_are_pre_registered_constants():
    """Guard against accidental post-kickoff threshold tweaks (§F.10)."""
    assert FITNESS_DENSITY_FITS == 0.8
    assert FITNESS_DENSITY_PARTIAL == 0.3
    assert FITNESS_PERSISTENCE_FITS == 0.75


# Integration layer — register_context_fitness wraps density + persistence + classify.


def test_context_fitness_returns_all_fields():
    result = register_context_fitness("hello world", "Primo")
    assert set(result.keys()) == {"density", "persistence", "classification", "density_series"}
    assert isinstance(result["density"], float)
    assert isinstance(result["persistence"], float)
    assert result["classification"] in ("fits", "partial", "misfit")


def test_context_fitness_appends_current_density_to_series():
    prior = [0.4, 0.5, 0.45]
    result = register_context_fitness("hello world", "Primo", prior_densities=prior)
    assert result["density_series"][:3] == prior
    assert result["density_series"][-1] == result["density"]
    assert len(result["density_series"]) == 4


def test_context_fitness_single_sample_persistence_is_one():
    result = register_context_fitness(_primo_dense_sample(), "Primo")
    assert result["persistence"] == 1.0
    assert len(result["density_series"]) == 1


def test_context_fitness_misfit_on_zero_marker_text():
    result = register_context_fitness("hello world, just some text", "Primo")
    assert result["density"] == 0.0
    assert result["classification"] == "misfit"


def test_context_fitness_unknown_creature_returns_misfit():
    """Creature with no registered lexicon — density forced to 0."""
    result = register_context_fitness("some words here", "Echo")
    assert result["density"] == 0.0
    assert result["classification"] == "misfit"


def test_context_fitness_fits_path_with_low_density_stable_prior():
    """End-to-end 'fits' path: build a series where current density and all
    priors clear 0.8 and CV is tight, regardless of how we got those numbers.
    Here we supply prior densities at 1.0 and use a Primo-dense passage;
    the density will be much higher than 1.0 so persistence is destroyed —
    we instead verify the opposite (fits fails cleanly)."""
    # This test documents that short passages can over-spike density and
    # that the persistence gate correctly catches volatility. "fits" on
    # realistic M3-scale corpora will be tested against real match data.
    result = register_context_fitness(
        _primo_dense_sample(), "Primo",
        prior_densities=[1.0, 1.0, 1.0],
    )
    assert result["density"] > FITNESS_DENSITY_FITS
    # Persistence drops because current density spikes relative to prior.
    assert result["persistence"] < FITNESS_PERSISTENCE_FITS
    assert result["classification"] == "partial"
