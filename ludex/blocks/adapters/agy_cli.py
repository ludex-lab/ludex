"""
Antigravity CLI Adapter — subprocess-based, speech-act only.

Connects to Google's Antigravity CLI (`agy`, Go-rewrite) as the LLM
backend. Pinned to Gemini 3.5 Flash, which is the only model `agy -p`
headless mode exposes as of v1.0.2 (no `--model` CLI flag; the
interactive `/model` slash command does not apply to print mode).

Speech-act path ONLY. Agentic tool calls would require
`--dangerously-skip-permissions`, which re-opens the D-074
vulnerability class (auto-approved write_file / shell etc.). That
flag is forbidden by project policy; see memory
`gemini_cli_deprecation` and `creature_mortality_principle`.

Usage requires agy CLI installed (https://antigravity.google/docs/cli-using)
and the working folder trusted via agy's separate folder-trust prompt.

Reference: gemini_cli adapter (D-074 Phase A.1 / B / A.2 inherited;
Phase A.2 trust corollary skipped — agy's trust mechanism is folder-
scoped and granted once, not per-invocation). Antigravity CLI v1.0.2
behavior surfaced 2026-05-25.
"""

from __future__ import annotations

import os
import re
import time
import shutil
import tempfile
import subprocess
import logging

from ludex.blocks.adapters.base import BaseAdapter, AdapterResponse
from ludex.blocks.adapters._cli_env import cli_subprocess_env
from ludex.blocks.adapters._creature_context import load_creature_context

logger = logging.getLogger(__name__)

# shutil.which resolves the real executable (Windows PATHEXT: agy.exe from the curl
# install OR agy.cmd from npm — the hardcoded .cmd missed agy.exe). Fall back to the
# old default if which finds nothing (e.g. not on PATH at import).
_AGY_CMD = shutil.which("agy") or ("agy.cmd" if os.name == "nt" else "agy")
_PINNED_MODEL = "gemini-3.5-flash"


