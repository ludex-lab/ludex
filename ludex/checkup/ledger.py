"""Checkup ledger parser — the audit markdown IS the source of truth.

V0 of the checkup view (design note 2026-08-01, layer 1): parse the
organ grid and the surface registry out of docs/organ-checkup-audit.md
so the view can never drift from the ledger. Read-only, no state.

Honesty rules enforced HERE (not in CSS), so any consumer inherits them:
- unmeasured is an explicit status, never an empty cell;
- wall-null / not-measurable is its own status, NOT a failure;
- retracted or re-scoped findings stay in the note text (we do not strip
  them) — the E1/E1g keep-annotated precedent.
"""
from __future__ import annotations

import re
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent.parent / "docs" / "organ-checkup-audit.md"

# status vocabulary → (normalized key, human label, honest gloss)
_STATUS = [
    # ORDER MATTERS: "unmeasured" contains "measured" — the negative forms
    # must be tested first, or the view would render an unmeasured surface
    # as measured (the exact lie the honesty rules exist to prevent).
    ("unmeasured", "unmeasured", "UNMEASURED",
     "no controlled effect measurement — honest debt"),
    ("미측정", "unmeasured", "UNMEASURED",
     "no controlled effect measurement — honest debt"),
    ("not-measurable", "notmeasurable", "NOT-MEASURABLE",
     "wall did not bind — measurement impossible here, not a failure"),
    ("wall-null", "notmeasurable", "NOT-MEASURABLE",
     "wall did not bind — measurement impossible here, not a failure"),
    ("not built", "notbuilt", "NOT BUILT", "surface does not exist yet"),
    ("reviewed", "reviewed", "REVIEWED",
     "architecture examined; effect never measured"),
    ("partially measured", "partial", "PARTIALLY MEASURED",
     "some claims measured, others not — see note"),
    ("surface-split", "split", "SURFACE-SPLIT",
     "measured per surface, not per organ"),
    ("measured-null", "null", "MEASURED-NULL",
     "measured; the effect was absent — a finding, not a failure"),
    ("measured", "measured", "MEASURED", "controlled effect measured"),
    ("pilot", "pilot", "PILOT", "observed in the wild, effect unmeasured"),
    ("theory", "theory", "THEORY (unmeasured)",
     "attached on design reasoning — never examined for effect"),
    ("infra", "infra", "INFRA", "substrate, not an effect organ"),
]


def _norm_status(cell: str) -> dict:
    low = cell.lower()
    # Mixed verdicts (some claims established, others wall-null) are
    # PARTIAL — immune.chain is the canonical case. Checked before the
    # single-status table so the stronger negative doesn't swallow it.
    _established = ("확립", "established", "pass", "confirmed")
    _negative = ("wall-null", "not-measurable", "not measurable")
    if any(e in low for e in _established) and any(n in low for n in _negative):
        return {"key": "partial", "label": "PARTIALLY MEASURED",
                "gloss": "some claims measured, others not-measurable — see note",
                "extra": "closed" if "closed" in low else ""}
    for needle, key, label, gloss in _STATUS:
        if needle in low:
            extra = "dormant" if "dormant" in low else (
                "closed" if "closed" in low else "")
            return {"key": key, "label": label, "gloss": gloss, "extra": extra}
    # honesty rule: never return an empty status
    return {"key": "unknown", "label": "UNCLASSIFIED",
            "gloss": "not yet classified in the ledger", "extra": ""}


def _rows(md: str, header_startswith: str) -> list[list[str]]:
    """Return the data rows of the first table whose header line starts
    with `header_startswith`."""
    out, in_tbl = [], False
    for line in md.splitlines():
        if not in_tbl:
            if line.startswith(header_startswith):
                in_tbl = True
            continue
        if line.startswith("|---") or line.startswith("|--"):
            continue
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        out.append(cells)
    return out


def _plain(cell: str) -> str:
    """Strip markdown emphasis/code ticks for display keys."""
    return re.sub(r"[*`~]", "", cell).strip()


def parse(path: Path | None = None) -> dict:
    md = (path or LEDGER).read_text(encoding="utf-8")
    organs = []
    for r in _rows(md, "| organ | default-on | status"):
        if len(r) < 5:
            continue
        organs.append({
            "organ": _plain(r[0]),
            "default_on": "✔" in r[1],
            "default_label": _plain(r[1]),
            "status": _norm_status(r[2]),
            "status_raw": _plain(r[2]),
            "note": r[3],
            "priority": _plain(r[4]),
        })
    surfaces = []
    for r in _rows(md, "| surface | organs | consumer"):
        if len(r) < 7:
            continue
        surfaces.append({
            "surface": _plain(r[0]),
            "organs": _plain(r[1]),
            "consumer": r[2],
            "readiness": _plain(r[3]),
            "toggle": r[4],
            "dose": _plain(r[5]),
            "status": _norm_status(r[6]),
            "status_raw": r[6],
        })
    measured = sum(1 for o in organs
                   if o["status"]["key"] in ("measured", "partial", "split"))
    effect_organs = [o for o in organs if o["status"]["key"] != "infra"]
    debt = [o["organ"] for o in effect_organs
            if o["default_on"] and o["status"]["key"] in ("theory", "pilot",
                                                          "unknown")]
    return {
        "organs": organs,
        "surfaces": surfaces,
        "summary": {
            "organ_count": len(organs),
            "measured_count": measured,
            "effect_organ_count": len(effect_organs),
            "default_on_unmeasured": debt,
            "surface_count": len(surfaces),
            "surface_measured": sum(
                1 for s in surfaces
                if s["status"]["key"] in ("measured", "null", "partial")),
        },
        "source": str((path or LEDGER).name),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(parse()["summary"], ensure_ascii=False, indent=1))
