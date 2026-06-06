"""D-074 Phase B — speech-act creature context provenance.

When narrative-substrate adapters (gemini_cli, codex_cli) make
speech-act brain calls (`tools=None`), they have no workspace
access (Phase A) and cannot fabricate citations under a directive
(Phase A.1) — but they also cannot ground their prose in the
creature's actual state (SELF.md, bonds, recent memory). Phase B
closes that gap by *reading the creature state for them* and
prepending it to the user prompt as text.

The brain still cannot mutate (no tools), still cannot read
unrequested files (no cwd in subprocess), and the directive
against fabrication remains. But the most-likely-needed context —
the creature's own identity, last reflection, and bond list — is
now in the prompt directly. Fabrication failure mode (Wick
referencing non-existent `ray_duo_quill_anvil_001`) is addressed
at the cause: the brain doesn't need to invent because real
context is provided.

This is preferable to a CLI-flag-level read-only allowlist for
two reasons:

1. **Platform-agnostic.** gemini-cli and codex-cli expose
   different tool-permission flags; a prompt-level approach works
   identically across both. claude_cli already uses an
   `--allowed-tools "mcp__ludex__*"` whitelist that achieves the
   same outcome through its own mechanism, but we don't need to
   force gemini_cli/codex_cli to expose an analogous flag.

2. **Auditable.** The exact context the brain saw is in the
   prompt, visible in transcripts. With a tool-allowlist, the
   brain might have called `read_file('SELF.md')` or might not
   have — we'd have no way to know what it grounded on.

What this module does NOT do:
- Does not handle agentic-phase calls (`tools` truthy) — those
  are expected to manage their own context via tool calls.
- Does not write files. Read-only.
- Does not bypass the framework-mediated reflection write path
  (D-044, `selfhood.py:216-230`). That contract is unchanged.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# Conservative defaults — enough to ground identity + recent state
# without bloating prompts. Caller can override.
MAX_SELF_CHARS = 3000
MAX_BOND_FILES = 3
MAX_BOND_CHARS_EACH = 600


def load_creature_context(
    cwd: str | None,
    max_self_chars: int = MAX_SELF_CHARS,
    max_bond_files: int = MAX_BOND_FILES,
    max_bond_chars_each: int = MAX_BOND_CHARS_EACH,
) -> str:
    """Return a prompt-friendly text block describing the creature
    living at `cwd`, or empty string if no creature dir is provided
    or readable.

    Read order:
        SELF.md                 — identity + last reflection
        bonds/*.md (top N)      — most recently touched relationships

    Resilient: any read failure on a single file is logged and
    skipped. Returns whatever it could read.
    """
    if not cwd:
        return ""
    creature_dir = Path(cwd)
    if not creature_dir.is_dir():
        return ""

    parts: list[str] = []

    self_md = creature_dir / "SELF.md"
    if self_md.is_file():
        try:
            text = self_md.read_text(encoding="utf-8").strip()
            if len(text) > max_self_chars:
                text = text[:max_self_chars] + "\n[…SELF.md truncated]"
            parts.append(f"--- {creature_dir.name}/SELF.md ---\n{text}")
        except Exception as e:
            logger.warning(f"creature_context: failed to read SELF.md: {e}")

    bonds_dir = creature_dir / "bonds"
    if bonds_dir.is_dir():
        try:
            bond_files = sorted(
                (p for p in bonds_dir.glob("*.md") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:max_bond_files]
            for bf in bond_files:
                try:
                    text = bf.read_text(encoding="utf-8").strip()
                    if len(text) > max_bond_chars_each:
                        text = text[:max_bond_chars_each] + "\n[…bond truncated]"
                    parts.append(f"--- {creature_dir.name}/bonds/{bf.name} ---\n{text}")
                except Exception as e:
                    logger.warning(f"creature_context: failed to read {bf}: {e}")
        except Exception as e:
            logger.warning(f"creature_context: failed to list bonds dir: {e}")

    if not parts:
        return ""

    header = (
        "[Your current state, provided so you can ground your response "
        "in real context rather than fabricate. Read-only — you cannot "
        "modify these files; the framework will write any state changes "
        "based on your prose.]\n\n"
    )
    return header + "\n\n".join(parts) + "\n\n"
