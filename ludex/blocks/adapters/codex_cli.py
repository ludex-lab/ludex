"""
Codex CLI Adapter — subprocess-based, mirrors Claude/Gemini CLI pattern.

Connects to OpenAI's Codex CLI as the LLM backend.
Useful for ChatGPT Plus/Pro/Team subscribers.

Uses `codex exec "prompt"` for non-interactive single-shot calls.
Mac/Linux native (Windows experimental).

Codex CLI does not have a --system-prompt flag; system context is
prepended to the user prompt (same approach as Gemini CLI).

MCP: Codex has experimental `codex mcp` support — wiring TBD.

Reference: Claude CLI + Gemini CLI adapter patterns
"""

from __future__ import annotations

import os
import re
import sys
import time
import json
import shutil
import tempfile
import subprocess
import logging

from ludex.blocks.adapters.base import BaseAdapter, AdapterResponse
from ludex.blocks.adapters._creature_context import load_creature_context


# D-068 fatigue patterns for Codex / ChatGPT subscription substrate.
# Mirrors claude_cli's pattern set; ChatGPT free-tier specifically
# surfaces "you have hit your daily message limit" / "rate limit"
# style messages.
_CODEX_FATIGUE_PATTERNS = [
    (re.compile(r"\bdaily message limit\b", re.IGNORECASE), "subscription_limit"),
    (re.compile(r"\b3[\s-]*hour limit\b", re.IGNORECASE), "subscription_limit"),
    (re.compile(r"\b5[\s-]*hour limit\b", re.IGNORECASE), "subscription_limit"),
    (re.compile(r"\busage limit\b", re.IGNORECASE), "subscription_limit"),
    (re.compile(r"\brate[_\s-]?limit\b", re.IGNORECASE), "rate_limited"),
    (re.compile(r"\bquota\s+exhausted\b", re.IGNORECASE), "quota_exhausted"),
    (re.compile(r"\b429\b"), "rate_limited"),
    (re.compile(r"\binsufficient_quota\b", re.IGNORECASE), "quota_exhausted"),
]


# Codex weekly subscription resets surface as an absolute timestamp
# (verified 2026-05-09: "try again at May 12th, 2026 3:16 PM."). The
# parser lives in `_fatigue.py` so claude_cli can share it.
from ludex.blocks.adapters._fatigue import parse_reset_at as _parse_reset_at


def _detect_codex_fatigue(text: str):
    """Return (cause, short_detail, reset_in_seconds | None) on match,
    else None. reset_in_seconds is non-None only when Codex stderr
    carries a parseable absolute reset timestamp (weekly subscription
    case)."""
    if not text:
        return None
    for pat, cause in _CODEX_FATIGUE_PATTERNS:
        m = pat.search(text)
        if m:
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 80)
            reset_s = _parse_reset_at(text)
            return cause, text[start:end].strip(), reset_s
    return None

logger = logging.getLogger(__name__)

_CODEX_CMD = "codex"


