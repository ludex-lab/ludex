"""Brain class taxonomy + OrganismConfig.brain_class wiring.

Surfaced 2026-05-01 from Wick distill_dead investigation: brain
identity (provider + model) groups into narrative / structured /
hybrid / unknown classes that govern *what fields a creature can run*.
"""

from __future__ import annotations

import pytest

from ludex.core.brain_class import (
    BRAIN_CLASSES,
    HYBRID,
    NARRATIVE,
    STRUCTURED,
    UNKNOWN,
    classify_brain,
)
from ludex.core.organism_config import OrganismConfig


# --- classify_brain ---


def test_gemini_cli_is_narrative():
    assert classify_brain("gemini_cli", "gemini-3.1-pro-preview") == NARRATIVE
    # Any model under gemini_cli routes narrative regardless of model
    assert classify_brain("gemini_cli", "") == NARRATIVE


def test_opus_is_hybrid():
    assert classify_brain("claude_cli", "claude-opus-4-7") == HYBRID
    assert classify_brain("claude_sdk", "claude-opus-4-7") == HYBRID


def test_sonnet_4_6_is_hybrid():
    assert classify_brain("claude_cli", "claude-sonnet-4-6") == HYBRID


def test_sonnet_5_is_hybrid():
    # New frontier Sonnet (2026-07-01). Frontier hybrid, like sonnet-4-6/opus.
    # Guards against the "sonnet-5" substring colliding with sonnet-4-5 (structured):
    # test_sonnet_4_5_is_structured must still pass.
    assert classify_brain("claude_cli", "claude-sonnet-5") == HYBRID


def test_gpt_5_5_is_hybrid():
    assert classify_brain("codex_cli", "gpt-5.5") == HYBRID


def test_haiku_is_structured():
    assert classify_brain("claude_cli", "claude-haiku-4-5") == STRUCTURED


def test_sonnet_4_5_is_structured():
    assert classify_brain("claude_cli", "claude-sonnet-4-5") == STRUCTURED


def test_ollama_is_structured():
    assert classify_brain("ollama", "qwen3:8b") == STRUCTURED
    assert classify_brain("ollama", "exaone3.5:7.8b") == STRUCTURED
    assert classify_brain("ollama", "qwen3.5:4b") == STRUCTURED


def test_unknown_provider_is_unknown():
    assert classify_brain("custom_brain", "x") == UNKNOWN


def test_classes_constant_is_complete():
    """All four classes are present, no duplicates."""
    assert set(BRAIN_CLASSES) == {NARRATIVE, STRUCTURED, HYBRID, UNKNOWN}


# --- OrganismConfig.brain_class wiring ---


def test_organism_brain_class_derives_from_brain():
    cfg = OrganismConfig(brain={"provider": "ollama", "model": "qwen3:8b"})
    assert cfg.brain_class == STRUCTURED


def test_organism_brain_class_explicit_overrides_derivation():
    """Caretaker can pin `brain.class` in ludex.yaml; explicit beats
    auto-classification."""
    cfg = OrganismConfig(brain={
        "provider": "ollama", "model": "qwen3:8b", "class": "narrative",
    })
    assert cfg.brain_class == NARRATIVE


def test_organism_brain_class_for_gemini_creature():
    """Wick-shape config: gemini-cli + narrative-only."""
    cfg = OrganismConfig(brain={
        "provider": "gemini_cli", "model": "gemini-3.1-pro-preview",
    })
    assert cfg.brain_class == NARRATIVE


def test_organism_brain_class_unknown_safe_default():
    """Empty brain dict shouldn't blow up — returns UNKNOWN."""
    cfg = OrganismConfig(brain={})
    assert cfg.brain_class == UNKNOWN


def test_organism_brain_class_loads_from_yaml(tmp_path):
    """ludex.yaml round-trip preserves an explicit `class` override."""
    import yaml
    cdir = tmp_path / "C"
    cdir.mkdir()
    (cdir / "ludex.yaml").write_text(yaml.safe_dump({
        "name": "C",
        "brain": {"provider": "ollama", "model": "qwen3:8b", "class": "hybrid"},
        "habitat": {"mode": "temporary"},
    }), encoding="utf-8")
    cfg = OrganismConfig.load(str(cdir))
    assert cfg.brain_class == HYBRID
