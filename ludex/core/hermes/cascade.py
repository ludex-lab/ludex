"""Hermes cascade — brain discovery + selection.

Probes installed CLIs in priority order (default: Opus → Codex 5.5
→ Gemini 3.1 Pro per JJ 2026-04-28). Returns first available
brain, or None when none discovered.

Configuration override: `~/.ludex/coordinator.yaml`. Schema:

    hermes:
      brain:
        provider: claude_cli  # or codex_cli, gemini_cli
        model: claude-opus-4-7
      fallback:
        - {provider: codex_cli, model: gpt-5.5}
        - {provider: gemini_cli, model: gemini-3.1-pro-preview}
      on_unavailable: prompt | fail | skip
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Default cascade per JJ 2026-04-28 framing.
DEFAULT_CASCADE = [
    ("claude_cli", "claude-opus-4-7"),
    ("codex_cli", "gpt-5.5"),
    ("gemini_cli", "gemini-3.1-pro-preview"),
]


@dataclass(frozen=True)
class HermesBrain:
    provider: str
    model: str
    cli_path: str = ""


def _config_path() -> Path:
    return Path.home() / ".ludex" / "coordinator.yaml"


def _which_cli(provider: str) -> str:
    """Return the CLI binary path for a provider, or '' if missing."""
    candidates = {
        "claude_cli": ["claude.cmd", "claude"] if os.name == "nt" else ["claude"],
        "codex_cli": ["codex.cmd", "codex"] if os.name == "nt" else ["codex"],
        "gemini_cli": ["gemini.cmd", "gemini"] if os.name == "nt" else ["gemini"],
    }.get(provider, [])
    for c in candidates:
        path = shutil.which(c)
        if path:
            return path
    return ""


def _load_config() -> dict:
    """Read user override config; empty dict when missing."""
    cfg_path = _config_path()
    if not cfg_path.exists():
        return {}
    try:
        import yaml as _yaml
        return _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning(f"Failed to read {cfg_path}: {e}")
        return {}


def discover_cascade() -> list[HermesBrain]:
    """Build the resolution cascade.

    User-specified primary + fallbacks first; then any default-
    cascade entries not already represented. Each entry is
    probed for CLI availability — only available ones are
    returned.
    """
    cfg = _load_config().get("hermes", {})
    user_primary = cfg.get("brain")
    user_fallbacks = cfg.get("fallback", []) or []

    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []

    if isinstance(user_primary, dict) and user_primary.get("provider"):
        ordered.append((user_primary["provider"], user_primary.get("model", "")))
    for f in user_fallbacks:
        if isinstance(f, dict) and f.get("provider"):
            ordered.append((f["provider"], f.get("model", "")))
    for entry in DEFAULT_CASCADE:
        if entry not in ordered:
            ordered.append(entry)

    available: list[HermesBrain] = []
    for provider, model in ordered:
        key = (provider, model)
        if key in seen:
            continue
        seen.add(key)
        cli = _which_cli(provider)
        if cli:
            available.append(HermesBrain(provider=provider, model=model, cli_path=cli))
    return available


def select_active_brain() -> Optional[HermesBrain]:
    """Return the first available brain in the cascade, or None.

    Doesn't account for fatigue here — Hermes service-level fatigue
    handling lives in `fatigue.py`. This is pure availability
    (binary present + has plausible model name).
    """
    cascade = discover_cascade()
    return cascade[0] if cascade else None


def status_report() -> str:
    """Human-readable discovery report. Each provider, available
    or not, with the active selection at end."""
    lines = ["Hermes coordinator status"]
    cascade = discover_cascade()
    seen_active = False
    # Show all default+user entries with availability tag.
    cfg = _load_config().get("hermes", {})
    primary = cfg.get("brain")
    user_fallbacks = cfg.get("fallback", []) or []
    rows = []
    if isinstance(primary, dict) and primary.get("provider"):
        rows.append((primary["provider"], primary.get("model", ""), "user-primary"))
    for f in user_fallbacks:
        if isinstance(f, dict) and f.get("provider"):
            rows.append((f["provider"], f.get("model", ""), "user-fallback"))
    for entry in DEFAULT_CASCADE:
        if not any(r[:2] == entry for r in rows):
            rows.append((*entry, "default"))
    for provider, model, source in rows:
        cli = _which_cli(provider)
        avail = "✓ available" if cli else "✗ missing"
        active = ""
        if cli and not seen_active:
            active = "  ← active"
            seen_active = True
        lines.append(f"- {provider:11s} ({model:30s}) [{source:14s}] {avail}{active}")
    if not seen_active:
        lines.append("(no brain available — Hermes will be unavailable)")
    return "\n".join(lines)
