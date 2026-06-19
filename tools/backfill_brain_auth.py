"""backfill_brain_auth.py — write the birth-time brain.auth field into creatures that predate it.

`brain.auth` (subscription|api) is a per-creature, birth-time decision (see
ludex/blocks/adapters/_cli_env.py). Creatures born before the field existed carry
no auth, so the adapter falls back to a per-provider default. This backfills an
EXPLICIT auth so the decision is recorded in the config — JJ's principle: auth is
chosen at birth and must be honored, never inferred from whether a key happens to
sit in os.environ.

Provider → auth written:
  claude_cli → subscription   (Max subscription; never bill the API)
  codex_cli  → api            (lab choice, 2026-06-18)
  gemini_cli → api            (consumer tier deprecated 2026-06-18; paid key required)
  agy_cli    → api
  ollama / HTTP providers → skipped (local or no CLI subprocess key to govern)

Idempotent: skips any creature that already has brain.auth. Edits ludex.yaml
(targeted line insert — preserves comments/order, no PyYAML round-trip) and
ludex.json (parse + set) in lockstep so the two never drift on this field.

Usage:
  python tools/backfill_brain_auth.py --dry-run         # show what would change
  python tools/backfill_brain_auth.py                   # apply to ./creatures
  python tools/backfill_brain_auth.py Aria Echo          # specific creatures
  python tools/backfill_brain_auth.py --root ~/ludex/creatures
"""
from __future__ import annotations

import argparse
import json
import os
import re

# Provider → auth to write. Mirrors _cli_env defaults except codex (lab chose api).
PROVIDER_AUTH = {
    "claude_cli": "subscription",
    "codex_cli":  "api",
    "gemini_cli": "api",
    "agy_cli":    "api",
}

_PROVIDER_RE = re.compile(r"^(\s*)provider:\s*[\"']?([\w.-]+)", re.MULTILINE)


def _brain_block_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Return (start, end) line indices spanning the `brain:` mapping body
    (the indented lines under a top-level `brain:` key), or None."""
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^brain:\s*$", ln):
            start = i + 1
            break
    if start is None:
        return None
    end = start
    while end < len(lines) and (lines[end].strip() == "" or lines[end].startswith((" ", "\t"))):
        end += 1
    return start, end


def backfill_one(creature_dir: str, dry_run: bool) -> dict:
    name = os.path.basename(creature_dir.rstrip("/\\"))
    yaml_path = os.path.join(creature_dir, "ludex.yaml")
    json_path = os.path.join(creature_dir, "ludex.json")
    if not os.path.exists(yaml_path):
        return {"creature": name, "action": "skip", "why": "no ludex.yaml"}

    text = open(yaml_path, encoding="utf-8").read()
    lines = text.splitlines(keepends=True)
    bounds = _brain_block_bounds(lines)
    if bounds is None:
        return {"creature": name, "action": "skip", "why": "no brain block"}
    bstart, bend = bounds
    block = lines[bstart:bend]

    # provider + existing auth within the brain block only
    provider, indent, prov_idx = "", "  ", None
    for j, ln in enumerate(block):
        m = re.match(r"^(\s*)provider:\s*[\"']?([\w.-]+)", ln)
        if m:
            indent, provider, prov_idx = m.group(1), m.group(2), j
            break
    if any(re.match(r"^\s*auth:", ln) for ln in block):
        return {"creature": name, "action": "skip", "why": "auth already set", "provider": provider}
    if provider not in PROVIDER_AUTH:
        return {"creature": name, "action": "skip", "why": f"provider '{provider}' has no auth concept"}
    mode = PROVIDER_AUTH[provider]

    # YAML: insert `  auth: <mode>` right after the provider line (preserve formatting)
    insert_at = bstart + (prov_idx if prov_idx is not None else 0) + 1
    eol = "\n"
    new_line = f"{indent}auth: {mode}{eol}"
    if not dry_run:
        new_lines = lines[:insert_at] + [new_line] + lines[insert_at:]
        with open(yaml_path, "w", encoding="utf-8", newline="") as f:
            f.write("".join(new_lines))

    # JSON (if present): set brain.auth in lockstep so yaml/json don't drift
    json_done = "—"
    if os.path.exists(json_path):
        try:
            d = json.load(open(json_path, encoding="utf-8"))
            brain = d.get("brain")
            if isinstance(brain, dict) and "auth" not in brain:
                brain["auth"] = mode
                if not dry_run:
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(d, f, indent=2, ensure_ascii=False)
                json_done = "set"
            else:
                json_done = "already" if isinstance(brain, dict) else "no brain"
        except Exception as e:
            json_done = f"err:{e}"

    return {"creature": name, "action": "would-set" if dry_run else "set",
            "provider": provider, "auth": mode, "json": json_done}


def main():
    ap = argparse.ArgumentParser(description="Backfill brain.auth into creatures (idempotent).")
    ap.add_argument("creatures", nargs="*", help="creature names (default: sweep --root)")
    ap.add_argument("--root", default="creatures", help="creatures dir (default: ./creatures)")
    ap.add_argument("--dry-run", action="store_true", help="show changes without writing")
    args = ap.parse_args()
    if args.creatures:
        dirs = [os.path.join(args.root, c) for c in args.creatures]
    else:
        dirs = [os.path.join(args.root, d) for d in sorted(os.listdir(args.root))
                if os.path.isdir(os.path.join(args.root, d))]
    print(f"{'creature':14} {'action':10} {'provider':12} {'auth':13} json")
    print("-" * 60)
    counts = {}
    for d in dirs:
        r = backfill_one(d, args.dry_run)
        counts[r["action"]] = counts.get(r["action"], 0) + 1
        print(f"{r['creature']:14} {r['action']:10} {r.get('provider',''):12} "
              f"{r.get('auth', r.get('why','')):13} {r.get('json','')}")
    print("-" * 60)
    print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if args.dry_run:
        print("\n(dry run — nothing written. Re-run without --dry-run to apply.)")


if __name__ == "__main__":
    main()
