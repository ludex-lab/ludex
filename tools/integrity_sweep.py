"""integrity_sweep.py — D-090 Cleaner v0: caretaker integrity sweep (heuristic, recommend-only).

D-090's Prevention layer (`ephemeral_creature`) blocks foreign writes by construction. This is
the **Cleaner**: a cheap heuristic sweep that DETECTS the residue of writes that slipped past
discipline. It RECOMMENDS only — it never mutates, quarantines, or kills (mortality principle:
tools can't kill; JJ decides). Run it inside the caretaker pass, never as a daemon.

Forcing incidents — both an unsanctioned writer reaching canonical, caught only by hand:
  - Pulsar (2026-06-13): an in-session experiment loaded a LIVE creature and mutated its store.
  - Stray Windows cron (2026-06-20): `LudexRayHabitatHeartbeat` (every 6h since 04-21, a
    pre-06-13 leftover) auto-fired post-hiatus wakes on the 6 Ray creatures — once on 6-week
    stale state, once on canonical — across the two-lab split. Automation-drift + cross-lab-drift.
Discipline breaks under load, so the check has to be a tool, not a habit.

Three heuristic checks (v0; the full write-lease/provenance spine is the later precision layer):
  1. field-history anomaly  — per-environment state (`world_models/`) for an environment the
                              creature never entered (its spans ARE its legit-field registry).
  2. unsanctioned automation — a scheduled task/daemon set to run ludex on creatures without a
                              sanctioned caretaker lease (the stray-cron class; Mac side here,
                              Windows `schtasks` side is Ray's).
  3. stale-checkout write    — creature files dirty while the local checkout is behind canonical
                              (the stale-wake class).

Session-bound BY DESIGN: it's a plain CLI you run in the caretaker pass. A police *daemon* would
just be the next stray cron — the exact thing check 2 hunts. Findings are non-destructive
recommendations (flag / quarantine-ephemeral / restore-from-snapshot are all JJ's call).

Usage:
  python tools/integrity_sweep.py                      # sweep ./creatures + this checkout + host
  python tools/integrity_sweep.py --root ~/ludex/creatures
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

_AUTOMATION_TOKENS = ("ludex", "heartbeat", "creature")  # + the repo path, added at runtime


def _legit_environments(creature_dir: str) -> set[str]:
    """The set of environments a creature actually entered — its own field-history is its
    legit-field registry (the interim heuristic until write-provenance lands). Built from
    `field_start` (field_type/field_name) and `lxm.match_experience` (meta.game) spans."""
    legit: set[str] = set()
    spans = os.path.join(creature_dir, "store", "spans.jsonl")
    if not os.path.exists(spans):
        return legit
    for ln in open(spans, encoding="utf-8"):
        try:
            s = json.loads(ln)
        except Exception:
            continue
        a = s.get("attributes") or {}
        if s.get("kind") == "field_start":
            for k in ("field_type", "field_name"):
                if a.get(k):
                    legit.add(str(a[k]).lower())
        elif s.get("kind") == "lxm.match_experience":
            game = ((a.get("meta") or {}).get("game"))
            if game:
                legit.add(str(game).lower())
    return legit


def check_field_history(creature_dir: str, name: str) -> list[dict]:
    """Flag per-environment state keyed to an environment absent from the creature's
    field-history — the Pulsar signal (a world-model from a field it never entered)."""
    findings = []
    wm = os.path.join(creature_dir, "world_models")
    if not os.path.isdir(wm):
        return findings  # no per-env state → trivially clean (forward-looking guard)
    legit = _legit_environments(creature_dir)
    for key in sorted(os.listdir(wm)):
        k = key.lower().rsplit(".", 1)[0]  # strip extension if any
        if not k:
            continue
        related = any(k in t or t in k for t in legit)
        if not related:
            findings.append({
                "check": "field-history",
                "target": f"{name}/world_models/{key}",
                "detail": f"world-model for '{key}' but no field-history entry (legit: {sorted(legit) or 'none'})",
                "recommend": "verify the creature actually entered this environment; if foreign, "
                             "quarantine the world-model (ephemeral) + restore from snapshot — JJ's call",
            })
    return findings


def check_stale_checkout(repo_root: str) -> list[dict]:
    """Flag creature files dirty while the checkout is behind canonical — the stale-wake class
    (writes made on a copy N commits behind, then at risk of clobbering canonical). Shares the
    heartbeat ① gate's freshness check (network-free here — the sweep stays offline)."""
    from ludex.core.freshness import checkout_is_current
    ok, detail = checkout_is_current(repo_root, fetch=False)
    behind = (not ok) and detail.startswith("behind")  # genuine "behind upstream", not a git error
    try:
        dirty = [ln for ln in subprocess.run(
            ["git", "-C", repo_root, "status", "--porcelain", "--", "creatures"],
            capture_output=True, text=True, timeout=15).stdout.splitlines() if ln.strip()]
    except Exception:
        dirty = []
    findings = []
    if behind and dirty:
        findings.append({
            "check": "stale-checkout",
            "target": f"{repo_root} (creatures/)",
            "detail": f"{len(dirty)} dirty creature path(s) while {detail} (last-known; sweep is network-free) "
                      f"— possible stale-state writes",
            "recommend": "`git pull --ff-only` then re-verify the creature writes before committing; "
                         "park stale writes on a branch if they predate canonical evolution",
        })
    elif behind:
        findings.append({
            "check": "stale-checkout",
            "target": f"{repo_root}",
            "detail": f"checkout is {detail} (last-known) — pull before any creature write",
            "recommend": "`git pull --ff-only` before running creatures, so writes land on canonical",
        })
    return findings


