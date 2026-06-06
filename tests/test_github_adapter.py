"""Tests for `ludex.mcp.github_adapter` — D-062 Phase 2b.1.

Covers `GitHubSessionClient` construction + error-path behaviour that
does not touch git. Schema dataclasses + YAML / frontmatter helpers
+ `machine_slug` are exercised in `test_schema_io.py` now; this file
only keeps coverage of the MCP-surface client wrapper.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ludex.mcp.github_adapter import GitHubSessionClient
from ludex.mcp.local_adapter import TOOL_ENGINE_SUBMIT, ToolResult
from ludex.reach.schema_io import Participant, SessionMeta, TurnPointer


# ---------------------------------------------------------------------------
# GitHubSessionClient construction + error paths
# (schema helpers moved to test_schema_io.py in Phase 2b.1 refactor.)
# ---------------------------------------------------------------------------


def test_github_session_client_raises_when_session_dir_missing(tmp_path):
    # Repo root exists but no sessions/<id>/ inside it.
    with pytest.raises(FileNotFoundError):
        GitHubSessionClient(
            repo_root=tmp_path,
            session_id="reach_2026-04-24_absent_001",
            peer_creature="Primo",
            peer_machine_id="34d41615-0001",
        )


def test_github_session_client_call_tool_rejects_unsupported_tool(tmp_path):
    # Prepare minimal session dir so constructor succeeds.
    session_dir = tmp_path / "sessions" / "reach_2026-04-24_hearth_primo_001"
    session_dir.mkdir(parents=True)
    client = GitHubSessionClient(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        peer_creature="Primo",
        peer_machine_id="34d41615-0001",
    )
    result = client.call_tool("bogus_tool", {"prompt": "hi"})
    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "unsupported tool" in result.error


def test_github_session_client_call_tool_rejects_empty_prompt(tmp_path):
    session_dir = tmp_path / "sessions" / "reach_2026-04-24_hearth_primo_001"
    session_dir.mkdir(parents=True)
    client = GitHubSessionClient(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        peer_creature="Primo",
        peer_machine_id="34d41615-0001",
    )
    result = client.call_tool(TOOL_ENGINE_SUBMIT, {"prompt": ""})
    assert result.is_error
    assert "empty prompt" in result.error


def test_github_session_client_close_twice_is_safe(tmp_path, monkeypatch):
    # close() shells out to git; for this test we just want to prove
    # that a second close() is a no-op after the first one runs its
    # post-close branch and flips _closed = True.
    session_dir = tmp_path / "sessions" / "reach_2026-04-24_hearth_primo_001"
    session_dir.mkdir(parents=True)
    client = GitHubSessionClient(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        peer_creature="Primo",
        peer_machine_id="34d41615-0001",
    )
    calls = []
    monkeypatch.setattr(client, "_publish_close", lambda **kw: calls.append(kw))
    monkeypatch.setattr(client, "_emit_retracted_span", lambda: calls.append("retracted"))
    client.close()
    client.close()
    # First close: publish_close + retracted_span. Second: no more calls.
    assert calls == [{"reason": "explicit_retract"}, "retracted"]


def test_github_session_client_organism_name_defaults_for_mcp_protocol(tmp_path):
    """MCPClient Protocol requires `organism_name` on the instance."""
    session_dir = tmp_path / "sessions" / "reach_2026-04-24_hearth_primo_001"
    session_dir.mkdir(parents=True)
    client = GitHubSessionClient(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        peer_creature="Primo",
        peer_machine_id="34d41615-0001",
    )
    assert client.organism_name == "reach_client"  # default
    client2 = GitHubSessionClient(
        repo_root=tmp_path,
        session_id="reach_2026-04-24_hearth_primo_001",
        peer_creature="Primo",
        peer_machine_id="34d41615-0001",
        local_observer_name="field_host_primo",
    )
    assert client2.organism_name == "field_host_primo"


# ---------------------------------------------------------------------------
# SessionMeta.to_yaml_dict — flattens nested Participants correctly
# ---------------------------------------------------------------------------


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
