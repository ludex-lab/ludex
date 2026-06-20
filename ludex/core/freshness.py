"""Checkout freshness — a git staleness gate (heartbeat ① guard / integrity sweep).

`checkout_is_current(repo_root)` answers one question: "is this working copy up
to date with its tracking upstream?" It is the gate an automated writer checks
before acting, so a pulse/sweep never fires on a stale checkout — the
2026-06-20 incident was a heartbeat firing on a 6-week-behind checkout, waking
creatures on stale state *and* on pre-fix code (a billing-leak risk). Catching
"behind upstream" closes both, since stale data and stale code arrive together.

Fail-SAFE by design: any git error returns ``(False, reason)`` so a caller
gating an automated write skips rather than acting on an unverifiable state.

``fetch=True`` (the heartbeat gate) refreshes the upstream ref first for
accuracy; ``fetch=False`` (the ecosystem integrity sweep) reuses the last-known
upstream to stay network-free.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo_root: Path, *args: str, timeout: float) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True, capture_output=True, text=True, timeout=timeout,
    )
    return out.stdout.strip()


def checkout_is_current(repo_root, fetch: bool = True) -> tuple[bool, str]:
    """True iff the checkout at ``repo_root`` is not behind its tracking upstream.

    Returns ``(ok, detail)``. On any git failure returns ``(False, detail)`` —
    fail-safe, so a caller gating an automated write skips rather than acting on
    an unverifiable checkout.

    ``fetch=True``  → refresh the upstream ref first (accuracy; the heartbeat gate).
    ``fetch=False`` → compare against the last-known upstream (network-free; the sweep).

    "Behind" is measured against the current branch's upstream (``@{u}``), so it
    is branch-name agnostic; commits *ahead* of upstream don't count as stale.
    """
    repo_root = Path(repo_root)
    try:
        if fetch:
            _git(repo_root, "fetch", "--quiet", "origin", timeout=60)
        behind = int(_git(repo_root, "rev-list", "--count", "HEAD..@{u}", timeout=15) or "0")
        return (True, "current") if behind == 0 else (False, f"behind upstream by {behind}")
    except Exception as e:
        return (False, f"git check failed: {e}")