def _windows_scheduled_tasks() -> list[tuple[str, str, str]]:
    """(name, command, state) for every Windows scheduled task, via PowerShell — its property
    names are locale-independent (schtasks CSV headers localize, and this box is Korean). [] on error."""
    ps = ("Get-ScheduledTask | ForEach-Object { "
          "$c = ($_.Actions | ForEach-Object { \"$($_.Execute) $($_.Arguments)\" }) -join ' | '; "
          "[pscustomobject]@{ n=($_.TaskPath+$_.TaskName); c=$c; s=\"$($_.State)\" } } "
          "| ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        data = json.loads(out) if out else []
    except Exception:
        return []
    if isinstance(data, dict):  # ConvertTo-Json emits a bare object for a single task
        data = [data]
    return [((d.get("n") or "").strip(), (d.get("c") or "").strip(), (d.get("s") or "").strip())
            for d in data if isinstance(d, dict)]


def check_unsanctioned_automation(repo_root: str) -> list[dict]:
    """Flag schedulers/daemons configured to run ludex on creatures — the stray-cron class.
    Mac side (crontab + LaunchAgents/LaunchDaemons) here; Windows `schtasks` is Ray's half."""
    findings = []
    tokens = tuple(_AUTOMATION_TOKENS) + (os.path.basename(repo_root.rstrip("/")),)

    def _hits(text: str) -> bool:
        t = (text or "").lower()
        return any(tok and tok.lower() in t for tok in tokens)

    from ludex.core.integrity import is_sanctioned

    def _drift(text: str) -> bool:
        """ludex automation that is NOT a sanctioned lease — the thing to flag."""
        return _hits(text) and not is_sanctioned(text)

    if sys.platform == "darwin":
        # user crontab
        try:
            cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
            for ln in cron.splitlines():
                if ln.strip() and not ln.strip().startswith("#") and _drift(ln):
                    findings.append({"check": "unsanctioned-automation", "target": "crontab",
                                     "detail": f"cron line references ludex (not a sanctioned lease): {ln.strip()[:90]}",
                                     "recommend": "add a lease to config/sanctioned_automation.yaml if intended; "
                                                  "else `crontab -e` and remove (auto-wakes write canonical unsupervised)"})
        except Exception:
            pass
        # launchd agents/daemons
        for base in (os.path.expanduser("~/Library/LaunchAgents"),
                     "/Library/LaunchAgents", "/Library/LaunchDaemons"):
            if not os.path.isdir(base):
                continue
            for f in sorted(os.listdir(base)):
                if not f.endswith(".plist"):
                    continue
                p = os.path.join(base, f)
                try:
                    body = open(p, encoding="utf-8", errors="replace").read()
                except Exception:
                    continue
                if _hits(body) and not (is_sanctioned(f) or is_sanctioned(body)):
                    findings.append({"check": "unsanctioned-automation", "target": p,
                                     "detail": "LaunchAgent/Daemon plist references ludex (not a sanctioned lease)",
                                     "recommend": "add a lease to config/sanctioned_automation.yaml if intended; "
                                                  "else `launchctl bootout` + remove the plist"})
    elif sys.platform.startswith("win"):
        for name, cmd, state in _windows_scheduled_tasks():
            if _drift(name + " " + cmd):
                findings.append({"check": "unsanctioned-automation", "target": f"schtasks: {name}",
                                 "detail": f"scheduled task ({state}) runs ludex (not a sanctioned lease): {cmd[:90]}",
                                 "recommend": "add a lease to config/sanctioned_automation.yaml if intended; else "
                                              "disable/remove (`schtasks /Change /TN <name> /DISABLE` or /Delete) — "
                                              "auto-wakes write canonical unsupervised (the LudexRayHabitatHeartbeat class)"})
    return findings


def main():
    # Windows consoles default to a localized codec (cp949 on this Korean box); the sweep's
    # box-drawing + ✓/⚠/→ glyphs crash on encode otherwise. Force utf-8 I/O.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="D-090 Cleaner v0 — caretaker integrity sweep (recommend-only).")
    ap.add_argument("--root", default="creatures", help="creatures dir to sweep (default: ./creatures)")
    args = ap.parse_args()
    try:
        repo_root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                   capture_output=True, text=True, timeout=10).stdout.strip() or os.getcwd()
    except Exception:
        repo_root = os.getcwd()

    findings: list[dict] = []
    findings += check_unsanctioned_automation(repo_root)
    findings += check_stale_checkout(repo_root)
    for d in sorted(os.listdir(args.root)):
        p = os.path.join(args.root, d)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "ludex.yaml")):
            findings += check_field_history(p, d)

    print("D-090 Cleaner v0 — integrity sweep (heuristic, recommend-only; never auto-acts)")
    print("=" * 92)
    if not findings:
        print("  ✓ no anomalies — field-history clean, no unsanctioned automation, checkout current.")
    else:
        for f in findings:
            print(f"\n  ⚠ [{f['check']}] {f['target']}")
            print(f"      {f['detail']}")
            print(f"      → {f['recommend']}")
    print("\n" + "=" * 92)
    by = {}
    for f in findings:
        by[f["check"]] = by.get(f["check"], 0) + 1
    print("  " + ("  ".join(f"{k}={v}" for k, v in sorted(by.items())) if by else "all clear"))
    print("  Recommendations only — flag / quarantine-ephemeral / restore-from-snapshot are JJ's call (tools can't kill).")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main())
