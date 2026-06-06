"""D-062 Phase 2b peer-side polling agent (skeleton).

Counterpart of `ludex/mcp/github_adapter.py`'s `GitHubSessionClient`:

- `GitHubSessionClient` runs on the *field host*. It publishes prompts
  addressed to a remote creature and polls the shared repo for the
  remote's response.
- `ReachOrchestrator` runs on the *peer*. It polls the shared repo for
  prompts addressed to its local creature, runs the local engine, and
  publishes the response.

Together they close the reach-session loop. The two halves never talk
to each other directly — they only read and write the files described
in `docs/reach_session_schema.md`.

Status: skeleton. Git is shelled out via `subprocess`; no retry /
conflict handling yet. `run()` blocks the calling thread; a future
revision may accept a threading.Event for clean shutdown. Tests
parallel to `tests/test_local_mcp_adapter.py` land in Phase 2b.1.

CLI (once Phase 2b.1 wires it):

    python -m ludex.reach.reach_orchestrator \\
        --repo-root /path/to/ludus-ex-machina \\
        --session-id reach_2026-04-24_hearth_primo_001 \\
        --creature Hearth \\
        --machine-id 92520f1d-ea8b-4b7d-99dc-b50ad5e817d0 \\
        --habitat /d/projects/ludex/creatures/Hearth
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from ludex.core import trace as _trace
from ludex.reach.schema_io import (
    Participant,
    TurnPointer,
    acquire_session_lock,
    compose_next_prompt_body,
    is_engine_error_response,
    is_session_closed,
    is_transient_engine_error,
    load_yaml,
    machine_slug,
    read_prompt_body,
    read_turn_pointer,
    release_session_lock,
    utcnow_iso,
    write_prompt,
    write_response,
    write_turn_pointer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ReachOrchestrator
# ---------------------------------------------------------------------------


@dataclass
class OrchestratorConfig:
    """Tunables for the polling loop. Kept separate from constructor
    args so the CLI can build one without touching the runtime class."""
    poll_interval_seconds: float = 5.0
    idle_grace_seconds: float = 1800.0   # stop loop after this much quiet time
    git_remote: str = "origin"
    engine_max_retries: int = 4         # 2b.1.1 — retry transient engine errors
    engine_initial_backoff_s: float = 5.0
    engine_backoff_factor: float = 2.0
    response_sentences: int = 4         # default sentence count in next-prompt framing
    # 2b.1.2 — narrative-identity hooks. R4.P v2 ran clean at the wire
    # level but left no trace in the participating creatures' bonds /
    # memory / snapshots / SELF.md. These hooks close that gap. All run
    # only when `local_organism` is set (response_fn-only orchestrators
    # such as the LxM mirror are responsible for their own equivalents).
    remember_per_turn: bool = True       # write each turn into episodic memory
    update_bond_on_close: bool = True    # D-061 Allos: update bonds/<peer>.md
    take_snapshot_on_close: bool = True  # D-027 ethnography milestone
    reflect_on_close: bool = False       # opt-in (engine call cost; benefits from prior memory accumulation)


def _excerpt(text: str, limit: int) -> str:
    """Single-line, ellipsis-truncated extract of `text` for memory
    notes. Collapses internal whitespace so a multi-paragraph
    response renders compactly inside an episodic memory entry."""
    if not text:
        return ""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 1)].rstrip() + "…"


def _organism_response_fn(organism: Any) -> Callable[[str], str]:
    """Wrap a Ludex organism as a bare `prompt -> text` callable.

    The orchestrator only needs the engine's text output to write a
    response file; wrapping the organism as a `response_fn` keeps the
    class agnostic to Ludex's `OrganismConfig.build()` API and lets
    the LxM mirror plug in non-organism callables (mocks, relay
    clients, future reach-specific interpreters) without a second
    code path.
    """
    def _call(prompt: str) -> str:
        eng = organism.get_block("engine")
        result = eng.handle_submit(prompt)
        text = getattr(result, "response", None)
        if text is None:
            raise RuntimeError(f"engine returned no response: {result!r}")
        return text
    return _call


class ReachOrchestrator:
    """Peer-side reach session agent.

    Responsibilities:
      1. Poll `sessions/<session_id>/turn.yaml` on a fixed interval.
      2. When `next.creature == local_creature` and `prompt_available`
         is true, read `prompts/NNN.md`, strip frontmatter, and invoke
         `local_organism.get_block("engine").handle_submit(prompt)`.
      3. Write the response to `responses/NNN_<creature>_<machine>.md`
         with frontmatter, commit, and push.
      4. Detect session close (`close_*.md` or `meta.yaml.status !=
         active`) and terminate.

    What this skeleton does NOT do yet:
      - Retry on push contention. A single `git push` failure exits
        the loop with an error; Phase 2b.1 adds exponential backoff.
      - Multi-session concurrency. One orchestrator instance = one
        session. A future daemon wrapper can supervise several.
      - Consent check. Phase 2b assumes the pairing was approved at
        session preparation; this agent does not re-verify per turn.
    """

    def __init__(
        self,
        repo_root: Path,
        session_id: str,
        local_creature: str,
        local_machine_id: str,
        local_organism: Any = None,
        response_fn: Optional[Callable[[str], str]] = None,
        config: Optional[OrchestratorConfig] = None,
        machine_alias: str = "",
    ) -> None:
        # Exactly one of local_organism / response_fn must be set — the
        # invariant is "one path to produce text from a prompt." LxM
        # Cody's mirror uses response_fn; Ludex's CLI + existing tests
        # use local_organism. Disallowing both keeps the code path
        # single.
        if local_organism is None and response_fn is None:
            raise ValueError(
                "ReachOrchestrator requires one of local_organism or "
                "response_fn (both are None)."
            )
        if local_organism is not None and response_fn is not None:
            raise ValueError(
                "ReachOrchestrator accepts local_organism OR response_fn, "
                "not both."
            )
        self.repo_root = Path(repo_root).resolve()
        self.session_id = session_id
        self.session_dir = self.repo_root / "sessions" / session_id
        self.local_creature = local_creature
        self.local_machine_id = local_machine_id
        self.local_machine_alias = machine_alias
        self.organism = local_organism
        self._response_fn = response_fn or _organism_response_fn(local_organism)
        self.config = config or OrchestratorConfig()
        self._last_activity_at = time.time()
        self._answered_turns: set[int] = set()
        self._reach_span_id = ""
        self._extended_at: Optional[float] = None

        if not self.session_dir.exists():
            raise FileNotFoundError(
                f"reach session dir not found: {self.session_dir}. "
                "Field host must create meta.yaml + turn.yaml before "
                "the peer joins."
            )

        # Phase 2b.1.2 — peer info cached on first tick from meta.yaml
        # so the on-close bond/snapshot hooks know who to address.
        # 2b.1.3 — also caches `peer_brain` so `update_bond` writes the
        # real brain in the bond header, not whatever stale
        # `creatures/<peer>/ludex.json` happens to claim in the shared
        # repo.
        self._peer_creature: Optional[str] = None
        self._peer_machine_alias: str = ""
        self._peer_machine_id: str = ""
        self._peer_brain: str = ""
        # Lightweight per-turn log used to compose the bond summary at
        # session-close. Kept small (turn number, peer body length, our
        # body length) — does not duplicate engine output, which lives
        # in `responses/` and `memory.handle_remember` already.
        self._session_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Loop entry point
    # ------------------------------------------------------------------

    def run(self) -> int:
        """Block until the session closes or idle grace elapses.
        Returns the number of turns this agent answered.

        Acquires a single-process lock for the (session, creature)
        pair on entry; releases it on exit (Phase 2b.1.1 — prevents
        the multi-orchestrator race that produced four turn-2
        commits in R4.P v1).
        """
        import os
        lock_path = acquire_session_lock(
            self.session_dir,
            creature=self.local_creature,
            machine_id=self.local_machine_id,
            pid=os.getpid(),
        )
        logger.info("acquired session lock: %s", lock_path)
        self._emit_extended_span()
        try:
            while True:
                if self._is_session_closed():
                    logger.info("reach session %s closed", self.session_id)
                    break
                if (time.time() - self._last_activity_at) > self.config.idle_grace_seconds:
                    logger.info(
                        "reach session %s idle > %.0fs, exiting",
                        self.session_id, self.config.idle_grace_seconds,
                    )
                    break
                did_work = self._tick()
                if not did_work:
                    time.sleep(self.config.poll_interval_seconds)
        except KeyboardInterrupt:
            logger.info("reach orchestrator interrupted")
        finally:
            # Phase 2b.1.2 — narrative-identity hooks fire BEFORE the
            # retracted span so the span's downstream consumers (e.g.
            # heartbeat consolidation) can see the bond/snapshot
            # already written. Hooks no-op cleanly when local_organism
            # is None (response_fn-only orchestrators).
            self._on_session_close()
            self._emit_retracted_span()
            release_session_lock(
                self.session_dir,
                creature=self.local_creature,
                machine_id=self.local_machine_id,
            )
        return len(self._answered_turns)

    # ------------------------------------------------------------------
    # Phase 2b.1.2 narrative-identity hooks
    # ------------------------------------------------------------------

    def _cache_peer_info(self) -> None:
        """Read meta.yaml once to learn the peer creature's name +
        machine for use in on-close bond / snapshot hooks. Idempotent."""
        if self._peer_creature is not None:
            return
        meta_path = self.session_dir / "meta.yaml"
        if not meta_path.exists():
            return
        meta = load_yaml(meta_path)
        for p in meta.get("participants") or []:
            if p.get("creature") and p.get("creature") != self.local_creature:
                self._peer_creature = str(p.get("creature"))
                self._peer_machine_alias = str(p.get("machine_alias", "") or "")
                self._peer_machine_id = str(p.get("machine_id", "") or "")
                self._peer_brain = str(p.get("brain", "") or "")
                break

    def _remember_turn(
        self,
        turn_n: int,
        prompt_body: str,
        response_text: str,
    ) -> None:
        if self.organism is None:
            return
        try:
            mem = self.organism.get_block("memory")
        except Exception:
            mem = None
        if mem is None:
            return
        peer = self._peer_creature or "peer"
        # Cap the strings so memory entries do not balloon. Full text
        # already lives in `responses/` and `prompts/`; this is a
        # condensed reminder for the creature's own recall.
        peer_excerpt = _excerpt(prompt_body, 240)
        mine_excerpt = _excerpt(response_text, 240)
        content = (
            f"Reach session {self.session_id} turn {turn_n}: "
            f"{peer} said “{peer_excerpt}”. "
            f"I responded “{mine_excerpt}”."
        )
        try:
            mem.handle_remember(
                content,
                memory_type="episodic",
                tags=["reach", self.session_id, f"peer:{peer}"],
                source=f"reach:{self.session_id}",
                metadata={
                    "turn": turn_n,
                    "reach_span_id": self._reach_span_id,
                    "peer_creature": peer,
                    "peer_machine_alias": self._peer_machine_alias,
                },
            )
        except Exception:
            logger.debug("remember_turn failed", exc_info=True)

    def _on_session_close(self) -> None:
        """Run all configured narrative hooks at session end. Each is
        wrapped so that one hook's failure does not block the others
        or the lock release.

        Guard: skip when this run answered zero turns. R4.P v3
        produced two duplicate snapshots because two earlier Anvil
        orchestrator instances had crashed during git-pull retries
        and each fired hooks in their finally blocks despite never
        producing a turn response. Hooks are turn-driven artifacts;
        no turn = no hook output (Phase 2b.1.3).
        """
        if self.organism is None:
            return
        if not self._answered_turns:
            logger.info(
                "on-close hooks skipped: zero turns answered in this run"
            )
            return
        # Resolve peer info if not yet cached (e.g. orchestrator
        # exited before any tick produced work).
        self._cache_peer_info()
        if self._peer_creature is None:
            return
        if self.config.update_bond_on_close:
            self._safe_run("update_bond_on_close", self._update_bond_on_close)
        if self.config.take_snapshot_on_close:
            self._safe_run("take_snapshot_on_close", self._take_snapshot_on_close)
        if self.config.reflect_on_close:
            self._safe_run("reflect_on_close", self._reflect_on_close)

    def _safe_run(self, label: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception:
            logger.warning("hook %s failed", label, exc_info=True)

    def _compose_bond_summary(self) -> str:
        n = len(self._answered_turns)
        peer_machine = self._peer_machine_alias or self._peer_machine_id or "another habitat"
        return (
            f"Cross-habitat reach session "
            f"`{self.session_id}` with {self._peer_creature} on "
            f"{peer_machine}. I answered {n} turn(s) over the pipe "
            f"(transport=git_polling, pipe_kind=github_session). "
            f"This was a real meeting at a distance: their utterances "
            f"reached me, my replies reached them, and the pipe held."
        )

    def _update_bond_on_close(self) -> None:
        from ludex.core.selfhood import update_bond
        # 2b.1.3 — pass `other_brain` so the bond header records the
        # peer's actual brain (from meta.yaml), not whatever stale
        # `creatures/<peer>/ludex.json` happens to be tracked in the
        # shared repo. R4.P v3 hit this: Primo's tracked ludex.json
        # said `claude_cli/haiku` but the running creature was
        # actually opus-4.7.
        update_bond(
            self.organism,
            other_name=self._peer_creature,
            shared_experience=self._compose_bond_summary(),
            other_brain=self._peer_brain,
            context="genuine",
        )
        logger.info(
            "update_bond_on_close: wrote bonds/%s.md for reach %s "
            "(peer brain=%r)",
            (self._peer_creature or "peer").lower(), self.session_id,
            self._peer_brain,
        )

    def _take_snapshot_on_close(self) -> None:
        from ludex.core.ethnography import take_snapshot
        # 2b.1.3 — reason is the session_id directly. The session_id
        # already carries the `reach_` prefix, so the prior
        # `reach-{session_id}` doubled it after slugification
        # (`reach-reach-...`). Snapshot path now reads cleanly as
        # `snapshots/<date>-reach-<peer-a>-<peer-b>-<nnn>/`.
        snapshot_dir = take_snapshot(
            self.organism,
            reason=str(self.session_id),
            note=(
                f"Cross-habitat reach with "
                f"{self._peer_creature or 'peer'} "
                f"({self._peer_machine_alias or self._peer_machine_id or 'remote'}); "
                f"answered {len(self._answered_turns)} turn(s)."
            ),
        )
        logger.info("take_snapshot_on_close: %s", snapshot_dir)

    def _reflect_on_close(self) -> None:
        from ludex.core.selfhood import reflect
        reflect(
            self.organism,
            trigger=f"reach_complete:{self.session_id}",
        )
        logger.info("reflect_on_close: SELF.md updated for %s", self.session_id)

    # ------------------------------------------------------------------
    # One polling iteration
    # ------------------------------------------------------------------

    def _tick(self) -> bool:
        """Pull, see if it's our turn, answer once if so. Return True if
        we answered a turn (caller skips the sleep)."""
        self._git_pull()
        pointer = read_turn_pointer(self.session_dir)
        if pointer is None:
            return False
        if not pointer.prompt_available:
            return False
        if pointer.next_creature != self.local_creature:
            return False
        if pointer.turn in self._answered_turns:
            return False
        prompt_path = self.session_dir / "prompts" / f"{pointer.turn:03d}.md"
        if not prompt_path.exists():
            logger.warning(
                "turn.yaml says prompt_available but %s missing",
                prompt_path,
            )
            return False
        prompt_body = read_prompt_body(self.session_dir, pointer.turn)
        response_text = self._submit_with_retry(prompt_body)
        if is_engine_error_response(response_text):
            # All retries exhausted (or non-transient error). Surface
            # to the operator rather than poisoning the conversation
            # by publishing the error string as the creature's reply.
            logger.error(
                "engine returned an error response after retries; "
                "session=%s turn=%s response=%r",
                self.session_id, pointer.turn, response_text[:200],
            )
            return False
        self._publish_response(
            turn_n=pointer.turn,
            prompt_body=prompt_body,
            response_text=response_text,
        )
        self._answered_turns.add(pointer.turn)
        self._last_activity_at = time.time()
        # Phase 2b.1.2 — episodic memory write per turn. Cheap (in-
        # memory dict insert + future flush); preserves the conversation
        # in the creature's own remembering rather than only in
        # responses/.
        self._cache_peer_info()
        if self.config.remember_per_turn:
            self._remember_turn(pointer.turn, prompt_body, response_text)
        self._session_log.append({
            "turn": pointer.turn,
            "incoming_chars": len(prompt_body or ""),
            "outgoing_chars": len(response_text or ""),
        })
        # Phase 2b.1.1 — orchestrator now drives turn passing itself
        # rather than waiting for a separate field-host process.
        self._advance_after_response(pointer, response_text)
        return True

    # ------------------------------------------------------------------
    # Local engine invocation
    # ------------------------------------------------------------------

    def _submit_to_local_engine(self, prompt: str) -> str:
        """Produce text for `prompt` via the configured response path.

        Runtime indirection: `self._response_fn` was built at __init__
        from either the supplied callable (LxM mirror style) or an
        organism-bound wrapper (Ludex CLI default). This method stays
        a named entry point so tests + tracing can hook it.
        """
        return self._response_fn(prompt)

    def _submit_with_retry(self, prompt: str) -> str:
        """Wrap `_submit_to_local_engine` in retry-with-backoff for
        transient errors (Anthropic 529, 503, rate limit, etc.).

        Phase 2b.1.1 — added after R4.P v1 caught a real 529
        Overloaded mid-call and pushed the error string as Hearth's
        response. Configuration errors (e.g. missing
        CLAUDE_CODE_GIT_BASH_PATH) are surfaced fast, not retried,
        because retrying does not fix them.
        """
        backoff = self.config.engine_initial_backoff_s
        last_response = ""
        for attempt in range(1, self.config.engine_max_retries + 2):
            try:
                response = self._submit_to_local_engine(prompt)
            except Exception as e:  # pragma: no cover - skeleton
                logger.warning(
                    "engine call raised %s (attempt %d/%d): %s",
                    type(e).__name__, attempt,
                    self.config.engine_max_retries + 1, e,
                )
                response = f"[Error: {type(e).__name__}: {e}]"
            last_response = response
            if not is_engine_error_response(response):
                return response
            if not is_transient_engine_error(response):
                logger.error(
                    "engine returned a non-transient error; not "
                    "retrying. response=%r", response[:200],
                )
                return response
            if attempt > self.config.engine_max_retries:
                logger.error(
                    "engine still failing after %d retries; giving up. "
                    "response=%r", self.config.engine_max_retries,
                    response[:200],
                )
                return response
            logger.warning(
                "engine transient error (attempt %d/%d), backing off "
                "%.1fs: %r",
                attempt, self.config.engine_max_retries + 1,
                backoff, response[:120],
            )
            time.sleep(backoff)
            backoff *= self.config.engine_backoff_factor
        return last_response

    def _advance_after_response(
        self,
        prev_pointer: TurnPointer,
        my_response_body: str,
    ) -> None:
        """After publishing my response, set up the next turn for
        the other participant: write the next prompt + advance
        `turn.yaml` + commit + push.

        Phase 2b.1.1 — fixes R4.P v1 issue #1 (orchestrator stuck
        because no one wrote `prompts/<N+1>.md` after a response).
        Both halves of a reach call this; they alternate cleanly
        because each side advances only the *other* creature's turn.
        """
        meta_path = self.session_dir / "meta.yaml"
        if not meta_path.exists():
            logger.warning(
                "advance: meta.yaml missing under %s; cannot advance",
                self.session_dir,
            )
            return
        meta = load_yaml(meta_path)
        participants = meta.get("participants") or []
        max_turns = int(meta.get("max_turns", 40) or 40)
        next_turn = prev_pointer.turn + 1
        if next_turn > max_turns:
            logger.info(
                "advance: max_turns (%d) reached after turn %d; "
                "leaving session for natural close",
                max_turns, prev_pointer.turn,
            )
            return
        other = next(
            (p for p in participants
             if p.get("creature") != self.local_creature),
            None,
        )
        if not other:
            logger.warning(
                "advance: no other participant in meta.yaml; cannot "
                "advance"
            )
            return
        next_prompt_body = compose_next_prompt_body(
            field_name=str(meta.get("field", "session")),
            peer_creature=self.local_creature,
            peer_machine_alias=self.local_machine_alias,
            peer_response_body=my_response_body,
            peer_turn_n=prev_pointer.turn,
            addressee_creature=str(other.get("creature", "peer")),
            sentences=self.config.response_sentences,
        )
        addressee = Participant(
            creature=str(other.get("creature", "")),
            machine_id=str(other.get("machine_id", "")),
            machine_alias=str(other.get("machine_alias", "")),
            pairing_id=str(other.get("pairing_id", "")),
        )
        prompt_path = write_prompt(
            self.session_dir,
            turn_n=next_turn,
            session_id=self.session_id,
            addressee=addressee,
            prompt_body=next_prompt_body,
            field_phase=f"{meta.get('field', 'session')}_round_{next_turn}",
        )
        turn_path = write_turn_pointer(
            self.session_dir,
            TurnPointer(
                turn=next_turn,
                next_creature=addressee.creature,
                next_machine_id=addressee.machine_id,
                next_machine_alias=addressee.machine_alias,
                prompt_available=True,
                updated_at=utcnow_iso(),
            ),
        )
        self._git_commit_push(
            paths=[prompt_path, turn_path],
            message=(
                f"reach {self.session_id}: turn {next_turn} prompt "
                f"({self.local_creature} -> {addressee.creature})"
            ),
        )

    # ------------------------------------------------------------------
    # Schema I/O — delegates to ludex.reach.schema_io
    # ------------------------------------------------------------------

    def _publish_response(
        self,
        turn_n: int,
        prompt_body: str,
        response_text: str,
    ) -> None:
        out_path = write_response(
            self.session_dir,
            turn_n=turn_n,
            session_id=self.session_id,
            creature=self.local_creature,
            machine_id=self.local_machine_id,
            machine_alias=self.local_machine_alias,
            response_text=response_text,
            reach_span_id=self._reach_span_id,
            prompt_body_for_digest=prompt_body,
        )
        self._git_commit_push(
            paths=[out_path],
            message=(
                f"reach {self.session_id}: turn {turn_n} response "
                f"({self.local_creature})"
            ),
        )

    # ------------------------------------------------------------------
    # Session close detection
    # ------------------------------------------------------------------

    def _is_session_closed(self) -> bool:
        return is_session_closed(self.session_dir)

    # ------------------------------------------------------------------
    # Trace spans
    # ------------------------------------------------------------------

    def _emit_extended_span(self) -> None:
        self._reach_span_id = f"reach_ext_{uuid.uuid4().hex[:12]}"
        self._extended_at = time.time()
        _trace.emit_reach_extended(
            self.organism,
            pipe_kind="github_session",
            transport="git_polling",
            tool_name="ludex_engine_submit",
            field_name=None,
            attributes={
                "span_id": self._reach_span_id,
                "session_id": self.session_id,
                "role": "peer",
                "local_creature": self.local_creature,
                "local_machine_id": self.local_machine_id,
            },
        )

    def _emit_retracted_span(self) -> None:
        duration = (
            time.time() - self._extended_at
            if self._extended_at else None
        )
        _trace.emit_reach_retracted(
            self.organism,
            pipe_kind="github_session",
            transport="git_polling",
            tool_name="ludex_engine_submit",
            duration_s=duration,
            ok=True,
            error="",
            field_name=None,
            attributes={
                "span_id": self._reach_span_id,
                "session_id": self.session_id,
                "role": "peer",
                "turns_answered": len(self._answered_turns),
            },
        )

    # ------------------------------------------------------------------
    # Git shell-outs — mirror github_adapter.py._run_git; duplicated
    # intentionally so the two halves can diverge (retry policy,
    # pygit2 migration) without affecting each other.
    # ------------------------------------------------------------------

    def _git_commit_push(self, paths: list[Path], message: str) -> None:
        rel = [str(p.relative_to(self.repo_root)) for p in paths]
        self._run_git(["add", *rel])
        self._run_git(["commit", "-m", message])
        self._run_git(["push", self.config.git_remote, "HEAD"])

    def _git_pull(self) -> None:
        # `git pull --rebase` has been brittle in subprocess on
        # Windows — surfaces 'Cannot rebase onto multiple branches'
        # the moment the remote acquires new commits we need to
        # integrate, even when the same command works in an
        # interactive shell. R4.P v2 hit this twice. Splitting into
        # explicit fetch + rebase FETCH_HEAD avoids the ambiguity
        # entirely. `--autostash` on the rebase preserves dirty-tree
        # tolerance.
        self._run_git(["fetch", self.config.git_remote, "main"])
        # `origin/main` is the post-fetch remote-tracking ref; some git
        # versions reject `FETCH_HEAD` as a rebase upstream
        # (`fatal: invalid upstream`). `origin/main` works
        # consistently.
        self._run_git(["rebase", "--autostash", f"{self.config.git_remote}/main"])

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


# ---------------------------------------------------------------------------
# CLI (live wiring uses OrganismConfig.load + organism-bound response_fn)
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ludex.reach.reach_orchestrator",
        description=(
            "Peer-side polling agent for D-062 Phase 2b cross-habitat "
            "reach sessions."
        ),
    )
    p.add_argument("--repo-root", required=True,
                   help="Local checkout of the shared sessions repository.")
    p.add_argument("--session-id", required=True,
                   help="Session id matching sessions/<id>/ in the repo.")
    p.add_argument("--creature", required=True,
                   help="Local creature name this agent answers for.")
    p.add_argument("--machine-id", required=True,
                   help="Local machine_id (D-060) used in response frontmatter.")
    p.add_argument("--machine-alias", default="",
                   help="Optional local machine_alias (used in response filename).")
    p.add_argument("--habitat", required=True,
                   help="Path to the local creature habitat (for OrganismConfig.load).")
    p.add_argument("--poll-interval", type=float, default=5.0)
    p.add_argument("--idle-grace", type=float, default=1800.0)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    # Lazy import: OrganismConfig import chain is heavy.
    from ludex.core.organism_config import OrganismConfig  # noqa: WPS433

    habitat_path = Path(args.habitat)
    config = OrganismConfig.load(habitat_path)
    organism = config.build()

    orch = ReachOrchestrator(
        repo_root=Path(args.repo_root),
        session_id=args.session_id,
        local_creature=args.creature,
        local_machine_id=args.machine_id,
        local_organism=organism,
        machine_alias=args.machine_alias,
        config=OrchestratorConfig(
            poll_interval_seconds=args.poll_interval,
            idle_grace_seconds=args.idle_grace,
        ),
    )
    turns_answered = orch.run()
    print(f"reach orchestrator exited; answered {turns_answered} turn(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
