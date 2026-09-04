"""Headless brain-call state — what is the agent doing RIGHT NOW, not just
"has the deadline passed".

Sister of `_liveness.run_traced`. That one measures when bytes arrive; this one
reads each CLI's NDJSON stream and keeps a small state machine:

    starting → thinking ⇄ producing ⇄ tool_running → done
             ↘ waiting_auth · permission_denied · rate_limited   (fail fast)
             ↘ idle_stall (heartbeat CLIs only) · hard_cap        (last resort)

Measured 2026-09-03 (research/headless-liveness/RESEARCH.md, 16 captures):
claude / grok / agy emit a heartbeat while thinking (deltas, step_update);
codex emits nothing between turn.started and item.completed, so its idle
clock is OFF — we never kill a brain for a heartbeat it does not have.
Auth loss is explicit within a second on claude/grok/codex; agy instead waits
60s for a browser, but says so on stderr at 0.7s — so we read stderr.

Observation mode (default this first week): idle_stall is LABELLED, not
killed. Auth / permission / rate-limit signatures are certain and end the
call immediately with a typed error — the treasury's "blocked waste leaves
the denominator" rule reads that type.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import time
from dataclasses import dataclass, field

from ludex.blocks.adapters._liveness import _kill_tree

# ---- per-CLI wire shapes -------------------------------------------------

STREAM_FLAGS = {
    "claude": ["--output-format", "stream-json", "--verbose", "--include-partial-messages"],
    "codex":  ["--json"],
    "grok":   ["--output-format", "streaming-messages-json", "--include-partial-messages"],
    "agy":    ["--output-format", "stream-json"],
}
# codex emits nothing on stdout while reasoning, but it appends every reasoning
# item and tool call to ~/.codex/sessions/YYYY/MM/DD/rollout-*-<thread_id>.jsonl
# as it goes (measured 09-04: 240 events over a 392s bench session, max silence
# 73s). That file is codex's heartbeat — read as a secondary source below.
HEARTBEAT = {"claude": True, "grok": True, "agy": True, "codex": True}
# Live status files: LUDEX_LIVE_DIR if set, else <repo>/village/.live when the
# checkout has a village, else the OS temp dir. Core must not know the lab's path.
def _live_dir() -> str:
    d = os.environ.get("LUDEX_LIVE_DIR")
    if d:
        return d
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    v = os.path.join(repo, "village")
    return os.path.join(v, ".live") if os.path.isdir(v) else os.path.join(__import__("tempfile").gettempdir(), "ludex-live")


LIVE_DIR = _live_dir()


def _codex_rollout(thread_id: str):
    import glob as _glob
    base = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
    hits = _glob.glob(os.path.join(base, "*", "*", "*", f"rollout-*{thread_id}*.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None

# stderr / text signatures that are certain enough to act on
_AUTH_TEXT = (
    "authentication required",      # agy (then waits 60s for a browser)
    "not signed in",                # grok
    "not logged in",                # claude / codex
    "please run /login",
    "invalid api key",
)
_PERM_TEXT = (
    "auto-denied",                                  # agy headless
    "permission that headless mode cannot prompt",  # agy headless
)
_RATE_TEXT = ("rate limit", "rate_limit", "429", "quota exceeded", "overloaded")


@dataclass
class Event:
    kind: str            # heartbeat | tool_start | tool_end | tool_error | text | result | auth | permission | rate_limit | noise
    state: str | None    # state hint or None
    detail: str = ""


def _j(line: str):
    try:
        return json.loads(line)
    except (ValueError, TypeError):
        return None


def classify_line(cli: str, stream: str, line: str) -> Event:
    """Pure: one NDJSON/stderr line → what it says about the agent's state."""
    low = line.lower()
    if stream == "err" or not line.startswith("{"):
        if any(s in low for s in _AUTH_TEXT):
            return Event("auth", "waiting_auth", line[:200])
        if any(s in low for s in _PERM_TEXT):
            return Event("permission", "permission_denied", line[:200])
        if any(s in low for s in _RATE_TEXT):
            return Event("rate_limit", "rate_limited", line[:200])
        return Event("noise", None, line[:120])
    j = _j(line)
    if not isinstance(j, dict):
        return Event("noise", None)

    if cli == "claude":
        t, sub = j.get("type"), j.get("subtype")
        if t == "system":
            if sub == "permission_denied":
                return Event("permission", "permission_denied", f"{j.get('tool_name')}: {str(j.get('message',''))[:160]}")
            if sub in ("thinking_tokens", "status"):
                return Event("heartbeat", "thinking")
            return Event("noise", "starting" if sub == "init" else None)
        if t == "rate_limit_event":
            info = j.get("rate_limit_info") or {}
            if info.get("status") not in (None, "allowed"):
                return Event("rate_limit", "rate_limited", str(info)[:160])
            return Event("noise", None)
        if t == "stream_event":
            ev = j.get("event") or {}
            et = ev.get("type")
            if et == "content_block_start":
                cb = ev.get("content_block") or {}
                if cb.get("type") == "tool_use":
                    return Event("tool_start", "tool_running", cb.get("name", ""))
                return Event("heartbeat", "thinking" if cb.get("type") == "thinking" else "producing")
            if et == "content_block_delta":
                d = ev.get("delta") or {}
                return Event("heartbeat", "thinking" if d.get("type") == "thinking_delta" else "producing")
            return Event("heartbeat", None)
        if t == "user":                      # tool_result comes back as a user message
            content = (j.get("message") or {}).get("content") or []
            if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
                return Event("tool_end", "thinking")
            return Event("noise", None)
        if t == "assistant":
            msg = j.get("message") or {}
            if msg.get("model") == "<synthetic>":          # logged-out synthetic reply
                return Event("auth", "waiting_auth", "synthetic assistant (not logged in)")
            return Event("heartbeat", None)
        if t == "result" or "duration_api_ms" in j:
            return Event("result", "done", j.get("subtype") or "")
        return Event("noise", None)

    if cli == "codex":
        t = j.get("type", "")
        item = j.get("item") or {}
        if t == "turn.started":
            return Event("heartbeat", "thinking")
        if t == "item.started":
            return Event("tool_start" if item.get("type") == "command_execution" else "heartbeat",
                         "tool_running" if item.get("type") == "command_execution" else "producing",
                         str(item.get("command", ""))[:120])
        if t == "item.completed":
            if item.get("type") == "command_execution":
                out = str(item.get("aggregated_output", "")).lower()
                if item.get("exit_code") not in (0, None) and ("not permitted" in out or "sandbox" in out):
                    return Event("permission", "permission_denied", out[:160])
                return Event("tool_end", "thinking")
            text = str(item.get("text", ""))
            low_t = text.lower()
            # codex's sandbox block surfaces in the agent's own words, not in a
            # structured event (measured 09-03: the failed write never became an
            # item; only the agent's "operation not permitted" sentence did)
            if "operation not permitted" in low_t or "sandbox" in low_t and "permi" in low_t:
                return Event("permission", "permission_denied", text[:160])
            return Event("text", "producing", text[:120])
        if t == "turn.completed":
            return Event("result", "done")
        if t in ("turn.failed", "error"):
            msg = str(j.get("error") or j).lower()
            if any(s in msg for s in _RATE_TEXT):
                return Event("rate_limit", "rate_limited", msg[:160])
            return Event("result", "done", "failed")
        return Event("noise", "starting" if t == "thread.started" else None)

    if cli == "grok":
        t = j.get("type")
        if t == "stream_event":
            ev = j.get("event") or {}
            et = ev.get("type")
            if et == "content_block_start":
                cb = ev.get("content_block") or {}
                if cb.get("type") == "tool_use":
                    return Event("tool_start", "tool_running", cb.get("name", ""))
                return Event("heartbeat", "thinking" if cb.get("type") == "thinking" else "producing")
            if et == "content_block_delta":
                d = ev.get("delta") or {}
                return Event("heartbeat", "thinking" if d.get("type") == "thinking_delta" else "producing")
            return Event("heartbeat", None)
        if t == "user":
            return Event("tool_end", "thinking")
        if t == "result":
            if j.get("is_error") or j.get("subtype") == "error_during_execution":
                return Event("result", "done", "error")
            return Event("result", "done", "success")
        return Event("noise", "starting" if t == "system" else None)

    if cli == "agy":
        ev = j.get("event")
        if ev == "step_update":
            su = j.get("step_update") or {}
            st, stype = su.get("state"), su.get("step_type", "")
            if stype == "tool":
                if st == "ERROR":
                    # a failed tool step is not a denial by itself — agy retries
                    # (measured 09-03: write_to_file ERROR then DONE). The denial
                    # signature is the stderr "auto-denied" line, read above.
                    return Event("tool_error", "thinking", f"{su.get('tool_name','')}: error")
                return Event("tool_start" if st == "ACTIVE" else "tool_end",
                             "tool_running" if st == "ACTIVE" else "thinking")
            return Event("heartbeat", "producing" if st == "ACTIVE" else None)
        if ev == "result":
            return Event("result", "done", (j.get("result") or {}).get("status", ""))
        return Event("noise", "starting" if ev == "init" else None)

    return Event("noise", None)


