"""Hermes — per-PC ecosystem coordinator family (D-070 candidate).

Singleton coordinator at PC level. Phase 1 ships only the
`interpreter` sub-service (narrative ↔ structured form). Other
sub-services (`bridge`, `arbiter`, `mediator`, `curator`,
`diagnostician`) exist as architectural slots — calling them
raises `NotImplementedError` until their phase ships.

Usage:
    from ludex.core.hermes import hermes

    hints = hermes.interpreter.translate(
        source_text=distill_markdown,
        target_schema="policy_hints_yaml",
        field="academy/wilderness",
    )

Configuration override at `~/.ludex/coordinator.yaml`. See
`docs/hermes-coordinator-design.md`.
"""
from __future__ import annotations

from ludex.core.hermes.cascade import (
    DEFAULT_CASCADE,
    HermesBrain,
    discover_cascade,
    select_active_brain,
    status_report,
)
from ludex.core.hermes.interpreter import Interpreter


class _NotYetImplementedSubservice:
    """Placeholder for sub-services scheduled for later phases."""

    def __init__(self, name: str, phase: str):
        self._name = name
        self._phase = phase

    def __getattr__(self, attr: str):
        raise NotImplementedError(
            f"Hermes sub-service '{self._name}' is scheduled for Phase {self._phase}; "
            f"not yet implemented (attempted to access `.{attr}`)."
        )


class Hermes:
    """Singleton coordinator family. Sub-services accessed as attributes."""

    def __init__(self):
        # Phase 1 — interpreter
        self.interpreter = Interpreter()
        # Phase 2-3 — architectural slots, raise on use
        self.bridge = _NotYetImplementedSubservice("bridge", "2")
        self.arbiter = _NotYetImplementedSubservice("arbiter", "2-3")
        self.mediator = _NotYetImplementedSubservice("mediator", "3")
        self.curator = _NotYetImplementedSubservice("curator", "3")
        self.diagnostician = _NotYetImplementedSubservice("diagnostician", "3")

    def status(self) -> str:
        """Discovery status report — what brains are available."""
        return status_report()


# Module-level singleton.
hermes = Hermes()


__all__ = [
    "hermes",
    "Hermes",
    "HermesBrain",
    "DEFAULT_CASCADE",
    "discover_cascade",
    "select_active_brain",
    "status_report",
]
