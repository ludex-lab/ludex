"""D-071 pillar 2 — protected: bool first-class field + forgetting-pass
skip rule. Covers:

- Memory.is_forgettable composes protected + status correctly
- handle_remember keeps Day 0 identity-default behavior
- handle_protect explicit set/unset
- protect_legacy_identity_memories backfill is correct + idempotent
- Roundtrip through to_dict / from_dict preserves protected (Day 0
  already covers this; re-asserted here for D-071 regression coverage)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ludex.blocks.memory import Memory, MemoryBlock


@pytest.fixture
def mb(tmp_path):
    return MemoryBlock(storage_dir=str(tmp_path / "memory"))


def test_is_forgettable_unprotected_active_returns_true():
    m = Memory(id="m", content="x", memory_type="episodic")
    assert m.is_forgettable is True


def test_is_forgettable_protected_returns_false():
    m = Memory(id="m", content="x", memory_type="identity", protected=True)
    assert m.is_forgettable is False


def test_is_forgettable_archived_returns_false():
    m = Memory(id="m", content="x", memory_type="episodic", status="archived")
    assert m.is_forgettable is False


def test_is_forgettable_deleted_status_returns_false():
    m = Memory(id="m", content="x", memory_type="episodic", status="deleted")
    assert m.is_forgettable is False


def test_handle_remember_identity_defaults_protected_true(mb):
    mid = mb.handle_remember("I am Hearth", memory_type="identity")
    assert mb._memories[mid].protected is True


def test_handle_remember_episodic_defaults_protected_false(mb):
    mid = mb.handle_remember("I played quest 1", memory_type="episodic")
    assert mb._memories[mid].protected is False


def test_handle_protect_sets_flag(mb):
    mid = mb.handle_remember("a bond memory", memory_type="episodic")
    assert mb._memories[mid].protected is False
    assert mb.handle_protect(mid, protected=True) is True
    assert mb._memories[mid].protected is True


def test_handle_protect_unprotect(mb):
    mid = mb.handle_remember("identity entry", memory_type="identity")
    assert mb._memories[mid].protected is True
    assert mb.handle_protect(mid, protected=False) is True
    assert mb._memories[mid].protected is False


def test_handle_protect_idempotent(mb):
    mid = mb.handle_remember("ep", memory_type="episodic")
    assert mb.handle_protect(mid, protected=False) is True  # already false; ok
    assert mb._memories[mid].protected is False


def test_handle_protect_unknown_returns_false(mb):
    assert mb.handle_protect("nonexistent", protected=True) is False


def test_legacy_backfill_promotes_identity_only(mb):
    # Simulate pre-Day-0 state: identity entries with protected=False.
    legacy_id = mb.handle_remember("legacy identity", memory_type="identity")
    mb._memories[legacy_id].protected = False  # force legacy state
    eps_id = mb.handle_remember("legacy episodic", memory_type="episodic")
    new_id = mb.handle_remember("post-Day-0 identity", memory_type="identity")

    n = mb.protect_legacy_identity_memories()
    assert n == 1  # only the forced-legacy identity got promoted
    assert mb._memories[legacy_id].protected is True
    assert mb._memories[eps_id].protected is False
    assert mb._memories[new_id].protected is True


def test_legacy_backfill_idempotent(mb):
    mid = mb.handle_remember("identity", memory_type="identity")
    mb._memories[mid].protected = False
    assert mb.protect_legacy_identity_memories() == 1
    assert mb.protect_legacy_identity_memories() == 0  # second call: nothing to do


def test_to_dict_from_dict_roundtrip_preserves_protected():
    m = Memory(id="m1", content="x", memory_type="identity", protected=True)
    d = m.to_dict()
    assert d["protected"] is True
    m2 = Memory.from_dict(d)
    assert m2.protected is True
    assert m2.is_forgettable is False


def test_legacy_data_without_protected_field_loads_unprotected():
    """Pre-Day-0 JSONL entries had no `protected` field; they must load
    cleanly with protected=False."""
    legacy = {
        "id": "m_old",
        "content": "old entry",
        "memory_type": "identity",
        # no `protected` key
    }
    m = Memory.from_dict(legacy)
    assert m.protected is False
    assert m.is_forgettable is True  # this is exactly what the backfill targets
