"""Topos — contextual / spatial sensory organ (D-060 Phase A).

Topos is the creature's **"where am I"** sense. Digital creatures
don't live in 3D space — they live in a **nested context stack**:

    machine (identified by UUID + user alias)
      └─ habitat (Ray-habitat / Mac-habitat / ...)
           └─ field (Wilderness, Council, Forum, ...)
                └─ phase (field-specific: position / argument / ...)
                     └─ focus (current Opsis/Akoué source if any)

Each layer is a different semantic "where" — activity, home,
substrate, machine — analogous to biological proprioception +
exteroception + allocentric mapping.

Field locality matters in a federated future: a field can live on
the local machine (current case), on another user's machine (MCP-
joined), or at a web URL. Topos exposes the locality so downstream
organs (memory, bond consolidation) tag events accurately.

Identity: `machine_id` (UUID4, auto-assigned on first habitat save
per D-060) + `machine_alias` (user-readable, collision-tolerant).
A computed `machine_short` presents alias with a short UUID prefix
when collisions require disambiguation; for Phase A with a single
machine the alias alone is used.

Context: D-047 named `Topos` as a candidate future AI-native
sensory organ ("sense of field / phase / environmental context;
currently implicit in current_field() contextvar"). D-060 promotes
it formally, introducing the machine layer for federation-readiness.
"""
from __future__ import annotations

import logging
import platform
import socket
from dataclasses import dataclass, field
from typing import Iterable

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)


# Field-locality vocabulary. Phase A ships with the local-host value;
# the enum anticipates federation without implementing it.
LOCAL_HOST = "local_host"
REMOTE_HOST = "remote_host"
WEB = "web"
SHARED_DOC = "shared_doc"


# Last-observed field, per organism name, so Topos can report
# `previous_field` on a transition. Module-level tracker (like the
# sensory_consolidation last-seen store — cheap, process-scoped).
_LAST_FIELD: dict[str, str] = {}


@dataclass(frozen=True)
class ToposReading:
    """A snapshot of the creature's structured spatial context.

    Layers (top-down):
    - **Activity** — field_name / field_kind / field_phase / focus
    - **Locality** — where the field lives (field_locality / field_host
      / my_participation)
    - **Substrate** — the machine (machine_id / machine_alias /
      machine_short / hostname / os_name)
    - **Home** — the habitat (habitat_origin / habitat_dir)
    - **Transition** — previous_field

    Missing sources default to "" rather than None so serialization
    stays flat and summary() can join without type checks.
    """
    # Activity
    field_name: str = ""
    field_kind: str = ""
    field_phase: str = ""
    focus: str = ""

    # Locality (Phase A ships the enum + host; always "local_host")
    field_locality: str = LOCAL_HOST
    field_host: str = ""
    my_participation: str = "owner_local"

    # Substrate (D-060 machine identity)
    machine_id: str = ""
    machine_alias: str = ""
    machine_short: str = ""
    hostname: str = ""
    os_name: str = ""

    # Home (D-052)
    habitat_origin: str = ""
    habitat_dir: str = ""

    # Transition
    previous_field: str = ""

    # Free-form field-specific extras
    field_state: dict = field(default_factory=dict)

    def summary(self) -> str:
        act: list[str] = []
        if self.field_name:
            tag = self.field_name
            if self.field_kind and self.field_kind != self.field_name:
                tag = f"{self.field_name}/{self.field_kind}"
            if self.field_phase:
                tag += f" [{self.field_phase}]"
            act.append(tag)
        if self.focus:
            act.append(f"focus={self.focus}")
        loc_tag = self.machine_short or self.machine_alias or self.hostname or "?"
        if self.habitat_origin:
            loc_tag = f"{self.habitat_origin}@{loc_tag}"
        if self.field_locality != LOCAL_HOST:
            loc_tag = f"{loc_tag}<{self.field_locality}>"
        if not act:
            return loc_tag or "unlocated"
        return f"{' | '.join(act)} @ {loc_tag}"


def _machine_short(alias: str, machine_id: str) -> str:
    """Display-friendly machine tag. Alias when present; alias plus
    4-char UUID prefix when both exist (future collision-aware
    display); UUID prefix alone when alias is empty; empty otherwise."""
    if alias and machine_id:
        return f"{alias} [{machine_id[:4]}]"
    if alias:
        return alias
    if machine_id:
        return machine_id[:8]
    return ""


