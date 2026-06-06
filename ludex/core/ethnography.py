"""
Creature Ethnography — longitudinal developmental record.

D-027: Snapshots and parent journals preserve a creature's birth, nurturing,
and evolution over time. Complementary to statistical reproducibility papers:
this is N-of-1 narrative + artifact, modelled on primatology field notes and
child-development journals.

Snapshots freeze salient habitat state; the parent journal (written by the
caretaker, not the creature) records what was done and why.

Usage:
    from ludex.core.ethnography import take_snapshot

    # Manual milestone
    take_snapshot(organism, reason="baseline", note="First ethnography entry.")

    # Auto-triggered (wired into reflect() today)
    take_snapshot(organism, reason="after_reflection")
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


SNAPSHOT_ITEMS = [
    ("SELF.md", "file"),
    ("ludex.json", "file"),
    ("bonds", "dir"),
    ("emotion/baseline.json", "file"),
    ("humoral", "dir"),
]


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:40] or "snapshot"


def _habitat_dir(organism) -> str:
    config = getattr(organism, "config", None)
    if not config:
        return ""
    return config.get("habitat_dir", "") if hasattr(config, "get") else ""


def _memory_count(habitat_dir: str) -> int:
    memfile = Path(habitat_dir) / "memory" / "memories.jsonl"
    if not memfile.exists():
        return 0
    try:
        with memfile.open(encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def take_snapshot(
    organism,
    reason: str,
    note: str = "",
) -> str:
    """Freeze salient habitat state into `snapshots/YYYY-MM-DD-{slug}/`.

    Copies SELF.md, ludex.json, bonds/, emotion/baseline.json, humoral/.
    Memory is not copied (size); its count is recorded in snapshot.json.
    A parent journal entry remains a separate caretaker responsibility.

    Args:
        organism: The Organism instance.
        reason: Short label used for the snapshot directory name.
        note: Optional caretaker/creature note included in snapshot.json.

    Returns:
        Absolute path to the snapshot directory, or empty string on failure.
    """
    habitat_dir = _habitat_dir(organism)
    if not habitat_dir:
        logger.warning("take_snapshot: habitat_dir unresolved")
        return ""

    habitat = Path(habitat_dir)
    if not habitat.exists():
        logger.warning(f"take_snapshot: habitat missing: {habitat_dir}")
        return ""

    today = datetime.now().strftime("%Y-%m-%d")
    slug = _slugify(reason)
    base = habitat / "snapshots" / f"{today}-{slug}"

    # Disambiguate same-day same-reason snapshots with a counter
    target = base
    counter = 2
    while target.exists():
        target = Path(f"{base}-{counter}")
        counter += 1
    target.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    skipped: list[str] = []
    for rel, kind in SNAPSHOT_ITEMS:
        src = habitat / rel
        if not src.exists():
            skipped.append(rel)
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if kind == "file":
                shutil.copy2(src, dst)
            else:
                shutil.copytree(src, dst, dirs_exist_ok=True)
            copied.append(rel)
        except Exception as e:
            logger.warning(f"take_snapshot: failed to copy {rel}: {e}")
            skipped.append(rel)

    meta: dict[str, Any] = {
        "creature": organism.name,
        "reason": reason,
        "note": note,
        "timestamp": time.time(),
        "timestamp_iso": datetime.now().isoformat(timespec="seconds"),
        "memory_count": _memory_count(habitat_dir),
        "copied": copied,
        "skipped": skipped,
    }
    # Pull a few lightweight facts from ludex.json
    try:
        lud = json.loads((habitat / "ludex.json").read_text(encoding="utf-8"))
        meta["session_count"] = lud.get("session_count")
        meta["born_at"] = lud.get("born_at")
        brain = lud.get("brain", {})
        meta["brain"] = f"{brain.get('provider')}/{brain.get('model')}"
    except Exception:
        pass

    try:
        (target / "snapshot.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"take_snapshot: failed to write snapshot.json: {e}")

    logger.info(f"Snapshot taken: {organism.name} → {target}")
    return str(target)
