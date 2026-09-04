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

The defaults are not permanent facts about providers. They encode which auth
path is known to work, and a re-brain can move that — see agy_cli below.
"""
from __future__ import annotations

import os

# provider → (API-key env var that bills, default auth when brain.auth is unset).
# Defaults are SAFE per provider: a creature with no explicit auth must never
# silently bill, so a provider defaults to api only where subscription has no
# working path.
#
# agy_cli defaulted to api until 2026-08-05 on the 06-18 reading that the gemini
# consumer tier was gone. Nova falsifies it: her 07-20 re-brain moved her ONTO
# agy_cli specifically to reach subscription (gemini_cli/gemini-3-pro-preview/api
# was winding down), and she has run there since. agy is the subscription path
# for this lineage, so the unsafe default was the api one.
#
# gemini_cli stays api: nothing has shown its consumer tier coming back, and
# Nova's rescue moved OFF it to reach subscription rather than re-authenticating.
_PROVIDER_AUTH = {
    "claude_cli": ("ANTHROPIC_API_KEY", "subscription"),
    "codex_cli":  ("OPENAI_API_KEY",    "subscription"),
    "grok_cli":   ("XAI_API_KEY",       "subscription"),
    "gemini_cli": ("GEMINI_API_KEY",    "api"),
    "agy_cli":    ("GEMINI_API_KEY",    "subscription"),
    # cursor: Cursor Ultra rides the Grok Super Heavy bundle (2026-08-17) —
    # subscription is the working (and free-credit) path; CURSOR_API_KEY bills.
    "cursor_cli": ("CURSOR_API_KEY",    "subscription"),
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
