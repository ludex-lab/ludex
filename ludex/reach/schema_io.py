"""D-062 reach-session schema I/O — shared between halves.

Single home for the on-disk shape agreed with LxM Cody in
`docs/reach_session_schema.md`: session directory layout, YAML
frontmatter conventions, machine-slug rule, and the handful of
read/write helpers both the field-host client
(`ludex/mcp/github_adapter.py::GitHubSessionClient`) and the peer
orchestrator (`ludex/reach/reach_orchestrator.py::ReachOrchestrator`)
call. LxM's `lxm/reach_orchestrator.py` imports from this module too,
which is why the function surface stays framework-neutral.

Before this module existed, Ludex shipped a hand-rolled flat-only
YAML parser (`_parse_flat_yaml`) that silently dropped nested blocks,
and discovered a `TurnPointer` round-trip regression on the day the
field-host CLI came online (see commit `55c8182`). Adopting PyYAML
here removes that class of bug entirely; the matching LxM mirror
already uses `yaml.safe_load`.

Dataclasses live here rather than next to the clients because both
halves construct them from disk identically, and keeping the
definitions in one file makes schema evolution a single-site edit.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

TOOL_ENGINE_SUBMIT = "ludex_engine_submit"


# ---------------------------------------------------------------------------
# Schema dataclasses — see docs/reach_session_schema.md
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Participant:
    creature: str
    machine_id: str
    machine_alias: str = ""
    pairing_id: str = ""
    role: str = "discussant"
    # Free-form brain identifier ("claude-opus-4-7 (claude_cli)",
    # "gpt-5.5 (codex_cli)") written by the field-host at session
    # bootstrap. Carried through meta.yaml so peer orchestrators can
    # tag bond / snapshot artifacts with the *actual* brain rather
    # than relying on the stale `creatures/<peer>/ludex.json` lookup
    # that `update_bond._lookup_other_brain` falls back to. Phase
    # 2b.1.3 fix.
    brain: str = ""


@dataclass
class SessionMeta:
    """Serializable view of `sessions/<id>/meta.yaml`."""
    session_id: str
    field: str
    field_host: Participant
    participants: list[Participant]
    transport: str = "git_polling"
    pipe_kind: str = "github_session"
    max_idle_seconds: int = 1800
    max_turns: int = 40
    status: str = "active"
    close_reason: str = ""
    created_at: str = ""

    def to_yaml_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["field_host"] = asdict(self.field_host)
        d["participants"] = [asdict(p) for p in self.participants]
        return d


@dataclass
class TurnPointer:
    """Serializable view of `sessions/<id>/turn.yaml`.

    Flat fields for construction ease; `to_yaml_dict()` re-nests the
    `next:` block to match the on-disk shape the parser + viewer
    expect.
    """
    turn: int
    next_creature: str
    next_machine_id: str
    next_machine_alias: str = ""
    prompt_available: bool = False
    updated_at: str = ""

    def to_yaml_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "next": {
                "creature": self.next_creature,
                "machine_id": self.next_machine_id,
                "machine_alias": self.next_machine_alias,
            },
            "prompt_available": self.prompt_available,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_yaml_dict(cls, data: dict[str, Any]) -> "TurnPointer":
        """Inverse of `to_yaml_dict`. Tolerates flat `next_*` keys in
        legacy files as well as the canonical nested form."""
        if not isinstance(data, dict):
            raise TypeError(f"turn.yaml root must be a mapping, got {type(data)}")
        nxt = data.get("next") or {}
        if not isinstance(nxt, dict):
            nxt = {}
        return cls(
            turn=int(data.get("turn", 0) or 0),
            next_creature=str(nxt.get("creature") or data.get("next_creature", "") or ""),
            next_machine_id=str(nxt.get("machine_id") or data.get("next_machine_id", "") or ""),
            next_machine_alias=str(nxt.get("machine_alias") or data.get("next_machine_alias", "") or ""),
            prompt_available=bool(data.get("prompt_available", False)),
            updated_at=str(data.get("updated_at", "") or ""),
        )


@dataclass(frozen=True)
class TurnEnvelope:
    """Parsed response (or prompt) envelope. The raw file is
    frontmatter + markdown body; this dataclass holds the fields the
    caller actually uses. Unknown frontmatter keys are preserved in
    `extras`."""
    turn: int
    creature: str
    machine_id: str
    machine_alias: str
    session_id: str
    timestamp: str
    body: str
    reach_span_id: str = ""
    prompt_digest: str = ""
    consent_hash: str = ""
    tool_call: str = TOOL_ENGINE_SUBMIT
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_frontmatter(cls, fm: dict[str, Any], body: str) -> "TurnEnvelope":
        known_keys = {
            "turn", "creature", "machine_id", "machine_alias",
            "session_id", "timestamp", "reach_span_id",
            "prompt_digest", "consent_hash", "tool_call",
        }
        extras = {k: v for k, v in (fm or {}).items() if k not in known_keys}
        return cls(
            turn=int((fm or {}).get("turn", 0) or 0),
            creature=str((fm or {}).get("creature", "") or ""),
            machine_id=str((fm or {}).get("machine_id", "") or ""),
            machine_alias=str((fm or {}).get("machine_alias", "") or ""),
            session_id=str((fm or {}).get("session_id", "") or ""),
            timestamp=str((fm or {}).get("timestamp", "") or ""),
            body=body,
            reach_span_id=str((fm or {}).get("reach_span_id", "") or ""),
            prompt_digest=str((fm or {}).get("prompt_digest", "") or ""),
            consent_hash=str((fm or {}).get("consent_hash", "") or ""),
            tool_call=str((fm or {}).get("tool_call", TOOL_ENGINE_SUBMIT)),
            extras=extras,
        )


@dataclass(frozen=True)
class CloseEnvelope:
    session_id: str
    by_creature: str
    by_machine_id: str
    timestamp: str
    reason: str
    turn: int
    body: str = ""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prompt_digest(prompt: str) -> str:
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def machine_slug(machine_alias: str, machine_id: str) -> str:
    """Shared rule for `<machine>` in reach-session file paths.
    Agreed with LxM Cody 2026-04-24 (ack §3(4-a)):

    - alias (stripped) when non-empty,
    - else machine_id without hyphens, first 8 chars,
    - else "unknown".
    """
    alias = (machine_alias or "").strip()
    if alias:
        return alias
    mid = (machine_id or "").replace("-", "")
    return mid[:8] or "unknown"


# ---------------------------------------------------------------------------
# YAML helpers — PyYAML-backed (no hand-rolled parser anywhere)
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> dict[str, Any]:
    """`safe_load` the file; return `{}` when the file is empty or
    parses to None. Raises on malformed YAML (caller should treat as
    hard error — schema is our contract)."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def dump_yaml_text(data: Any) -> str:
    """PyYAML dump with stable, readable defaults."""
    return yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def write_yaml(path: Path, data: Any) -> Path:
    path.write_text(dump_yaml_text(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Frontmatter parse / render
# ---------------------------------------------------------------------------


def parse_frontmatter_md(text: str) -> tuple[dict[str, Any], str]:
    """Split `---\\n...\\n---\\n\\n<body>` into (frontmatter_dict, body).

    Tolerant of: missing frontmatter (whole text is body), incomplete
    `---` framing, empty body. Adopted from LxM Cody's
    `_parse_frontmatter_md` — battle-tested via
    `test_reach_session_export.py`.
    """
    if not text.startswith("---"):
        return {}, text.strip()
    # Find the closing `---` on its own line after the opening one.
    parts = text.split("\n---\n", 1)
    # text[:3] == "---"; strip the opening marker + its newline.
    if len(parts) < 2:
        # No closing marker.
        return {}, text.strip()
    head = parts[0][3:].strip()    # between the two `---` markers
    body = parts[1]
    try:
        meta = yaml.safe_load(head) if head else {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, body.strip()


def render_frontmatter_md(frontmatter: dict[str, Any], body: str) -> str:
    fm_text = dump_yaml_text(frontmatter).rstrip("\n")
    return f"---\n{fm_text}\n---\n\n{body}\n"


# ---------------------------------------------------------------------------
# Session-shape operations — the three "thin wrappers" Cody's LxM
# orchestrator is waiting for. Both halves go through these.
# ---------------------------------------------------------------------------


def read_turn_pointer(session_dir: Path) -> TurnPointer | None:
    """Read `<session_dir>/turn.yaml`; return None if it does not
    exist yet. Raises on malformed contents so the caller doesn't
    silently advance with stale state."""
    turn_path = session_dir / "turn.yaml"
    if not turn_path.exists():
        return None
    data = load_yaml(turn_path)
    return TurnPointer.from_yaml_dict(data)


def write_turn_pointer(session_dir: Path, pointer: TurnPointer) -> Path:
    return write_yaml(session_dir / "turn.yaml", pointer.to_yaml_dict())


def read_prompt_body(session_dir: Path, turn_n: int) -> str:
    """Read `<session_dir>/prompts/<NNN>.md`; return only the body
    (frontmatter stripped). Raises FileNotFoundError if the prompt
    file is absent."""
    path = session_dir / "prompts" / f"{turn_n:03d}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    _fm, body = parse_frontmatter_md(path.read_text(encoding="utf-8"))
    return body


def write_prompt(
    session_dir: Path,
    turn_n: int,
    session_id: str,
    addressee: Participant,
    prompt_body: str,
    **extra_fm: Any,
) -> Path:
    prompts_dir = session_dir / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    fm: dict[str, Any] = {
        "session_id": session_id,
        "turn": turn_n,
        "addressee": {
            "creature": addressee.creature,
            "machine_id": addressee.machine_id,
        },
        "issued_at": utcnow_iso(),
    }
    fm.update({k: v for k, v in extra_fm.items() if v is not None})
    path = prompts_dir / f"{turn_n:03d}.md"
    path.write_text(render_frontmatter_md(fm, prompt_body), encoding="utf-8")
    return path


def write_response(
    session_dir: Path,
    turn_n: int,
    session_id: str,
    creature: str,
    machine_id: str,
    machine_alias: str,
    response_text: str,
    reach_span_id: str = "",
    consent_hash: str = "",
    prompt_body_for_digest: str | None = None,
    tool_call: str = TOOL_ENGINE_SUBMIT,
    field_locality: str = "shared_doc",
) -> Path:
    """Write `<session_dir>/responses/<NNN>_<creature>_<slug>.md` with
    full frontmatter. Returns the written path."""
    responses_dir = session_dir / "responses"
    responses_dir.mkdir(exist_ok=True)
    slug = machine_slug(machine_alias, machine_id)
    out_path = responses_dir / f"{turn_n:03d}_{creature}_{slug}.md"
    frontmatter: dict[str, Any] = {
        "session_id": session_id,
        "turn": turn_n,
        "creature": creature,
        "machine_id": machine_id,
        "machine_alias": machine_alias,
        "field_locality": field_locality,
        "timestamp": utcnow_iso(),
        "reach_span_id": reach_span_id,
        "pipe_kind": "github_session",
        "transport": "git_polling",
        "tool_call": tool_call,
    }
    if prompt_body_for_digest is not None:
        frontmatter["prompt_digest"] = prompt_digest(prompt_body_for_digest)
    if consent_hash:
        frontmatter["consent_hash"] = consent_hash
    out_path.write_text(
        render_frontmatter_md(frontmatter, response_text),
        encoding="utf-8",
    )
    return out_path


def write_close(
    session_dir: Path,
    session_id: str,
    by_creature: str,
    by_machine_id: str,
    by_machine_alias: str,
    reason: str,
    turn: int,
    body: str = "",
) -> Path:
    slug = machine_slug(by_machine_alias, by_machine_id)
    path = session_dir / f"close_{by_creature}_{slug}.md"
    fm = {
        "session_id": session_id,
        "by_creature": by_creature,
        "by_machine_id": by_machine_id,
        "by_machine_alias": by_machine_alias,
        "timestamp": utcnow_iso(),
        "reason": reason,
        "turn": turn,
    }
    path.write_text(render_frontmatter_md(fm, body), encoding="utf-8")
    return path


def is_session_closed(session_dir: Path) -> bool:
    """True when a close marker exists OR meta.yaml.status is not
    `active`. Both halves use this; Phase 2b.2 lobby pattern will
    widen the accepted statuses (waiting/ready will be treated as
    "not closed")."""
    for f in session_dir.glob("close_*.md"):
        if f.is_file():
            return True
    meta_path = session_dir / "meta.yaml"
    if meta_path.exists():
        status = str(load_yaml(meta_path).get("status", "active")).strip()
        if status and status != "active":
            return True
    return False


# ---------------------------------------------------------------------------
# Next-prompt composition (Phase 2b.1.1)
# ---------------------------------------------------------------------------


def compose_next_prompt_body(
    field_name: str,
    peer_creature: str,
    peer_machine_alias: str,
    peer_response_body: str,
    peer_turn_n: int,
    addressee_creature: str,
    sentences: int = 4,
) -> str:
    """Format the body of the next prompt so the addressee's
    `engine.handle_submit` sees a single conversational user-message.

    See `docs/reach_session_schema.md` §2.4.1 for the rationale —
    R4.P v1 showed that header-style framing
    ("`Primo (turn 1, machine):`" + markdown stage directions) gets
    parsed by creature engines as metadata rather than dialogue.
    The blockquote-the-peer-utterance + plain-prose-framing pattern
    avoids that failure mode.

    Both halves of a reach (`ReachOrchestrator` on Ludex,
    `lxm/reach_orchestrator.py` on LxM) call this so identical
    bytes land in `prompts/<NNN>.md` regardless of which side
    composed it.
    """
    # Blockquote each line of the peer's body, preserving blank-line
    # paragraph separators (an empty blockquote line for those).
    quoted_lines: list[str] = []
    for line in peer_response_body.splitlines():
        if line.strip():
            quoted_lines.append(f"> {line.rstrip()}")
        else:
            quoted_lines.append(">")
    quoted = "\n".join(quoted_lines)

    peer_loc = peer_machine_alias or "their habitat"
    return (
        f"You are in a {field_name} session with {peer_creature}, "
        f"a creature on {peer_loc}. {peer_creature} just spoke "
        f"(turn {peer_turn_n}):\n\n"
        f"{quoted}\n\n"
        f"{addressee_creature} — your turn. Respond in your own "
        f"register, {sentences} sentences. You may engage "
        f"{peer_creature}'s reflection, or notice something about the "
        f"reach yourself, or both."
    )


# ---------------------------------------------------------------------------
# Single-process lock (Phase 2b.1.1) — prevents the multi-orchestrator race
# ---------------------------------------------------------------------------


def _is_pid_alive(pid: int) -> bool:
    """Best-effort check that a PID is still running. Cross-platform
    (psutil-free) — Windows uses `os.kill(pid, 0)` which raises
    OSError if the process is gone or PermissionError if it exists
    but we cannot signal it. Either non-error result implies "alive
    enough" — we err on the side of "still running" because the
    cost of false-positive (refuse to start) is lower than the cost
    of false-negative (multi-orchestrator race)."""
    import os
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, OSError):
        return False


def acquire_session_lock(
    session_dir: Path,
    creature: str,
    machine_id: str,
    pid: int,
) -> Path:
    """Write `<session_dir>/.orchestrator_<creature>_<slug>.lock` with
    the current pid. Raises `RuntimeError` if a lock already exists
    held by another *live* PID on the same creature.

    A stale lock (PID no longer running) is silently overwritten —
    this lets the lock file be left behind by a hard kill without
    blocking subsequent orchestrator restarts.
    """
    slug = machine_slug(machine_alias="", machine_id=machine_id)
    lock_path = session_dir / f".orchestrator_{creature}_{slug}.lock"
    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text(encoding="utf-8").strip().split()[0])
        except (ValueError, IndexError):
            existing_pid = -1
        if existing_pid > 0 and existing_pid != pid and _is_pid_alive(existing_pid):
            raise RuntimeError(
                f"orchestrator already running for {creature} "
                f"(pid={existing_pid}, lock={lock_path}). Stop the "
                f"existing process or remove the lock file."
            )
    lock_path.write_text(
        f"{pid} {utcnow_iso()} {creature}\n",
        encoding="utf-8",
    )
    return lock_path


def release_session_lock(session_dir: Path, creature: str, machine_id: str) -> None:
    slug = machine_slug(machine_alias="", machine_id=machine_id)
    lock_path = session_dir / f".orchestrator_{creature}_{slug}.lock"
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Transient-error classification (Phase 2b.1.1)
# ---------------------------------------------------------------------------


# Substrings that, when they appear at the start of an engine response
# string, indicate the response is actually an error message rather than
# real model output. Matches what claude_cli / API adapters tend to
# stuff into the response field on transient or config errors.
_ENGINE_ERROR_PREFIXES: tuple[str, ...] = (
    "[Error:",
    "API Error:",
    "Error: ",
)

_ENGINE_TRANSIENT_HINTS: tuple[str, ...] = (
    "529",       # Anthropic Overloaded
    "503",       # Service Unavailable
    "Overloaded",
    "rate limit",
    "rate_limit",
    "timeout",
    "temporarily",
    "try again",
)


def is_engine_error_response(response_text: str) -> bool:
    """True when the engine's response text looks like an error string
    rather than a real model output. The orchestrator should retry
    these rather than publish them as the creature's reply."""
    if not response_text:
        return True
    head = response_text.lstrip()
    return any(head.startswith(p) for p in _ENGINE_ERROR_PREFIXES)


def is_transient_engine_error(response_text: str) -> bool:
    """Subset of `is_engine_error_response` for errors that retrying
    is likely to fix (server overloaded, rate limit, transient
    network). Distinguished from configuration errors like
    `CLAUDE_CODE_GIT_BASH_PATH not found` which retrying does not
    fix and which the orchestrator should surface fast."""
    if not is_engine_error_response(response_text):
        return False
    lowered = response_text.lower()
    return any(hint.lower() in lowered for hint in _ENGINE_TRANSIENT_HINTS)
