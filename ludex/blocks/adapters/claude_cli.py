"""
Claude Code CLI Adapter — subprocess-based

Connects to a locally installed Claude Code CLI as the LLM backend.
Useful for Claude Max subscribers who have unlimited CLI usage.

Uses `claude -p "prompt"` for non-interactive single-shot calls.
Windows: claude.cmd, Mac/Linux: claude

Reference: LxM claude subprocess pattern
"""

from __future__ import annotations

import os
import re
import time
import subprocess
import logging

from ludex.blocks.adapters.base import BaseAdapter, AdapterResponse


# D-068 fatigue patterns for Claude subscription substrate.
# Format: (regex, cause_label). First match wins.
_CLAUDE_FATIGUE_PATTERNS = [
    (re.compile(r"\bdaily message limit reached\b", re.IGNORECASE), "subscription_limit"),
    (re.compile(r"\b5[\s-]*hour limit reached\b", re.IGNORECASE), "subscription_limit"),
    (re.compile(r"\bweekly message limit reached\b", re.IGNORECASE), "subscription_limit"),
    (re.compile(r"\brate[_\s-]?limit\b", re.IGNORECASE), "rate_limited"),
    (re.compile(r"\bquota\s+exhausted\b", re.IGNORECASE), "quota_exhausted"),
    (re.compile(r"\b429\b"), "rate_limited"),
]


# D-077 parity (2026-05-09): share the absolute-timestamp parser
# with codex_cli. Anthropic's weekly-cap stderr format is not yet
# verified in this codebase; the shared parser matches several
# spellings ("try again at <date>", "resets at <date>", etc.) and
# returns None when nothing matches — preserving the pre-D-077
# fall-back to the resilience block's 1h default.
from ludex.blocks.adapters._fatigue import parse_reset_at as _parse_reset_at


def _detect_claude_fatigue(text: str):
    """Return (cause, short_detail, reset_in_seconds | None) on match,
    else None. reset_in_seconds is non-None only when Claude stderr
    carries a parseable absolute reset timestamp; format unverified
    for Anthropic's weekly cap, so today this returns None in
    practice — the infrastructure is in place for the moment a
    sample arrives."""
    if not text:
        return None
    for pat, cause in _CLAUDE_FATIGUE_PATTERNS:
        m = pat.search(text)
        if m:
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 80)
            reset_s = _parse_reset_at(text)
            return cause, text[start:end].strip(), reset_s
    return None

logger = logging.getLogger(__name__)

# Detect OS for correct CLI command
_CLAUDE_CMD = "claude.cmd" if os.name == "nt" else "claude"


