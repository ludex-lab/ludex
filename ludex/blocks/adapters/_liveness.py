"""Brain-call liveness telemetry — was it stuck, or still thinking?

Today a CLI brain that produces nothing hands back the same empty string
whether it hung, refused, or was mid-reasoning when our deadline killed it.
The physics battery hit all three within one afternoon and could not tell them
apart: grok emitted a preamble and then sat silent for four minutes, gpt-5.6-luna
blew a 240s ceiling on every prompt carrying observations, and claude-fable-5
blew the same ceiling on environments that are mathematically underdetermined —
that last one was the model working correctly, and a blunt timeout would have
recorded it as a weak lineage. Reasoning models make this worse over time, not
better.

So this module measures instead of guessing. It is a drop-in for
`subprocess.run(cmd, capture_output=True, text=True, timeout=...)` that reads
the pipes incrementally and records WHEN bytes arrived, then labels the call:

    no_output            nothing ever arrived — silence, not slowness
    stalled_after_output bytes arrived, then a long silence before the deadline
    still_producing      bytes were still arriving when the deadline hit
    exited               the process finished on its own

`still_producing` is the one a total-elapsed timeout cannot see, and it is the
one that means "raise the ceiling", not "this brain is broken".

DELIBERATELY BEHAVIOUR-PRESERVING. Same timeout semantics, same return shape,
same TimeoutExpired on expiry (with telemetry attached). This is the measuring
step of the interoception organ candidate: collect the distribution first, and
only then decide what an idle threshold should be. Picking one now would be
inventing another arbitrary constant.

Caveat, recorded rather than hidden: a CLI that buffers everything until exit
has no liveness signal at all, so its calls read as `no_output` on timeout even
while the model is working. `has_liveness_signal` says whether the stream ever
produced anything mid-flight; a streaming output mode is what makes idle
detection meaningful, and adopting one per CLI is the follow-up.
"""
from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import time
from pathlib import Path

STALL_LABEL_S = 30.0      # label only — does NOT cut a call short
_SINK_ENV = "LUDEX_BRAIN_TELEMETRY"


def _label(outcome, first_byte_ms, idle_at_end_ms, total_bytes):
    if outcome == "exited":
        return "exited"
    if not total_bytes:
        return "no_output"
    if idle_at_end_ms is not None and idle_at_end_ms > STALL_LABEL_S * 1000:
        return "stalled_after_output"
    return "still_producing"


