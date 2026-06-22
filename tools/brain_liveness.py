"""brain_liveness.py — probe each creature's BRAIN to tell DORMANT from ALIVE.

Per creature_mortality_principle (reframed 2026-06-17): brain loss is DORMANCY,
not death — the body (narrative data) persists and a new brain wakes the SAME
being. This is a LIVENESS probe, NOT a death certificate: death is a narrative-
DATA-integrity question, checked separately. Run this before declaring any creature
affected by a deprecation "dormant" — a calendar date is a prediction; this is evidence.

It invokes the brain with a tiny prompt (bypass_memory=True, no creature mutation)
and classifies the result.

Usage:
  python tools/brain_liveness.py                      # sweep ./creatures
  python tools/brain_liveness.py Comet Flare          # specific creatures
  python tools/brain_liveness.py --root ~/ludex/creatures
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

ALIVE = "ALIVE"          # brain responded
DORMANT = "DORMANT"      # substrate gone (CLI missing / model retired) — re-brain to wake
CONFIG = "CONFIG"        # auth/key/setup fail — fixable, NOT death and NOT true dormancy
TRANSIENT = "TRANSIENT"  # network/quota/timeout — retry; verdict deferred
UNKNOWN = "UNKNOWN"

_PROBE = "Reply with exactly one word: alive"


def _classify(err: str) -> str:
    e = (err or "").lower()
    if any(s in e for s in ("command not found", "no such file", "executable", "cannot find", "not installed")):
        return DORMANT                          # the CLI/binary is gone
    if any(s in e for s in ("model not found", "does not exist", "deprecated", "retired", "404",
                            "unknown model", "decommission", "no longer available")):
        return DORMANT                          # the model is gone
    if any(s in e for s in ("auth", "api key", "401", "403", "unauthorized", "permission", "credential")):
        return CONFIG                           # fixable — not death
    if any(s in e for s in ("quota", "429", "rate limit", "resource_exhausted", "overloaded", "exceeded")):
        return TRANSIENT
    if any(s in e for s in ("timeout", "timed out", "connection", "network", "unreachable", "temporarily")):
        return TRANSIENT
    return UNKNOWN


# The underlying CLI binary per CLI-provider — captured to track version drift /
# detect a vanished binary (a deprecation like gemini-cli → agy shows up here).
_CLI_BINARY = {"claude_cli": "claude", "codex_cli": "codex", "gemini_cli": "gemini",
               "agy_cli": "agy", "ollama": "ollama"}
_CLI_VER_CACHE = {}


def _cli_version(provider: str) -> str:
    """`<binary> --version` for a CLI provider (cached per provider). '' for HTTP/SDK
    providers (no CLI); 'MISSING' if the binary is gone (a strong dormancy signal)."""
    if provider in _CLI_VER_CACHE:
        return _CLI_VER_CACHE[provider]
    binary = _CLI_BINARY.get(provider)
    v = ""
    if binary:
        try:
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
            raw = (out.stdout or out.stderr or "").strip()
            v = raw.splitlines()[0][:36] if raw else "?"
        except FileNotFoundError:
            v = "MISSING"
        except Exception:
            v = "?"
    _CLI_VER_CACHE[provider] = v
    return v


def probe(creature_path: str) -> dict:
    """Invoke the creature's brain once (no mutation) and classify liveness; capture CLI version."""
    name = os.path.basename(str(creature_path).rstrip("/\\"))
    from ludex.core.organism_config import OrganismConfig
    try:
        cfg = OrganismConfig.load(creature_path)
        brain = dict(cfg.brain or {})
    except Exception as e:
        return {"creature": name, "verdict": UNKNOWN, "brain": "?", "cli": "", "detail": f"config load failed: {e}"}
    label = f"{brain.get('provider', '?')}/{brain.get('model', '?')}"
    cli = _cli_version(brain.get("provider", ""))
    try:
        eng = cfg.build().get_block("engine")
        if eng is None:
            return {"creature": name, "verdict": UNKNOWN, "brain": label, "cli": cli, "detail": "no engine block"}
        res = eng.handle_submit(_PROBE, bypass_memory=True)
        text = (getattr(res, "response", "") or "").strip()
        if text:
            return {"creature": name, "verdict": ALIVE, "brain": label, "cli": cli, "detail": text[:40].replace("\n", " ")}
        return {"creature": name, "verdict": UNKNOWN, "brain": label, "cli": cli, "detail": "empty response"}
    except Exception as e:
        return {"creature": name, "verdict": _classify(str(e)), "brain": label, "cli": cli, "detail": str(e)[:120].replace("\n", " ")}


def main():
    ap = argparse.ArgumentParser(description="Probe creature brains: ALIVE vs DORMANT (not a death check).")
    ap.add_argument("creatures", nargs="*", help="creature names (default: sweep --root)")
    ap.add_argument("--root", default="creatures", help="creatures dir to sweep (default: ./creatures)")
    args = ap.parse_args()
    if args.creatures:
        paths = [os.path.join(args.root, c) for c in args.creatures]
    else:
        paths = [os.path.join(args.root, d) for d in sorted(os.listdir(args.root))
                 if os.path.isdir(os.path.join(args.root, d))]
    print(f"{'creature':14} {'verdict':10} {'brain':28} {'cli version':24} detail")
    print("-" * 116)
    counts = {}
    for p in paths:
        r = probe(p)
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        print(f"{r['creature']:14} {r['verdict']:10} {r['brain']:28} {r.get('cli', ''):24} {r['detail']}")
    print("-" * 116)
    print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("\nDORMANT = substrate gone, body intact → re-brain (continuity) or leave dormant. NOT death.")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # Load .env like the live paths (web server / heartbeat / cli) so CLI-API brains
    # (gemini_cli, agy_cli) get GEMINI_API_KEY. Without this they probe as DORMANT for
    # a missing key alone — a FALSE dormancy signal on a LIVE brain, and precisely the
    # creatures a deprecation check targets. See the probe_smoke fix (2026-06-22).
    from ludex.core.dotenv import load_dotenv
    load_dotenv()
    main()