def extract_answer(cli: str, lines: list[str]) -> str:
    """The final answer text from the NDJSON stream (what plain mode printed)."""
    if cli == "claude":
        for line in reversed(lines):
            j = _j(line)
            if isinstance(j, dict) and (j.get("type") == "result" or "duration_api_ms" in j):
                return str(j.get("result") or "")
    elif cli == "codex":
        texts = []
        for line in lines:
            j = _j(line)
            if isinstance(j, dict) and j.get("type") == "item.completed":
                item = j.get("item") or {}
                if item.get("type") == "agent_message":
                    texts.append(str(item.get("text", "")))
        return texts[-1] if texts else ""
    elif cli == "grok":
        for line in reversed(lines):
            j = _j(line)
            if isinstance(j, dict) and j.get("type") == "result":
                return str(j.get("result") or "")
    elif cli == "agy":
        for line in reversed(lines):
            j = _j(line)
            if isinstance(j, dict) and j.get("event") == "result":
                return str((j.get("result") or {}).get("response") or "")
    return ""


def extract_usage(cli: str, lines: list[str]) -> dict | None:
    for line in reversed(lines):
        j = _j(line)
        if not isinstance(j, dict):
            continue
        if cli == "claude" and ("duration_api_ms" in j or j.get("type") == "result"):
            return j.get("usage")
        if cli == "codex" and j.get("type") == "turn.completed":
            return j.get("usage")
        if cli == "grok" and j.get("type") == "result":
            return j.get("usage")
        if cli == "agy" and j.get("event") == "result":
            return (j.get("result") or {}).get("usage") or j.get("usage")
    return None


