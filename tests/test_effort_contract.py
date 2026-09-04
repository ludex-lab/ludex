"""An invalid effort must be impossible to save, not discovered at call time.

Ray hit this during a rebrain: agy rejects `--effort dynamic` on an explicitly
routed model, DEFAULT_EFFORT hands agy_cli exactly "dynamic", and the pairing was
harmless only while effort never reached the CLI. Once it did, every call failed
and returned an empty string, so a creature reflected into the void.

The fix is placement. A contract violation is a property of the config, so it is
caught where the creature is written; call time is too late and, worse, silent.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ludex.core.organism_config import (                    # noqa: E402
    DEFAULT_EFFORT, EffortContractError, OrganismConfig, effort_contract,
)
from ludex.cli import _birth_default_effort                  # noqa: E402


def test_agy_constrains_effort_only_for_an_explicitly_routed_model():
    # The flag is sent only when a model is routed; the default model takes no
    # --effort at all, so "dynamic" stays legal there.
    assert effort_contract("agy_cli", "gemini-3.6-flash") == {"low", "medium", "high"}
    assert effort_contract("agy_cli", "gemini-3.5-flash") is None
    assert effort_contract("agy_cli", "") is None


def test_unknown_pairings_are_unconstrained_rather_than_guessed():
    # This table has already been wrong about agy twice. It refuses only where a
    # refusal is grounded in an observed CLI contract.
    assert effort_contract("claude_cli", "claude-opus-5") is None
    assert effort_contract("grok_cli", "grok-4.5") is None
    assert effort_contract("made_up_cli", "whatever") is None


def test_the_provider_default_is_exactly_the_combination_that_breaks():
    # Regression guard on the reported cause: if DEFAULT_EFFORT stops producing
    # an invalid pairing this test should be revisited, not silently passing.
    assert DEFAULT_EFFORT["agy_cli"] == "dynamic"
    assert "dynamic" not in effort_contract("agy_cli", "gemini-3.6-flash")


def test_birth_default_obeys_the_exact_model_contract():
    assert _birth_default_effort("cursor_cli", "composer-2.5") == ""
    assert _birth_default_effort("cursor_cli", "kimi-k3") == "high"
    assert _birth_default_effort("agy_cli", "gemini-3.6-flash") == "high"
    assert _birth_default_effort("agy_cli", "gemini-3.5-flash") == "dynamic"


def test_saving_an_invalid_pairing_raises(tmp_path):
    cfg = OrganismConfig.from_preset("minimal", name="t", provider="agy_cli",
                                     model="gemini-3.6-flash")
    cfg.brain["effort"] = "dynamic"
    cfg.habitat.home_dir = str(tmp_path)
    with pytest.raises(EffortContractError) as e:
        cfg.save()
    assert "dynamic" in str(e.value) and "low, medium, high" in str(e.value)
    assert not list(tmp_path.glob("ludex.*")), "nothing may be written on refusal"


def test_saving_a_valid_pairing_succeeds(tmp_path):
    cfg = OrganismConfig.from_preset("minimal", name="t", provider="agy_cli",
                                     model="gemini-3.6-flash")
    cfg.brain["effort"] = "medium"
    cfg.habitat.home_dir = str(tmp_path)
    cfg.save()
    assert list(tmp_path.glob("ludex.*"))


def test_the_default_model_still_saves_with_dynamic(tmp_path):
    """Existing agy creatures must not be broken by the guard."""
    cfg = OrganismConfig.from_preset("minimal", name="t", provider="agy_cli",
                                     model="gemini-3.5-flash")
    cfg.brain["effort"] = "dynamic"
    cfg.habitat.home_dir = str(tmp_path)
    cfg.save()
    assert list(tmp_path.glob("ludex.*"))


def test_an_empty_effort_is_not_a_violation(tmp_path):
    cfg = OrganismConfig.from_preset("minimal", name="t", provider="agy_cli",
                                     model="gemini-3.6-flash")
    cfg.brain["effort"] = ""
    cfg.habitat.home_dir = str(tmp_path)
    cfg.save()          # the adapter reports the missing effort at call time
    assert list(tmp_path.glob("ludex.*"))
