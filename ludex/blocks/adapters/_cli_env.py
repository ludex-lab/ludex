"""Shared subprocess-env builder for CLI brains — honors per-creature brain.auth.

A creature's auth mode (subscription login vs billed API key) is a BIRTH-TIME
decision recorded in its ludex.yaml `brain.auth`, NOT inferred from whether an
API key happens to sit in os.environ. Ludex loads .env into os.environ (the
model-currency check needs the key for GET /v1/models); without this gate that
key leaks into every CLI subprocess and the CLI bills the API instead of the
logged-in subscription — surfaced 2026-06-18 by a real Anthropic billing email.

  subscription → strip the provider's API key  → CLI uses its logged-in session
  api          → keep the key                  → CLI bills the API
  "" (unset)   → per-provider default (see _PROVIDER_AUTH below)
"""
from __future__ import annotations

import os

# provider → (API-key env var that bills, default auth when brain.auth is unset).
# Defaults are SAFE per provider: claude/codex default to subscription (the
# billing-leak fix — a creature with no explicit auth must never silently bill);
# gemini/agy default to api because their consumer/free tiers are deprecated
# (2026-06-18) and a paid key is the only working path.
_PROVIDER_AUTH = {
    "claude_cli": ("ANTHROPIC_API_KEY", "subscription"),
    "codex_cli":  ("OPENAI_API_KEY",    "subscription"),
    "grok_cli":   ("XAI_API_KEY",       "subscription"),
    "gemini_cli": ("GEMINI_API_KEY",    "api"),
    "agy_cli":    ("GEMINI_API_KEY",    "api"),
}


def cli_subprocess_env(provider: str, auth: str = "", extra: dict | None = None) -> dict:
    """Build the env dict for a CLI-brain subprocess, honoring brain.auth.

    `provider` selects which API key governs billing and the default auth mode.
    `auth` is the creature's `brain.auth` ("subscription" | "api" | ""); unset
    falls to the per-provider default. `extra` overlays additional vars (e.g.
    gemini's trust-workspace flag). Always sets PYTHONIOENCODING=utf-8.
    """
    key, default = _PROVIDER_AUTH.get(provider, (None, "api"))
    mode = (auth or default or "").strip().lower()
    strip = key if (mode == "subscription" and key) else None
    env = {k: v for k, v in os.environ.items() if k != strip}  # strip=None → no-op
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env
