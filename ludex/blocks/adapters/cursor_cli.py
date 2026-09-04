"""
Cursor CLI Adapter — subprocess-based, mirrors the Grok/Codex CLI pattern.

Connects to Cursor's `cursor-agent` as the LLM backend. Opened by the Grok
Super Heavy bundle's Cursor Ultra subscription (2026-08-17, Organum preflight);
this is the village's road to the Kimi/GLM lineages — the first non-US
frontier brains — plus Cursor's own Composer.

Headless verified live 2026-08-18 (kimi-k3-low):
    cursor-agent -p "<prompt>" --output-format json --mode ask --trust
→ one JSON envelope on stdout:
    {"type":"result","subtype":"success","is_error":false,"duration_ms":…,
     "result":"…","session_id":"…","usage":{"inputTokens":…,"outputTokens":…,
     "cacheReadTokens":…,"cacheWriteTokens":…}}

Two properties matter here:
- **usage is in-band and real** — the first CLI brain whose spans can carry
  token_source="measured" instead of len//4 estimates.
- **effort lives inside the model id** (kimi-k3-low/-high/-max …). Ludex keeps
  M and E as separate substrate axes, so `call()` takes (model, effort) apart
  and composes the wire id internally — the birth config never writes a fused
  id. (Same separation Organum adopted for their inspector adapter, 08-18.)

No system-prompt flag exists, so system context is prepended to the prompt
(codex/gemini pattern, not grok's real system role). Session transcripts
persist under ~/.cursor (JSONL+SQLite) — observability per Organum, but also
a disk-residue lane to watch (cf. ~/.grok/sessions leak note).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import logging

from ludex.blocks.adapters.base import BaseAdapter, AdapterResponse
from ludex.blocks.adapters._cli_env import cli_subprocess_env
from ludex.blocks.adapters._creature_context import load_creature_context
from ludex.blocks.adapters._fatigue import parse_reset_at as _parse_reset_at

logger = logging.getLogger(__name__)

def _resolve_cursor_cmd() -> list[str]:
    """Locate cursor-agent, preferring its node entrypoint on Windows.

    The Windows install ships only `cursor-agent.cmd`, a batch file that
    re-launches PowerShell with `%*`. A prompt containing newlines does not
    survive that hop: everything after the first newline is dropped, taking
    the trailing flags with it, so `--output-format json --mode ask --trust`
    silently vanish and the CLI answers in prose or stops to ask for
    workspace trust. Every creature prompt is multi-line, so on Windows the
    wrapper is unusable. The wrapper itself just runs
    `versions/<v>/node.exe versions/<v>/index.js`, so we call that directly
    and the arguments arrive intact (2026-08-22, Ray lab).
    """
    exe = shutil.which("cursor-agent")
    if exe and os.name == "nt":
        versions = os.path.join(os.path.dirname(exe), "versions")
        if os.path.isdir(versions):
            for name in sorted(os.listdir(versions), reverse=True):
                node = os.path.join(versions, name, "node.exe")
                entry = os.path.join(versions, name, "index.js")
                if os.path.exists(node) and os.path.exists(entry):
                    return [node, entry]
    return [exe or "cursor-agent"]


_CURSOR_CMD = _resolve_cursor_cmd()

# PROVISIONAL fatigue set — quota/rate strings for the Cursor Ultra pool are
# UNVERIFIED (burn rate unmeasured per Organum's preflight note). Mirrors the
# grok set; refine against the first real captured limit.
_CURSOR_FATIGUE_PATTERNS = [
    (re.compile(r"\busage limit\b", re.IGNORECASE), "subscription_limit"),
    (re.compile(r"\brate[_\s-]?limit\b", re.IGNORECASE), "rate_limited"),
    (re.compile(r"\bquota\b", re.IGNORECASE), "quota_exhausted"),
    (re.compile(r"\b429\b"), "rate_limited"),
    (re.compile(r"\binsufficient[_\s]quota\b", re.IGNORECASE), "quota_exhausted"),
]

# Effort tiers that appear as model-id suffixes in cursor-agent --list-models.
_EFFORT_SUFFIXES = ("none", "low", "medium", "high", "xhigh", "max")


def _compose_model_id(model: str, effort: str) -> str:
    """(base_model, effort) → wire id. A model already carrying an effort
    suffix wins over the effort arg (explicit full id = operator override)."""
    if not model:
        return model
    tail = model.rsplit("-", 1)[-1]
    if tail in _EFFORT_SUFFIXES or tail == "fast":
        return model
    if effort:
        return f"{model}-{effort}"
    return model


def _detect_cursor_fatigue(text: str):
    if not text:
        return None
    for pat, cause in _CURSOR_FATIGUE_PATTERNS:
        m = pat.search(text)
        if m:
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 80)
            return cause, text[start:end].strip(), _parse_reset_at(text)
    return None


class CursorCliAdapter(BaseAdapter):
    """Cursor CLI adapter via subprocess."""

    provider_name = "cursor_cli"

    def __init__(self, base_url: str = "", timeout_ms: int = 240000, cwd: str = "", auth: str = "", **kwargs):
        super().__init__(base_url=base_url or _CURSOR_CMD[0], timeout_ms=timeout_ms, **kwargs)
        # Always argv. provider.DEFAULT_BASE_URLS injects _CURSOR_CMD[0], so a
        # base_url matching it means "the default" and expands to the full
        # argv — on Windows that is [node.exe, index.js]. Anything else is a
        # genuine operator override and is taken as the whole command.
        if not base_url or base_url == _CURSOR_CMD[0]:
            self._cmd = list(_CURSOR_CMD)
        else:
            self._cmd = [base_url] if isinstance(base_url, str) else list(base_url)
        self._cwd = cwd or None
        self._auth = auth  # birth-time auth mode (subscription|api); see _cli_env

    def call(self, model="", prompt="", system="", messages=None,
             temperature=0.7, max_tokens=4096, tools=None, effort=""):
        """Call cursor-agent headless. System context is prepended (no system
        flag); last user message only (single-message default, same as the
        other CLIs). Speech acts run `--mode ask` (read-only Q&A) in an empty
        sandbox cwd; agentic calls get the real cwd + --force."""
        system_prompt = system or ""
        last_user_msg = ""
        if messages:
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    system_prompt = content if not system_prompt else system_prompt + "\n\n" + content
                elif role == "user":
                    last_user_msg = content
            full_prompt = last_user_msg
        else:
            full_prompt = prompt
        if not full_prompt:
            full_prompt = "(no message)"

        agentic = bool(tools)
        sandbox_cwd: str | None = None
        if not agentic:
            creature_context = load_creature_context(self._cwd)
            directive = (
                "" if os.environ.get("LUDEX_SUPPRESS_SPEECH_DIRECTIVE") == "1" else
                "Respond directly with your contribution. Do not narrate "
                "retrieval, exploration, or planning steps (no \"I will read X\" "
                "/ \"I'll examine Y\"). If context beyond what is in this prompt "
                "would change your answer, say so plainly rather than fabricating "
                "a citation.\n\n"
            )
            full_prompt = directive + creature_context + full_prompt
            sandbox_cwd = tempfile.mkdtemp(prefix="ludex_sb_")

        if system_prompt:
            full_prompt = f"System context:\n{system_prompt}\n\n---\n\n{full_prompt}"

        wire_model = _compose_model_id(model, effort)
        cmd = [*self._cmd, "-p", full_prompt, "--output-format", "json"]
        if wire_model and wire_model != "auto":
            cmd.extend(["--model", wire_model])
        if agentic:
            cmd.append("--force")
        else:
            # Read-only Q&A mode + trust on an EMPTY mkdtemp cwd: nothing to
            # read, nowhere to write — a speech act must never touch disk.
            cmd.extend(["--mode", "ask", "--trust"])

        start = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_ms / 1000,
                encoding="utf-8",
                errors="replace",
                env=cli_subprocess_env("cursor_cli", self._auth),
                cwd=self._cwd if agentic else sandbox_cwd,
                stdin=subprocess.DEVNULL,
            )
            elapsed_ms = (time.time() - start) * 1000
            stdout_text = (result.stdout or "").strip()
            stderr_text = (result.stderr or "")

            fatigue_match = _detect_cursor_fatigue(stderr_text + "\n" + stdout_text)
            if fatigue_match is not None:
                cause, detail, reset_s = fatigue_match
                msg = f"Cursor subscription limit reached (cause: {cause}). {detail}"
                if reset_s is not None:
                    msg += f" Reset in ~{reset_s / 3600:.1f}h."
                logger.error(msg)
                raw = {"returncode": result.returncode, "error": cause,
                       "stderr": stderr_text, "fatigue_detail": detail}
                if reset_s is not None:
                    raw["reset_in_seconds"] = reset_s
                return AdapterResponse(content=f"[Error: {msg}]", raw=raw)

            # One JSON envelope on stdout (verified 2026-08-18). Anything else
            # is an error surface, not a degraded answer.
            envelope = None
            for line in stdout_text.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "result":
                        envelope = obj
                        break

            if envelope is None or envelope.get("is_error"):
                error_msg = (envelope or {}).get("result") or stderr_text.strip() \
                    or stdout_text[:300] or f"CLI exited with code {result.returncode}"
                logger.error(f"Cursor CLI error: {error_msg}")
                return AdapterResponse(content=f"[Error: {error_msg}]",
                                       raw={"returncode": result.returncode,
                                            "stderr": stderr_text[:1000],
                                            "stdout_head": stdout_text[:300]})

            content = (envelope.get("result") or "").strip()
            usage = envelope.get("usage") or {}
            tokens_in = int(usage.get("inputTokens") or 0)
            tokens_out = int(usage.get("outputTokens") or 0)
            measured = bool(usage) and (tokens_in or tokens_out)
            return AdapterResponse(
                content=content,
                tokens_in=tokens_in if measured else len(full_prompt) // 4,
                tokens_out=tokens_out if measured else len(content) // 4,
                # Routing evidence in-band (measured≠asserted): wire id + argv
                # prove what brain ran at what effort; usage rides along so the
                # span layer can mark token_source="measured".
                raw={"returncode": result.returncode, "elapsed_ms": round(elapsed_ms, 1),
                     "cmd": self._cmd, "model": model, "effort": effort,
                     "wire_model": wire_model, "usage": usage,
                     "token_source": "measured" if measured else "estimated",
                     "session_id": envelope.get("session_id", ""),
                     "argv": [a for a in cmd if a != full_prompt]},
            )

        except subprocess.TimeoutExpired as e:
            elapsed_ms = (time.time() - start) * 1000
            po = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            pe = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            logger.error(f"Cursor CLI timeout after {elapsed_ms:.0f}ms | "
                         f"partial stdout[:300]={po[:300]!r} | partial stderr[:300]={pe[:300]!r}")
            return AdapterResponse(content="[Error: Cursor CLI timed out]",
                                   raw={"timeout": True, "elapsed_ms": round(elapsed_ms, 1),
                                        "partial_stdout": po[:1000], "partial_stderr": pe[:1000]})
        except FileNotFoundError:
            return AdapterResponse(
                content=f"[Error: '{self._cmd}' not found. Is cursor-agent installed and on PATH?]",
                raw={"error": "command_not_found", "cmd": self._cmd},
            )
        finally:
            if sandbox_cwd:
                shutil.rmtree(sandbox_cwd, ignore_errors=True)

    def health_check(self) -> dict:
        try:
            result = subprocess.run([*self._cmd, "-v"], capture_output=True, text=True, timeout=10)
            version = result.stdout.strip() or result.stderr.strip()
            return {"status": "ok", "version": version, "cmd": self._cmd}
        except FileNotFoundError:
            return {"status": "error", "error": f"'{self._cmd}' not found"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def list_models(self) -> list[str]:
        """The genuinely-new terrain among cursor-agent's 208 ids (2026-08-18
        census — the rest are effort×thinking×fast variants of lineages the
        village already hosts). Base ids only: effort is axis E, composed by
        the adapter, never written fused into a birth config."""
        return ["kimi-k3", "kimi-k2.7-code", "glm-5.2", "composer-2.5"]
