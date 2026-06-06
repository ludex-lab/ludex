"""Akoué file-source adapter (D-049 Phase A).

Local audio file path → validated Path. Allowed extensions kept
conservative for Phase A; ffmpeg / whisper accept more formats
but this list covers common speech content.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".webm"}


def acquire_file(path_str: str) -> Optional[Path]:
    """Validate a local audio file path.

    Returns None (with warning log) on failure. Caller surfaces
    None as Akoué acquisition error.
    """
    try:
        p = Path(path_str).expanduser().resolve()
    except Exception as e:
        logger.warning(f"akoue file_source: path parse failed {path_str!r}: {e}")
        return None

    if not p.exists():
        logger.warning(f"akoue file_source: not found {p}")
        return None
    if not p.is_file():
        logger.warning(f"akoue file_source: not a file {p}")
        return None
    if p.suffix.lower() not in ALLOWED_EXTENSIONS:
        logger.warning(
            f"akoue file_source: unsupported extension {p.suffix!r}; "
            f"allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )
        return None

    return p
