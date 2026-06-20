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


# --- Layer 3 seed: sanctioned-automation leases (D-090 Recognition) -----------
# Prevention (above) stops tooling from writing the live creature. Recognition asks
# the complementary question of an *automated writer*: is this scheduler a sanctioned
# part of the ecosystem, or drift? `is_sanctioned(text)` is the v0 of that — a lease
# registry the integrity sweep consults so a known heartbeat reads as SELF, not a
# stray cron. The full write-lease/provenance spine is the later precision layer.

_LEASE_CACHE = None


def _lease_registry_path() -> str:
    # config/ sits at the repo root, a sibling of ludex/ (this file is ludex/core/integrity.py).
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "config", "sanctioned_automation.yaml")


def _load_leases():
    """Sanctioned-automation leases from config/sanctioned_automation.yaml (cached). [] if absent."""
    global _LEASE_CACHE
    if _LEASE_CACHE is not None:
        return _LEASE_CACHE
    leases = []
    try:
        import yaml
        with open(_lease_registry_path(), encoding="utf-8") as f:
            leases = (yaml.safe_load(f) or {}).get("leases") or []
    except Exception:
        leases = []
    _LEASE_CACHE = leases
    return leases


def is_sanctioned(text: str) -> bool:
    """True if `text` — a scheduler's identifier (a cron line, a LaunchAgent label/body, or a
    Windows task name+command) — matches a lease in config/sanctioned_automation.yaml. The
    D-090 Recognition primitive: a leased automation is recognized as SELF (a sanctioned
    writer), so the integrity sweep treats it as known rather than drift. Adding a lease is a
    deliberate caretaker act — sanctioning a writer is JJ's call, never inferred."""
    if not text:
        return False
    t = text.lower()
    for lease in _load_leases():
        for m in (lease.get("match") or []):
            if m and str(m).lower() in t:
                return True
    return False
