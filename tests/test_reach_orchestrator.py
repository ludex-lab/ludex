"""Tests for `ludex.reach.reach_orchestrator` — D-062 Phase 2b.0.

Unit coverage of the pure helpers + filesystem behaviour of
`ReachOrchestrator`. Git shell-outs are not exercised here; Phase
2b.1 will add a fake-git integration test that drives the full
`run()` loop.

Tests treat `meta.yaml.status` as variable rather than hardcoding
`active`, so the Phase 2b.2 lobby-pattern extension (waiting / ready
statuses) does not force test rewrites.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ludex.reach.reach_orchestrator import (
    OrchestratorConfig,
    ReachOrchestrator,
)
from ludex.reach.schema_io import TurnPointer, read_turn_pointer


NESTED_TURN_YAML = """turn: 1
next:
  creature: Hearth
  machine_id: 92520f1d-ea8b-4b7d-99dc-b50ad5e817d0
  machine_alias: win-nautilus-001
prompt_available: true
updated_at: "2026-04-24T11:26:20Z"
"""


# ---------------------------------------------------------------------------
# ReachOrchestrator construction
# ---------------------------------------------------------------------------


class _FakeEngine:
    """Minimal engine stub exposing `handle_submit(prompt) -> TurnResult-like`."""

    def __init__(self, response_text: str = "ok"):
        self._response_text = response_text
        self.calls: list[str] = []

    def handle_submit(self, prompt: str):
        self.calls.append(prompt)

        class _R:
            response = self._response_text
        _R.response = self._response_text
        return _R()


class _FakeOrganism:
    """Organism stub exposing `get_block("engine") -> _FakeEngine`."""

    def __init__(self, engine: _FakeEngine):
        self._engine = engine

    def get_block(self, name: str):
        if name == "engine":
            return self._engine
        raise KeyError(name)


def _make_session_dir(tmp_path: Path) -> Path:
    session_dir = tmp_path / "sessions" / "reach_2026-04-24_hearth_primo_001"
    (session_dir / "prompts").mkdir(parents=True)
    (session_dir / "responses").mkdir(parents=True)
    return session_dir


def test_orchestrator_raises_when_session_dir_missing(tmp_path):
    org = _FakeOrganism(_FakeEngine())
    with pytest.raises(FileNotFoundError):
        ReachOrchestrator(
            repo_root=tmp_path,
            session_id="reach_2026-04-24_no_such_session",
            local_creature="Hearth",
            local_machine_id="92520f1d-0001",
            local_organism=org,
        )


def test_orchestrator_default_config_values():
    cfg = OrchestratorConfig()
    assert cfg.poll_interval_seconds == 5.0
    assert cfg.idle_grace_seconds == 1800.0
    assert cfg.git_remote == "origin"


# ---------------------------------------------------------------------------
# _is_session_closed — status + close_*.md detection
# ---------------------------------------------------------------------------


def _make_meta(session_dir: Path, status: str) -> None:
    (session_dir / "meta.yaml").write_text(
        f"session_id: reach_test\nstatus: {status}\n",
        encoding="utf-8",
    )


def _new_orch(tmp_path: Path) -> ReachOrchestrator:
    session_dir = _make_session_dir(tmp_path)
    _make_meta(session_dir, "active")
    return ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        local_organism=_FakeOrganism(_FakeEngine()),
        machine_alias="win-nautilus-001",
    )


def test_is_session_closed_false_when_active_and_no_close_file(tmp_path):
    orch = _new_orch(tmp_path)
    assert orch._is_session_closed() is False


def test_is_session_closed_true_when_close_file_exists(tmp_path):
    orch = _new_orch(tmp_path)
    (orch.session_dir / "close_Primo_mac-studio-001.md").write_text(
        "---\nreason: explicit_retract\n---\nbye",
        encoding="utf-8",
    )
    assert orch._is_session_closed() is True


def test_is_session_closed_true_when_meta_status_closed(tmp_path):
    orch = _new_orch(tmp_path)
    _make_meta(orch.session_dir, "closed")
    assert orch._is_session_closed() is True


def test_is_session_closed_true_when_meta_status_interrupted(tmp_path):
    orch = _new_orch(tmp_path)
    _make_meta(orch.session_dir, "interrupted")
    assert orch._is_session_closed() is True


# ---------------------------------------------------------------------------
# _submit_to_local_engine — uses the local organism's engine
# ---------------------------------------------------------------------------


def test_submit_to_local_engine_returns_engine_response(tmp_path):
    engine = _FakeEngine(response_text="Hearth answers.")
    session_dir = _make_session_dir(tmp_path)
    _make_meta(session_dir, "active")
    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        local_organism=_FakeOrganism(engine),
        machine_alias="win-nautilus-001",
    )
    text = orch._submit_to_local_engine("What is reachable?")
    assert text == "Hearth answers."
    assert engine.calls == ["What is reachable?"]


def test_submit_to_local_engine_raises_on_missing_response(tmp_path):
    # Engine result object without a `.response` attribute.
    class _NoResponse:
        pass

    class _BadEngine:
        def handle_submit(self, prompt):
            return _NoResponse()

    class _BadOrg:
        def get_block(self, name):
            return _BadEngine()

    session_dir = _make_session_dir(tmp_path)
    _make_meta(session_dir, "active")
    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        local_organism=_BadOrg(),
        machine_alias="win-nautilus-001",
    )
    with pytest.raises(RuntimeError, match="engine returned no response"):
        orch._submit_to_local_engine("x")


def test_orchestrator_accepts_response_fn_directly(tmp_path):
    """R1 addition: response_fn kwarg bypasses organism indirection."""
    session_dir = _make_session_dir(tmp_path)
    _make_meta(session_dir, "active")
    received: list[str] = []

    def my_fn(prompt: str) -> str:
        received.append(prompt)
        return "callable reply"

    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        response_fn=my_fn,
    )
    text = orch._submit_to_local_engine("ping")
    assert text == "callable reply"
    assert received == ["ping"]


def test_orchestrator_rejects_when_both_organism_and_response_fn_none(tmp_path):
    session_dir = _make_session_dir(tmp_path)
    _make_meta(session_dir, "active")
    with pytest.raises(ValueError, match="both are None"):
        ReachOrchestrator(
            repo_root=tmp_path,
            session_id="reach_2026-04-24_hearth_primo_001",
            local_creature="Hearth",
            local_machine_id="92520f1d-0001",
        )


def test_orchestrator_rejects_when_both_organism_and_response_fn_set(tmp_path):
    session_dir = _make_session_dir(tmp_path)
    _make_meta(session_dir, "active")
    with pytest.raises(ValueError, match="OR response_fn, not both"):
        ReachOrchestrator(
            repo_root=tmp_path,
            session_id="reach_2026-04-24_hearth_primo_001",
            local_creature="Hearth",
            local_machine_id="92520f1d-0001",
            local_organism=_FakeOrganism(_FakeEngine()),
            response_fn=lambda p: "x",
        )


# Note: turn-pointer reading and YAML parse tests moved to
# test_schema_io.py after the Phase 2b.1 refactor — `read_turn_pointer`
# is now a schema_io module function rather than an instance method.


# ---------------------------------------------------------------------------
# _publish_response — writes frontmatter+body using machine_slug
# ---------------------------------------------------------------------------


def test_publish_response_uses_alias_in_filename_and_writes_frontmatter(tmp_path, monkeypatch):
    orch = _new_orch(tmp_path)
    monkeypatch.setattr(orch, "_git_commit_push", lambda paths, message: None)

    orch._publish_response(
        turn_n=1,
        prompt_body="Council convenes.",
        response_text="Hearth holds the hearth.",
    )
    expected = orch.session_dir / "responses" / "001_Hearth_win-nautilus-001.md"
    assert expected.exists()
    content = expected.read_text(encoding="utf-8")
    assert "creature: Hearth" in content
    assert "pipe_kind: github_session" in content
    assert "Hearth holds the hearth." in content


def test_publish_response_falls_back_to_machine_id_slug_when_alias_empty(tmp_path, monkeypatch):
    session_dir = _make_session_dir(tmp_path)
    _make_meta(session_dir, "active")
    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0000-0000-0000-000000000000",
        local_organism=_FakeOrganism(_FakeEngine()),
        machine_alias="",  # force fallback
    )
    monkeypatch.setattr(orch, "_git_commit_push", lambda paths, message: None)
    orch._publish_response(turn_n=2, prompt_body="p", response_text="r")
    expected = orch.session_dir / "responses" / "002_Hearth_92520f1d.md"
    assert expected.exists()


# ---------------------------------------------------------------------------
# Phase 2b.1.1 additions — _advance_after_response, _submit_with_retry
# ---------------------------------------------------------------------------


def _meta_with_two_participants(session_dir):
    """Write a meta.yaml that the advance logic needs to find the
    'other' participant. Uses Hearth + Primo by default."""
    (session_dir / "meta.yaml").write_text(
        "session_id: reach_2026-04-24_hearth_primo_001\n"
        "field: Council\n"
        "status: active\n"
        "max_turns: 4\n"
        "participants:\n"
        "  - creature: Hearth\n"
        "    machine_id: 92520f1d-0001\n"
        "    machine_alias: win-nautilus-001\n"
        "  - creature: Primo\n"
        "    machine_id: 34d41615-0001\n"
        "    machine_alias: mac-studio-001\n",
        encoding="utf-8",
    )


def test_advance_after_response_writes_next_prompt_for_other(tmp_path, monkeypatch):
    session_dir = _make_session_dir(tmp_path)
    _meta_with_two_participants(session_dir)
    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        local_organism=_FakeOrganism(_FakeEngine()),
        machine_alias="win-nautilus-001",
    )
    monkeypatch.setattr(orch, "_git_commit_push", lambda paths, message: None)

    prev_pointer = TurnPointer(
        turn=2, next_creature="Hearth",
        next_machine_id="92520f1d-0001", next_machine_alias="win-nautilus-001",
        prompt_available=True, updated_at="t",
    )
    orch._advance_after_response(prev_pointer, "Hearth speaks plainly.")

    next_prompt = session_dir / "prompts" / "003.md"
    assert next_prompt.exists()
    body = next_prompt.read_text(encoding="utf-8")
    # Body uses the agreed format, addressed to Primo.
    assert "Primo — your turn" in body
    assert "creature on win-nautilus-001" in body
    assert "> Hearth speaks plainly." in body

    # turn.yaml advanced to turn 3 with Primo as next.
    pointer = read_turn_pointer(session_dir)
    assert pointer is not None
    assert pointer.turn == 3
    assert pointer.next_creature == "Primo"
    assert pointer.next_machine_id == "34d41615-0001"
    assert pointer.prompt_available is True


def test_advance_after_response_skips_when_max_turns_reached(tmp_path, monkeypatch):
    session_dir = _make_session_dir(tmp_path)
    _meta_with_two_participants(session_dir)
    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        local_organism=_FakeOrganism(_FakeEngine()),
        machine_alias="win-nautilus-001",
    )
    commits = []
    monkeypatch.setattr(orch, "_git_commit_push", lambda paths, message: commits.append(message))

    # max_turns is 4 in fixture; advancing after turn 4 should be a no-op.
    prev_pointer = TurnPointer(
        turn=4, next_creature="Hearth",
        next_machine_id="92520f1d-0001", next_machine_alias="win-nautilus-001",
        prompt_available=True, updated_at="t",
    )
    orch._advance_after_response(prev_pointer, "final word")

    # No prompt 005, no commit attempted.
    assert not (session_dir / "prompts" / "005.md").exists()
    assert commits == []


def test_submit_with_retry_returns_first_success(tmp_path):
    session_dir = _make_session_dir(tmp_path)
    _make_meta(session_dir, "active")
    calls = []

    def fn(p):
        calls.append(p)
        return "good response"

    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        response_fn=fn,
    )
    out = orch._submit_with_retry("hello?")
    assert out == "good response"
    assert calls == ["hello?"]


def test_submit_with_retry_retries_transient_then_succeeds(tmp_path, monkeypatch):
    session_dir = _make_session_dir(tmp_path)
    _make_meta(session_dir, "active")
    sequence = iter([
        "API Error: 529 Overloaded.",
        "API Error: 529 Overloaded.",
        "real response",
    ])

    def fn(p):
        return next(sequence)

    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        response_fn=fn,
        config=OrchestratorConfig(
            engine_max_retries=4,
            engine_initial_backoff_s=0.0,  # no real waits in tests
            engine_backoff_factor=1.0,
        ),
    )
    monkeypatch.setattr("ludex.reach.reach_orchestrator.time.sleep", lambda s: None)
    out = orch._submit_with_retry("hello?")
    assert out == "real response"


def test_submit_with_retry_does_not_retry_non_transient_errors(tmp_path, monkeypatch):
    session_dir = _make_session_dir(tmp_path)
    _make_meta(session_dir, "active")
    calls = []

    def fn(p):
        calls.append(p)
        # GIT_BASH_PATH error is config, not transient.
        return '[Error: Claude Code was unable to find CLAUDE_CODE_GIT_BASH_PATH path "D:Gitbinbash.exe"]'

    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        response_fn=fn,
        config=OrchestratorConfig(
            engine_max_retries=4,
            engine_initial_backoff_s=0.0,
            engine_backoff_factor=1.0,
        ),
    )
    monkeypatch.setattr("ludex.reach.reach_orchestrator.time.sleep", lambda s: None)
    out = orch._submit_with_retry("hello?")
    # Returned the error response; only ONE attempt (no retry).
    assert "CLAUDE_CODE_GIT_BASH_PATH" in out
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Phase 2b.1.2 — narrative-identity hooks (remember / bond / snapshot / reflect)
# ---------------------------------------------------------------------------


class _FakeMemoryBlock:
    def __init__(self):
        self.calls: list[dict] = []

    def handle_remember(self, content, memory_type="episodic", tags=None,
                        importance=0.5, source="", metadata=None):
        self.calls.append({
            "content": content,
            "memory_type": memory_type,
            "tags": tags or [],
            "source": source,
            "metadata": metadata or {},
        })
        return f"mem_{len(self.calls):04d}"


class _OrgWithMemory:
    """Stub organism that resolves get_block('memory') to a fake block,
    get_block('engine') to a fake engine. Other blocks return None."""

    def __init__(self, mem: _FakeMemoryBlock, eng: _FakeEngine, name="Hearth"):
        self._mem = mem
        self._eng = eng
        self.name = name

    def get_block(self, name):
        if name == "memory":
            return self._mem
        if name == "engine":
            return self._eng
        return None


def _make_orch_with_hooks(tmp_path, organism, config=None) -> ReachOrchestrator:
    session_dir = _make_session_dir(tmp_path)
    _meta_with_two_participants(session_dir)
    return ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        local_organism=organism,
        machine_alias="win-nautilus-001",
        config=config or OrchestratorConfig(
            engine_initial_backoff_s=0.0,
            engine_backoff_factor=1.0,
        ),
    )


def test_remember_per_turn_writes_episodic_with_session_tags(tmp_path, monkeypatch):
    mem = _FakeMemoryBlock()
    org = _OrgWithMemory(mem, _FakeEngine("ok"))
    orch = _make_orch_with_hooks(tmp_path, org)
    orch._cache_peer_info()
    orch._remember_turn(turn_n=2, prompt_body="Primo says hi", response_text="Hearth replies")
    assert len(mem.calls) == 1
    call = mem.calls[0]
    assert call["memory_type"] == "episodic"
    assert "reach" in call["tags"]
    assert "reach_2026-04-24_hearth_primo_001" in call["tags"]
    assert call["source"].startswith("reach:")
    assert call["metadata"]["turn"] == 2
    assert call["metadata"]["peer_creature"] == "Primo"
    assert "Primo says hi" in call["content"]
    assert "Hearth replies" in call["content"]


def test_remember_per_turn_skipped_when_organism_is_none(tmp_path, monkeypatch):
    session_dir = _make_session_dir(tmp_path)
    _meta_with_two_participants(session_dir)
    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        response_fn=lambda p: "ok",   # no organism
    )
    # Should be a no-op rather than raise.
    orch._cache_peer_info()
    orch._remember_turn(turn_n=1, prompt_body="x", response_text="y")
    # No assertion on memory calls — there's no memory block to record on.


def test_remember_per_turn_excerpts_long_bodies(tmp_path, monkeypatch):
    mem = _FakeMemoryBlock()
    org = _OrgWithMemory(mem, _FakeEngine("ok"))
    orch = _make_orch_with_hooks(tmp_path, org)
    orch._cache_peer_info()
    long_body = "abcdef " * 200
    orch._remember_turn(turn_n=1, prompt_body=long_body, response_text=long_body)
    content = mem.calls[0]["content"]
    # Excerpt cap is 240 chars per side; ellipsis present.
    assert "…" in content
    # Memory content should be much shorter than 2x raw long body (~2400 chars).
    assert len(content) < 700


def test_on_session_close_calls_update_bond_via_selfhood(tmp_path, monkeypatch):
    mem = _FakeMemoryBlock()
    org = _OrgWithMemory(mem, _FakeEngine("ok"))
    orch = _make_orch_with_hooks(tmp_path, org)
    orch._answered_turns.add(2)
    orch._answered_turns.add(4)
    captured = {}

    def fake_update_bond(organism, other_name, shared_experience, **kwargs):
        captured["organism"] = organism
        captured["other_name"] = other_name
        captured["shared_experience"] = shared_experience
        captured.update(kwargs)
        return ""

    monkeypatch.setattr("ludex.core.selfhood.update_bond", fake_update_bond)
    orch._on_session_close()
    assert captured["other_name"] == "Primo"
    assert "reach_2026-04-24_hearth_primo_001" in captured["shared_experience"]
    assert "2 turn" in captured["shared_experience"]
    assert captured.get("context") == "genuine"


def test_on_session_close_calls_take_snapshot_with_session_id_reason(tmp_path, monkeypatch):
    mem = _FakeMemoryBlock()
    org = _OrgWithMemory(mem, _FakeEngine("ok"))
    orch = _make_orch_with_hooks(tmp_path, org)
    orch._answered_turns.add(2)   # 2b.1.3 — guard requires non-empty
    captured = {}

    def fake_take_snapshot(organism, reason, note=""):
        captured["organism"] = organism
        captured["reason"] = reason
        captured["note"] = note
        return "/path/to/snapshot"

    monkeypatch.setattr("ludex.core.ethnography.take_snapshot", fake_take_snapshot)
    # update_bond fires too — give it a no-op so it doesn't blow up.
    monkeypatch.setattr("ludex.core.selfhood.update_bond", lambda *a, **k: "")
    orch._on_session_close()
    # 2b.1.3 — reason is the session_id directly. No "reach-reach-..." doubling
    # after slugification (the session_id already starts with "reach_").
    assert captured["reason"] == "reach_2026-04-24_hearth_primo_001"
    assert "Primo" in captured["note"]


def test_on_session_close_skips_reflect_when_disabled(tmp_path, monkeypatch):
    mem = _FakeMemoryBlock()
    org = _OrgWithMemory(mem, _FakeEngine("ok"))
    orch = _make_orch_with_hooks(tmp_path, org)
    orch._answered_turns.add(1)
    # Default config has reflect_on_close=False.
    reflect_calls = []
    monkeypatch.setattr("ludex.core.selfhood.reflect",
                        lambda *a, **k: reflect_calls.append((a, k)) or "")
    monkeypatch.setattr("ludex.core.selfhood.update_bond", lambda *a, **k: "")
    monkeypatch.setattr("ludex.core.ethnography.take_snapshot", lambda *a, **k: "")
    orch._on_session_close()
    assert reflect_calls == []


def test_on_session_close_fires_reflect_when_enabled(tmp_path, monkeypatch):
    mem = _FakeMemoryBlock()
    org = _OrgWithMemory(mem, _FakeEngine("ok"))
    orch = _make_orch_with_hooks(
        tmp_path, org,
        config=OrchestratorConfig(
            engine_initial_backoff_s=0.0,
            engine_backoff_factor=1.0,
            reflect_on_close=True,
        ),
    )
    orch._answered_turns.add(1)
    captured = {}

    def fake_reflect(organism, trigger="manual", engine=None):
        captured["organism"] = organism
        captured["trigger"] = trigger
        return ""

    monkeypatch.setattr("ludex.core.selfhood.reflect", fake_reflect)
    monkeypatch.setattr("ludex.core.selfhood.update_bond", lambda *a, **k: "")
    monkeypatch.setattr("ludex.core.ethnography.take_snapshot", lambda *a, **k: "")
    orch._on_session_close()
    assert captured["trigger"].startswith("reach_complete:")


def test_on_session_close_no_op_when_organism_missing(tmp_path, monkeypatch):
    session_dir = _make_session_dir(tmp_path)
    _meta_with_two_participants(session_dir)
    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        response_fn=lambda p: "ok",
    )
    # All three downstream functions should NOT be called.
    bond_calls, snap_calls, reflect_calls = [], [], []
    monkeypatch.setattr("ludex.core.selfhood.update_bond",
                        lambda *a, **k: bond_calls.append(1))
    monkeypatch.setattr("ludex.core.ethnography.take_snapshot",
                        lambda *a, **k: snap_calls.append(1))
    monkeypatch.setattr("ludex.core.selfhood.reflect",
                        lambda *a, **k: reflect_calls.append(1))
    orch._on_session_close()
    assert bond_calls == [] and snap_calls == [] and reflect_calls == []


def test_on_session_close_skips_when_no_turns_answered(tmp_path, monkeypatch):
    """Phase 2b.1.3 — guard against duplicate hook outputs from
    crashed orchestrator runs that never produced a turn (R4.P v3
    accumulated two reach snapshots from earlier dying instances)."""
    mem = _FakeMemoryBlock()
    org = _OrgWithMemory(mem, _FakeEngine("ok"))
    orch = _make_orch_with_hooks(tmp_path, org)
    # Deliberately leave _answered_turns empty.
    bond_calls, snap_calls = [], []
    monkeypatch.setattr("ludex.core.selfhood.update_bond",
                        lambda *a, **k: bond_calls.append(1))
    monkeypatch.setattr("ludex.core.ethnography.take_snapshot",
                        lambda *a, **k: snap_calls.append(1))
    orch._on_session_close()
    assert bond_calls == [] and snap_calls == []


def test_update_bond_on_close_passes_peer_brain_from_meta(tmp_path, monkeypatch):
    """Phase 2b.1.3 — orchestrator passes the meta.yaml-recorded
    peer brain through to update_bond, so the bond header reflects
    the actual brain rather than whatever stale
    `creatures/<peer>/ludex.json` happens to be tracked."""
    session_dir = _make_session_dir(tmp_path)
    # meta.yaml that includes a brain field for Primo.
    (session_dir / "meta.yaml").write_text(
        "session_id: reach_2026-04-24_hearth_primo_001\n"
        "field: Council\n"
        "status: active\n"
        "max_turns: 4\n"
        "participants:\n"
        "  - creature: Hearth\n"
        "    machine_id: 92520f1d-0001\n"
        "    machine_alias: win-nautilus-001\n"
        "    brain: claude-haiku-4-5 (claude_cli)\n"
        "  - creature: Primo\n"
        "    machine_id: 34d41615-0001\n"
        "    machine_alias: mac-studio-001\n"
        "    brain: claude-opus-4-7 (claude_cli)\n",
        encoding="utf-8",
    )
    mem = _FakeMemoryBlock()
    org = _OrgWithMemory(mem, _FakeEngine("ok"))
    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        local_organism=org,
        machine_alias="win-nautilus-001",
    )
    orch._answered_turns.add(1)
    captured = {}
    monkeypatch.setattr("ludex.core.selfhood.update_bond",
                        lambda organism, **kw: captured.update(kw) or "")
    monkeypatch.setattr("ludex.core.ethnography.take_snapshot",
                        lambda *a, **k: "")
    orch._on_session_close()
    assert captured.get("other_brain") == "claude-opus-4-7 (claude_cli)"


def test_on_session_close_one_hook_failure_does_not_block_others(tmp_path, monkeypatch):
    mem = _FakeMemoryBlock()
    org = _OrgWithMemory(mem, _FakeEngine("ok"))
    orch = _make_orch_with_hooks(tmp_path, org)
    orch._answered_turns.add(2)

    def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    snap_calls = []
    monkeypatch.setattr("ludex.core.selfhood.update_bond", boom)
    monkeypatch.setattr("ludex.core.ethnography.take_snapshot",
                        lambda *a, **k: snap_calls.append(1))
    monkeypatch.setattr("ludex.core.selfhood.reflect", lambda *a, **k: "")
    # Should not raise.
    orch._on_session_close()
    # Snapshot still fired despite update_bond failing first.
    assert snap_calls == [1]


def test_submit_with_retry_gives_up_after_max_retries(tmp_path, monkeypatch):
    session_dir = _make_session_dir(tmp_path)
    _make_meta(session_dir, "active")
    attempts = {"n": 0}

    def fn(p):
        attempts["n"] += 1
        return "API Error: 529 Overloaded."

    orch = ReachOrchestrator(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        local_creature="Hearth",
        local_machine_id="92520f1d-0001",
        response_fn=fn,
        config=OrchestratorConfig(
            engine_max_retries=2,  # 1 initial + 2 retries = 3 calls
            engine_initial_backoff_s=0.0,
            engine_backoff_factor=1.0,
        ),
    )
    monkeypatch.setattr("ludex.reach.reach_orchestrator.time.sleep", lambda s: None)
    out = orch._submit_with_retry("hello?")
    assert "529" in out
    assert attempts["n"] == 3  # initial + 2 retries
