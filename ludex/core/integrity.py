"""Ecosystem integrity — the ecosystem-level immune system (D-090).

A creature's own immune organ guards its *beliefs*; this guards its persistent
*self* from foreign writes. The lesson that forced it (2026-06-13): an
experiment loaded a LIVE creature by its real path and mutated its store —
caught only by hand. Prompt/discipline breaks under load, so the safety has to
be **structural, not behavioral**.

Layer 1 (here, PREVENTION by construction): `ephemeral_creature(path)` loads a
creature into a throwaway copy. The organism holds NO reference to the real
path, so tooling/experiments *cannot* write to the live creature — the safe
path is made the easy one. This generalizes the manual copy-on-load that
contained the original incident.

Planned (D-090, not yet built): recognition by write-provenance (self/non-self
by origin, blocking unsanctioned writes at the source), repair from full-state
snapshots, and a caretaker integrity sweep that flags foreign artifacts.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager

from ludex.core.organism_config import OrganismConfig


@contextmanager
def ephemeral_creature(path, *, keep_snapshots=False):
    """Yield an ``OrganismConfig`` loaded from a THROWAWAY copy of ``path``.

    Any write during play (spans, world-models, organ state) lands in the temp
    copy and is discarded on exit — the live creature at ``path`` is never
    touched. Snapshots (large, historical, never needed for play) are excluded
    by default; pass ``keep_snapshots=True`` if a tool genuinely needs them.

    Usage::

        with ephemeral_creature(path) as cfg:
            org = cfg.build()
            play_episode(org, bridge, ...)        # writes hit the copy, not the creature
    """
    src = os.path.abspath(path)
    tmp = tempfile.mkdtemp(prefix="ludex_ephemeral_")
    dst = os.path.join(tmp, os.path.basename(src.rstrip(os.sep)) or "creature")
    try:
        ignore = None if keep_snapshots else shutil.ignore_patterns("snapshots")
        shutil.copytree(src, dst, ignore=ignore)
        yield OrganismConfig.load(dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
