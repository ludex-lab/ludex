"""D-062 Phase 2b — GitHub-backed cross-habitat reach client.

Third `MCPClient` implementation in the transport-agnostic family
that Phase 1 (`LocalMCPClient`) and Phase 2a (`SubprocessMCPClient`)
started in `local_adapter.py`. Same
`call_tool(name, args) -> ToolResult` surface, so
`make_mcp_response_fn(client)` in `local_adapter.py` accepts this
client with no changes.

How the three transports differ:

  LocalMCPClient         — in-process dispatch
  SubprocessMCPClient    — stdio MCP to a sibling process same-host
  GitHubSessionClient    — file commits to a shared repository; the
                           peer runs `ReachOrchestrator` against its
                           local organism and commits responses back

Phase 2b.1 refactor (2026-04-24): all schema I/O lives in
`ludex.reach.schema_io`. This file keeps only the MCP-surface
plumbing and the git shell-outs; parsing, rendering, slugs, and
session-shape helpers all route through schema_io so the field-host
half and the peer half can never drift.

See `docs/cross-habitat-reach-design.md` §5.1 (transport) / §6
(lifecycle) and `docs/reach_session_schema.md` for the contracts.
"""
from __future__ import annotations

import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional

from ludex.core import trace as _trace
from ludex.mcp.local_adapter import TOOL_ENGINE_SUBMIT, ToolResult
from ludex.reach.schema_io import (
    CloseEnvelope,
    Participant,
    SessionMeta,
    TurnEnvelope,
    TurnPointer,
    machine_slug,
    parse_frontmatter_md,
    prompt_digest,
    read_turn_pointer,
    render_frontmatter_md,
    utcnow_iso,
    write_close,
    write_prompt,
    write_turn_pointer,
    write_yaml,
)

logger = logging.getLogger(__name__)


