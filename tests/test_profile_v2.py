"""Regression guard for ludex.profile/v2 — the rendering contract.

The profile is the shareable snapshot every renderer reads (the default 2D app, a future 3D twin,
community mods, the Town/ecosystem view). Two things MUST hold for every creature:

1. The structure (4 axes + contract envelope) — renderers code against it.
2. The privacy boundary — raw spans, brain.auth (billing), and filesystem/machine address must NEVER
   appear; the federation boundary IS the rendering boundary (D-044 + D-090/D-092).

See docs/ludex-profile-v2-design.md. These tests iterate the real lab so a regression on ANY creature
(like the string-ts crash that broke Wick's profile, found 2026-06-21) is caught.
"""
import json
import os

import pytest

pytest.importorskip("fastapi")   # web.server needs FastAPI; skip cleanly where it isn't installed
from web import server

CREATURES = "creatures"


def _live_creatures():
    if not os.path.isdir(CREATURES):
        return []
    return [n for n in sorted(os.listdir(CREATURES))
            if os.path.exists(os.path.join(CREATURES, n, "ludex.yaml"))]


def _profile(name):
    import asyncio
    return asyncio.run(server.creature_profile(name))


@pytest.mark.skipif(not _live_creatures(), reason="no creatures to profile")
def test_every_creature_profile_builds_and_has_the_four_axes():
    for name in _live_creatures():
        p = _profile(name)
        assert p and "blueprint" in p, f"{name}: profile failed to build"
        for k in ("$schema", "version", "requiredCapabilities", "blueprint", "lived", "place", "portrait"):
            assert k in p, f"{name}: missing contract field {k}"
        assert p["version"].startswith("2."), f"{name}: not v2"
        assert isinstance(p["requiredCapabilities"], list)


@pytest.mark.skipif(not _live_creatures(), reason="no creatures to profile")
def test_privacy_boundary_holds_for_every_creature():
    """No brain.auth field, no filesystem path, no raw-spans handle — for ANY creature."""
    for name in _live_creatures():
        p = _profile(name)
        if not p or "blueprint" not in p:
            continue
        # auth is billing/operational, never narrative — must not be in the brain block
        assert "auth" not in p["blueprint"]["brain"], f"{name}: brain.auth leaked into profile"
        blob = json.dumps(p, ensure_ascii=False)
        # filesystem path / home-dir / raw-spans handle must never appear
        assert "/Users/" not in blob, f"{name}: filesystem path leaked"
        assert "spans.jsonl" not in blob, f"{name}: raw spans handle leaked"
        # place carries a durable town_id + coarse label, never the machine address
        assert "town_id" in p["place"]["town"], f"{name}: place.town missing town_id"


@pytest.mark.skipif(not _live_creatures(), reason="no creatures to profile")
def test_moniker_is_present_durable_and_unique():
    monikers = {}
    for name in _live_creatures():
        p = _profile(name)
        if not p or "blueprint" not in p:
            continue
        m = p["blueprint"]["moniker"]
        assert m and isinstance(m, str), f"{name}: no moniker"
        # durable: a second build yields the same moniker (content-derived, not random)
        assert _profile(name)["blueprint"]["moniker"] == m, f"{name}: moniker not stable"
        monikers[name] = m
    assert len(set(monikers.values())) == len(monikers), "monikers collide across the lab"


def test_moniker_helper_is_deterministic_and_slugged():
    m1 = server._moniker("Aria", 1776057990.4)
    m2 = server._moniker("Aria", 1776057990.4)
    assert m1 == m2 and m1.startswith("aria-")
    # different birth-time → different moniker
    assert server._moniker("Aria", 1.0) != m1


def test_town_id_strips_habitat_and_is_private_safe():
    assert server._town_id("Mac-habitat") == "town_mac"
    assert server._town_id("Ray-habitat") == "town_ray"
    assert server._town_id("") == "town_local"