class CodexCliAdapter(BaseAdapter):
    """Codex CLI adapter via subprocess."""

    provider_name = "codex_cli"

    def __init__(self, base_url: str = "", timeout_ms: int = 120000, cwd: str = "", **kwargs):
        super().__init__(base_url=base_url or _CODEX_CMD, timeout_ms=timeout_ms, **kwargs)
        self._cmd = base_url or _CODEX_CMD
        self._cwd = cwd or None

    def call(self, model="", prompt="", system="", messages=None,
             temperature=0.7, max_tokens=4096, tools=None, effort=""):
        """
        Call Codex CLI with exec subcommand (non-interactive).

        System prompt: Codex CLI has no --system-prompt flag.
        We prepend system context to the user message, same as Gemini CLI.
        Codex reads AGENTS.md from cwd if present (similar to CLAUDE.md).

        Multi-turn: send only the LAST user message to avoid pattern-matching
        on flattened conversation history.
        """
        # Extract system prompt + last user message
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

        # Codex has no --system-prompt; prepend as context (like Gemini)
        if system_prompt:
            brief_system = system_prompt[:500] if len(system_prompt) > 500 else system_prompt
            full_prompt = (
                f"[Context about you: {brief_system}]\n\n"
                f"{full_prompt}"
            )

        # Use a temp file to capture last message cleanly
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            output_file = tmp.name

        # D-074: speech-act calls (tools is None/empty) must not have
        # workspace permissions. Pre-patch, codex exec ran with
        # --full-auto and -C creature_dir for every call, giving the
        # model auto-approved tool access scoped to the creature's
        # habitat. The architectural risk parallels gemini_cli's
        # yolo+cwd posture (D-074 trigger). Codex Anvil has not
        # manifested filesystem mutation in council transcripts to
        # date, but parity with gemini_cli + claude_cli's
        # speech-only contract is what this adapter is being aligned to.
        # Long prompts as argv can exceed ARG_MAX or trigger Codex's stdin
        # fallback (which echoes the CLI banner). Always pipe via stdin using
        # `-` as PROMPT — Codex reads instructions from stdin.
        agentic = bool(tools)

        # D-074 Phase A.1 — speech-act prompt directive. Mirrors the
        # gemini_cli adapter: blocks retrieval-narration prose and
        # asks the model to flag missing context rather than
        # fabricate citations. Codex (gpt-5.5) has not manifested
        # the failure mode in council transcripts to date, but the
        # directive is parity-cheap and defensive.
        #
        # D-074 Phase B (2026-05-08) — creature-context provenance,
        # mirrors gemini_cli adapter. Reads SELF.md + bonds for the
        # creature at self._cwd (no subprocess access; pure file
        # read on the parent process side) and prepends as text.
        # The brain has its own identity / recent state in-prompt
        # and does not need to invent.
        # D-074 Phase A.2 (2026-05-09) — implicit pwd file-read sandbox.
        # Verified for gemini-cli; applied to codex-cli for parity.
        # Codex's default sandbox=read-only would still expose the
        # parent python's pwd to read calls. Routing non-agentic
        # subprocess cwd to a fresh tempdir closes the same channel.
        sandbox_cwd: str | None = None
        if not agentic:
            creature_context = load_creature_context(self._cwd)
            full_prompt = (
                "Respond directly with your contribution. Do not "
                "narrate retrieval, exploration, or planning steps "
                "(no \"I will read X\" / \"I'll examine Y\"). If "
                "context beyond what is in this prompt would change "
                "your answer, say so plainly rather than fabricating "
                "a citation.\n\n" + creature_context + full_prompt
            )
            # Neutral prefix — see gemini_cli.py for rationale (Phase A.2
            # validation found that domain-suggestive dir names seed model
            # hallucinations of project context).
            sandbox_cwd = tempfile.mkdtemp(prefix="ludex_sb_")
        cmd = [
            self._cmd, "exec",
            "-",
            "--output-last-message", output_file,
            "--skip-git-repo-check",
        ]
        if agentic:
            cmd.append("--full-auto")
        if model and model not in ("codex", "", None):
            cmd.extend(["-m", model])
        if agentic and self._cwd:
            cmd.extend(["-C", self._cwd])

        start = time.time()
        try:
            try:
                result = subprocess.run(
                    cmd,
                    input=full_prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_ms / 1000,
                    encoding="utf-8",
                    # Parity with gemini_cli: Windows OEM-codepage bytes on
                    # stderr can crash the reader thread under encoding=utf-8.
                    errors="replace",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    cwd=(self._cwd if agentic else sandbox_cwd),
                )
            finally:
                if sandbox_cwd:
                    shutil.rmtree(sandbox_cwd, ignore_errors=True)

            elapsed_ms = (time.time() - start) * 1000

            # Read output from the last-message file (most reliable)
            content = ""
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
            except Exception:
                pass
            finally:
                try:
                    os.unlink(output_file)
                except Exception:
                    pass

            # Fallback to stdout if file was empty
            if not content:
                content = result.stdout.strip()
            stderr_text = (result.stderr or "")

            # D-068 fatigue detection — ChatGPT subscription substrate
            # surfaces quota / rate limits in similar patterns to Claude.
            # Free-tier ChatGPT especially hits these quickly. Patterns
            # checked in stderr + content (Codex sometimes emits status
            # text via stdout).
            #
            # 2026-05-12 (Cody): scan only the *post-prompt* portion of
            # stderr. Codex echoes the user prompt verbatim to stderr
            # between "user\n" and "codex\n" markers; if the creature
            # context (D-074 load_creature_context) includes Echo's
            # SELF.md Self-care history of prior fatigue events, the
            # echoed prompt itself carries "subscription_limit",
            # "Upgrade to Pro", "usage limit" — and the regex matches
            # the creature's own historical self-report instead of a
            # real new error. Result: a creature with prior fatigue
            # entries in SELF.md becomes permanently false-fatigued.
            # The post-"codex\n" tail still includes any real codex
            # error or post-completion message.
            # Use rsplit (last occurrence) not split (first occurrence):
            # if the prompt echo quotes prior codex stderr verbatim
            # (e.g., a SELF.md journal entry includes a previous
            # error capture with its own "user\n...\ncodex\n"
            # markers), the FIRST "\ncodex\n" occurrence will be
            # inside the prompt-echo region, and split(maxsplit=1)
            # would start the scan window mid-prompt — re-introducing
            # the original false-positive failure mode at a deeper
            # level of prompt nesting. The LAST occurrence is always
            # the real boundary between prompt-echo and codex output.
            # See test_prompt_echo_contains_literal_codex_marker_self_md_quote.
            stderr_scan = stderr_text
            if "\ncodex\n" in stderr_text:
                stderr_scan = stderr_text.rsplit("\ncodex\n", 1)[1]
            fatigue_match = _detect_codex_fatigue(stderr_scan + "\n" + content)
            if fatigue_match is not None:
                cause, detail, reset_s = fatigue_match
                msg = f"Codex / ChatGPT subscription limit reached (cause: {cause}). {detail}"
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
                logger.error(f"Codex CLI error: {error_msg}")
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
                },
            )

        except subprocess.TimeoutExpired:
            elapsed_ms = (time.time() - start) * 1000
            logger.error(f"Codex CLI timeout after {elapsed_ms:.0f}ms")
            if sandbox_cwd:
                shutil.rmtree(sandbox_cwd, ignore_errors=True)
            try:
                os.unlink(output_file)
            except Exception:
                pass
            return AdapterResponse(
                content="[Error: Codex CLI timed out]",
                raw={"timeout": True, "elapsed_ms": round(elapsed_ms, 1)},
            )
        except FileNotFoundError:
            if sandbox_cwd:
                shutil.rmtree(sandbox_cwd, ignore_errors=True)
            try:
                os.unlink(output_file)
            except Exception:
                pass
            return AdapterResponse(
                content=f"[Error: '{self._cmd}' not found. Is Codex CLI installed? npm install -g @openai/codex]",
                raw={"error": "command_not_found", "cmd": self._cmd},
            )

    def health_check(self) -> dict:
        """Check if Codex CLI is available."""
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
        """Codex CLI available models (OpenAI)."""
        return ["gpt-5", "o3", "o4-mini"]