# ---- the runner ------------------------------------------------------------

@dataclass
class Trace:
    cli: str
    states: list[tuple[float, str]] = field(default_factory=list)   # (t since start, state)
    last_state: str = "starting"
    last_event_t: float = 0.0
    heartbeats: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    signature: str = ""          # auth | permission | rate_limit | ""
    signature_detail: str = ""
    ended_by: str = ""           # exited | fail_fast | idle_stall | hard_cap
    elapsed_ms: int = 0
    lines_out: int = 0
    lines_err: int = 0

    rollout: str | None = None
    rollout_size: int = 0
    tag: str = ""

    def publish(self, t0: float) -> None:
        """Live status for the caretaker: village/.live/<tag>.json — what this
        session is doing right now, not what the ring will report later."""
        if not self.tag:
            return
        try:
            os.makedirs(LIVE_DIR, exist_ok=True)
            d = self.summary(); d["since"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0))
            d["elapsed_s"] = int(time.time() - t0); d["idle_s"] = int(time.time() - self.last_event_t)
            d["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            tmp = os.path.join(LIVE_DIR, f".{self.tag}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(tmp, os.path.join(LIVE_DIR, f"{self.tag}.json"))
        except Exception:
            pass

    def enter(self, t: float, state: str) -> None:
        if state and state != self.last_state:
            self.states.append((round(t, 2), state))
            self.last_state = state

    def summary(self) -> dict:
        return {"cli": self.cli, "last_state": self.last_state, "ended_by": self.ended_by,
                "elapsed_ms": self.elapsed_ms, "heartbeats": self.heartbeats,
                "tool_calls": self.tool_calls, "tool_errors": self.tool_errors,
                "signature": self.signature,
                "signature_detail": self.signature_detail[:200],
                "states": self.states[-12:], "lines": self.lines_out + self.lines_err}


def run_streamed(cmd: list[str], cli: str, *, env=None, cwd=None, input: str | None = None,
                 idle_s: float = 120.0, hard_cap_s: float = 600.0,
                 kill_on_idle: bool = False, encoding="utf-8", errors="replace", tag: str = ""):
    """Run a streaming headless CLI call; return (result, trace).

    result: SimpleNamespace(returncode, stdout=<answer text>, stderr, lines, usage)
    trace : Trace (see .summary())

    Fail-fast on auth / permission / rate-limit signatures. idle_s applies only
    to heartbeat CLIs and, unless kill_on_idle, only labels (observation mode).
    """
    import types
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                            env=env, cwd=cwd, start_new_session=True)
    if input is not None:
        import threading
        def _feed():
            try:
                proc.stdin.write(input.encode(encoding, errors)); proc.stdin.close()
            except Exception:
                pass
        threading.Thread(target=_feed, daemon=True).start()

    tr = Trace(cli=cli); tr.tag = tag or (os.path.basename(cwd.rstrip(os.sep)) if cwd else "")
    bufs = {proc.stdout: b"", proc.stderr: b""}
    names = {proc.stdout: "out", proc.stderr: "err"}
    lines_out: list[str] = []
    err_text: list[str] = []
    idle_flagged = False
    heartbeat_cli = HEARTBEAT.get(cli, False)

    def _handle(stream: str, line: str, now: float) -> bool:
        """Returns True when the call must end now (fail-fast)."""
        if stream == "out":
            lines_out.append(line); tr.lines_out += 1
        else:
            err_text.append(line); tr.lines_err += 1
        ev = classify_line(cli, stream, line.strip())
        if cli == "codex" and tr.rollout is None and '"thread.started"' in line:
            j = _j(line.strip())
            if isinstance(j, dict) and j.get("thread_id"):
                tr.rollout = _codex_rollout(j["thread_id"])
        if ev.kind != "noise":
            tr.last_event_t = now
        if ev.kind == "heartbeat":
            tr.heartbeats += 1
        elif ev.kind == "tool_start":
            tr.tool_calls += 1
        elif ev.kind == "tool_error":
            tr.tool_errors += 1
        if ev.state:
            tr.enter(now - t0, ev.state)
        if ev.kind in ("auth", "rate_limit"):
            tr.signature, tr.signature_detail = ev.kind, ev.detail
            return True
        if ev.kind == "permission":
            # a denied tool is not fatal by itself (claude keeps going) — label,
            # keep the first detail, do not kill
            if not tr.signature:
                tr.signature, tr.signature_detail = "permission", ev.detail
        return False

    tr.last_event_t = t0
    last_pub = 0.0
    try:
        while True:
            now = time.time()
            if now - t0 > hard_cap_s:
                tr.ended_by = "hard_cap"; _kill_tree(proc); break
            if heartbeat_cli and tr.last_state in ("thinking", "producing", "tool_running") \
                    and now - tr.last_event_t > idle_s:
                if not idle_flagged:
                    idle_flagged = True
                    tr.enter(now - t0, "idle_stall")
                if kill_on_idle:
                    tr.ended_by = "idle_stall"; _kill_tree(proc); break
            if cli == "codex":
                if tr.rollout is None and tr.lines_out:
                    pass
                elif tr.rollout:
                    try:
                        sz = os.path.getsize(tr.rollout)
                    except OSError:
                        sz = tr.rollout_size
                    if sz > tr.rollout_size:
                        tr.rollout_size = sz; tr.last_event_t = now; tr.heartbeats += 1
                        tr.enter(now - t0, "thinking" if tr.last_state in ("starting", "thinking", "producing") else tr.last_state)
            if now - last_pub > 5:
                tr.publish(t0); last_pub = now
            rl, _, _ = select.select(list(bufs), [], [], 0.25)
            stop = False
            for fd in rl:
                chunk = os.read(fd.fileno(), 65536)
                if not chunk:
                    continue
                bufs[fd] += chunk
                while b"\n" in bufs[fd]:
                    raw, bufs[fd] = bufs[fd].split(b"\n", 1)
                    if _handle(names[fd], raw.decode(encoding, errors), time.time()):
                        stop = True
            if stop:
                tr.ended_by = "fail_fast"; _kill_tree(proc); break
            if proc.poll() is not None:
                for fd in list(bufs):
                    rest = (bufs[fd] + (fd.read() or b"")).decode(encoding, errors)
                    for raw in rest.split("\n"):
                        if raw.strip():
                            _handle(names[fd], raw, time.time())
                tr.ended_by = "exited"; break
    finally:
        tr.elapsed_ms = int((time.time() - t0) * 1000)
        if tr.ended_by == "exited":
            if tr.last_state != "done":
                tr.enter(time.time() - t0, "done" if proc.returncode == 0 else "failed")
        tr.publish(t0)
        try:   # the live file is for running sessions; the trace lives in the span
            if tr.tag:
                os.replace(os.path.join(LIVE_DIR, f"{tr.tag}.json"), os.path.join(LIVE_DIR, f"{tr.tag}.last.json"))
        except OSError:
            pass

    answer = extract_answer(cli, lines_out) if tr.ended_by == "exited" else ""
    return types.SimpleNamespace(returncode=proc.returncode, stdout=answer,
                                 stderr="\n".join(err_text), lines=lines_out,
                                 usage=extract_usage(cli, lines_out)), tr