class ToposBlock(Block):
    """Contextual sensing organ. Aggregator; holds no state.

    provides: sense
    requires: (none; reads config + trace.current_field() defensively)

    D-060 Phase A.
    """

    name = "topos"
    provides = [
        Port("sense", description="Read unified spatial / contextual state"),
    ]
    requires = []

    def __init__(self):
        super().__init__()

    def on_attach(self) -> None:
        pass

    # --- Provides: sense ---

    def handle_sense(self) -> ToposReading:
        cfg_get = self._cfg_getter()
        name = getattr(self._organism, "name", "")

        # Activity layer
        field_name = self._current_field()
        previous_field = _LAST_FIELD.get(name, "")
        if field_name and field_name != previous_field:
            _LAST_FIELD[name] = field_name

        # Substrate layer
        machine_id = cfg_get("machine_id", "")
        machine_alias = cfg_get("machine_alias", "") or _default_alias()
        hostname = _safe_hostname()
        os_name = platform.system() or ""
        machine_short = _machine_short(machine_alias, machine_id)

        reading = ToposReading(
            # Activity — field_kind/phase/focus remain empty in Phase A
            # (no generic introspection API across field types yet).
            field_name=field_name,
            field_kind=self._field_kind(field_name),
            field_phase="",
            focus=self._current_focus(),

            # Locality — Phase A always local; enum shape anticipates
            # federation.
            field_locality=LOCAL_HOST,
            field_host=machine_short,
            my_participation="owner_local",

            # Substrate
            machine_id=machine_id,
            machine_alias=machine_alias,
            machine_short=machine_short,
            hostname=hostname,
            os_name=os_name,

            # Home
            habitat_origin=cfg_get("habitat_origin", ""),
            habitat_dir=cfg_get("habitat_dir", ""),

            # Transition
            previous_field=previous_field,

            field_state={},
        )

        try:
            from ludex.core import trace as _tr
            _tr.emit_topos_sensed(
                self._organism,
                field_name=field_name,
                field_locality=reading.field_locality,
                machine_short=machine_short,
                habitat_origin=reading.habitat_origin,
                summary=reading.summary(),
            )
        except Exception:
            pass

        return reading

    # ------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------

    def _cfg_getter(self):
        """Return a ``get(key, default)`` callable that safely reads from
        the organism config or the habitat block, whichever exposes it."""
        cfg = getattr(self._organism, "config", None)
        habitat_origin = ""
        machine_id = ""
        machine_alias = ""
        habitat_dir = ""
        if cfg is not None and hasattr(cfg, "get"):
            habitat_origin = cfg.get("habitat_origin", "") or cfg.get("origin", "")
            machine_id = cfg.get("machine_id", "")
            machine_alias = cfg.get("machine_alias", "")
            habitat_dir = cfg.get("habitat_dir", "")
        # HabitatConfig fields (if present on the builder) are the
        # authoritative source; Config dict is a mirror set at build.
        # Fall back to organism.config mirror.
        store = {
            "machine_id": machine_id,
            "machine_alias": machine_alias,
            "habitat_dir": habitat_dir,
            "habitat_origin": habitat_origin,
        }
        return store.get

    def _current_field(self) -> str:
        try:
            from ludex.core import trace as _tr
            return _tr.current_field() or ""
        except Exception:
            return ""

    def _field_kind(self, field_name: str) -> str:
        """Heuristic: derive field kind from field_name token when the
        caller followed the convention. E.g. 'ray_council_v1' → 'Council'.

        Only Ludex's OWN internal fields are enumerated here. Kinds for
        external/bridged environments (LxM games, TextArena, …) come from
        the bridge's environment_id, never a hardcoded list in this organ
        — topos stays environment-agnostic (D-089)."""
        if not field_name:
            return ""
        lower = field_name.lower()
        for token, kind in (
            ("council", "Council"), ("forum", "Forum"),
            ("academy", "Academy"), ("agora", "Agora"),
            ("wilderness", "Wilderness"),
        ):
            if token in lower:
                return kind
        return ""

    def _current_focus(self) -> str:
        """Phase A placeholder: the perception-action doc anticipates a
        shared 'current Opsis/Akoue focus' register; none exists yet.
        Returns empty string. Phase B work.
        """
        return ""


def _safe_hostname() -> str:
    try:
        return socket.gethostname() or ""
    except Exception:
        return ""


def _default_alias() -> str:
    return _safe_hostname()
