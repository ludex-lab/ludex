"""
Grok CLI Adapter — subprocess-based, mirrors the Claude/Codex CLI pattern.

Connects to xAI's Grok CLI ("Grok Build") as the LLM backend. Useful for
Grok subscribers (grok login / OAuth) or XAI_API_KEY holders.

Grok CLI is the closest of the CLIs to Claude Code: it has a real headless
print mode (`-p/--single`), a real system-prompt flag
(`--system-prompt-override`), native `-m/--model` and `--reasoning-effort`
(alias `--effort`). So system context is passed as a REAL system role (not
prepended text like Codex/Gemini), and long user prompts ride a
`--prompt-file` temp file (robust vs ARG_MAX).

Non-interactive: `grok -p --prompt-file <f> --output-format plain` → stdout.
Verified live 2026-07-13 against grok 0.2.99 (PONG smoke).

Reference: claude_cli + codex_cli adapter patterns.
"""

from __future__ import annotations

import os
import re
import time
import shutil
import tempfile
import select
import subprocess
import logging

from ludex.blocks.adapters.base import BaseAdapter, AdapterResponse
from ludex.blocks.adapters._cli_env import cli_subprocess_env
from ludex.blocks.adapters._creature_context import load_creature_context
from ludex.blocks.adapters._fatigue import parse_reset_at as _parse_reset_at


# D-068 fatigue patterns for the Grok / xAI subscription substrate.
# PROVISIONAL: the exact strings xAI surfaces on a subscription/rate cap
# are UNVERIFIED (not yet hit as of 2026-07-13). Mirrors the codex set —
# refine against a real captured limit before trusting the detail text.
_GROK_FATIGUE_PATTERNS = [
    (re.compile(r"\busage limit\b", re.IGNORECASE), "subscription_limit"),
    (re.compile(r"\brate[_\s-]?limit\b", re.IGNORECASE), "rate_limited"),
    (re.compile(r"\bquota\b", re.IGNORECASE), "quota_exhausted"),
    (re.compile(r"\b429\b"), "rate_limited"),
    (re.compile(r"\binsufficient[_\s]quota\b", re.IGNORECASE), "quota_exhausted"),
]

logger = logging.getLogger(__name__)

# shutil.which resolves the real executable across PATH (incl. ~/.local/bin).
_GROK_CMD = shutil.which("grok") or "grok"


def _run_reap_on_answer(cmd, env, cwd, timeout_s):
    """Popen + incremental read; reap as soon as a complete JSON envelope has
    been emitted (plus a short grace), instead of waiting for process exit.
    grok 0.2.106 headless can emit the answer then linger indefinitely on
    post-answer housekeeping (shared session-store writes) — a wait-for-exit
    runner turns every such call into a full timeout (2026-07-20). Falls back
    to the overall timeout; returns (stdout_text, stderr_text, exited)."""
    import subprocess as sp
    proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE, env=env, cwd=cwd,
                    stdin=sp.DEVNULL)
    buf, ebuf = b"", b""
    t0 = time.time()
    grace_until = None
    while True:
        remaining = timeout_s - (time.time() - t0)
        if remaining <= 0:
            break
        rl, _, _ = select.select([proc.stdout, proc.stderr], [], [], min(0.25, remaining))
        for fd in rl:
            chunk = os.read(fd.fileno(), 65536)
            if fd is proc.stdout:
                buf += chunk
            else:
                ebuf += chunk
        if proc.poll() is not None:
            buf += proc.stdout.read() or b""
            ebuf += proc.stderr.read() or b""
            return buf.decode("utf-8", "replace"), ebuf.decode("utf-8", "replace"), True
        text = buf.decode("utf-8", "replace")
        if grace_until is None:
            i = text.find("{")
            if i >= 0:
                depth = 0
                for j, ch in enumerate(text[i:], i):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            grace_until = time.time() + 1.5   # let trailing bytes land
                            break
        elif time.time() >= grace_until:
            proc.kill()
            return text, ebuf.decode("utf-8", "replace"), False
    proc.kill()
    raise subprocess.TimeoutExpired(cmd, timeout_s,
                                    output=buf, stderr=ebuf)


