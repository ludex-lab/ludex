"""Tests for `ludex.reach.start_session` — D-062 Phase 2b.0 field-host CLI.

The CLI writes meta.yaml + turn.yaml + prompts/001.md + optionally
commits and pushes. These tests cover:

- Participant-spec parsing (`_parse_participant`, `_parse_field_host`).
- Session id auto-generation with collision avoidance.
- File writers produce the schema Cody's export_static.py ingests
  (nested `next:` block, frontmatter+body on prompts).
- `main()` runs end-to-end in a tmpdir with `--no-commit --no-push`.

Tests treat the eventual Phase 2b.2 lobby pattern as variable — the
current `main()` writes `status: active` directly, and tests assert
on that shape today; when `--lobby` lands, the lobby-specific tests
go alongside these rather than replacing them.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from ludex.reach import start_session as ss


# ---------------------------------------------------------------------------
# _parse_participant
# ---------------------------------------------------------------------------


def test_parse_participant_full_form():
    p = ss._parse_participant(
        "Hearth@92520f1d-ea8b-4b7d-99dc-b50ad5e817d0:win-nautilus-001:sym-01"
    )
    assert p.creature == "Hearth"
    assert p.machine_id == "92520f1d-ea8b-4b7d-99dc-b50ad5e817d0"
    assert p.machine_alias == "win-nautilus-001"
    assert p.pairing_id == "sym-01"
    assert p.role == "discussant"
    assert p.brain == ""   # 2b.1.3 — brain optional, defaults empty


def test_parse_participant_with_brain_segment():
    """Phase 2b.1.3 — optional 4th colon-separated segment carries
    the peer's brain, threaded through meta.yaml so on-close hooks
    can stamp the right brain in the bond header."""
    p = ss._parse_participant(
        "Primo@34d41615-1642-4094-be71-05024185149d"
        ":mac-studio-001:sym-02:claude-opus-4-7 (claude_cli)"
    )
    assert p.creature == "Primo"
    assert p.brain == "claude-opus-4-7 (claude_cli)"


def test_parse_participant_brain_can_contain_colons():
    """The 4th segment is parsed with `split(':', 3)` so any colons
    inside the brain identifier are preserved."""
    p = ss._parse_participant(
        "X@id::pair:gpt-5.5 (codex_cli) :: research-preview"
    )
    assert p.brain == "gpt-5.5 (codex_cli) :: research-preview"


def test_parse_participant_empty_alias_and_pairing():
    p = ss._parse_participant("Primo@34d41615::")
    assert p.creature == "Primo"
    assert p.machine_id == "34d41615"
    assert p.machine_alias == ""
    assert p.pairing_id == ""


def test_parse_participant_missing_at_sign_raises():
    with pytest.raises(argparse.ArgumentTypeError, match="missing @"):
        ss._parse_participant("nopunctuation")


def test_parse_participant_empty_creature_raises():
    with pytest.raises(argparse.ArgumentTypeError, match="needs at least creature"):
        ss._parse_participant("@machine-id")


def test_parse_participant_empty_machine_id_raises():
    with pytest.raises(argparse.ArgumentTypeError, match="needs at least creature"):
        ss._parse_participant("Hearth@")


def test_parse_field_host_without_pairing_id():
    # Pairing_id optional for field host (covers _parse_field_host code path).
    p = ss._parse_field_host("Primo@34d41615-0001:mac-studio-001")
    assert p.creature == "Primo"
    assert p.pairing_id == ""
    assert p.machine_alias == "mac-studio-001"


# ---------------------------------------------------------------------------
# _generate_session_id
# ---------------------------------------------------------------------------


def _participants():
    return [
        ss.Participant(creature="Hearth", machine_id="92520f1d-0001",
                       machine_alias="", pairing_id="", role="discussant"),
        ss.Participant(creature="Primo", machine_id="34d41615-0001",
                       machine_alias="", pairing_id="", role="discussant"),
    ]


def test_generate_session_id_fresh_dir_returns_001(tmp_path):
    sid = ss._generate_session_id(tmp_path, _participants())
    assert sid.endswith("_hearth_primo_001")
    assert sid.startswith("reach_")


def test_generate_session_id_increments_past_existing(tmp_path):
    sid1 = ss._generate_session_id(tmp_path, _participants())
    (tmp_path / sid1).mkdir()
    sid2 = ss._generate_session_id(tmp_path, _participants())
    assert sid2 == sid1.replace("_001", "_002")


def test_generate_session_id_solo_participant_uses_solo_beta(tmp_path):
    solo = [ss.Participant(creature="Hearth", machine_id="x",
                           machine_alias="", pairing_id="", role="discussant")]
    sid = ss._generate_session_id(tmp_path, solo)
    assert "_hearth_solo_" in sid


# ---------------------------------------------------------------------------
# File writers produce nested / frontmatter shapes
# ---------------------------------------------------------------------------


def _build_meta():
    host = ss.Participant("Primo", "34d41615-0001", "mac-studio-001", "", "discussant")
    parts = _participants()
    return ss.SessionMeta(
        session_id="reach_2026-04-24_hearth_primo_001",
        field="Council",
        field_host=host,
        participants=parts,
        max_idle_seconds=1800,
        max_turns=10,
        created_at="2026-04-24T10:00:00Z",
    )


def test_write_meta_yaml_includes_note_and_smoke_when_provided(tmp_path):
    session_dir = tmp_path / "sessions" / "reach_2026-04-24_hearth_primo_001"
    session_dir.mkdir(parents=True)
    meta_path = ss._write_meta_yaml(
        session_dir, _build_meta(), note="provenance here", smoke=True,
    )
    content = meta_path.read_text(encoding="utf-8")
    assert "field: Council" in content
    assert "participants:" in content
    assert "note:" in content
    assert "provenance here" in content
    assert "smoke: true" in content


def test_write_meta_yaml_omits_note_when_empty(tmp_path):
    session_dir = tmp_path / "sessions" / "reach_2026-04-24_hearth_primo_001"
    session_dir.mkdir(parents=True)
    meta_path = ss._write_meta_yaml(
        session_dir, _build_meta(), note="", smoke=False,
    )
    content = meta_path.read_text(encoding="utf-8")
    assert "note:" not in content
    assert "smoke:" not in content


def test_write_turn_yaml_produces_nested_next_block(tmp_path):
    session_dir = tmp_path / "sessions" / "reach_2026-04-24_hearth_primo_001"
    session_dir.mkdir(parents=True)
    pointer = ss.TurnPointer(
        turn=1,
        next_creature="Hearth",
        next_machine_id="92520f1d-0001",
        next_machine_alias="win-nautilus-001",
        prompt_available=True,
        updated_at="2026-04-24T10:00:00Z",
    )
    turn_path = ss._write_turn_yaml(session_dir, pointer)
    content = turn_path.read_text(encoding="utf-8")
    assert content.startswith("turn: 1")
    assert "next:\n" in content
    assert "  creature: Hearth" in content
    assert "  machine_id: 92520f1d-0001" in content
    assert "  machine_alias: win-nautilus-001" in content
    assert "prompt_available: true" in content
    # Flat-form keys MUST NOT appear (Phase 2b.0 TurnPointer bug regression).
    assert "next_creature" not in content
    assert "next_machine_id" not in content


def test_write_first_prompt_writes_frontmatter_plus_body(tmp_path):
    session_dir = tmp_path / "sessions" / "reach_2026-04-24_hearth_primo_001"
    (session_dir / "prompts").mkdir(parents=True)
    first_actor = ss.Participant(
        "Hearth", "92520f1d-0001", "win-nautilus-001", "sym-01", "discussant"
    )
    body = "Council convenes. Hearth: open the round."
    path = ss._write_first_prompt(
        session_dir,
        "reach_2026-04-24_hearth_primo_001",
        first_actor,
        body,
    )
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "session_id: reach_2026-04-24_hearth_primo_001" in content
    assert "turn: 1" in content
    assert "creature: Hearth" in content
    assert body in content


# ---------------------------------------------------------------------------
# main() end-to-end with --no-commit --no-push
# ---------------------------------------------------------------------------


def test_main_no_commit_no_push_writes_session_layout(tmp_path):
    # Fake an empty repo root; main must tolerate the absence of a
    # git checkout because --no-commit is set.
    repo_root = tmp_path
    (repo_root / "sessions").mkdir()
    prompt_file = repo_root / "opening.md"
    prompt_file.write_text("Council convenes.", encoding="utf-8")

    rc = ss.main([
        "--repo-root", str(repo_root),
        "--field", "Council",
        "--field-host",
        "Primo@34d41615-0001:mac-studio-001:",
        "--participant",
        "Hearth@92520f1d-0001:win-nautilus-001:sym-01",
        "--participant",
        "Primo@34d41615-0001:mac-studio-001:sym-02",
        "--first-actor", "Hearth",
        "--prompt-file", str(prompt_file),
        "--max-turns", "5",
        "--max-idle-seconds", "900",
        "--smoke",
        "--note", "unit test",
        "--no-commit",
        "--no-push",
    ])
    assert rc == 0

    # Find the auto-generated session dir.
    session_dirs = list((repo_root / "sessions").iterdir())
    assert len(session_dirs) == 1
    sdir = session_dirs[0]
    assert sdir.is_dir()

    meta = (sdir / "meta.yaml").read_text(encoding="utf-8")
    turn = (sdir / "turn.yaml").read_text(encoding="utf-8")
    prompt = (sdir / "prompts" / "001.md").read_text(encoding="utf-8")

    assert "field: Council" in meta
    assert "smoke: true" in meta
    assert "unit test" in meta

    assert "next:\n" in turn
    assert "  creature: Hearth" in turn

    assert "turn: 1" in prompt
    assert "Council convenes." in prompt


def test_main_rejects_first_actor_not_in_participants(tmp_path):
    repo_root = tmp_path
    (repo_root / "sessions").mkdir()
    prompt_file = repo_root / "opening.md"
    prompt_file.write_text("x", encoding="utf-8")
    rc = ss.main([
        "--repo-root", str(repo_root),
        "--field", "Council",
        "--field-host",
        "Primo@34d41615-0001:mac-studio-001:",
        "--participant",
        "Hearth@92520f1d-0001:win-nautilus-001:",
        "--first-actor", "Ghost",  # not in participants
        "--prompt-file", str(prompt_file),
        "--no-commit",
        "--no-push",
    ])
    assert rc == 2


def test_main_rejects_empty_prompt(tmp_path):
    repo_root = tmp_path
    (repo_root / "sessions").mkdir()
    prompt_file = repo_root / "empty.md"
    prompt_file.write_text("", encoding="utf-8")
    rc = ss.main([
        "--repo-root", str(repo_root),
        "--field", "Council",
        "--field-host",
        "Primo@34d41615-0001:mac-studio-001:",
        "--participant",
        "Hearth@92520f1d-0001:win-nautilus-001:",
        "--prompt-file", str(prompt_file),
        "--no-commit",
        "--no-push",
    ])
    assert rc == 2
