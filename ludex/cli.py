"""
Ludex CLI — first FORGE wedge.

`python -m ludex create` walks an interactive flow that emits a creature
`ludex.yaml` from the existing OrganismConfig + DEFAULT_ORGANS + PRESETS.
No skill scaffolding, no identity files, no validation beyond name shape —
those are deliberate follow-up wedges.

Non-interactive use:
    python -m ludex create --name Ember --provider ollama --model qwen3.5:4b --preset full
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ludex.blocks.provider import ADAPTER_REGISTRY
from ludex.core.habitat import HabitatConfig
from ludex.core.organism_config import DEFAULT_ORGANS, DEFAULT_EFFORT, PRESETS, OrganismConfig
from ludex.core.stage import audit_creature

PROVIDERS = list(ADAPTER_REGISTRY.keys())
PRESET_NAMES = list(PRESETS.keys())
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

# Maintenance: keep a RANGE per provider — flagship → mid → small/fast — and refresh
# as model lines move. CLI providers are NOT single-model (Codex/Gemini CLIs take a
# model via -m; our Echo went 5.4→5.5 that way). Shared with the web Forge via
# /api/model-hints, so this is the one place to update.
PROVIDER_MODEL_HINTS = {
    "claude_cli": "claude-fable-5  |  claude-sonnet-5  |  claude-opus-4-8  |  claude-opus-4-7  |  claude-sonnet-4-6  |  claude-haiku-4-5",
    "claude_sdk": "claude-fable-5  |  claude-sonnet-5  |  claude-opus-4-8  |  claude-opus-4-7  |  claude-sonnet-4-6  |  claude-haiku-4-5",
    "anthropic": "claude-fable-5  |  claude-sonnet-5  |  claude-opus-4-8  |  claude-opus-4-7  |  claude-sonnet-4-6  |  claude-haiku-4-5",
    "ollama": "qwen3.5:4b  |  exaone3.5:7.8b  |  llama3.1:8b",
    "gemini_cli": "gemini-3.1-pro-preview  |  gemini-3.5-flash",
    "agy_cli": "gemini-3.5-flash",   # agy is partial-support / agy-only flash (see gemini_cli_deprecation)
    "codex_cli": "gpt-5.5  |  gpt-5.4-mini  |  gpt-5.4-nano",
    "openai": "gpt-5.5  |  gpt-5.4-mini  |  gpt-5.4-nano",
    "gemini_api": "gemini-3.5-flash  |  gemini-3.1-flash-lite",
}


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    return raw or default


def _ask_choice(prompt: str, choices: list[str], default: str = "") -> str:
    while True:
        print(f"\n{prompt}")
        for i, c in enumerate(choices, 1):
            marker = " (default)" if c == default else ""
            print(f"  {i}) {c}{marker}")
        raw = input(f"choose 1-{len(choices)}{f' [{default}]' if default else ''}: ").strip()
        if not raw and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        if raw in choices:
            return raw
        print(f"  invalid — pick a number 1-{len(choices)} or a name from the list")


def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    raw = input(f"{prompt}{suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _validate_name(name: str) -> str | None:
    if not name:
        return "name is empty"
    if not NAME_PATTERN.match(name):
        return "name must start with a letter and contain only letters/digits/_/-"
    return None


def _custom_organs() -> dict:
    """Walk per-organ enable/disable. Required organs auto-stay-on."""
    organs = {k: dict(v) for k, v in DEFAULT_ORGANS.items()}
    print("\nCustom organ selection (engine + resilience are required):")
    for k, cfg in organs.items():
        if cfg.get("required"):
            cfg["enabled"] = True
            print(f"  {k}: required (always on)")
            continue
        cfg["enabled"] = _ask_yes_no(f"  enable {k}?", default=cfg.get("enabled", False))
    return organs


def _resolve_out_dir(name: str, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    return (Path.cwd() / "creatures" / name).resolve()


def cmd_create(args: argparse.Namespace) -> int:
    interactive = not (args.name and args.provider and args.model)

    if interactive:
        print("ludex create — assemble a new creature\n")

    # Name
    name = args.name or ""
    while True:
        if not name:
            name = _ask("name", default="")
        err = _validate_name(name)
        if err is None:
            break
        print(f"  {err}")
        if not interactive:
            return 2
        name = ""

    # Provider
    provider = args.provider or ""
    if not provider:
        provider = _ask_choice("brain provider:", PROVIDERS, default="claude_cli")
    elif provider not in PROVIDERS:
        print(f"unknown provider '{provider}'. choices: {', '.join(PROVIDERS)}")
        return 2

    # Model
    model = args.model or ""
    if not model:
        hint = PROVIDER_MODEL_HINTS.get(provider, "")
        if hint:
            print(f"\n  hint — common {provider} models: {hint}")
        model = _ask("model", default="")
        if not model:
            print("  model is required")
            return 2

    # Effort (reasoning-depth baseline — substrate axis E). Pin it explicitly so the
    # creature is never silently coupled to the host's CLI effort default.
    effort = (args.effort or "").strip().lower()
    if not effort:
        default_eff = DEFAULT_EFFORT.get(provider, "")
        effort = _ask("reasoning effort baseline (low/medium/high/xhigh/max, or 'dynamic')",
                      default=default_eff) if interactive else default_eff

    # Preset
    preset = args.preset or ""
    if not preset:
        preset = _ask_choice(
            "organ preset (or 'custom' to choose per organ):",
            PRESET_NAMES + ["custom"],
            default="full",
        )
    elif preset not in PRESET_NAMES and preset != "custom":
        print(f"unknown preset '{preset}'. choices: {', '.join(PRESET_NAMES)}, custom")
        return 2

    # Build config
    if preset == "custom":
        cfg = OrganismConfig(
            name=name,
            brain={"provider": provider, "model": model},
            organs=_custom_organs(),
        )
    else:
        cfg = OrganismConfig.from_preset(preset=preset, name=name, provider=provider, model=model)

    # Effort is a birth-time substrate property (axis E) — pin it on the brain so a new
    # creature starts at a deliberate baseline, not whatever the host CLI defaults to.
    if effort:
        cfg.brain["effort"] = effort

    # Output directory
    out_dir = _resolve_out_dir(name, args.out)
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"\n  target {out_dir} already exists and is not empty")
        if interactive:
            if not _ask_yes_no("  proceed anyway (will write ludex.yaml only, won't touch other files)?", default=False):
                print("aborted.")
                return 1
        else:
            if not args.force:
                print("  pass --force to write into a non-empty directory")
                return 1

    # A creature written to a real folder is a persistent local habitat — not
    # the default temporary one (temporary habitats also skip identity-file
    # scaffolding, which would leave the creature under-equipped for CLI brains).
    cfg.habitat = HabitatConfig.local(str(out_dir))

    # Custom instructions (e.g. conversation language) — feed the engine's system
    # prompt (API brains) AND the identity files (CLI brains read them).
    system = (args.system or "").strip()
    if system and "engine" in cfg.organs:
        cfg.organs["engine"]["system_prompt"] = system

    # Summary + confirm
    enabled = cfg.get_enabled_organs()
    print("\n--- summary ---")
    print(f"  name:     {name}")
    print(f"  brain:    {provider} / {model}")
    print(f"  preset:   {preset}")
    print(f"  organs:   {', '.join(enabled)}")
    print(f"  out:      {out_dir}")
    if system:
        print(f"  system:   {system}")
    print()

    if interactive and not _ask_yes_no("write ludex.yaml?", default=True):
        print("aborted.")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    written = cfg.save(str(out_dir))
    print(f"\nwrote {written}")

    # Write the identity files CLI brains auto-discover (Claude Code → CLAUDE.md,
    # Codex → AGENTS.md, Gemini → GEMINI.md) plus native skills — the same step
    # the web FORGE path runs. Without it a creature made from the CLI is
    # under-scaffolded for the very CLI brains it's most likely to use.
    try:
        ok = cfg.habitat.write_identity_files(
            creature_name=name,
            brain_model=model,
            brain_provider=provider,
            organs=enabled,
            custom_instructions=system,
        )
        print("wrote CLAUDE.md, AGENTS.md, GEMINI.md (+ skills)" if ok
              else "note: identity files skipped (habitat not persistent)")
    except Exception as e:
        print(f"note: identity files not written ({e})")

    print(f"\nnext: open {out_dir} and start talking to {name} with your CLI brain.")
    print("Capability probing runs on first build.")
    return 0


def _format_brain(brain: dict, capabilities: list[str], probed_for: str, brain_class: str = "") -> str:
    provider = brain.get("provider", "?")
    model = brain.get("model", "?")
    cap_str = ", ".join(capabilities) if capabilities else "(unprobed)"
    probed_note = ""
    if capabilities and probed_for:
        if probed_for != f"{provider}:{model}":
            probed_note = f"  ⚠ capabilities probed for '{probed_for}' — brain identity has changed since"
    class_line = f"\n  class: {brain_class}" if brain_class else ""
    return f"{provider} / {model}{class_line}\n  capabilities: {cap_str}{probed_note}"


def _format_age_days(age_days: float) -> str:
    if age_days < 1:
        return f"{age_days * 24:.1f}h"
    return f"{age_days:.1f}d"


def cmd_inspect(args: argparse.Namespace) -> int:
    creature_arg = args.creature
    creature_dir = Path(creature_arg)
    if not creature_dir.is_absolute() and not creature_dir.exists():
        # try resolving against creatures/<name>
        alt = Path.cwd() / "creatures" / creature_arg
        if alt.exists():
            creature_dir = alt
    if not creature_dir.exists() or not (creature_dir / "ludex.yaml").exists():
        print(f"no ludex.yaml at {creature_dir}", file=sys.stderr)
        return 2

    cfg = OrganismConfig.load(str(creature_dir))
    report = audit_creature(creature_dir)

    print(f"\n=== {cfg.name} ===")
    print(f"path:       {creature_dir.resolve()}")
    print()

    # Identity / lifecycle
    s = report.signals
    print("lifecycle:")
    print(f"  born:           {_format_age_days(s['age_days'])} ago")
    print(f"  sessions:       {s['session_count']}")
    if s["last_session_days_ago"] is not None:
        print(f"  last activity:  {_format_age_days(s['last_session_days_ago'])} ago")
    else:
        print(f"  last activity:  (no memory timestamps found)")
    print(f"  stage:          {report.name}")
    if report.flags:
        print(f"  flags:          {', '.join(report.flags)}")
    print()

    # Brain
    print("brain:")
    probed_for = cfg.capability_probed_brain or ""
    brain_line = _format_brain(cfg.brain, list(cfg.brain_capabilities), probed_for, cfg.brain_class)
    for line in brain_line.split("\n"):
        print(f"  {line}")
    print()

    # Organs
    enabled = cfg.get_enabled_organs()
    disabled = [k for k in cfg.organs if k not in enabled]
    print(f"organs ({len(enabled)} enabled / {len(cfg.organs)} total):")
    print(f"  enabled:  {', '.join(enabled)}")
    if disabled:
        print(f"  disabled: {', '.join(disabled)}")
    print()

    # Memory + fields + bonds (counts already in report.signals)
    print("activity counts:")
    print(f"  active memories:     {s['memory_count']}")
    print(f"  fields (world_models): {s['field_count']}")
    print(f"  bonds:               {s['bond_count']}")
    print(f"  total field hints:   {s['field_distill_total']}")

    # Field detail
    wm_dir = creature_dir / "memory" / "world_models"
    if wm_dir.exists():
        fields = sorted(wm_dir.rglob("*.md"))
        if fields:
            print(f"\n  fields touched:")
            for f in fields:
                rel = f.relative_to(wm_dir).as_posix().replace(".md", "")
                print(f"    - {rel}")

    bonds_dir = creature_dir / "bonds"
    if bonds_dir.exists():
        from ludex.core.bond_staleness import list_bonds, DEFAULT_STALE_THRESHOLD_DAYS
        rows = list_bonds(creature_dir)
        if rows:
            print(f"\n  bonds:")
            for name, days in sorted(rows, key=lambda r: r[1]):
                stale_marker = "  ⚠ stale" if days > DEFAULT_STALE_THRESHOLD_DAYS else ""
                age_str = f"{days:.1f}d ago" if days >= 1 else f"{days * 24:.1f}h ago"
                print(f"    - {name}  (last update: {age_str}){stale_marker}")
    print()

    # Habitat
    print("habitat:")
    print(f"  mode:        {cfg.habitat.mode}")
    print(f"  origin:      {cfg.habitat.origin or '(unset)'}")
    print(f"  persistent:  {cfg.habitat.persistent}")

    # Canonical-host guard status
    ok, msg = cfg.check_canonical_host()
    status_label = "✓ OK" if ok else "✗ MISMATCH"
    print(f"  canonical-host guard: {status_label}")
    if not ok:
        print(f"    {msg}")
    elif msg and "skipped" in msg:
        print(f"    ({msg})")
    print()

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Cohort-level breadth view. Delegates to tools/cohort_stage.py logic."""
    base = Path(args.dir)
    if not base.exists():
        print(f"no such directory: {base}", file=sys.stderr)
        return 2

    # Reuse the cohort tool's printing logic to avoid duplication.
    from collections import Counter

    rows = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        if not (d / "ludex.yaml").exists():
            continue
        try:
            report = audit_creature(d)
        except Exception:
            continue
        # Best-effort brain_class read — falls back to "?" if config
        # load fails (e.g. malformed yaml).
        klass = "?"
        try:
            cfg = OrganismConfig.load(str(d))
            klass = cfg.brain_class or "?"
        except Exception:
            pass
        rows.append((d.name, report, klass))

    if not rows:
        print("(no creatures found)")
        return 0

    from ludex.core.bond_staleness import stale_bonds as _stale_bonds
    print(f"\n{'creature':<14} {'stage':<10} {'class':<11} {'sess':>5} {'mem':>5} {'flds':>5} {'bond':>5} {'stale':>5}  flags")
    print("-" * 92)
    for name, r, klass in rows:
        sg = r.signals
        flags = ", ".join(r.flags) if r.flags else ""
        # Count stale bonds for the flags-adjacent column
        try:
            n_stale = len(_stale_bonds(base / name))
        except Exception:
            n_stale = 0
        stale_str = str(n_stale) if n_stale else "-"
        print(f"{name:<14} {r.name:<10} {klass:<11} {sg['session_count']:>5} "
              f"{sg['memory_count']:>5} {sg['field_count']:>5} "
              f"{sg['bond_count']:>5} {stale_str:>5}  {flags}")

    dist = Counter(r.name for _, r, _ in rows)
    print("\nstage distribution:", dict(dist))
    print(f"total: {len(rows)} creatures")
    return 0


