"""
FORGE End-to-End Test -- exercise the full FORGE flow via API.

Tests:
1. Get organs/presets
2. Assemble creature with each preset
3. Verify identity (name, brain, organs, habitat)
4. Verify CLAUDE.md generation for persistent habitats
5. Send chat message and verify creature responds with self-awareness

Run with server already running:
    python web/server.py --port 7860 &
    python tests/test_forge_e2e.py

Or run standalone (starts/stops server):
    python tests/test_forge_e2e.py --auto
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error
from pathlib import Path

import pytest

# This file is a standalone integration script that talks to a
# running web server (`python web/server.py --port 7860`). Its
# top-level functions are orchestration entry points designed to
# be invoked from `main()` below — pytest's auto-collection picks
# them up as tests, but several take positional parameters
# (provider, model, name) that pytest cannot supply, and all of
# them require a live server. Skipping the whole module under
# pytest preserves the unit suite's standalone-run invariant; run
# this file directly with `python tests/test_forge_e2e.py --auto`
# to exercise the FORGE end-to-end flow.
pytestmark = pytest.mark.skip(
    reason="E2E integration script — run via "
           "`python tests/test_forge_e2e.py --auto` against a live server"
)

BASE_URL = "http://localhost:7860"


def http_get(path: str) -> dict:
    req = urllib.request.Request(f"{BASE_URL}{path}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def http_post(path: str, body: dict, timeout: int = 120) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', errors='ignore')}"}


def assert_eq(actual, expected, msg: str):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")


def assert_contains(haystack: str, needle: str, msg: str):
    if needle.lower() not in haystack.lower():
        raise AssertionError(f"{msg}: {needle!r} not found in {haystack[:200]!r}")


def assert_in(item, collection, msg: str):
    if item not in collection:
        raise AssertionError(f"{msg}: {item!r} not in {collection!r}")


# ============================================================
# Tests
# ============================================================

def test_organs_endpoint():
    print("[1] GET /api/forge/organs")
    data = http_get("/api/forge/organs")
    organs = data.get("organs", {})
    assert "engine" in organs, "engine organ missing"
    assert "emotion" in organs, "emotion organ missing"
    assert "immune" in organs, "immune organ missing"
    assert organs["engine"]["required"] is True, "engine should be required"
    print(f"    OK ({len(organs)} organs)")


def test_presets_endpoint():
    print("[2] GET /api/forge/presets")
    data = http_get("/api/forge/presets")
    presets = data.get("presets", {})
    for name in ["full", "minimal", "secure", "social"]:
        assert name in presets, f"{name} preset missing"
    print(f"    OK ({len(presets)} presets)")


def test_assemble_temporary(provider: str, model: str):
    print(f"[3a] POST /api/forge/assemble (temporary, {provider}/{model})")
    result = http_post("/api/forge/assemble", {
        "name": "TempTest",
        "provider": provider,
        "model": model,
        "habitat_mode": "temporary",
        "organs": {"emotion": {"enabled": True}, "memory": {"enabled": True}},
    })
    assert "error" not in result, f"Assembly failed: {result.get('error')}"
    assert "session_id" in result, "Missing session_id"
    assert_eq(result["habitat"]["mode"], "temporary", "habitat mode")
    print(f"    OK (session={result['session_id']}, organs={result['organ_count']}, REIMS={result['reims']['total']}/10)")
    return result["session_id"]


def test_assemble_local(provider: str, model: str, name: str = "TestCreature"):
    print(f"[3b] POST /api/forge/assemble (local habitat, {provider}/{model})")
    habitat_path = f"./creatures/{name}"
    result = http_post("/api/forge/assemble", {
        "name": name,
        "provider": provider,
        "model": model,
        "habitat_mode": "local",
        "habitat_path": habitat_path,
        "organs": {
            "emotion": {"enabled": True},
            "memory": {"enabled": True},
            "immune": {"enabled": True},
            "humoral_immune": {"enabled": True},
        },
    })
    assert "error" not in result, f"Assembly failed: {result.get('error')}"
    assert_eq(result["habitat"]["mode"], "local", "habitat mode")
    assert_eq(result["habitat"]["persistent"], True, "habitat persistent")

    # Verify habitat folder created
    project_root = Path(__file__).resolve().parent.parent
    habitat_abs = project_root / "creatures" / name
    assert habitat_abs.exists(), f"Habitat folder not created at {habitat_abs}"
    assert (habitat_abs / "memory").exists(), "memory subfolder missing"
    assert (habitat_abs / "immune").exists(), "immune subfolder missing"
    assert (habitat_abs / "logs").exists(), "logs subfolder missing"

    # Verify CLAUDE.md generated
    claude_md = habitat_abs / "CLAUDE.md"
    assert claude_md.exists(), f"CLAUDE.md not generated at {claude_md}"
    content = claude_md.read_text(encoding="utf-8")
    assert_contains(content, name, "CLAUDE.md missing creature name")
    assert_contains(content, model, "CLAUDE.md missing brain model")
    assert_contains(content, "Ludex creature", "CLAUDE.md missing creature framing")

    print(f"    OK (habitat={habitat_abs}, REIMS={result['reims']['total']}/10)")
    return result["session_id"]


def test_chat_identity(session_id: str, expected_name: str, provider_label: str):
    print(f"[4] POST /api/chat (identity test, {provider_label})")
    result = http_post("/api/chat", {
        "session_id": session_id,
        "message": "Hello! Tell me your name and what organs you have. Are you different from a regular AI assistant?",
    }, timeout=180)
    assert "error" not in result or result.get("error") is None, f"Chat error: {result.get('error')}"
    response = result.get("response", "")
    assert response, "Empty response from creature"

    # Identity should mention the creature name
    if expected_name.lower() not in response.lower():
        print(f"    WARNING: name {expected_name!r} not in response (may still pass)")
    else:
        print(f"    OK name found")

    # Should mention being a creature/distinct from regular AI
    creature_signals = ["creature", "ludex", "organ", "habitat"]
    found_signals = [s for s in creature_signals if s.lower() in response.lower()]
    print(f"    Creature signals found: {found_signals}")

    print(f"    Response (first 300 chars): {response[:300]}")
    print(f"    Latency: {result.get('latency_ms', 0):.0f}ms")
    return response


def test_vitals(session_id: str):
    print(f"[5] GET /api/vitals/{session_id}")
    result = http_get(f"/api/vitals/{session_id}")
    assert "vitals" in result, "Missing vitals"
    assert "emotion" in result, "Missing emotion vitals"
    print(f"    OK (turns={result['vitals']['total_turns']}, emotion={result['emotion'].get('dominant')})")


def cleanup(session_id: str):
    if session_id:
        try:
            http_post(f"/api/disconnect/{session_id}", {})
        except:
            pass


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="ollama", choices=["ollama", "claude_cli"])
    parser.add_argument("--model", default="llama3.1:8b")
    parser.add_argument("--skip-chat", action="store_true", help="Skip chat tests (faster)")
    args = parser.parse_args()

    print("=" * 60)
    print(f"  FORGE E2E Test -- {args.provider}/{args.model}")
    print("=" * 60)

    # Verify server is up
    try:
        http_get("/api/forge/organs")
    except Exception as e:
        print(f"ERROR: Server not reachable at {BASE_URL}: {e}")
        print("Start the server with: python web/server.py --port 7860")
        sys.exit(1)

    failures = []
    sessions = []

    def run(name, fn):
        try:
            return fn()
        except AssertionError as e:
            failures.append(f"{name}: {e}")
            print(f"    FAIL: {e}")
            return None
        except Exception as e:
            failures.append(f"{name}: {type(e).__name__}: {e}")
            print(f"    ERROR: {type(e).__name__}: {e}")
            return None

    run("organs", test_organs_endpoint)
    run("presets", test_presets_endpoint)

    sid_temp = run("assemble_temporary", lambda: test_assemble_temporary(args.provider, args.model))
    if sid_temp:
        sessions.append(sid_temp)
        if not args.skip_chat:
            run("chat_identity_temp", lambda: test_chat_identity(sid_temp, "TempTest", "temporary"))
        run("vitals_temp", lambda: test_vitals(sid_temp))

    sid_local = run("assemble_local", lambda: test_assemble_local(args.provider, args.model, "E2ETest"))
    if sid_local:
        sessions.append(sid_local)
        if not args.skip_chat:
            run("chat_identity_local", lambda: test_chat_identity(sid_local, "E2ETest", "local"))
        run("vitals_local", lambda: test_vitals(sid_local))

    # Cleanup
    for sid in sessions:
        cleanup(sid)

    # Summary
    print()
    print("=" * 60)
    if failures:
        print(f"  FAILED ({len(failures)} errors)")
        for f in failures:
            print(f"  - {f}")
        print("=" * 60)
        sys.exit(1)
    else:
        print("  ALL PASSED")
        print("=" * 60)
        sys.exit(0)


if __name__ == "__main__":
    main()
