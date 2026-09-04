"""Nothing lands in creatures/ without a habitat, and tests never forge there.

PersistTest was deleted twice on 2026-08-05 — once by Ray, once by me — and
came back both times, because test_persistence_e2e forged it into the live
./creatures directory on every suite run. The second resurrection went further
than the first: my own `git add` after a verifying pytest run swept the
freshly re-forged body into the commit that claimed to delete it.

Two properties keep that from recurring, and the second is the one that
generalizes. A creature built by a raw OrganismConfig (as opposed to the forge)
carries no habitat origin, and an originless body is invisible to every
habitat-scoped sweep — care rotation, heartbeat, the cohort census. So it sits
in the roster being counted by nothing. Pinning that every directory in
creatures/ declares an origin catches any future test that writes a body there,
not just this one.
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CREATURES = Path(__file__).resolve().parent.parent / "creatures"


def _roster():
    return sorted(p for p in CREATURES.glob("*/ludex.yaml"))


def test_every_creature_declares_a_habitat_origin():
    """An originless body is in the roster but outside every sweep that cares."""
    orphans = []
    for p in _roster():
        cfg = yaml.safe_load(p.read_text()) or {}
        if not (cfg.get("habitat") or {}).get("origin"):
            orphans.append(p.parent.name)
    assert not orphans, (
        f"creature(s) with no habitat origin: {orphans} — a test forging into "
        f"./creatures is the usual cause; forge into tmp instead"
    )


def test_the_persistence_e2e_does_not_forge_into_the_live_habitat():
    src = (Path(__file__).parent / "test_persistence_e2e.py").read_text()
    assert './creatures/' not in src, \
        "test_persistence_e2e must forge into a tmp root, not ./creatures"
    assert "tempfile" in src, "expected the tmp-root forge"