def _resolve_creature_dir(arg: str) -> Path | None:
    """Resolve a creature argument to a directory: absolute path, relative
    path, or bare name → ./creatures/<name>. Returns None if not found."""
    p = Path(arg)
    if not p.is_absolute() and not p.exists():
        alt = Path.cwd() / "creatures" / arg
        if alt.exists():
            p = alt
    if not p.exists():
        return None
    return p


def cmd_audit(args: argparse.Namespace) -> int:
    """Memory audit — accumulation patterns, recall surface, deprecated
    counts, age histogram, top tags, name frequencies, and (with
    --show-forgotten) the forgotten-memory diagnostic view. Delegates
    to tools/memory_audit.py to avoid duplicating logic."""
    from tools.memory_audit import render_report

    creature_dir = _resolve_creature_dir(args.creature)
    if creature_dir is None:
        print(f"creature not found: {args.creature}", file=sys.stderr)
        return 2

    names_set: set[str] | None = None
    if args.names:
        names_set = {n.strip() for n in args.names.split(",") if n.strip()}

    return render_report(
        creature_dir,
        top_tags=args.top_tags,
        top_names=args.top_names,
        names=names_set,
        show_forgotten=args.show_forgotten,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ludex",
        description="Ludex CLI — assemble and inspect creatures.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="assemble a new creature into creatures/<name>/ludex.yaml")
    create.add_argument("--name", help="creature name (letters/digits/_/-, must start with letter)")
    create.add_argument("--provider", choices=PROVIDERS, help="brain provider")
    create.add_argument("--model", help="brain model id")
    create.add_argument("--preset", help=f"organ preset: {', '.join(PRESET_NAMES)}, or 'custom'")
    create.add_argument("--out", help="output directory (default: creatures/<name>)")
    create.add_argument("--system", default="", help="custom instructions / system prompt (e.g. preferred conversation language)")
    create.add_argument("--effort", default="", help="baseline reasoning effort / substrate axis E (low/medium/high/xhigh/max, or 'dynamic' for gemini/agy). Default: high for claude/codex, dynamic for gemini/agy — pinned so the creature isn't host-coupled.")
    create.add_argument("--force", action="store_true", help="write into non-empty directory without asking")
    create.set_defaults(func=cmd_create)

    inspect = sub.add_parser("inspect", help="show a creature's identity, stage, organs, activity, and habitat")
    inspect.add_argument("creature", help="creature name (resolves under ./creatures/) or absolute/relative path")
    inspect.set_defaults(func=cmd_inspect)

    # `list` and `cohort` share the same handler — `cohort` is the
    # discoverable name post-stage-tool consolidation, `list` is kept
    # for muscle memory.
    for cmd_name in ("list", "cohort"):
        c = sub.add_parser(cmd_name, help="cohort-level stage table (breadth view)")
        c.add_argument("--dir", default="creatures", help="creatures parent dir (default: creatures)")
        c.set_defaults(func=cmd_list)

    audit = sub.add_parser(
        "audit",
        help="memory audit: accumulation patterns, recall surface, top tags, --show-forgotten",
    )
    audit.add_argument("creature", help="creature name (resolves under ./creatures/) or path")
    audit.add_argument("--top-tags", type=int, default=20)
    audit.add_argument("--top-names", type=int, default=15)
    audit.add_argument(
        "--names",
        help="comma-separated creature names to restrict name-frequency to",
    )
    audit.add_argument(
        "--show-forgotten",
        action="store_true",
        help="list forgotten memories with reasons (D-071 pillar 3)",
    )
    audit.set_defaults(func=cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    from ludex.core.dotenv import load_dotenv
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