def _kill_tree(proc, grace=5.0):
    """Kill the child AND everything it spawned, then stop waiting.

    A CLI brain is a wrapper around a native helper (`codex` → the darwin-arm64
    binary; `claude` likewise). `proc.kill()` reaches only the wrapper, and the
    helper survives holding the write end of our pipes. That is not a tidiness
    problem — it is the hang: any later drain of those pipes waits on a process
    nobody is watching.

    Observed 2026-08-14: a codex call whose ceiling was 240s returned after
    5,801,615ms (97 min), and the orphaned helper was still running at ppid 1
    when we went looking. Its sibling had held on for 76 minutes.

    So we kill the process GROUP (children spawn into it via start_new_session)
    and give the reap a bounded grace. If the group is gone or we were not
    granted our own session, fall back to the child alone — never block.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=grace)
    except Exception:
        pass


def run_capture(cmd, *, input=None, timeout=None, env=None, cwd=None,
                encoding="utf-8", errors="replace"):
    """`subprocess.run(capture_output=True, text=True, timeout=…)` that cannot
    outlive its own timeout.

    CPython's `run()` kills the direct child on expiry and then drains the pipes
    with an **unbounded** `communicate()`. When the child has spawned a helper
    that inherited those pipes, the drain never returns — the caller sits past
    its ceiling by orders of magnitude while the adapter's own error message
    still reports "timeout". Two brain calls did exactly that today.

    Same signature and same `TimeoutExpired` as the call it replaces; the only
    difference is that the deadline is real.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        env=env, cwd=cwd, start_new_session=True)
    try:
        out, err = proc.communicate(
            input.encode(encoding, errors) if input is not None else None,
            timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:                      # the group is dead; this drain is bounded
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = b"", b""
        e = subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
        raise e
    import types
    return types.SimpleNamespace(
        returncode=proc.returncode,
        stdout=(out or b"").decode(encoding, errors),
        stderr=(err or b"").decode(encoding, errors))


def run_traced(cmd, *, env=None, cwd=None, timeout=None, encoding="utf-8",
               errors="replace", stdin=subprocess.DEVNULL, input=None, tag=""):
    """Run `cmd`, returning (CompletedProcess-like, telemetry dict).

    Raises subprocess.TimeoutExpired on expiry exactly as subprocess.run does,
    with `.telemetry` attached so the caller can record why it expired.

    `input` (str) is written to stdin from a helper thread rather than inline:
    a prompt larger than the pipe buffer would otherwise block the writer while
    nobody drains stdout, which is a deadlock, not a slow brain.
    """
    t0 = time.time()
    # start_new_session: the child gets its own process group so _kill_tree can
    # take the helper down with it (see _kill_tree — an orphan holding our pipes
    # is the hang, not a leak).
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=cwd, start_new_session=True,
                            stdin=subprocess.PIPE if input is not None else stdin)
    if input is not None:
        import threading

        def _feed():
            try:
                proc.stdin.write(input.encode(encoding, errors))
                proc.stdin.close()
            except Exception:
                pass
        threading.Thread(target=_feed, daemon=True).start()
    out, err = b"", b""
    first_byte = None
    last_byte = None
    gaps = []
    prev_event = t0
    try:
        if os.name != "nt":
            while True:
                remaining = None if timeout is None else timeout - (time.time() - t0)
                if remaining is not None and remaining <= 0:
                    break
                wait = 0.25 if remaining is None else min(0.25, remaining)
                rl, _, _ = select.select([proc.stdout, proc.stderr], [], [], wait)
                got = False
                for fd in rl:
                    chunk = os.read(fd.fileno(), 65536)
                    if not chunk:
                        continue
                    got = True
                    if fd is proc.stdout:
                        out += chunk
                    else:
                        err += chunk
                now = time.time()
                if got:
                    if first_byte is None:
                        first_byte = now
                    gap_ms = (now - prev_event) * 1000
                    if gap_ms > STALL_LABEL_S * 1000:
                        gaps.append(round(gap_ms))
                    prev_event = now
                    last_byte = now
                if proc.poll() is not None:
                    out += proc.stdout.read() or b""
                    err += proc.stderr.read() or b""
                    break
        else:
            # Windows: select() accepts only sockets — on pipes it dies with
            # WinError 10038 (surfaced 2026-08-02: every claude_cli/agy_cli
            # call on Ray-habitat failed instantly). Reader threads pump each
            # pipe into a queue instead; arrival times are stamped at os.read
            # return, so first_byte/last_byte/gaps keep the same meaning.
            import queue
            import threading

            q = queue.Queue()

            def _pump(stream, name):
                try:
                    while True:
                        chunk = os.read(stream.fileno(), 65536)
                        if not chunk:
                            break
                        q.put((name, chunk, time.time()))
                except (OSError, ValueError):
                    pass
                finally:
                    q.put((name, None, time.time()))

            for stream, name in ((proc.stdout, "out"), (proc.stderr, "err")):
                threading.Thread(target=_pump, args=(stream, name),
                                 daemon=True).start()
            open_pipes = 2
            while True:
                remaining = None if timeout is None else timeout - (time.time() - t0)
                if remaining is not None and remaining <= 0:
                    break
                wait = 0.25 if remaining is None else min(0.25, remaining)
                try:
                    name, chunk, ts = q.get(timeout=wait)
                except queue.Empty:
                    # A 250ms silent window after exit means the pumps are
                    # blocked on a pipe a grandchild still holds — the POSIX
                    # branch's poll() break, not data worth waiting for.
                    if proc.poll() is not None:
                        break
                    continue
                if chunk is None:
                    open_pipes -= 1
                    if open_pipes == 0:
                        proc.wait()
                        break
                    continue
                if name == "out":
                    out += chunk
                else:
                    err += chunk
                if first_byte is None:
                    first_byte = ts
                gap_ms = (ts - prev_event) * 1000
                if gap_ms > STALL_LABEL_S * 1000:
                    gaps.append(round(gap_ms))
                prev_event = ts
                last_byte = ts
    finally:
        exited = proc.poll() is not None
        if not exited:
            _kill_tree(proc)

    now = time.time()
    tele = {
        "tag": tag,
        "total_ms": round((now - t0) * 1000),
        "first_byte_ms": None if first_byte is None else round((first_byte - t0) * 1000),
        "last_byte_ms": None if last_byte is None else round((last_byte - t0) * 1000),
        "idle_at_end_ms": None if last_byte is None else round((now - last_byte) * 1000),
        "long_gaps_ms": gaps,
        "bytes_out": len(out),
        "bytes_err": len(err),
        "has_liveness_signal": first_byte is not None,
        "outcome": "exited" if exited else "timeout",
    }
    tele["label"] = _label(tele["outcome"], tele["first_byte_ms"],
                           tele["idle_at_end_ms"], len(out))
    _emit(tele)

    stdout = out.decode(encoding, errors)
    stderr = err.decode(encoding, errors)
    if not exited:
        e = subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
        e.telemetry = tele
        raise e

    import types
    return (types.SimpleNamespace(returncode=proc.returncode, stdout=stdout,
                                  stderr=stderr), tele)


def _emit(tele):
    """Append to the JSONL sink if one is configured. A sidecar file is used
    because the engine's TurnResult does not carry adapter raw, and threading
    it through would be a wider change than a measuring step deserves."""
    sink = os.environ.get(_SINK_ENV, "")
    if not sink:
        return
    try:
        rec = dict(tele)
        rec["at"] = time.time()
        with Path(sink).open("a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass          # telemetry must never break a measurement run
