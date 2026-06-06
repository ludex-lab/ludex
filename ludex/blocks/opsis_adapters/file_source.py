"""Opsis file-source adapter (D-048 Phase A).

Local file path → validated Path. Trivial, but kept as an explicit
adapter so the acquisition layer has a uniform shape across all
future sources (URL, screenshot, camera).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def acquire_file(path_str: str) -> Optional[Path]:
    """Validate a local file path as an image source.

    Returns None (with a warning log) if the file does not exist,
    is not readable, or has an unsupported extension. Caller
    translates None into an Opsis acquisition failure.
    """
    try:
        p = Path(path_str).expanduser().resolve()
    except Exception as e:
        logger.warning(f"file_source: path parse failed {path_str!r}: {e}")
        return None

    if not p.exists():
        logger.warning(f"file_source: not found {p}")
        return None
    if not p.is_file():
        logger.warning(f"file_source: not a file {p}")
        return None
    if p.suffix.lower() not in ALLOWED_EXTENSIONS:
        logger.warning(
            f"file_source: unsupported extension {p.suffix!r}; "
            f"allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )
        return None

    return p
