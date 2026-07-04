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
    """Contextual sensing organ + spatial memory (cognitive map).

    provides: sense (D-060 Phase A) · observe_place / map_view / frontier
    (memory-systems step 3, 2026-07-04)
    requires: (none; reads config + trace.current_field() defensively)

    The cognitive map is the spatial memory the GIVEN-MAP probe proved causal
    (exact-p=.0096, d=1.61 — structure's FORM moves exploration where content
    could not). The organ is env-agnostic: callers (bridges/fields) parse
    their environment and feed STRUCTURED observations; topos accumulates a
    per-field place graph (`memory/topos/<field>.map.json`, the physis
    per-field-store pattern) and emits a map-shaped, frontier-explicit view —
    the probe-validated format, no prose (Ray reply13: the payoff concentrates
    in the frontier line).
    """

    name = "topos"
    provides = [
        Port("sense", description="Read unified spatial / contextual state"),
        Port("observe_place", description="Record arrival at a place + its exits (builds the place graph)"),
        Port("map_view", description="Compact map-shaped emission: places/exits + explicit frontier"),
        Port("frontier", description="Structured frontier: (place, exit) pairs not yet walked"),
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

    # ------------------------------------------------------------
    # Cognitive map (memory-systems step 3) — place graph store
    # ------------------------------------------------------------
    # Graph shape (memory/topos/<field>.map.json):
    #   {"places": {<name>: {"exits": {<dir>: <dest-or-"?">}, "visited": bool,
    #                        "last_seen": ts}},
    #    "current": <name>, "updated": ts}
    # "?" = an exit seen but never successfully walked — the FRONTIER.

    def _map_path(self, field: str):
        cfg_get = self._cfg_getter()
        habitat = cfg_get("habitat_dir", "")
        if not habitat or not field:
            return None
        from pathlib import Path
        parts = [p for p in field.replace("\\", "/").split("/") if p and p not in (".", "..")]
        return Path(habitat) / "memory" / "topos" / Path(*parts[:-1] or ["."]) / f"{parts[-1]}.map.json"

    def _load_map(self, field: str) -> dict:
        path = self._map_path(field)
        if path is None or not path.exists():
            return {"places": {}, "current": "", "updated": 0.0}
        try:
            import json
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"topos map read failed ({field}): {e}")
            return {"places": {}, "current": "", "updated": 0.0}

    def _save_map(self, field: str, m: dict) -> None:
        path = self._map_path(field)
        if path is None:
            return
        try:
            import json
            import time as _t
            m["updated"] = _t.time()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:
            logger.warning(f"topos map write failed ({field}): {e}")

    def handle_observe_place(self, field: str, place: str, exits: list[str] | None = None,
                             moved_from: str = "", moved_dir: str = "") -> dict:
        """Record arrival at `place` (marks visited, registers its exits) and —
        when the caller reports HOW it got here (moved_from + moved_dir) — bind
        that edge's destination, retiring it from the frontier. Idempotent;
        persists per call. Returns the updated place entry."""
        if not field or not place:
            return {}
        import time as _t
        m = self._load_map(field)
        places = m.setdefault("places", {})
        entry = places.setdefault(place, {"exits": {}, "visited": False, "last_seen": 0.0})
        entry["visited"] = True
        entry["last_seen"] = _t.time()
        for d in exits or []:
            entry["exits"].setdefault(d, "?")          # seen, not yet walked
        if moved_from and moved_dir and moved_from in places:
            places[moved_from]["exits"][moved_dir] = place   # edge bound — off the frontier
        m["current"] = place
        self._save_map(field, m)
        return entry

    def handle_frontier(self, field: str) -> list[tuple[str, str]]:
        """(place, exit-direction) pairs seen but never successfully walked,
        current place first — the next-action signal the probe validated."""
        m = self._load_map(field)
        cur = m.get("current", "")
        out: list[tuple[str, str]] = []
        for name, entry in m.get("places", {}).items():
            for d, dest in (entry.get("exits") or {}).items():
                if dest == "?":
                    out.append((name, d))
        out.sort(key=lambda pd: (pd[0] != cur, pd[0], pd[1]))   # current place's exits first
        return out

    def handle_map_view(self, field: str) -> str:
        """The probe-validated emission: map-shaped, frontier-explicit, no prose
        (reply13: the payoff concentrates in the frontier line). Empty string
        when nothing has been observed yet."""
        m = self._load_map(field)
        places = m.get("places", {})
        if not places:
            return ""
        cur = m.get("current", "")
        lines = [f"[Map] {len(places)} places known · you are at: {cur or '?'}"]
        for name, entry in places.items():
            exits = entry.get("exits") or {}
            if exits:
                ex = " · ".join(f"{d}→{dest if dest != '?' else '?'}" for d, dest in exits.items())
            else:
                ex = "(no exits seen)"
            lines.append(f"- {name}: {ex}")
        here = [d for (p, d) in self.handle_frontier(field) if p == cur]
        if here:
            lines.append(f"Exits you have not walked from where you now stand: {', '.join(here)}")
        elsewhere = [f"{d} from {p}" for (p, d) in self.handle_frontier(field) if p != cur]
        if elsewhere:
            lines.append(f"Unwalked exits elsewhere: {'; '.join(elsewhere)}")
        if not here and not elsewhere:
            lines.append("Frontier: none — every seen exit has been walked.")
        return "\n".join(lines)


def _safe_hostname() -> str:
    try:
        return socket.gethostname() or ""
    except Exception:
        return ""


def _default_alias() -> str:
    return _safe_hostname()
