"""
Gemini CLI Adapter — subprocess-based, mirrors Claude CLI pattern.

Connects to Google's Gemini CLI as the LLM backend.
Uses `gemini -p "prompt"` for non-interactive single-shot calls.
MCP organ server wired via inline config (same pattern as Claude CLI).

Usage requires Gemini CLI installed:
    npm install -g @anthropic-ai/gemini-cli
    (or however Gemini CLI is distributed)

Reference: Claude CLI adapter pattern
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
from ludex.blocks.adapters._cli_env import cli_subprocess_env
from ludex.blocks.adapters._creature_context import load_creature_context

logger = logging.getLogger(__name__)

# shutil.which resolves the real executable (Windows PATHEXT: gemini.cmd from npm or
# gemini.exe — the hardcoded .cmd missed .exe installs). Fall back to the old default.
_GEMINI_CMD = shutil.which("gemini") or ("gemini.cmd" if os.name == "nt" else "gemini")


class GeminiCliAdapter(BaseAdapter):
    """Gemini CLI adapter via subprocess."""

    provider_name = "gemini_cli"

    def __init__(self, base_url: str = "", timeout_ms: int = 120000, cwd: str = "", auth: str = "", **kwargs):
        super().__init__(base_url=base_url or _GEMINI_CMD, timeout_ms=timeout_ms, **kwargs)
        self._cmd = base_url or _GEMINI_CMD
        self._cwd = cwd or None
        self._auth = auth  # birth-time auth mode (subscription|api); see _cli_env

    def call(self, model="", prompt="", system="", messages=None,
             temperature=0.7, max_tokens=4096, tools=None, effort=""):
        """
        Call Gemini CLI with -p flag (non-interactive/headless mode).

        System prompt: Gemini CLI doesn't have --system-prompt flag like Claude.
        We prepend it to the user message as context. Gemini is generally
        less sensitive to prompt injection than Claude Code.

        MCP: wired via gemini mcp add or inline approach.
        """
        # Extract system prompt + last user message (same pattern as claude_cli)
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

        # Gemini doesn't have --system-prompt; append as brief context AFTER the question.
        # Putting system context BEFORE the question causes Gemini to over-follow the
        # identity instructions and ignore the actual question (instruction adherence
        # rejection). Putting it after with explicit "answer the question above" framing
        # helps Gemini prioritize the user's actual request.
        if system_prompt:
            # Truncate system prompt to essentials for Gemini
            # (it doesn't need the full creature identity every turn)
            brief_system = system_prompt[:500] if len(system_prompt) > 500 else system_prompt
            full_prompt = (
                f"{full_prompt}\n\n"
                f"(Context about you: {brief_system}. "
                f"Answer the question above first, then stay in character.)"
            )

        # D-074: speech-act calls (tools is None/empty) must not have
        # workspace permissions. Pre-patch, gemini-cli ran with
        # --approval-mode yolo and subprocess cwd=creature_dir even
        # when no tools were registered, which gave the model implicit
        # filesystem access. Wick's apprenticeship-mode brain
        # consequently wrote to its own SELF.md and created a snapshot
        # dir during what was supposed to be a prose-only phase
        # (ray_academy_quill_wick_asymmetric_evidence_v1, 2026-05-06).
        # Speech-act path now: no yolo, no subprocess cwd. Agentic
        # path (tools truthy) preserves the prior behavior.
        agentic = bool(tools)

        # D-074 Phase A.1 — speech-act prompt directive. Even with file
        # ops blocked, gemini-cli's training emits planning prose
        # ("I will read X", "I'll examine Y") and then *fabricates*
        # citations as if the reads had occurred (verified
        # 2026-05-06 rerun: Wick referenced a non-existent wilderness
        # `ray_duo_quill_anvil_001`). This directive tells the model
        # to skip retrieval narration and produce only the requested
        # contribution. Speech-act path only; agentic path expects
        # planning prose.
        #
        # D-074 Phase B (2026-05-08) — creature-context provenance.
        # Phase A.1 mitigated the symptom (fabricated citations) but
        # not the cause: the brain wanted SELF.md / bonds context and
        # had no way to get it. Phase B reads that context for the
        # brain and prepends it to the prompt as text. The brain still
        # cannot mutate (no workspace), still cannot read unrequested
        # files (cwd not passed to subprocess), but now has its own
        # identity and recent state in-prompt and does not need to
        # invent. The Phase A.1 directive remains as a defence
        # against residual planning prose, but the "fabrication"
        # clause becomes mostly preventive — context is now present.
        # D-074 Phase A.2 (2026-05-09) — implicit pwd file-read sandbox.
        # Verified 2026-05-09 by `experiments/diag_gemini_pwd_2026-05-09/probe.py`:
        # gemini-cli reads files from its subprocess pwd even on the
        # speech-act path (no --include-directories, no yolo). With
        # cwd=None the subprocess inherits the parent python's pwd
        # (= project root in cohort runs), exposing every creature's
        # SELF.md / bonds and every experiment JSON. Wick's "Seed 509,
        # 8 Ticks, Final energy 11" fabrication in the post-Phase-B
        # Academy retry was actually a leak through this channel.
        # Phase A.2 routes non-agentic subprocess cwd to a fresh
        # tempdir so implicit pwd reads see an empty dir.
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
            # Use a neutral prefix that does NOT suggest a project domain.
            # 2026-05-09 validation rerun caught the model hallucinating a
            # "Gemini Speech" project (README, requirements.txt, main.py)
            # because the sandbox dir was named `gemini_speech_<rand>`. The
            # dir name itself ends up in the model's working-directory
            # context and is read as a project hint. `ludex_sb_` is opaque
            # enough to not seed any specific domain.
            sandbox_cwd = tempfile.mkdtemp(prefix="ludex_sb_")
        cmd = [self._cmd, "-p", full_prompt, "-o", "text"]
        if agentic:
            cmd.extend(["--approval-mode", "yolo"])
            if self._cwd:
                cmd.extend(["--include-directories", self._cwd])
        else:
            # D-074 Phase A.2 corollary (2026-05-09 validation rerun):
            # gemini-cli's "trusted folder" gate refuses to run in an
            # unknown directory unless --skip-trust (or env var
            # GEMINI_CLI_TRUST_WORKSPACE=true) is set. The sandbox
            # tempdir we route to is by construction not in the user's
            # trusted-folder list. --skip-trust bypasses the gate;
            # safe here because we have already (a) blocked workspace
            # tools (no --approval-mode yolo, no --include-directories)
            # and (b) routed cwd to an empty tempdir (sandbox_cwd).
            # Without this flag the call returns
            # `[Error: Gemini CLI is not running in a trusted directory]`.
            cmd.append("--skip-trust")
        if model and model not in ("gemini", "", None):
            cmd.extend(["-m", model])

        start = time.time()
        try:
            try:
                # D-074 Phase A.2 corollary belt-and-suspenders: set the
                # GEMINI_CLI_TRUST_WORKSPACE env var in addition to the
                # --skip-trust flag. Verified 2026-05-09 that the flag
                # alone is not enough to satisfy the trust gate when
                # invoked through gemini.cmd on Windows; the env var
                # is the documented headless escape hatch and is more
                # reliable across CLI versions.
                child_env = cli_subprocess_env("gemini_cli", self._auth)
                if not agentic:
                    child_env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_ms / 1000,
                    encoding="utf-8",
                    # gemini-cli on Windows occasionally emits OEM-codepage
                    # spinner/progress bytes on stderr; without errors=replace
                    # the reader thread crashes with UnicodeDecodeError mid-run.
                    # Verified 2026-05-09 Quill+Wick Academy apprentice_application
                    # phase exited with this exact failure mode.
                    errors="replace",
                    env=child_env,
                    cwd=(self._cwd if agentic else sandbox_cwd),
                )
            finally:
                if sandbox_cwd:
                    shutil.rmtree(sandbox_cwd, ignore_errors=True)

            elapsed_ms = (time.time() - start) * 1000
            content = result.stdout.strip()
            stderr_text = (result.stderr or "")

            # Detect quota exhaustion explicitly. Gemini CLI surfaces this as
            # a TerminalQuotaError on stderr with "QUOTA_EXHAUSTED" and a
            # `quota will reset after Xh Ym Zs` message. Without this branch,
            # quota errors look like generic "Gemini CLI timed out" or
            # "[object Object]" critical errors — opaque and easy to misread
            # as substrate / prompt issues. Surfaced 2026-04-27.
            #
            # No scan-window narrowing needed here (parallel to claude_cli;
            # unlike codex_cli which uses rsplit on "\ncodex\n"). Audited
            # 2026-05-13: Gemini CLI's stderr is empty on success — no
            # prompt echo, no SELF.md content reflected back. Detection
            # tokens ("QUOTA_EXHAUSTED", "TerminalQuotaError") are also
            # extremely specific identifiers unlikely to appear in
            # creature_context even if the stderr shape changes.
            if "QUOTA_EXHAUSTED" in stderr_text or "TerminalQuotaError" in stderr_text:
                # Try to extract the reset window for caller context.
                reset_msg = ""
                m = re.search(r"reset after\s+([0-9hms ]+)", stderr_text)
                if m:
                    reset_msg = f" Quota resets in {m.group(1).strip()}."
                err = f"Gemini quota exhausted (HTTP 429).{reset_msg}"
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
                logger.error(f"Gemini CLI error: {error_msg}")
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
            if sandbox_cwd:
                shutil.rmtree(sandbox_cwd, ignore_errors=True)
            return AdapterResponse(
                content="[Error: Gemini CLI timed out]",
                raw={"timeout": True, "elapsed_ms": round(elapsed_ms, 1)},
            )
        except FileNotFoundError:
            if sandbox_cwd:
                shutil.rmtree(sandbox_cwd, ignore_errors=True)
            return AdapterResponse(
                content=f"[Error: '{self._cmd}' not found. Is Gemini CLI installed?]",
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
        return ["gemini-2.5-flash", "gemini-2.5-pro"]
