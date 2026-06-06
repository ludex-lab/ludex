"""D-062 Phase 2b.0 field-host CLI — start a cross-habitat reach session.

Third Ray-side skeleton in the reach family (Phase 2b.0):

  ludex/mcp/github_adapter.py        — field host transport (GitHubSessionClient)
  ludex/reach/reach_orchestrator.py  — peer polling agent (ReachOrchestrator)
  ludex/reach/start_session.py       — field host bootstrap (this file)

A reach session needs someone to **prepare** the `sessions/<id>/`
directory before either side can do anything useful with it. That
someone is the field host — typically the caretaker of the machine
that will host the conversational field (Council, Forum, Academy).
This CLI does exactly that preparation:

    1. Validate participant + pairing arguments.
    2. Write `meta.yaml` with session id, field name, field host,
       participants, and transport metadata.
    3. Write `turn.yaml` with turn=1 pointed at the first actor.
    4. Write `prompts/001.md` with the opening prompt (read from a
       file or stdin).
    5. Commit + push (unless --no-push).

What it does *not* do: run the field loop. That belongs to
whatever ConversationField implementation lives on the field host
(Council, Forum, etc.) and uses `GitHubSessionClient` +
`LocalMCPClient` as per-participant ResponseFns. Keeping the
bootstrap separate keeps the field logic reusable across local /
subprocess / github transports.

Example:

    python -m ludex.reach.start_session \\
        --repo-root /d/projects/ludus-ex-machina \\
        --session-id reach_2026-04-24_hearth_primo_real_001 \\
        --field Council \\
        --field-host Primo@34d41615-1642-4094-be71-05024185149d:mac-studio-001 \\
        --participant Hearth@92520f1d-ea8b-4b7d-99dc-b50ad5e817d0:win-nautilus-001:sym-92520f1d-34d41615-01 \\
        --participant Primo@34d41615-1642-4094-be71-05024185149d:mac-studio-001:sym-34d41615-92520f1d-01 \\
        --first-actor Hearth \\
        --prompt-file opening_prompt.md \\
        --max-turns 10

`--participant` format: `<creature>@<machine_id>:<machine_alias>:<pairing_id>`
Alias and pairing_id may be empty (two trailing colons). Role defaults
to `discussant`; override per participant with `--role creature=role`
kwargs if needed in a later revision.

Auto session id: if `--session-id` is omitted, the CLI generates
`reach_<YYYY-MM-DD>_<alpha>_<beta>_<nnn>` where alpha/beta are the
first two participants' creature names (lowercased) and nnn is a
three-digit counter chosen from existing directories.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ludex.reach.schema_io import (
    Participant,
    SessionMeta,
    TurnPointer,
    dump_yaml_text,
    render_frontmatter_md,
    utcnow_iso,
    write_prompt,
    write_yaml,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_participant(spec: str) -> Participant:
    """Parse `<creature>@<machine_id>:<machine_alias>:<pairing_id>:<brain>`.

    All segments after `machine_id` may be empty. The optional `brain`
    segment (4th colon-separated field) carries a free-form brain
    identifier like ``"claude-opus-4-7 (claude_cli)"`` which the
    orchestrator threads to ``update_bond(other_brain=...)``. Without
    it, peer-bond headers fall back to whatever stale
    ``creatures/<peer>/ludex.json`` is tracked in the shared repo
    (Phase 2b.1.3 fix).

    `role` is always `discussant` at this CLI revision.
    """
    try:
        head, tail = spec.split("@", 1)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"participant spec missing @: {spec!r} "
            "(expected '<creature>@<machine_id>:<alias>:<pairing_id>[:<brain>]')"
        )
    parts = tail.split(":", 3)
    # Pad to 4 segments: machine_id, alias, pairing_id, brain.
    while len(parts) < 4:
        parts.append("")
    machine_id, machine_alias, pairing_id, brain = parts[:4]
    if not head or not machine_id:
        raise argparse.ArgumentTypeError(
            f"participant needs at least creature and machine_id: {spec!r}"
        )
    return Participant(
        creature=head,
        machine_id=machine_id,
        machine_alias=machine_alias,
        pairing_id=pairing_id,
        role="discussant",
        brain=brain,
    )


def _parse_field_host(spec: str) -> Participant:
    """Same grammar as `_parse_participant` but pairing_id is optional
    and defaults to empty."""
    return _parse_participant(spec + ":" * (2 - spec.count(":")))


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ludex.reach.start_session",
        description=(
            "Bootstrap a D-062 Phase 2b cross-habitat reach session "
            "(writes meta.yaml + turn.yaml + first prompt, commits "
            "and optionally pushes)."
        ),
    )
    p.add_argument("--repo-root", required=True,
                   help="Local checkout of the shared sessions repository.")
    p.add_argument("--field", required=True,
                   help="Field name (Council, Forum, Academy, ...).")
    p.add_argument(
        "--field-host", required=True, type=_parse_field_host,
        help=(
            "Field host participant: "
            "<creature>@<machine_id>:<machine_alias>[:<pairing_id>]"
        ),
    )
    p.add_argument(
        "--participant", action="append", required=True,
        type=_parse_participant, dest="participants",
        help=(
            "Participant spec (repeatable): "
            "<creature>@<machine_id>:<machine_alias>:<pairing_id>"
        ),
    )
    p.add_argument(
        "--first-actor", default=None,
        help=(
            "Creature name that speaks first (must match one of the "
            "--participant specs). Defaults to the first --participant."
        ),
    )
    p.add_argument("--session-id", default=None,
                   help="Override auto-generated session id.")
    p.add_argument(
        "--prompt-file", type=Path, default=None,
        help=(
            "Markdown file to use as the opening prompt body. If "
            "omitted, the CLI reads stdin. Frontmatter is added "
            "automatically."
        ),
    )
    p.add_argument("--max-turns", type=int, default=40)
    p.add_argument("--max-idle-seconds", type=int, default=1800)
    p.add_argument("--note", default="",
                   help="Optional provenance note embedded in meta.yaml.")
    p.add_argument("--smoke", action="store_true",
                   help="Tag meta.yaml with smoke: true (test sessions).")
    p.add_argument("--no-push", action="store_true",
                   help="Do not run `git push` (leaves the commit local).")
    p.add_argument("--no-commit", action="store_true",
                   help="Do not run `git add`/`git commit` (inspect the "
                        "session dir before committing by hand).")
    p.add_argument("--git-remote", default="origin")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


# ---------------------------------------------------------------------------
# Session id generation
# ---------------------------------------------------------------------------


def _generate_session_id(
    sessions_dir: Path,
    participants: list[Participant],
    now: Optional[datetime] = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    date_s = now.strftime("%Y-%m-%d")
    alpha = participants[0].creature.lower()
    beta = (
        participants[1].creature.lower()
        if len(participants) > 1 else "solo"
    )
    base = f"reach_{date_s}_{alpha}_{beta}_"
    existing: set[str] = set()
    if sessions_dir.is_dir():
        existing = {p.name for p in sessions_dir.iterdir() if p.is_dir()}
    for n in range(1, 1000):
        candidate = f"{base}{n:03d}"
        if candidate not in existing:
            return candidate
    raise RuntimeError("session id counter exhausted (1000 sessions/day)")


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------


def _write_meta_yaml(
    session_dir: Path,
    meta: SessionMeta,
    note: str,
    smoke: bool,
) -> Path:
    data = meta.to_yaml_dict()
    if note:
        data["note"] = note
    if smoke:
        data["smoke"] = True
    return write_yaml(session_dir / "meta.yaml", data)


def _write_turn_yaml(session_dir: Path, pointer: TurnPointer) -> Path:
    return write_yaml(session_dir / "turn.yaml", pointer.to_yaml_dict())


def _write_first_prompt(
    session_dir: Path,
    session_id: str,
    first_actor: Participant,
    prompt_body: str,
) -> Path:
    return write_prompt(
        session_dir,
        turn_n=1,
        session_id=session_id,
        addressee=first_actor,
        prompt_body=prompt_body,
    )


# ---------------------------------------------------------------------------
# Git shell-outs (shared pattern with github_adapter / reach_orchestrator).
# Lives here too rather than imported so the CLI has no runtime dependency
# on the field-host client or the peer orchestrator.
# ---------------------------------------------------------------------------


def _run_git(repo_root: Path, argv: list[str]) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *argv],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(argv)} failed rc={result.returncode}: "
            f"{result.stderr.strip()}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    repo_root = Path(args.repo_root).resolve()
    sessions_root = repo_root / "sessions"
    sessions_root.mkdir(exist_ok=True)

    # Pick the opening actor.
    participant_map = {p.creature: p for p in args.participants}
    first_name = args.first_actor or args.participants[0].creature
    if first_name not in participant_map:
        print(
            f"--first-actor {first_name!r} not in --participant list: "
            f"{[p.creature for p in args.participants]}",
            file=sys.stderr,
        )
        return 2
    first_actor = participant_map[first_name]

    # Allocate session id.
    session_id = args.session_id or _generate_session_id(
        sessions_root, args.participants
    )
    session_dir = sessions_root / session_id
    if session_dir.exists() and any(session_dir.iterdir()):
        print(
            f"session dir already exists and is non-empty: {session_dir}",
            file=sys.stderr,
        )
        return 2
    session_dir.mkdir(parents=True, exist_ok=True)

    # Load opening prompt body.
    if args.prompt_file:
        prompt_body = args.prompt_file.read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            print("reading opening prompt from stdin (ctrl-D to end)...",
                  file=sys.stderr)
        prompt_body = sys.stdin.read()
    if not prompt_body.strip():
        print("opening prompt is empty — aborting", file=sys.stderr)
        return 2

    # Build meta.yaml.
    meta = SessionMeta(
        session_id=session_id,
        field=args.field,
        field_host=args.field_host,
        participants=args.participants,
        max_idle_seconds=args.max_idle_seconds,
        max_turns=args.max_turns,
        created_at=utcnow_iso(),
        status="active",
        close_reason="",
    )
    meta_path = _write_meta_yaml(session_dir, meta, args.note, args.smoke)

    # Build turn.yaml — prompt_available=True because we are writing the
    # opening prompt in the same commit.
    pointer = TurnPointer(
        turn=1,
        next_creature=first_actor.creature,
        next_machine_id=first_actor.machine_id,
        next_machine_alias=first_actor.machine_alias,
        prompt_available=True,
        updated_at=utcnow_iso(),
    )
    turn_path = _write_turn_yaml(session_dir, pointer)

    # Opening prompt.
    prompt_path = _write_first_prompt(
        session_dir, session_id, first_actor, prompt_body
    )

    print(f"prepared reach session {session_id}")
    for p in (meta_path, turn_path, prompt_path):
        print(f"  wrote {p.relative_to(repo_root)}")

    if args.no_commit:
        print("--no-commit set, leaving files unstaged")
        return 0

    # Git commit + push.
    rel = [str(p.relative_to(repo_root)) for p in (meta_path, turn_path, prompt_path)]
    _run_git(repo_root, ["add", *rel])
    commit_msg = f"reach {session_id}: prepare session (turn 1 -> {first_actor.creature})"
    _run_git(repo_root, ["commit", "-m", commit_msg])
    print(f"committed: {commit_msg}")

    if args.no_push:
        print("--no-push set, not pushing")
        return 0

    _run_git(repo_root, ["push", args.git_remote, "HEAD"])
    print(f"pushed to {args.git_remote}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
