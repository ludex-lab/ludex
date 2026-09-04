"""
Antigravity CLI Adapter — subprocess-based; speech-act by default, bounded
agentic (edits-only inside the bench cwd) when tools are passed — see call().

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

from ludex.blocks.adapters._liveness import run_traced
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
        # Bounded agentic mode (2026-09-02, founder: the agy tribe sat idle
        # because this adapter was speech-act only). `--mode accept-edits
        # --add-dir <cwd>` auto-approves FILE EDITS inside the caller's cwd
        # (the bench) and nothing else: headless agy auto-DENIES any tool
        # that needs the "command" permission, so shell stays closed — the
        # D-074 class this module's policy guards against. Measured 09-02:
        # write via edit tool OK, shell attempt denied. Still never
        # --dangerously-skip-permissions.
        agentic = bool(tools)
        if agentic and not self._cwd:
            err = "agy agentic call needs a cwd (the bench) — refusing to edit an unbounded tree"
            logger.error(err)
            return AdapterResponse(content=f"[Error: {err}]", raw={"error": "agentic_no_cwd"})

        # Model routing. Until agy v1.0.2 `-p` exposed no `--model`, so the
        # adapter pinned the default and rejected anything else. v1.1.9 does
        # expose it (measured 2026-08-01: `--model gemini-3.6-flash --effort
        # medium` routes; a bogus name errors with the menu, so the flag is
        # really consulted). agy REQUIRES --effort alongside --model — effort
        # is part of model selection there ("Gemini 3.6 Flash (Medium)"), not a
        # separate dial.
        #
        # Deliberately narrow: when the requested model is the default, the
        # command is built exactly as before, with no flags. Passing an
        # explicit effort to creatures born without one would move them along
        # substrate axis E as a side effect of a bug fix, and effort changes
        # go through a deliberate re-brain, never through ops.
        model_flags = []
        if model and model != _PINNED_MODEL:
            if not effort:
                err = (f"agy --model {model} requires an effort "
                       f"(low|medium|high); creature has none pinned")
                logger.error(err)
                return AdapterResponse(
                    content=f"[Error: {err}]",
                    raw={"error": "effort_required", "requested": model},
                )
            model_flags = ["--model", model, "--effort", effort]

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
        if agentic:
            # Tell the brain what this mode can and cannot do — measured 09-03:
            # left to itself agy reaches for the shell "command" tool, headless
            # auto-denies it twice and the turn ends empty; told up front, it
            # uses the file tools and the same task completes.
            full_prompt = (
                "Headless bounded mode: shell/command tools are auto-DENIED here and "
                "cannot be approved. Use only the file read/write/edit tools, with "
                "paths relative to the current working directory.\n\n" + full_prompt
            )
        # Agentic calls run in the real bench cwd instead (writes are bounded
        # by --add-dir + cwd, the same shape the grok/codex lanes use).
        sandbox_cwd = None if agentic else tempfile.mkdtemp(prefix="ludex_sb_")
        run_cwd = self._cwd if agentic else sandbox_cwd
        agentic_flags = ["--mode", "accept-edits", "--add-dir", self._cwd] if agentic else []

        # Windows argv cap: WinError 206 at >=34KB (measured 2026-07-15, Wick
        # 80-day catch-up consolidation). Large prompts go via STDIN — `agy`
        # with no -p reads the prompt from stdin (verified v1.0.10, 50KB OK).
        # Small prompts keep the long-tested argv path.
        _ARGV_SAFE_CHARS = 30000
        use_stdin = len(full_prompt) > _ARGV_SAFE_CHARS
        cmd = ([self._cmd] if use_stdin else [self._cmd, "-p", full_prompt]) + model_flags + agentic_flags
        # HARD POLICY: never pass --dangerously-skip-permissions. Folder trust
        # alone does NOT auto-approve tool calls in v1.0.2.

        start = time.time()
        try:
            try:
                child_env = cli_subprocess_env("agy_cli", self._auth)
                run_kwargs = dict(
                    timeout=self.timeout_ms / 1000,
                    encoding="utf-8",
                    errors="replace",
                    env=child_env,
                    cwd=run_cwd,
                    tag="agy_cli",
                )
                if use_stdin:
                    # Large prompt: feed via stdin (argv would hit WinError 206).
                    run_kwargs["input"] = full_prompt
                else:
                    # Headless: never inherit the parent's stdin. agy reads the prompt
                    # from argv (-p), not stdin; an inherited TTY/console stdin can hang
                    # the call waiting on interactive input — observed on Windows agy.cmd
                    # (2026-06-22). DEVNULL gives the child immediate EOF instead.
                    run_kwargs["stdin"] = subprocess.DEVNULL
                if agentic:
                    # 2026-09-03: bench calls go through the headless state
                    # machine (NDJSON stream) — state trace, fail-fast on auth
                    # (agy otherwise waits 60s for a browser) and permission
                    # denial. Observation mode for idle stalls. See
                    # research/headless-liveness/RESEARCH.md.
                    from ludex.blocks.adapters._headless_state import run_streamed, STREAM_FLAGS
                    result, _state = run_streamed(
                        cmd + STREAM_FLAGS["agy"], "agy", env=child_env, cwd=run_cwd,
                        input=full_prompt if use_stdin else None,
                        idle_s=120.0, hard_cap_s=self.timeout_ms / 1000)
                    _tele = _state.summary()
                    if _state.ended_by == "hard_cap":
                        raise subprocess.TimeoutExpired(cmd, self.timeout_ms / 1000)
                    if _state.ended_by == "fail_fast":
                        msg = f"agy {_state.signature}: {_state.signature_detail[:160]}"
                        logger.error(msg)
                        return AdapterResponse(content=f"[Error: {msg}]",
                                               raw={"error": _state.signature, "liveness": _tele,
                                                    "stderr": (result.stderr or "")[:1000]})
                    if not result.stdout.strip() and (_state.signature == "permission" or _state.tool_errors):
                        # agy's auto-deny (or a run of failed tool steps) ends the
                        # turn with an empty SUCCESS — name it instead of
                        # reporting an empty brain
                        why = "permission" if _state.signature == "permission" else "tool_errors"
                        msg = f"agy {why}: {_state.signature_detail[:160] or f'{_state.tool_errors} failed tool steps, empty response'}"
                        logger.error(msg)
                        return AdapterResponse(content=f"[Error: {msg}]",
                                               raw={"error": why, "liveness": _tele})
                else:
                    result, _tele = run_traced(cmd, **run_kwargs)
            finally:
                if sandbox_cwd:
                    shutil.rmtree(sandbox_cwd, ignore_errors=True)

            elapsed_ms = (time.time() - start) * 1000
            content = result.stdout.strip()
            stderr_text = (result.stderr or "")

            # An invalid flag is a CONTRACT error, not a quiet brain. agy answers
            # `--effort dynamic` on a routed model with "invalid model selection"
            # on stderr and nothing on stdout, which every caller upstream reads
            # as "the brain had nothing to say" — Ray hit it as an empty reflect
            # during a rebrain. The walk lane would have caught it via VOID-brain;
            # the creature lane had no such machine, so it is caught here.
            if not content and "invalid model selection" in stderr_text.lower():
                err = f"agy rejected the call contract: {stderr_text.strip()[:200]}"
                logger.error(err)
                return AdapterResponse(
                    content=f"[Error: {err}]",
                    raw={"error": "invalid_flag", "stderr": stderr_text,
                         "elapsed_ms": round(elapsed_ms, 1)},
                )

            # Quota detection: exact pattern not yet characterized for agy.
            # Generic heuristic until a real quota event lets us pin the
            # format. Refine the token list when observed.
            lower_err = stderr_text.lower()
            # Headless tool-permission denial (2026-08-01, PhysGym smoke):
            # agy tries a tool, headless cannot prompt for permission, the
            # call is auto-denied and STDOUT comes back EMPTY while stderr
            # carries the reason. Returning "" made this look like a silent
            # brain failure for a whole smoke run. Surface it instead —
            # investigation-flavoured prompts (reports, experiment design)
            # trigger it, which is exactly the physics-wall context.
            if not content and ("permission" in lower_err
                                and ("auto-denied" in lower_err
                                     or "headless" in lower_err)):
                err = ("agy tool-permission denied in headless mode (empty "
                       "output); prompt likely triggered a tool call")
                logger.error(f"{err}: {stderr_text.strip()[:200]}")
                return AdapterResponse(
                    content=f"[Error: {err}]",
                    # elapsed_ms belongs in raw — AdapterResponse has no such
                    # field, so passing it as a kwarg made this error path
                    # raise its own TypeError. It went unexercised until the
                    # canary walked into a tool-denial, i.e. the diagnosis
                    # branch failed exactly when a diagnosis was needed.
                    raw={"returncode": result.returncode,
                         "error": "headless_tool_denied",
                         "stderr": stderr_text,
                         "elapsed_ms": round(elapsed_ms, 1)},
                )
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
                    "model": model or _PINNED_MODEL,
                    "effort": effort,
                    # agentic: the headless state trace; speech: byte liveness
                    "liveness": _tele,
                    # agentic streams carry the CLI's own usage — the meter Ember
                    # measured (09-03): real tokens, not len/4
                    "usage": getattr(result, "usage", None),
                },
            )

        except subprocess.TimeoutExpired:
            elapsed_ms = (time.time() - start) * 1000
            if sandbox_cwd:
                shutil.rmtree(sandbox_cwd, ignore_errors=True)
            return AdapterResponse(
                content="[Error: agy CLI timed out]",
                raw={"timeout": True, "elapsed_ms": round(elapsed_ms, 1)},
            )
        except FileNotFoundError:
            if sandbox_cwd:
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
