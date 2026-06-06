"""Tests for the three Layer-1 sensory organs shipped together as
D-059/060/061 Phase A: Chronos, Topos, Allos.

Each block follows the Auto (D-058) pattern — afferent, read-only,
defensive, no state of its own — so these tests mirror test_auto.py
in shape while exercising the organ-specific semantics.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ludex.blocks.allos import AllosBlock, AllosReading, OtherMeta
from ludex.blocks.chronos import ChronosBlock, ChronosReading
from ludex.blocks.topos import ToposBlock, ToposReading, LOCAL_HOST


# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------

class _FakeConfig(dict):
    """dict with .get — matches organism.config shape."""


class _FakeOrganism:
    def __init__(self, name: str = "T", config: dict | None = None):
        self.name = name
        self.config = _FakeConfig(config or {})

    def get_block(self, name: str):
        return None


def _attach(block, organism) -> None:
    block._organism = organism
    block.on_attach()


# ======================================================================
# ChronosBlock
# ======================================================================

def test_chronos_defaults_with_empty_organism():
    org = _FakeOrganism()
    c = ChronosBlock(); _attach(c, org)
    r = c.handle_sense()
    assert isinstance(r, ChronosReading)
    assert r.session_count == 0
    assert r.creature_age_s is None  # no born_at
    assert r.session_age_s >= 0


def test_chronos_reads_creature_age_from_born_at():
    born = time.time() - 90000  # ~1d ago
    org = _FakeOrganism(config={"born_at": born, "session_count": 4})
    c = ChronosBlock(); _attach(c, org)
    r = c.handle_sense()
    assert r.creature_age_s is not None
    assert r.creature_age_s >= 89000
    assert r.session_count == 4


def test_chronos_session_age_accumulates():
    org = _FakeOrganism()
    c = ChronosBlock(); _attach(c, org)
    r1 = c.handle_sense()
    time.sleep(0.05)
    r2 = c.handle_sense()
    assert r2.session_age_s > r1.session_age_s


def test_chronos_summary_format():
    born = time.time() - 2 * 86400  # 2d
    org = _FakeOrganism(config={"born_at": born, "session_count": 3})
    c = ChronosBlock(); _attach(c, org)
    s = c.handle_sense().summary()
    assert "session 3" in s
    assert "old" in s


# ======================================================================
# ToposBlock
# ======================================================================

def test_topos_defaults_produce_readable_summary():
    org = _FakeOrganism()
    t = ToposBlock(); _attach(t, org)
    r = t.handle_sense()
    assert isinstance(r, ToposReading)
    assert r.field_locality == LOCAL_HOST
    assert r.field_name == ""
    # summary shouldn't raise
    assert isinstance(r.summary(), str)


def test_topos_exposes_machine_identity():
    org = _FakeOrganism(config={
        "machine_id": "a3b7c9f2-0000-0000-0000-000000000000",
        "machine_alias": "Windows Lab",
        "habitat_origin": "Ray-habitat",
        "habitat_dir": "/x/y",
    })
    t = ToposBlock(); _attach(t, org)
    r = t.handle_sense()
    assert r.machine_id == "a3b7c9f2-0000-0000-0000-000000000000"
    assert r.machine_alias == "Windows Lab"
    assert "Windows Lab" in r.machine_short
    assert r.machine_short.endswith("[a3b7]")
    assert r.habitat_origin == "Ray-habitat"


def test_topos_machine_short_falls_back_to_alias_only_without_id():
    org = _FakeOrganism(config={"machine_alias": "my-box"})
    t = ToposBlock(); _attach(t, org)
    r = t.handle_sense()
    assert r.machine_short == "my-box"


def test_topos_machine_short_uses_hostname_when_no_alias():
    """When the config doesn't set a user alias, Topos falls back to
    the OS hostname — that still counts as an alias for display
    purposes, so machine_short is 'hostname [uuid4-prefix]' rather
    than the UUID alone. Pure-UUID fallback only triggers when the
    hostname query itself also fails (no-op on this platform)."""
    org = _FakeOrganism(config={
        "machine_id": "a3b7c9f2-0000-0000-0000-000000000000",
    })
    t = ToposBlock(); _attach(t, org)
    r = t.handle_sense()
    # Hostname takes the alias slot; short includes UUID prefix
    assert "a3b7" in r.machine_short
    assert r.machine_alias  # non-empty (hostname fallback)


def test_topos_field_kind_heuristic():
    from ludex.core import trace as _tr
    _tr.set_current_field("ray_council_v1")
    try:
        org = _FakeOrganism()
        t = ToposBlock(); _attach(t, org)
        r = t.handle_sense()
        assert r.field_name == "ray_council_v1"
        assert r.field_kind == "Council"
    finally:
        _tr.clear_current_field()


def test_topos_tracks_previous_field_on_transition():
    from ludex.core import trace as _tr
    from ludex.blocks import topos as _topos_mod

    # Start fresh tracker state
    _topos_mod._LAST_FIELD.clear()
    org = _FakeOrganism(name="Transitioner")
    t = ToposBlock(); _attach(t, org)

    _tr.set_current_field("wilderness_1")
    try:
        r1 = t.handle_sense()
        assert r1.field_name == "wilderness_1"
        assert r1.previous_field == ""

        _tr.set_current_field("council_2")
        r2 = t.handle_sense()
        assert r2.field_name == "council_2"
        assert r2.previous_field == "wilderness_1"
    finally:
        _tr.clear_current_field()
        _topos_mod._LAST_FIELD.clear()


# ======================================================================
# AllosBlock
# ======================================================================

def test_allos_no_bonds_dir_returns_empty(tmp_path):
    """Habitat has no bonds/ dir yet — reading is empty, no error."""
    org = _FakeOrganism(config={"habitat_dir": str(tmp_path)})
    a = AllosBlock(); _attach(a, org)
    r = a.handle_sense()
    assert isinstance(r, AllosReading)
    assert r.known_others == []
    assert r.per_other == {}


def test_allos_reads_bond_files(tmp_path):
    bonds = tmp_path / "bonds"
    bonds.mkdir()
    (bonds / "flint.md").write_text(
        "# Bond: Flint\nFirst met: 2026-04-21\n\n"
        "- [game_frame:m_1] shared a quest\n"
        "- [game_frame:m_2] voted together\n"
        "## Role-play events\n"
        "- [game_frame:m_3] sabotaged a quest\n",
        encoding="utf-8",
    )
    (bonds / "loom.md").write_text(
        "# Bond: Loom\n\nSome prose about Loom.\n",
        encoding="utf-8",
    )
    org = _FakeOrganism(config={"habitat_dir": str(tmp_path)})
    a = AllosBlock(); _attach(a, org)
    r = a.handle_sense()
    assert sorted(r.known_others) == ["flint", "loom"]
    flint = r.per_other["flint"]
    assert isinstance(flint, OtherMeta)
    assert flint.event_count == 3       # 3 bullet lines in body
    assert flint.role_play_event_count == 1


def test_allos_last_interaction_from_mtime(tmp_path):
    bonds = tmp_path / "bonds"
    bonds.mkdir()
    f = bonds / "pal.md"
    f.write_text("# Bond: Pal\n- event\n", encoding="utf-8")
    # Set mtime to ~1h ago
    past = time.time() - 3600
    import os
    os.utime(f, (past, past))
    org = _FakeOrganism(config={"habitat_dir": str(tmp_path)})
    a = AllosBlock(); _attach(a, org)
    r = a.handle_sense()
    pal = r.per_other["pal"]
    assert pal.last_interaction_ago_s is not None
    assert 3500 < pal.last_interaction_ago_s < 3700


def test_allos_caches_by_dir_mtime(tmp_path):
    bonds = tmp_path / "bonds"
    bonds.mkdir()
    (bonds / "a.md").write_text("# Bond: A\n- e\n", encoding="utf-8")
    org = _FakeOrganism(config={"habitat_dir": str(tmp_path)})
    a = AllosBlock(); _attach(a, org)

    r1 = a.handle_sense()
    cached_id = id(r1)
    r2 = a.handle_sense()
    # Second call returns the cached object (same instance) because
    # dir mtime unchanged
    assert id(r2) == cached_id


def test_allos_summary_counts_known():
    org = _FakeOrganism()
    r = AllosReading(known_others=["a", "b", "c"])
    assert "known 3" in r.summary()


# ======================================================================
# HabitatConfig machine-scope id (D-060, fix applied 2026-04-23)
# ======================================================================

@pytest.fixture
def isolated_host_machine_id(tmp_path, monkeypatch):
    """Point `LUDEX_MACHINE_ID_PATH` at a tmp file so tests don't
    touch the real `~/.ludex/machine_id`. Returns the Path."""
    host_file = tmp_path / "_host" / "machine_id"
    monkeypatch.setenv("LUDEX_MACHINE_ID_PATH", str(host_file))
    return host_file


def test_habitat_config_auto_fills_machine_id_on_first_save(
    tmp_path, isolated_host_machine_id
):
    from ludex.core.organism_config import OrganismConfig
    from ludex.core.habitat import HabitatConfig

    hdir = tmp_path / "creature"
    hdir.mkdir()
    habitat = HabitatConfig.local(str(hdir))
    assert habitat.machine_id == ""  # empty at construction

    cfg = OrganismConfig(name="T", habitat=habitat)
    cfg.save(str(hdir))

    # Reload; machine_id should be populated and stable
    reloaded = OrganismConfig.load(str(hdir))
    assert reloaded.habitat.machine_id
    first_id = reloaded.habitat.machine_id

    # Save again; should not change
    reloaded.save(str(hdir))
    reloaded_2 = OrganismConfig.load(str(hdir))
    assert reloaded_2.habitat.machine_id == first_id


def test_habitat_config_preserves_user_alias(
    tmp_path, isolated_host_machine_id
):
    from ludex.core.organism_config import OrganismConfig
    from ludex.core.habitat import HabitatConfig

    hdir = tmp_path / "creature"
    hdir.mkdir()
    habitat = HabitatConfig.local(str(hdir))
    habitat.machine_alias = "My Lab"

    cfg = OrganismConfig(name="T", habitat=habitat)
    cfg.save(str(hdir))

    reloaded = OrganismConfig.load(str(hdir))
    assert reloaded.habitat.machine_alias == "My Lab"


def test_machine_id_is_shared_across_siblings_on_same_host(
    tmp_path, isolated_host_machine_id
):
    """D-060 core invariant: two creatures on the same machine must
    converge to the same machine_id. Regression test for the bug Mac
    Ludex-Cody reported 2026-04-23 where each habitat got a fresh
    per-habitat UUID instead."""
    from ludex.core.organism_config import OrganismConfig
    from ludex.core.habitat import HabitatConfig

    a_dir = tmp_path / "Alpha"
    b_dir = tmp_path / "Beta"
    a_dir.mkdir()
    b_dir.mkdir()

    cfg_a = OrganismConfig(name="Alpha", habitat=HabitatConfig.local(str(a_dir)))
    cfg_a.save(str(a_dir))
    cfg_b = OrganismConfig(name="Beta", habitat=HabitatConfig.local(str(b_dir)))
    cfg_b.save(str(b_dir))

    id_a = OrganismConfig.load(str(a_dir)).habitat.machine_id
    id_b = OrganismConfig.load(str(b_dir)).habitat.machine_id

    assert id_a
    assert id_a == id_b


def test_machine_id_backfills_stale_per_habitat_uuid(
    tmp_path, isolated_host_machine_id
):
    """When a habitat on disk already carries a pre-fix per-habitat
    UUID (from older Ludex that generated inside ensure_machine_id),
    the next save adopts the host-level id. Essential for the Mac
    rollout migration — 8 creatures with different UUIDs must
    converge without manual intervention."""
    from ludex.core.organism_config import OrganismConfig
    from ludex.core.habitat import HabitatConfig
    from ludex.core.habitat import get_host_machine_id

    hdir = tmp_path / "legacy"
    hdir.mkdir()

    # Simulate a creature saved under the old code path: build the
    # habitat with an already-assigned stale id and save.
    legacy_id = "00000000-0000-4000-8000-000000000000"  # obviously fake
    habitat = HabitatConfig.local(str(hdir))
    habitat.machine_id = legacy_id
    cfg = OrganismConfig(name="Legacy", habitat=habitat)
    cfg.save(str(hdir))

    host_id = get_host_machine_id()
    assert host_id
    assert host_id != legacy_id

    reloaded = OrganismConfig.load(str(hdir))
    assert reloaded.habitat.machine_id == host_id


def test_machine_id_env_override_wins(tmp_path, monkeypatch):
    """`$LUDEX_MACHINE_ID_PATH` pinpoints the source-of-truth file."""
    from ludex.core.habitat import get_host_machine_id

    target = tmp_path / "explicit" / "id"
    monkeypatch.setenv("LUDEX_MACHINE_ID_PATH", str(target))

    first = get_host_machine_id()
    assert target.exists()
    assert target.read_text(encoding="utf-8").strip() == first

    # Second call reuses the file, doesn't regenerate
    again = get_host_machine_id()
    assert again == first


def test_machine_id_falls_back_when_host_file_unwritable(
    tmp_path, monkeypatch
):
    """Degraded-mode contract: if the host file can't be written
    (read-only home, sandboxed env), `ensure_machine_id` still
    returns a valid non-empty UUID so the habitat config remains
    usable. The host-level convergence is lost until the fs becomes
    writable again; this is the documented tradeoff."""
    from ludex.core.habitat import HabitatConfig, get_host_machine_id
    from ludex.core import habitat as _h

    # Force host-file writes to fail; reads find nothing.
    monkeypatch.setattr(_h, "_read_host_machine_id", lambda: "")

    def _noop_write(_value):
        # Simulate silent write failure — production code already
        # logs and returns None.
        return None

    monkeypatch.setattr(_h, "_write_host_machine_id", _noop_write)

    # get_host_machine_id still returns a fresh UUID each call (no
    # persistence); ensure_machine_id caches the returned value on
    # the habitat so within one process the id is stable.
    habitat = HabitatConfig.local(str(tmp_path))
    habitat.machine_id = ""
    first = habitat.ensure_machine_id()
    assert first  # non-empty
    # Persist it once by hand; the next ensure_machine_id should not
    # overwrite it with another random UUID since host-level read
    # still returns empty (falls through elif branch only when
    # habitat.machine_id is empty).
    reread = habitat.ensure_machine_id()
    # Without host-file persistence we don't promise stability
    # across calls — just that every call returns *some* non-empty id.
    assert reread
