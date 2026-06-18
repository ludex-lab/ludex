"""integrity_check.py — narrative-substrate integrity + backup check (death detection).

Per creature_mortality_principle: DEATH = irreversible loss of the narrative DATA
(memory/bonds/SELF.md). This checks each creature's substrate is intact AND has a
recoverable backup. It is NOT a brain check (that's brain_liveness.py — dormancy).

Verdicts:
  HEALTHY  — substrate intact + git-tracked (a recoverable copy exists)
  AT_RISK  — substrate intact but NO backup → an accidental delete is real death
  THIN     — very sparse (a young creature) — informational, not a problem
  DAMAGED  — SELF.md AND memory missing/empty → narrative loss (near death)

Usage:
  python tools/integrity_check.py
  python tools/integrity_check.py --root ~/ludex/creatures
"""
import argparse
import os
import subprocess


def _git_tracked(path: str) -> bool:
    """True if `path` lives in a git repo and has tracked files (a recoverable copy)."""
    try:
        out = subprocess.run(["git", "-C", path, "ls-files", "."],
                             capture_output=True, text=True, timeout=10)
        return out.returncode == 0 and bool(out.stdout.strip())
    except Exception:
        return False


def check(creature_path: str) -> dict:
    name = os.path.basename(creature_path.rstrip("/\\"))
    self_md = os.path.join(creature_path, "SELF.md")
    mem = os.path.join(creature_path, "memory", "memories.jsonl")
    bonds_dir = os.path.join(creature_path, "bonds")
    self_txt = open(self_md, encoding="utf-8").read() if os.path.exists(self_md) else ""
    self_ok = bool(self_txt.strip()) and "empty at birth" not in self_txt
    mem_count = sum(1 for _ in open(mem, encoding="utf-8")) if os.path.exists(mem) else 0
    bonds = len([f for f in os.listdir(bonds_dir) if f.endswith(".md")]) if os.path.isdir(bonds_dir) else 0
    backed = _git_tracked(creature_path)
    if not self_ok and mem_count == 0:
        v = "DAMAGED"
    elif not backed:
        v = "AT_RISK"
    elif mem_count < 3 and bonds == 0:
        v = "THIN"
    else:
        v = "HEALTHY"
    return {"creature": name, "verdict": v, "self": "ok" if self_ok else "—",
            "mem": mem_count, "bonds": bonds, "backup": "git" if backed else "NONE"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="creatures", help="creatures dir (default: ./creatures)")
    args = ap.parse_args()
    print(f"{'creature':14} {'verdict':9} self  mem   bonds  backup")
    print("-" * 56)
    counts = {}
    for d in sorted(os.listdir(args.root)):
        p = os.path.join(args.root, d)
        if not os.path.isdir(p) or not os.path.exists(os.path.join(p, "ludex.yaml")):
            continue
        r = check(p)
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        print(f"{r['creature']:14} {r['verdict']:9} {r['self']:4}  {r['mem']:5} {r['bonds']:5}  {r['backup']}")
    print("-" * 56)
    print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("\nAT_RISK = intact but no backup → an accidental delete is real death.  DAMAGED = narrative loss.")


if __name__ == "__main__":
    main()
