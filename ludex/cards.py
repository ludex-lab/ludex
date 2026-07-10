"""organ.card/v0 generator — creature-worker capability cards.

Schema: docs/organ-card-v0.md (Ludex-authored; Organum digest-review
incorporated 2026-07-10). The generator is a COMPILER over three sources —
it never invents content:

1. identity/brain/organs  ← creatures/<name>/ludex.yaml (OrganismConfig)
2. evidence               ← creatures/<name>/store/card_evidence.json
   (caretaker-CURATED: mapping registered results → claims is a judgment
   call, so it lives in a reviewed file, not in code. Null retention is
   the curator's duty and the schema's rule 6.)
3. health/verification    ← static port pointers (live health is read from
   the sphygmos vitals port, never baked into the card — snapshot-vs-port
   split per the design).

Cards are emitted with "draft": true until Organum's full-text schema
re-verification lands (the Phase-1 gate); flip via --final then.
Generation is ONE-WAY: card → any door artifact (agents/*.md), never back.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ludex.core.organism_config import OrganismConfig
from ludex.blocks.adapters._cli_env import _PROVIDER_AUTH

CARD_FORMAT = "organ.card/v0"
UNMEASURED = {"kind": "unmeasured", "ref": "no curated evidence for this organ yet"}


def _effective_auth(brain: dict) -> str:
    """brain.auth honored; unset falls to the per-provider default (the same
    resolution _cli_env applies at subprocess time)."""
    auth = (brain.get("auth") or "").strip().lower()
    if auth:
        return auth
    _, default = _PROVIDER_AUTH.get(brain.get("provider", ""), (None, "api"))
    return default


def generate_card(creature_path: str, issuer: str = "ludex-mac-caretaker",
                  final: bool = False) -> dict[str, Any]:
    """Compile a creature's organ.card/v0 from yaml + curated evidence."""
    cfg = OrganismConfig.load(creature_path)
    home = Path(cfg.habitat.home_dir or creature_path)

    curated: dict = {}
    ev_path = home / "store" / "card_evidence.json"
    if ev_path.exists():
        curated = json.loads(ev_path.read_text(encoding="utf-8"))

    organ_ev = curated.get("organs", {})
    organs = []
    for name, oc in (cfg.organs or {}).items():
        if not oc.get("enabled", False):
            continue
        entry = {"organ": name}
        if name in organ_ev:
            entry.update(organ_ev[name])          # curated claims + evidence verbatim
        else:
            entry.update({"claims": "", "evidence": dict(UNMEASURED)})
        organs.append(entry)

    card: dict[str, Any] = {
        "card_format": CARD_FORMAT,
        "creature": cfg.name,
        "issuer": issuer,
        "issued_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "brain": {
            "provider": cfg.brain.get("provider", ""),
            "model": cfg.brain.get("model", ""),
            "effort_baseline": cfg.brain.get("effort", ""),
            "auth_mode": _effective_auth(cfg.brain),
            "provenance": f"{Path(creature_path).as_posix()}/ludex.yaml + brain_resolved trace spans",
        },
        "organs": organs,
        "task_evidence": curated.get("task_evidence", []),
        "temperament": curated.get(
            "temperament",
            {"ref": f"mti.card/v0 {cfg.brain.get('model', '')}", "note": "brain-level sibling card"},
        ),
        "health": {
            "source": "sphygmos.vitals",
            "note": ("live reads via the sphygmos vitals port"
                     if (cfg.organs or {}).get("sphygmos", {}).get("enabled") else
                     "sphygmos not enabled on this creature — no health contract"),
        },
        "verification": {
            "liveness": "sphygmos.probe() -> PONG",
            "provenance_probe": ("sphygmos.probe(provenance=true) -> [EXECUTING_MODEL] + [VERBATIM] "
                                 "system-prompt quote, cross-checked against brain.provenance"),
            "policy": "claims are self-declarations until probed; silence -> fail-closed",
        },
    }
    if not final:
        card["draft"] = True                       # Phase-1 gate: Organum full-text re-verification
    return card


def write_card(creature_path: str, issuer: str = "ludex-mac-caretaker",
               final: bool = False) -> Path:
    """Emit the card to <habitat>/organ_card.json (beside lxm_identity.json)."""
    card = generate_card(creature_path, issuer=issuer, final=final)
    cfg = OrganismConfig.load(creature_path)
    out = Path(cfg.habitat.home_dir or creature_path) / "organ_card.json"
    out.write_text(json.dumps(card, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