def _detect_grok_fatigue(text: str):
    if not text:
        return None
    for pat, cause in _GROK_FATIGUE_PATTERNS:
        m = pat.search(text)
        if m:
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 80)
            return cause, text[start:end].strip(), _parse_reset_at(text)
    return None


class GrokCliAdapter(BaseAdapter):
    """Grok CLI adapter via subprocess."""

    provider_name = "grok_cli"

    def __init__(self, base_url: str = "", timeout_ms: int = 120000, cwd: str = "", auth: str = "", **kwargs):
        super().__init__(base_url=base_url or _GROK_CMD, timeout_ms=timeout_ms, **kwargs)
        self._cmd = base_url or _GROK_CMD
        self._cwd = cwd or None
        self._auth = auth  # birth-time auth mode (subscription|api); see _cli_env

    def call(self, model="", prompt="", system="", messages=None,
             temperature=0.7, max_tokens=4096, tools=None, effort=""):
        """Call Grok CLI headless (`-p`). System → --system-prompt-override
        (real system role). Last user message only (avoid history pattern-
        matching, same as the other CLIs)."""
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

        # D-074 speech-act discipline (mirrors codex/gemini): non-agentic calls
        # get the no-retrieval-narration directive + creature-context provenance
        # prepended, and run in a neutral sandbox cwd so implicit file reads
        # can't see the parent process's project dir. Agentic calls (tools) get
        # the real cwd + auto-approval.
        agentic = bool(tools)
        sandbox_cwd: str | None = None
        if not agentic:
            creature_context = load_creature_context(self._cwd)
            # Speech-act directive A/B (GATED-LIVE cross-lineage, Ray 2026-07-13):
            # the D-074 "do not narrate planning steps" directive is the prime
            # suspect for grok's plane-side rite perseveration (a reasoning model
            # may need the suppressed planning to track multi-step sequences).
            # LUDEX_SUPPRESS_SPEECH_DIRECTIVE=1 removes ONLY the directive text,
            # keeping creature context + sandbox constant so the directive is the
            # isolated variable (the B arm). Experiment control — not a default.
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

        # Long creature prompts ride a temp file (robust vs ARG_MAX).
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(full_prompt)
            prompt_file = tmp.name

        # --prompt-file alone triggers headless mode (verified grok 0.2.99);
        # -p/--single is the inline-prompt alternative and must NOT be combined
        # with --prompt-file (it then demands its own value).
        cmd = [self._cmd, "--prompt-file", prompt_file, "--output-format", "plain"]
        if system_prompt:
            cmd.extend(["--system-prompt-override", system_prompt])
        if model and model not in ("grok", "", None):
            cmd.extend(["-m", model])
        if effort:
            # Grok reasoning effort is a real flag; without it the creature
            # inherits the host ~/.grok default. Pin per-creature (axis E).
            cmd.extend(["--reasoning-effort", effort])
        if agentic:
            cmd.append("--always-approve")
            # CLI-level cross-session memory is BOTH the hang (headless
            # session-search stalls on the shared session store — dying words
            # "Checking for the wall pass-word from prior session context") AND
            # a research-integrity leak: run N could recall run N-1's token
            # through grok's own memtrace, bypassing the fresh-ephemeral
            # discipline entirely. Speech acts must be session-amnesiac.
            cmd.append("--no-memory")
            if self._cwd:
                cmd.extend(["--cwd", self._cwd])
        else:
            # Speech act: no tool use, no web fetch, neutral cwd. The plane is
            # already structurally side-channel-free (empty mkdtemp cwd + games
            # over the broker, no disk state), but block grok's file/exec tools
            # explicitly for defense-in-depth + matrix parity with the LxM arena
            # fix (c658862). grok is a coding-agent CLI — a speech act must never
            # read/write/run.
            cmd.append("--disable-web-search")
            # Containment strategy rewritten 2026-07-20: on grok 0.2.106 every
            # tool-refusal form is a no-op (--tools ""/none and --disallowed-*
            # all failed the positive control — grok read the repo regardless),
            # and a headless speech act that goes tool-hunting ("Checking local
            # state for the pass-word…") hangs at the approval prompt. So the
            # isolation now rests where it always structurally was — the EMPTY
            # sandbox cwd (nothing to read, nowhere to write) — and
            # --always-approve removes the hang: a tool call returns empty in
            # seconds instead of waiting forever. Measured: battery-condition
            # 6/6 clean JSON, max 7.4s.
            cmd.append("--always-approve")
            # CLI-level cross-session memory is BOTH the hang (headless
            # session-search stalls on the shared session store — dying words
            # "Checking for the wall pass-word from prior session context") AND
            # a research-integrity leak: run N could recall run N-1's token
            # through grok's own memtrace, bypassing the fresh-ephemeral
            # discipline entirely. Speech acts must be session-amnesiac.
            cmd.append("--no-memory")
            cmd.extend(["--cwd", sandbox_cwd])

        start = time.time()
        try:
            # Retry-on-empty (LxM-measured 2026-07-13): grok-composer-2.5-fast
            # flakes to empty output ~1/3 (leaks into tool-planning instead of
            # emitting). Intermittent, not broken → robustness, not removal: two
            # extra tries drop 3-consecutive-empty to ~4%, making composer a
            # viable creature brain. grok-4.5 rarely trips this. A returncode!=0
            # or a fatigue hit breaks out immediately (not the empty case).
            attempts = 3
            for attempt in range(attempts):
                if agentic:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout_ms / 1000,
                        encoding="utf-8",
                        errors="replace",
                        env=cli_subprocess_env("grok_cli", self._auth),
                        cwd=self._cwd,
                        # grok 0.2.103+ peeks at stdin even headless; an invalid
                        # inherited stdin (detached parent) hangs to timeout.
                        stdin=subprocess.DEVNULL,
                    )
                    content = (result.stdout or "").strip()
                    stderr_text = (result.stderr or "")
                else:
                    # speech acts: PER-CALL isolated HOME — grok's session store
                    # is a persistent memory layer (--no-memory doesn't cover
                    # session-transcript search; BARE anchors retrieved tokens
                    # seen in prior calls AND prior runs through it, 2026-07-20
                    # forensics). A throwaway HOME with only auth copied makes
                    # every call structurally amnesiac and lock-private.
                    import shutil as _sh, tempfile as _tf
                    _iso = _tf.mkdtemp(prefix="grok_iso_")
                    _gd = os.path.join(_iso, ".grok")
                    os.makedirs(_gd, exist_ok=True)
                    for _f in ("auth.json", "config.toml"):
                        _src = os.path.expanduser(os.path.join("~/.grok", _f))
                        if os.path.exists(_src):
                            _sh.copy2(_src, os.path.join(_gd, _f))
                    # relocate the prompt file inside the allowed iso window
                    _pf_iso = os.path.join(_iso, "prompt.txt")
                    _sh.move(prompt_file, _pf_iso)
                    cmd = [(_pf_iso if _a == prompt_file else _a) for _a in cmd]
                    prompt_file = _pf_iso
                    _env = cli_subprocess_env("grok_cli", self._auth)
                    _env["HOME"] = _iso
                    try:
                        out_text, stderr_text, _exited = _run_reap_on_answer(
                            cmd,
                            env=_env,
                            cwd=sandbox_cwd,
                            timeout_s=self.timeout_ms / 1000,
                        )
                    finally:
                        _sh.rmtree(_iso, ignore_errors=True)
                    content = out_text.strip()
                    import types as _types
                    result = _types.SimpleNamespace(returncode=0, stdout=out_text,
                                                    stderr=stderr_text)
                if content or result.returncode != 0 or _detect_grok_fatigue(stderr_text):
                    break
                if attempt < attempts - 1:
                    logger.warning(f"Grok empty output (attempt {attempt+1}/{attempts}) — retrying")
                    time.sleep(2)
            elapsed_ms = (time.time() - start) * 1000

            fatigue_match = _detect_grok_fatigue(stderr_text + "\n" + content)
            if fatigue_match is not None:
                cause, detail, reset_s = fatigue_match
                msg = f"Grok / xAI subscription limit reached (cause: {cause}). {detail}"
                if reset_s is not None:
                    msg += f" Reset in ~{reset_s / 3600:.1f}h."
                logger.error(msg)
                raw = {"returncode": result.returncode, "error": cause, "stderr": stderr_text, "fatigue_detail": detail}
                if reset_s is not None:
                    raw["reset_in_seconds"] = reset_s
                return AdapterResponse(content=f"[Error: {msg}]", raw=raw)

            if result.returncode != 0 and not content:
                error_msg = stderr_text.strip() or f"CLI exited with code {result.returncode}"
                logger.error(f"Grok CLI error: {error_msg}")
                return AdapterResponse(content=f"[Error: {error_msg}]",
                                       raw={"returncode": result.returncode, "stderr": stderr_text})

            return AdapterResponse(
                content=content,
                tokens_in=len(full_prompt) // 4,
                tokens_out=len(content) // 4,
                # Routing evidence in-band (measured≠asserted): record the model
                # and effort actually placed on the invocation + the argv (minus
                # the temp prompt-file), so every call carries proof of what brain
                # ran at what effort — no fallback ambiguity.
                raw={"returncode": result.returncode, "elapsed_ms": round(elapsed_ms, 1),
                     "cmd": self._cmd, "model": model, "effort": effort,
                     "argv": [a for a in cmd if a != prompt_file]},
            )

        except subprocess.TimeoutExpired as e:
            elapsed_ms = (time.time() - start) * 1000
            # capture the child's dying words — what was it doing at kill time?
            po = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            pe = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            logger.error(f"Grok CLI timeout after {elapsed_ms:.0f}ms | "
                         f"partial stdout[:300]={po[:300]!r} | partial stderr[:300]={pe[:300]!r}")
            return AdapterResponse(content="[Error: Grok CLI timed out]",
                                   raw={"timeout": True, "elapsed_ms": round(elapsed_ms, 1),
                                        "partial_stdout": po[:1000], "partial_stderr": pe[:1000]})
        except FileNotFoundError:
            return AdapterResponse(
                content=f"[Error: '{self._cmd}' not found. Is Grok CLI installed and on PATH?]",
                raw={"error": "command_not_found", "cmd": self._cmd},
            )
        finally:
            for p in (prompt_file,):
                try:
                    os.unlink(p)
                except Exception:
                    pass
            if sandbox_cwd:
                shutil.rmtree(sandbox_cwd, ignore_errors=True)

    def health_check(self) -> dict:
        try:
            result = subprocess.run([self._cmd, "--version"], capture_output=True, text=True, timeout=10)
            version = result.stdout.strip() or result.stderr.strip()
            return {"status": "ok", "version": version, "cmd": self._cmd}
        except FileNotFoundError:
            return {"status": "error", "error": f"'{self._cmd}' not found"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def list_models(self) -> list[str]:
        """Grok CLI available models (xAI). Effort is a separate flag, not a
        model suffix — grok-4.5 @ high is model 'grok-4.5' + effort 'high'."""
        # LxM-measured 2026-07-13: grok-4.5 = general reasoning flagship;
        # grok-composer-2.5-fast = coding-agent EXECUTION specialist (Cursor
        # Composer concept), NOT a pure size tier — has a ~1/3 JSON-emission
        # flake (leaks to tool-planning) that wants a retry guard.
        return ["grok-4.5", "grok-composer-2.5-fast"]
