"""Tests for `ludex.reach.schema_io` — D-062 Phase 2b.1.

Covers the shared schema-I/O surface that replaced the hand-rolled
helpers previously inside `ludex.mcp.github_adapter` and
`ludex.reach.reach_orchestrator`. Two halves of the reach session
(field-host client, peer orchestrator) both call these functions, so
the assertions here are the load-bearing regression gates for
cross-half compatibility.

Organised by section:
  1. `machine_slug` — the alias-preferred rule agreed with LxM Cody.
  2. Dataclass serialization (TurnPointer / SessionMeta / TurnEnvelope).
  3. PyYAML-backed YAML + frontmatter helpers.
  4. Session-shape operations (read/write turn pointer, prompt, response,
     close, is_session_closed).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ludex.reach.schema_io import (
    CloseEnvelope,
    Participant,
    SessionMeta,
    TurnEnvelope,
    TurnPointer,
    dump_yaml_text,
    is_session_closed,
    load_yaml,
    machine_slug,
    parse_frontmatter_md,
    prompt_digest,
    read_prompt_body,
    read_turn_pointer,
    render_frontmatter_md,
    utcnow_iso,
    write_close,
    write_prompt,
    write_response,
    write_turn_pointer,
    write_yaml,
)


# ---------------------------------------------------------------------------
# 1. machine_slug
# ---------------------------------------------------------------------------


def test_machine_slug_prefers_alias_when_non_empty():
    assert machine_slug("win-nautilus-001", "34d41615-0000-0000") == "win-nautilus-001"


def test_machine_slug_falls_back_to_first_eight_hex_of_id():
    assert machine_slug("", "34d41615-1642-4094-be71-05024185149d") == "34d41615"


def test_machine_slug_collapses_short_id():
    assert machine_slug(None, "abc-def") == "abcdef"


def test_machine_slug_unknown_when_both_empty():
    assert machine_slug("", "") == "unknown"
    assert machine_slug(None, None) == "unknown"


def test_machine_slug_whitespace_alias_treated_as_empty():
    assert machine_slug("   ", "34d41615-xxxx") == "34d41615"


def test_machine_slug_strips_alias_whitespace_when_used():
    assert machine_slug("  mac-studio-001  ", "") == "mac-studio-001"


# ---------------------------------------------------------------------------
# 2. Dataclass serialization
# ---------------------------------------------------------------------------


def test_turn_pointer_to_yaml_dict_nests_next_block():
    p = TurnPointer(
        turn=3,
        next_creature="Primo",
        next_machine_id="34d41615-0001",
        next_machine_alias="mac-studio-001",
        prompt_available=True,
        updated_at="2026-04-24T10:00:00Z",
    )
    d = p.to_yaml_dict()
    assert d["turn"] == 3
    assert d["next"] == {
        "creature": "Primo",
        "machine_id": "34d41615-0001",
        "machine_alias": "mac-studio-001",
    }
    assert d["prompt_available"] is True
    for flat_key in ("next_creature", "next_machine_id", "next_machine_alias"):
        assert flat_key not in d


def test_turn_pointer_from_yaml_dict_parses_nested_shape():
    data = {
        "turn": 4,
        "next": {
            "creature": "Hearth",
            "machine_id": "92520f1d",
            "machine_alias": "win-nautilus-001",
        },
        "prompt_available": True,
        "updated_at": "2026-04-24T11:00:00Z",
    }
    p = TurnPointer.from_yaml_dict(data)
    assert p.turn == 4
    assert p.next_creature == "Hearth"
    assert p.next_machine_id == "92520f1d"
    assert p.next_machine_alias == "win-nautilus-001"
    assert p.prompt_available is True


def test_turn_pointer_from_yaml_dict_tolerates_flat_legacy_keys():
    """Protects against a mid-migration file produced before the
    TurnPointer nesting bug was fixed — we can still parse it."""
    data = {
        "turn": 2,
        "next_creature": "Hearth",
        "next_machine_id": "92520f1d",
        "prompt_available": False,
    }
    p = TurnPointer.from_yaml_dict(data)
    assert p.next_creature == "Hearth"
    assert p.next_machine_id == "92520f1d"


def test_turn_pointer_from_yaml_dict_rejects_non_mapping():
    with pytest.raises(TypeError, match="turn.yaml root must be a mapping"):
        TurnPointer.from_yaml_dict(["not", "a", "dict"])


def test_session_meta_to_yaml_dict_serializes_participants_as_list_of_dicts():
    meta = SessionMeta(
        session_id="reach_2026-04-24_hearth_primo_001",
        field="Council",
        field_host=Participant(
            creature="Primo", machine_id="34d41615-0001",
            machine_alias="mac-studio-001",
        ),
        participants=[
            Participant(
                creature="Hearth", machine_id="92520f1d-0001",
                machine_alias="win-nautilus-001", pairing_id="sym-01",
            ),
            Participant(
                creature="Primo", machine_id="34d41615-0001",
                machine_alias="mac-studio-001", pairing_id="sym-02",
            ),
        ],
        created_at="2026-04-24T10:00:00Z",
    )
    d = meta.to_yaml_dict()
    assert d["session_id"] == "reach_2026-04-24_hearth_primo_001"
    assert d["field"] == "Council"
    assert isinstance(d["field_host"], dict)
    assert d["field_host"]["creature"] == "Primo"
    assert isinstance(d["participants"], list)
    assert d["participants"][0]["creature"] == "Hearth"
    assert d["participants"][1]["pairing_id"] == "sym-02"


def test_turn_envelope_from_frontmatter_preserves_extras():
    fm = {
        "turn": 2,
        "creature": "Anvil",
        "machine_id": "x",
        "machine_alias": "y",
        "session_id": "s",
        "timestamp": "t",
        "custom_field": "keep me",
    }
    env = TurnEnvelope.from_frontmatter(fm, body="body text")
    assert env.turn == 2
    assert env.creature == "Anvil"
    assert env.extras.get("custom_field") == "keep me"
    # Known keys don't leak into extras.
    assert "creature" not in env.extras


# ---------------------------------------------------------------------------
# 3. YAML + frontmatter helpers
# ---------------------------------------------------------------------------


def test_load_yaml_returns_empty_dict_on_missing_file(tmp_path):
    assert load_yaml(tmp_path / "nope.yaml") == {}


def test_load_yaml_returns_empty_dict_on_empty_file(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    assert load_yaml(p) == {}


def test_load_yaml_parses_nested_structures(tmp_path):
    p = tmp_path / "n.yaml"
    p.write_text("a:\n  b: 1\n  c:\n    - x\n    - y\n", encoding="utf-8")
    data = load_yaml(p)
    assert data == {"a": {"b": 1, "c": ["x", "y"]}}


def test_dump_yaml_text_is_round_tripable():
    src = {"next": {"creature": "Primo", "machine_id": "x"}, "turn": 7}
    out = dump_yaml_text(src)
    assert yaml.safe_load(out) == src


def test_write_yaml_and_load_yaml_roundtrip(tmp_path):
    data = {"session_id": "reach_x", "status": "active"}
    path = write_yaml(tmp_path / "meta.yaml", data)
    assert load_yaml(path) == data


def test_parse_frontmatter_md_splits_frontmatter_and_body():
    text = "---\nturn: 1\ncreature: Primo\n---\n\nbody paragraph"
    fm, body = parse_frontmatter_md(text)
    assert fm == {"turn": 1, "creature": "Primo"}
    assert body == "body paragraph"


def test_parse_frontmatter_md_treats_missing_frontmatter_as_body():
    fm, body = parse_frontmatter_md("no frontmatter here")
    assert fm == {}
    assert body == "no frontmatter here"


def test_parse_frontmatter_md_tolerates_missing_closing_marker():
    # "---\nheader" with no closing "---" — returns whole text as body.
    fm, body = parse_frontmatter_md("---\nincomplete")
    assert fm == {}
    # body contains the raw text stripped.
    assert body == "---\nincomplete"


def test_parse_frontmatter_md_tolerates_malformed_yaml():
    # Opening and closing markers present but YAML body is garbage.
    text = "---\n: : : :\n---\n\nbody"
    fm, body = parse_frontmatter_md(text)
    assert fm == {}
    assert body == "body"


def test_render_and_parse_frontmatter_roundtrip_preserves_em_dash():
    fm = {"turn": 1, "creature": "Hearth"}
    body = "Hearth speaks — steady, slow."   # em-dash inside body
    text = render_frontmatter_md(fm, body)
    fm2, body2 = parse_frontmatter_md(text)
    assert fm2 == fm
    assert body2 == body


# ---------------------------------------------------------------------------
# 4. Session-shape operations
# ---------------------------------------------------------------------------


def _session_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sessions" / "reach_2026-04-24_hearth_primo_001"
    d.mkdir(parents=True)
    return d


def test_read_turn_pointer_returns_none_when_missing(tmp_path):
    d = _session_dir(tmp_path)
    assert read_turn_pointer(d) is None


def test_write_and_read_turn_pointer_roundtrip(tmp_path):
    d = _session_dir(tmp_path)
    original = TurnPointer(
        turn=3,
        next_creature="Primo",
        next_machine_id="34d41615",
        next_machine_alias="mac-studio-001",
        prompt_available=True,
        updated_at=utcnow_iso(),
    )
    write_turn_pointer(d, original)
    read = read_turn_pointer(d)
    assert read.turn == 3
    assert read.next_creature == "Primo"
    assert read.next_machine_id == "34d41615"
    assert read.next_machine_alias == "mac-studio-001"
    assert read.prompt_available is True


def test_write_prompt_and_read_prompt_body_roundtrip(tmp_path):
    d = _session_dir(tmp_path)
    addressee = Participant(
        creature="Hearth", machine_id="92520f1d", machine_alias="win-01",
    )
    body = "Council convenes. Hearth — open the round."
    write_prompt(d, turn_n=1, session_id="s", addressee=addressee, prompt_body=body)
    assert read_prompt_body(d, 1) == body


def test_read_prompt_body_raises_when_missing(tmp_path):
    d = _session_dir(tmp_path)
    with pytest.raises(FileNotFoundError):
        read_prompt_body(d, 42)


def test_write_response_uses_alias_in_filename_and_includes_prompt_digest(tmp_path):
    d = _session_dir(tmp_path)
    out = write_response(
        d,
        turn_n=2,
        session_id="reach_x",
        creature="Hearth",
        machine_id="92520f1d",
        machine_alias="win-nautilus-001",
        response_text="Hearth holds the hearth.",
        prompt_body_for_digest="Council opens.",
        reach_span_id="reach_ext_abc",
    )
    assert out.name == "002_Hearth_win-nautilus-001.md"
    fm, body = parse_frontmatter_md(out.read_text(encoding="utf-8"))
    assert fm["creature"] == "Hearth"
    assert fm["pipe_kind"] == "github_session"
    assert fm["reach_span_id"] == "reach_ext_abc"
    assert fm["prompt_digest"].startswith("sha256:")
    assert body == "Hearth holds the hearth."


def test_write_response_falls_back_to_machine_id_slug_when_alias_empty(tmp_path):
    d = _session_dir(tmp_path)
    out = write_response(
        d,
        turn_n=1,
        session_id="s",
        creature="Hearth",
        machine_id="92520f1d-0000-0000",
        machine_alias="",
        response_text="x",
    )
    assert out.name == "001_Hearth_92520f1d.md"


def test_write_close_produces_parseable_close_envelope(tmp_path):
    d = _session_dir(tmp_path)
    out = write_close(
        d,
        session_id="reach_x",
        by_creature="Primo",
        by_machine_id="34d41615",
        by_machine_alias="mac-studio-001",
        reason="explicit_retract",
        turn=5,
        body="Stepping back.",
    )
    assert out.name == "close_Primo_mac-studio-001.md"
    fm, body = parse_frontmatter_md(out.read_text(encoding="utf-8"))
    assert fm["reason"] == "explicit_retract"
    assert fm["turn"] == 5
    assert fm["by_creature"] == "Primo"
    assert body == "Stepping back."


def test_is_session_closed_false_on_active_without_close(tmp_path):
    d = _session_dir(tmp_path)
    (d / "meta.yaml").write_text("status: active\n", encoding="utf-8")
    assert is_session_closed(d) is False


def test_is_session_closed_true_with_close_file(tmp_path):
    d = _session_dir(tmp_path)
    (d / "meta.yaml").write_text("status: active\n", encoding="utf-8")
    (d / "close_Primo_mac.md").write_text("---\nreason: r\n---\n", encoding="utf-8")
    assert is_session_closed(d) is True


def test_is_session_closed_true_when_status_not_active(tmp_path):
    d = _session_dir(tmp_path)
    (d / "meta.yaml").write_text("status: closed\n", encoding="utf-8")
    assert is_session_closed(d) is True


def test_is_session_closed_false_when_meta_absent(tmp_path):
    # No meta.yaml at all — treated as not-yet-closed so the peer
    # waits rather than exiting prematurely.
    d = _session_dir(tmp_path)
    assert is_session_closed(d) is False


# ---------------------------------------------------------------------------
# prompt_digest + utcnow_iso
# ---------------------------------------------------------------------------


def test_prompt_digest_is_stable_sha256_with_prefix():
    d1 = prompt_digest("hello")
    d2 = prompt_digest("hello")
    assert d1 == d2
    assert d1.startswith("sha256:")
    assert len(d1) == len("sha256:") + 64


def test_prompt_digest_differs_for_different_prompts():
    assert prompt_digest("a") != prompt_digest("b")


def test_utcnow_iso_returns_z_suffixed_rfc3339():
    stamp = utcnow_iso()
    # Shape: YYYY-MM-DDTHH:MM:SSZ
    assert len(stamp) == 20
    assert stamp[-1] == "Z"
    assert stamp[4] == "-" and stamp[10] == "T"


# ---------------------------------------------------------------------------
# 5. Phase 2b.1.1 additions — next-prompt body, lock, error classifiers
# ---------------------------------------------------------------------------


from ludex.reach.schema_io import (
    acquire_session_lock,
    compose_next_prompt_body,
    is_engine_error_response,
    is_transient_engine_error,
    release_session_lock,
)


def test_compose_next_prompt_body_blockquotes_peer_utterance():
    out = compose_next_prompt_body(
        field_name="Council",
        peer_creature="Primo",
        peer_machine_alias="mac-studio-001",
        peer_response_body="*Primo speaks first*\n\nThe silence is the texture.",
        peer_turn_n=1,
        addressee_creature="Hearth",
        sentences=4,
    )
    assert "You are in a Council session with Primo" in out
    assert "creature on mac-studio-001" in out
    assert "(turn 1)" in out
    # Peer utterance lines are blockquote-prefixed.
    assert "> *Primo speaks first*" in out
    assert "> The silence is the texture." in out
    # Closing instruction.
    assert "Hearth — your turn" in out
    assert "4 sentences" in out
    # Must NOT use the failed-format header style from R4.P v1.
    assert "Primo (turn 1, mac-studio-001):" not in out


def test_compose_next_prompt_body_preserves_blank_lines_inside_blockquote():
    body = "First paragraph.\n\nSecond paragraph."
    out = compose_next_prompt_body(
        field_name="Forum",
        peer_creature="A",
        peer_machine_alias="m",
        peer_response_body=body,
        peer_turn_n=2,
        addressee_creature="B",
    )
    # Blank line between paragraphs becomes a `>` line, not stripped.
    assert "> First paragraph.\n>\n> Second paragraph." in out


def test_compose_next_prompt_body_falls_back_when_peer_alias_empty():
    out = compose_next_prompt_body(
        field_name="Council",
        peer_creature="Primo",
        peer_machine_alias="",
        peer_response_body="hi",
        peer_turn_n=1,
        addressee_creature="Hearth",
    )
    assert "creature on their habitat" in out


def test_acquire_session_lock_writes_pid_file(tmp_path):
    s = tmp_path / "sessions" / "x"
    s.mkdir(parents=True)
    path = acquire_session_lock(s, creature="Hearth", machine_id="92520f1d-aaaa", pid=12345)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert content.startswith("12345 ")
    assert "Hearth" in content


def test_acquire_session_lock_refuses_when_held_by_live_pid(tmp_path):
    s = tmp_path / "sessions" / "x"
    s.mkdir(parents=True)
    import os
    my_pid = os.getpid()
    # Simulate a held lock by writing the *current* process's pid.
    acquire_session_lock(s, creature="Hearth", machine_id="92520f1d-aaaa", pid=my_pid)
    with pytest.raises(RuntimeError, match="orchestrator already running"):
        acquire_session_lock(
            s, creature="Hearth", machine_id="92520f1d-aaaa", pid=my_pid + 1,
        )


def test_acquire_session_lock_overwrites_stale_lock(tmp_path):
    """A lock file pointing at a dead PID is treated as recoverable —
    the new orchestrator may take it. R4.P v1 left behind locks via
    KeyboardInterrupt + hard kill; this is the recovery path."""
    s = tmp_path / "sessions" / "x"
    s.mkdir(parents=True)
    slug = "92520f1d"
    lock_path = s / f".orchestrator_Hearth_{slug}.lock"
    lock_path.write_text("999999 stale stale\n", encoding="utf-8")  # PID won't be alive
    import os
    new_lock = acquire_session_lock(
        s, creature="Hearth", machine_id="92520f1d-aaaa", pid=os.getpid(),
    )
    assert new_lock.read_text(encoding="utf-8").startswith(f"{os.getpid()} ")


def test_release_session_lock_is_idempotent(tmp_path):
    s = tmp_path / "sessions" / "x"
    s.mkdir(parents=True)
    # Releasing when no lock exists is a no-op, not an error.
    release_session_lock(s, creature="Hearth", machine_id="92520f1d-aaaa")
    # Second call also fine.
    release_session_lock(s, creature="Hearth", machine_id="92520f1d-aaaa")


def test_release_session_lock_removes_existing_lock(tmp_path):
    s = tmp_path / "sessions" / "x"
    s.mkdir(parents=True)
    import os
    path = acquire_session_lock(s, creature="Hearth", machine_id="92520f1d-aaaa", pid=os.getpid())
    assert path.exists()
    release_session_lock(s, creature="Hearth", machine_id="92520f1d-aaaa")
    assert not path.exists()


def test_is_engine_error_response_recognizes_known_prefixes():
    assert is_engine_error_response("[Error: foo]") is True
    assert is_engine_error_response("API Error: 529 Overloaded.") is True
    assert is_engine_error_response("Error: something") is True
    assert is_engine_error_response("") is True   # empty also treated as error
    assert is_engine_error_response("Hearth replies thoughtfully.") is False


def test_is_transient_engine_error_distinguishes_recoverable_failures():
    # Transient (retry):
    assert is_transient_engine_error("API Error: 529 Overloaded.") is True
    assert is_transient_engine_error("API Error: 503 Service Unavailable.") is True
    assert is_transient_engine_error("[Error: rate_limit exceeded]") is True
    assert is_transient_engine_error("[Error: request timeout]") is True
    # Not transient (config / hard error):
    assert is_transient_engine_error(
        '[Error: Claude Code was unable to find CLAUDE_CODE_GIT_BASH_PATH path "D:Gitbinbash.exe"]'
    ) is False
    # Not an error at all:
    assert is_transient_engine_error("Hearth holds the hearth.") is False