class ClaudeCliAdapter(BaseAdapter):
    """Claude Code CLI adapter via subprocess."""

    provider_name = "claude_cli"

    def __init__(self, base_url: str = "", timeout_ms: int = 120000, cwd: str = "", **kwargs):
        super().__init__(base_url=base_url or _CLAUDE_CMD, timeout_ms=timeout_ms, **kwargs)
        self._cmd = base_url or _CLAUDE_CMD
        self._cwd = cwd or None  # None = inherit from parent process

    def call(self, model="", prompt="", system="", messages=None,
             temperature=0.7, max_tokens=4096, tools=None, effort=""):
        """
        Call Claude Code CLI with -p flag (print mode, non-interactive).

        System prompt is passed via --system-prompt (real system role, not text injection).

        Multi-turn handling: claude -p is stateless and Claude tends to pattern-match
        on prior conversation when we flatten message history into a single prompt
        (it ends up answering Q1 for Q2/Q3). To avoid this, we send only the LAST
        user message. Multi-turn context should be packed into that user message by
        the caller (e.g., "X just said Y. Respond.").

        cwd controls where Claude operates (CLAUDE.md auto-discovery).
        """
        # Extract system prompt from messages if present, find latest user message
        system_prompt = system or ""
        last_user_msg = ""
        if messages:
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    if not system_prompt:
                        system_prompt = content
                    else:
                        system_prompt = system_prompt + "\n\n" + content
                elif role == "user":
                    last_user_msg = content  # Keep overwriting → last one wins
            full_prompt = last_user_msg
        else:
            full_prompt = prompt

        if not full_prompt:
            full_prompt = "(no message)"

        # Build command — use --system-prompt for REAL system role.
        # Pipe the prompt via stdin (no value after -p): on Windows, when
        # claude.cmd is invoked from inside another claude session,
        # multi-line `-p "<...>"` argv values get truncated at the first
        # newline. Stdin avoids that quoting layer entirely and works
        # uniformly across platforms.
        cmd = [self._cmd, "-p", "--output-format", "text"]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        if self._cwd:
            cmd.extend(["--add-dir", self._cwd])
            # Wire Ludex MCP server as a subprocess — creature lives in its habitat
            # Claude Code spawns the MCP server, connects, and can call organ tools
            mcp_config = self._build_mcp_config()
            if mcp_config:
                cmd.extend(["--mcp-config", mcp_config])
                cmd.extend(["--allowed-tools", "mcp__ludex__*"])
        # Allow model override via the model parameter (sonnet/opus/haiku)
        if model and model not in ("claude-code", "", None):
            cmd.extend(["--model", model])
        if effort:
            cmd.extend(["--effort", effort])
        if max_tokens and max_tokens < 4096:
            cmd.extend(["--max-turns", "1"])

        start = time.time()
        try:
            result = subprocess.run(
                cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_ms / 1000,
                encoding="utf-8",
                # Parity with gemini_cli/codex_cli: defensive guard against
                # Windows OEM-codepage bytes on stderr crashing the reader
                # thread (verified in gemini_cli; pre-emptive for claude_cli).
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                cwd=self._cwd,
            )

            elapsed_ms = (time.time() - start) * 1000
            content = result.stdout.strip()
            stderr_text = (result.stderr or "")

            # D-068 fatigue detection — Claude subscriptions surface
            # quota / rate limits as "Daily message limit reached",
            # "rate limit", "5-hour limit reached" etc. on stderr (or
            # in stdout with status text). When matched, return a
            # structured fatigue marker so ResilienceBlock can mark
            # the creature as resting. Reset window varies (5h or
            # daily) — parse when present, default 5h otherwise.
            #
            # No scan-window narrowing here (unlike codex_cli, which
            # uses rsplit on "\ncodex\n" to skip its prompt-echo
            # region). Audited 2026-05-13: Claude CLI's stderr is
            # empty on success and minimal on error
            # (`error: unknown option ...`) — no prompt echo, no
            # SELF.md content reflected back. The false-positive
            # class that bit Echo on codex (creature_context echoed
            # to stderr where the fatigue regex matched SELF.md
            # self-care history) does not apply. Re-audit if Claude
            # CLI's stderr shape changes (e.g. a future
            # --debug-on-error default that echoes prompts).
            fatigue_match = _detect_claude_fatigue(stderr_text + "\n" + content)
            if fatigue_match is not None:
                cause, detail, reset_s = fatigue_match
                msg = f"Claude subscription limit reached (cause: {cause}). {detail}"
                if reset_s is not None:
                    hours = reset_s / 3600
                    msg += f" Reset in ~{hours:.1f}h."
                logger.error(msg)
                raw = {
                    "returncode": result.returncode,
                    "error": cause,
                    "stderr": stderr_text,
                    "fatigue_detail": detail,
                }
                if reset_s is not None:
                    raw["reset_in_seconds"] = reset_s
                return AdapterResponse(
                    content=f"[Error: {msg}]",
                    raw=raw,
                )

            if result.returncode != 0 and not content:
                error_msg = stderr_text.strip() or f"CLI exited with code {result.returncode}"
                logger.error(f"Claude CLI error: {error_msg}")
                return AdapterResponse(
                    content=f"[Error: {error_msg}]",
                    raw={"returncode": result.returncode, "stderr": stderr_text},
                )

            # Rough token estimation (4 chars per token)
            tokens_in = len(full_prompt) // 4
            tokens_out = len(content) // 4

            return AdapterResponse(
                content=content,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                raw={
                    "returncode": result.returncode,
                    "elapsed_ms": round(elapsed_ms, 1),
                    "cmd": self._cmd,
                },
            )

        except subprocess.TimeoutExpired:
            elapsed_ms = (time.time() - start) * 1000
            logger.error(f"Claude CLI timeout after {elapsed_ms:.0f}ms")
            return AdapterResponse(
                content="[Error: Claude CLI timed out]",
                raw={"timeout": True, "elapsed_ms": round(elapsed_ms, 1)},
            )
        except FileNotFoundError:
            return AdapterResponse(
                content=f"[Error: '{self._cmd}' not found. Is Claude Code CLI installed?]",
                raw={"error": "command_not_found", "cmd": self._cmd},
            )

    def _build_mcp_config(self) -> str:
        """
        Build an inline MCP config JSON string that tells Claude Code to spawn
        our Ludex MCP organ server as a child process.

        The MCP server loads the creature from its habitat and exposes organ
        tools. Claude Code natively connects via stdio MCP protocol.

        Returns empty string if no cwd (no habitat to connect to).
        """
        if not self._cwd:
            return ""
        import json as _json
        # Find python interpreter (use the same one as the server)
        import sys as _sys
        python = _sys.executable or "python"

        config = {
            "mcpServers": {
                "ludex": {
                    "command": python,
                    "args": ["-m", "ludex.mcp.ludex_mcp_server", "--habitat", self._cwd],
                }
            }
        }
        return _json.dumps(config)

    def health_check(self) -> dict:
        """Check if Claude CLI is available."""
        try:
            result = subprocess.run(
                [self._cmd, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            version = result.stdout.strip() or result.stderr.strip()
            return {"status": "ok", "version": version, "cmd": self._cmd}
        except FileNotFoundError:
            return {"status": "error", "error": f"'{self._cmd}' not found"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def list_models(self) -> list[str]:
        """Claude CLI uses whatever model is configured."""
        return ["claude-code (default)"]