class GitHubSessionClient:
    """Reach client backed by a shared git repository.

    The caller (field host's orchestrator) holds one client per remote
    creature participating in the session. A call to `call_tool`
    writes a prompt envelope, commits + pushes, then polls for the
    peer's response and returns its body as a `ToolResult`.

    This class expects the remote peer to be running a compatible
    orchestrator on its own machine — see
    `ludex/reach/reach_orchestrator.py`.

    Parameters
    ----------
    repo_root : Path
        Local checkout of the shared repository. Must be a clean
        working tree; the client commits only the files it writes.
    session_id : str
        Matches `sessions/<session_id>/` in the repo.
    peer_creature : str
        Name of the remote creature whose engine will answer this
        tool call.
    peer_machine_id : str
        Remote machine_id (D-060).
    peer_machine_alias : str
        Optional alias for the remote machine. Used in response
        filenames via `machine_slug()`.
    local_observer_name : str
        Creature/machine name that should appear as the *local* side
        in spans.
    poll_interval_seconds : float
        How often to re-pull while waiting for the peer's response.
    max_wait_seconds : float
        Upper bound on `call_tool` wall time before returning a
        timeout ToolResult.
    git_remote : str
        Remote name to pull / push against. Defaults to "origin".
    """

    def __init__(
        self,
        repo_root: Path,
        session_id: str,
        peer_creature: str,
        peer_machine_id: str,
        peer_machine_alias: str = "",
        local_observer_name: str = "reach_client",
        poll_interval_seconds: float = 5.0,
        max_wait_seconds: float = 300.0,
        git_remote: str = "origin",
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.session_id = session_id
        self.session_dir = self.repo_root / "sessions" / session_id
        self.peer_creature = peer_creature
        self.peer_machine_id = peer_machine_id
        self.peer_machine_alias = peer_machine_alias
        self.organism_name = local_observer_name  # MCPClient Protocol
        self.poll_interval = poll_interval_seconds
        self.max_wait = max_wait_seconds
        self.git_remote = git_remote
        self._closed = False
        self._reach_span_id = ""
        self._extended_at: Optional[float] = None
        self._turn_count = 0

        if not self.session_dir.exists():
            raise FileNotFoundError(
                f"reach session dir not found: {self.session_dir}. "
                "Field host must create meta.yaml + turn.yaml before the "
                "first call_tool()."
            )

    # ------------------------------------------------------------------
    # MCPClient Protocol surface
    # ------------------------------------------------------------------

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        if self._closed:
            return ToolResult(text="", is_error=True, error="client closed")
        if name != TOOL_ENGINE_SUBMIT:
            return ToolResult(
                text="",
                is_error=True,
                error=f"unsupported tool for reach transport: {name}",
            )
        args = dict(arguments or {})
        prompt = args.get("prompt", "")
        if not prompt:
            return ToolResult(text="", is_error=True, error="empty prompt")

        if not self._reach_span_id:
            self._reach_span_id = self._emit_extended_span()

        self._turn_count += 1
        turn_n = self._turn_count

        try:
            self._publish_prompt(turn_n, prompt, args)
            response_text = self._poll_for_response(turn_n)
            return ToolResult(text=response_text)
        except TimeoutError as e:
            return ToolResult(text="", is_error=True, error=f"timeout: {e}")
        except Exception as e:  # pragma: no cover - skeleton
            logger.exception("GitHubSessionClient.call_tool failed")
            return ToolResult(text="", is_error=True, error=f"{type(e).__name__}: {e}")

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._publish_close(reason="explicit_retract")
        finally:
            self._emit_retracted_span()
            self._closed = True

    # ------------------------------------------------------------------
    # Prompt / response I/O (delegates to schema_io)
    # ------------------------------------------------------------------

    def _publish_prompt(self, turn_n: int, prompt: str, extra_args: dict) -> None:
        addressee = Participant(
            creature=self.peer_creature,
            machine_id=self.peer_machine_id,
            machine_alias=self.peer_machine_alias,
        )
        extras: dict[str, Any] = {}
        for k in ("field_phase", "field_state_digest"):
            if k in extra_args:
                extras[k] = extra_args[k]
        prompt_path = write_prompt(
            self.session_dir,
            turn_n=turn_n,
            session_id=self.session_id,
            addressee=addressee,
            prompt_body=prompt,
            **extras,
        )
        turn_path = write_turn_pointer(
            self.session_dir,
            TurnPointer(
                turn=turn_n,
                next_creature=self.peer_creature,
                next_machine_id=self.peer_machine_id,
                next_machine_alias=self.peer_machine_alias,
                prompt_available=True,
                updated_at=utcnow_iso(),
            ),
        )
        self._git_commit_push(
            paths=[prompt_path, turn_path],
            message=f"reach {self.session_id}: turn {turn_n} prompt",
        )

    def _poll_for_response(self, turn_n: int) -> str:
        deadline = time.time() + self.max_wait
        expected = self._expected_response_path(turn_n)
        while time.time() < deadline:
            self._git_pull()
            if expected.exists():
                _fm, body = parse_frontmatter_md(expected.read_text(encoding="utf-8"))
                return body
            time.sleep(self.poll_interval)
        raise TimeoutError(
            f"no response at {expected} within {self.max_wait}s"
        )

    def _publish_close(self, reason: str) -> None:
        path = write_close(
            self.session_dir,
            session_id=self.session_id,
            by_creature=self.organism_name,
            by_machine_id="",
            by_machine_alias="",
            reason=reason,
            turn=self._turn_count,
        )
        self._git_commit_push(
            paths=[path],
            message=f"reach {self.session_id}: close reason={reason}",
        )

    def _expected_response_path(self, turn_n: int) -> Path:
        slug = machine_slug(self.peer_machine_alias, self.peer_machine_id)
        return (
            self.session_dir
            / "responses"
            / f"{turn_n:03d}_{self.peer_creature}_{slug}.md"
        )

    # ------------------------------------------------------------------
    # Trace spans
    # ------------------------------------------------------------------

    def _emit_extended_span(self) -> str:
        span_id = f"reach_ext_{uuid.uuid4().hex[:12]}"
        self._extended_at = time.time()
        _trace.emit_reach_extended(
            None,
            pipe_kind="github_session",
            transport="git_polling",
            tool_name=TOOL_ENGINE_SUBMIT,
            field_name=None,
            attributes={
                "span_id": span_id,
                "session_id": self.session_id,
                "peer_creature": self.peer_creature,
                "peer_machine_id": self.peer_machine_id,
                "observer": self.organism_name,
            },
        )
        return span_id

    def _emit_retracted_span(self, ok: bool = True, error: str = "") -> None:
        duration = (
            time.time() - self._extended_at
            if self._extended_at else None
        )
        _trace.emit_reach_retracted(
            None,
            pipe_kind="github_session",
            transport="git_polling",
            tool_name=TOOL_ENGINE_SUBMIT,
            duration_s=duration,
            ok=ok,
            error=error,
            field_name=None,
            attributes={
                "span_id": self._reach_span_id,
                "session_id": self.session_id,
                "turns": self._turn_count,
                "observer": self.organism_name,
            },
        )

    # ------------------------------------------------------------------
    # Git shell-outs
    # ------------------------------------------------------------------

    def _git_commit_push(self, paths: list[Path], message: str) -> None:
        rel = [str(p.relative_to(self.repo_root)) for p in paths]
        self._run_git(["add", *rel])
        self._run_git(["commit", "-m", message])
        self._run_git(["push", self.git_remote, "HEAD"])

    def _git_pull(self) -> None:
        # See ReachOrchestrator._git_pull for the rationale —
        # `git pull --rebase` raises 'Cannot rebase onto multiple
        # branches' in Windows subprocess as soon as the remote has
        # commits we need to integrate. `fetch + rebase FETCH_HEAD`
        # is the deterministic equivalent.
        self._run_git(["fetch", self.git_remote, "main"])
        self._run_git(["rebase", "--autostash", f"{self.git_remote}/main"])

    def _run_git(self, argv: list[str]) -> None:
        result = subprocess.run(
            ["git", "-C", str(self.repo_root), *argv],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(argv)} failed rc={result.returncode}: "
                f"{result.stderr.strip()}"
            )
