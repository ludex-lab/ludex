"""Tests for the canonical-host guard (A1, smoke_016 protection).

The guard compares `OrganismConfig.habitat.origin` with the host-level
marker resolved by `get_host_habitat_origin()`. Tests use a tmp file
via `LUDEX_HABITAT_ORIGIN_PATH` so they don't touch the user's real
`~/.ludex/habitat_origin`.
"""

from __future__ import annotations

import os

import pytest

from ludex.core.habitat import (
    HabitatConfig,
    HabitatMismatchError,
    get_host_habitat_origin,
)
from ludex.core.organism_config import OrganismConfig


@pytest.fixture
def isolated_host_origin(tmp_path, monkeypatch):
    """Point both env hooks at an empty tmp file. Tests opt in by writing
    a value or by setting LUDEX_HABITAT_ORIGIN directly."""
    origin_file = tmp_path / "habitat_origin"
    monkeypatch.setenv("LUDEX_HABITAT_ORIGIN_PATH", str(origin_file))
    monkeypatch.delenv("LUDEX_HABITAT_ORIGIN", raising=False)
    return origin_file


def _make_cfg(*, origin: str, persistent: bool) -> OrganismConfig:
    cfg = OrganismConfig.from_preset(preset="minimal", name="Guard")
    cfg.habitat = HabitatConfig(
        mode="local" if persistent else "temporary",
        home_dir="",
        persistent=persistent,
        origin=origin,
    )
    return cfg


def test_guard_skipped_when_persistent_false(isolated_host_origin):
    isolated_host_origin.write_text("Ray-habitat", encoding="utf-8")
    cfg = _make_cfg(origin="Mac-habitat", persistent=False)
    ok, _ = cfg.check_canonical_host()
    assert ok is True


def test_guard_skipped_when_host_origin_unset(isolated_host_origin):
    # File exists empty → host origin = "" → skip.
    cfg = _make_cfg(origin="Mac-habitat", persistent=True)
    ok, _ = cfg.check_canonical_host()
    assert ok is True


def test_guard_skipped_when_creature_origin_unset(isolated_host_origin):
    isolated_host_origin.write_text("Ray-habitat", encoding="utf-8")
    cfg = _make_cfg(origin="", persistent=True)
    ok, _ = cfg.check_canonical_host()
    assert ok is True


def test_guard_passes_on_match(isolated_host_origin):
    isolated_host_origin.write_text("Ray-habitat", encoding="utf-8")
    cfg = _make_cfg(origin="Ray-habitat", persistent=True)
    ok, _ = cfg.check_canonical_host()
    assert ok is True


def test_guard_fires_on_mismatch(isolated_host_origin):
    isolated_host_origin.write_text("Ray-habitat", encoding="utf-8")
    cfg = _make_cfg(origin="Mac-habitat", persistent=True)
    ok, msg = cfg.check_canonical_host()
    assert ok is False
    assert "Mac-habitat" in msg and "Ray-habitat" in msg


def test_env_var_overrides_file(isolated_host_origin, monkeypatch):
    isolated_host_origin.write_text("Ray-habitat", encoding="utf-8")
    monkeypatch.setenv("LUDEX_HABITAT_ORIGIN", "Mac-habitat")
    assert get_host_habitat_origin() == "Mac-habitat"


def test_origin_whitespace_normalized(isolated_host_origin):
    isolated_host_origin.write_text("  Ray-habitat\n", encoding="utf-8")
    cfg = _make_cfg(origin="Ray-habitat", persistent=True)
    ok, _ = cfg.check_canonical_host()
    assert ok is True


def test_build_raises_on_foreign_host(isolated_host_origin, monkeypatch):
    """Full build() path: foreign-host attempt must raise *before* any
    state mutation. session_count stays 0 after the failed call."""
    isolated_host_origin.write_text("Ray-habitat", encoding="utf-8")
    cfg = _make_cfg(origin="Mac-habitat", persistent=True)
    initial_sessions = cfg.session_count
    initial_born = cfg.born_at
    with pytest.raises(HabitatMismatchError):
        cfg.build()
    assert cfg.session_count == initial_sessions
    assert cfg.born_at == initial_born
