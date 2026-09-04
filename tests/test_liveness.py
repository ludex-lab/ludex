"""run_traced cross-platform contract — the 2026-08-02 Windows regression.

select.select() only accepts sockets on Windows, so the original
single-branch read loop died instantly with WinError 10038 on every
claude_cli/agy_cli call (Ray-habitat reflect_empty x3). These tests pin
the platform-independent contract: output capture, exit codes, timeout
with telemetry attached, and liveness timestamps. Subprocesses are
plain `python -c` — no brain, no quota.
"""
import subprocess
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ludex.blocks.adapters._liveness import run_traced


def test_captures_output_and_exit():
    res, tele = run_traced(
        [sys.executable, "-c", "import sys; print('ok'); sys.stderr.write('warn')"],
        timeout=30)
    assert res.returncode == 0
    assert res.stdout.strip() == "ok"
    assert "warn" in res.stderr
    assert tele["outcome"] == "exited"
    assert tele["has_liveness_signal"] is True
    assert tele["bytes_out"] > 0


def test_nonzero_exit_preserved():
    res, tele = run_traced([sys.executable, "-c", "raise SystemExit(3)"], timeout=30)
    assert res.returncode == 3
    assert tele["outcome"] == "exited"


def test_timeout_raises_with_telemetry():
    try:
        run_traced(
            [sys.executable, "-c",
             "import sys, time; print('early', flush=True); time.sleep(30)"],
            timeout=2)
    except subprocess.TimeoutExpired as e:
        assert e.telemetry["outcome"] == "timeout"
        assert e.telemetry["has_liveness_signal"] is True  # 'early' arrived
        assert b"early" in (e.output or b"")
    else:
        raise AssertionError("expected TimeoutExpired")


def test_stdin_input_round_trip():
    res, _ = run_traced(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        input="ping", timeout=30)
    assert res.stdout.strip() == "PING"


if __name__ == "__main__":
    test_captures_output_and_exit()
    test_nonzero_exit_preserved()
    test_timeout_raises_with_telemetry()
    test_stdin_input_round_trip()
    print("liveness: all 4 passed")