class AgyCliAdapter(BaseAdapter):
    """Antigravity CLI adapter (speech-act only, pinned to gemini-3.5-flash)."""

    provider_name = "agy_cli"

    def __init__(self, base_url: str = "", timeout_ms: int = 120000, cwd: str = "", auth: str = "", **kwargs):
        super().__init__(base_url=base_url or _AGY_CMD, timeout_ms=timeout_ms, **kwargs)
        self._cmd = base_url or _AGY_CMD
        self._cwd = cwd or None
        self._auth = auth  # birth-time auth mode (subscription|api); see _cli_env

    def call(self, model="", prompt="", system="", messages=None,
             temperature=0.7, max_tokens=4096, tools=None, effort=""):
        # Tools forbidden — see module docstring.
        if tools:
            err = "agy adapter is speech-act only; tool calls forbidden by policy"
            logger.error(err)
            return AdapterResponse(
                content=f"[Error: {err}]",
                raw={"error": "tools_forbidden"},
            )

        # Model pin: agy -p only routes the default model (Gemini 3.5 Flash as
        # of v1.0.2). Reject mismatched model rather than silently misrouting —
        # surfacing the mismatch is safer for paper provenance.
        if model and model not in ("", _PINNED_MODEL):
            err = f"agy CLI -p mode only routes {_PINNED_MODEL}; got {model}"
            logger.error(err)
            return AdapterResponse(
                content=f"[Error: {err}]",
                raw={"error": "model_mismatch", "requested": model, "pinned": _PINNED_MODEL},
            )

        # Extract system + last user message (parity with claude_cli / gemini_cli).
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
                    last_user_msg = content
            full_prompt = last_user_msg
        else:
            full_prompt = prompt

        if not full_prompt:
            full_prompt = "(no message)"

        # System prompt: agy has no --system-prompt flag. Append-after pattern
        # inherited from gemini_cli (instruction-adherence-rejection workaround
        # for Gemini family). Whether 3.5-flash strictly needs this is open;
        # default conservative until observed otherwise.
        if system_prompt:
            brief_system = system_prompt[:500] if len(system_prompt) > 500 else system_prompt
            full_prompt = (
                f"{full_prompt}\n\n"
                f"(Context about you: {brief_system}. "
                f"Answer the question above first, then stay in character.)"
            )

        # D-074 Phase A.1 + B: prohibit retrieval narration and prepend
        # creature context so the brain has its identity / recent state
        # in-prompt without needing filesystem access.
        creature_context = load_creature_context(self._cwd)
        full_prompt = (
            "Respond directly with your contribution. Do not "
            "narrate retrieval, exploration, or planning steps "
            "(no \"I will read X\" / \"I'll examine Y\"). If "
            "context beyond what is in this prompt would change "
            "your answer, say so plainly rather than fabricating "
            "a citation.\n\n" + creature_context + full_prompt
        )

        # D-074 Phase A.2 (defense-in-depth): route subprocess cwd to a fresh
        # tempdir. Whether agy v1.0.2 reads pwd implicitly in -p mode is
        # untested, but the cost is negligible and rules out the class of
        # implicit-pwd leaks observed in gemini-cli. Opaque prefix avoids
        # seeding domain hints.
        sandbox_cwd = tempfile.mkdtemp(prefix="ludex_sb_")

        cmd = [self._cmd, "-p", full_prompt]
        # HARD POLICY: never pass --dangerously-skip-permissions. Folder trust
        # alone does NOT auto-approve tool calls in v1.0.2.

        start = time.time()
        try:
            try:
                child_env = cli_subprocess_env("agy_cli", self._auth)
                result = subprocess.run(
                    cmd,
                    # Headless: never inherit the parent's stdin. agy reads the prompt
                    # from argv (-p), not stdin; an inherited TTY/console stdin can hang
                    # the call waiting on interactive input — observed on Windows agy.cmd
                    # (2026-06-22). DEVNULL gives the child immediate EOF instead.
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_ms / 1000,
                    encoding="utf-8",
                    errors="replace",
                    env=child_env,
                    cwd=sandbox_cwd,
                )
            finally:
                shutil.rmtree(sandbox_cwd, ignore_errors=True)

            elapsed_ms = (time.time() - start) * 1000
            content = result.stdout.strip()
            stderr_text = (result.stderr or "")

            # Quota detection: exact pattern not yet characterized for agy.
            # Generic heuristic until a real quota event lets us pin the
            # format. Refine the token list when observed.
            lower_err = stderr_text.lower()
            quota_tokens = ("quota_exhausted", "quota exhausted", "rate limit",
                            "limits exhausted", "credit exhausted", "429")
            if any(tok in lower_err for tok in quota_tokens):
                reset_msg = ""
                m = re.search(r"reset (?:after|in)\s+([0-9hms ]+)", stderr_text, re.IGNORECASE)
                if m:
                    reset_msg = f" Quota resets in {m.group(1).strip()}."
                err = f"agy quota exhausted.{reset_msg}"
                logger.error(err)
                return AdapterResponse(
                    content=f"[Error: {err}]",
                    raw={
                        "returncode": result.returncode,
                        "error": "quota_exhausted",
                        "stderr": stderr_text,
                    },
                )

            if result.returncode != 0 and not content:
                error_msg = stderr_text.strip() or f"CLI exited with code {result.returncode}"
                logger.error(f"agy CLI error: {error_msg}")
                return AdapterResponse(
                    content=f"[Error: {error_msg}]",
                    raw={"returncode": result.returncode, "stderr": stderr_text},
                )

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
                    "model": _PINNED_MODEL,
                },
            )

        except subprocess.TimeoutExpired:
            elapsed_ms = (time.time() - start) * 1000
            shutil.rmtree(sandbox_cwd, ignore_errors=True)
            return AdapterResponse(
                content="[Error: agy CLI timed out]",
                raw={"timeout": True, "elapsed_ms": round(elapsed_ms, 1)},
            )
        except FileNotFoundError:
            shutil.rmtree(sandbox_cwd, ignore_errors=True)
            return AdapterResponse(
                content=f"[Error: '{self._cmd}' not found. Is Antigravity CLI installed?]",
                raw={"error": "command_not_found", "cmd": self._cmd},
            )

    def health_check(self) -> dict:
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
        return [_PINNED_MODEL]
