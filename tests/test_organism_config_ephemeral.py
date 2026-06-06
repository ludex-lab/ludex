"""OrganismConfig _ephemeral guard — MCP server config-corruption fix.

Cody (Mac) hit this 2026-05-11: the MCP server mutates `config.brain`
in-memory to ollama:none for MCP-only mode (no --enable-engine). Then
config.build() bumps session_count and calls self.save() — persisting
the in-memory mutation to disk. Verse's on-disk ludex.yaml got
rewritten; six empty turns landed in a downstream OpenCouncil session
before Cody noticed.

Fix: OrganismConfig grows an `_ephemeral` flag. save() refuses to
write when True. The MCP server sets the flag BEFORE the mutation.
Both the session-count save (build line ~394) and the D-072 probe save
(build line ~591) go through save() and are protected.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ludex.core.organism_config import OrganismConfig
from ludex.core.habitat import HabitatConfig


def _baseline_yaml(tmp_path: Path) -> Path:
    """Write a minimal ludex.yaml that round-trips through load()."""
    cdir = tmp_path / "TestCreature"
    cdir.mkdir()
    (cdir / "ludex.yaml").write_text(
        "name: TestCreature\n"
        "brain:\n"
        "  provider: claude_cli\n"
        "  model: claude-sonnet-4-6\n"
        "  class: hybrid\n"
        "organs:\n"
        "  engine:\n"
        "    enabled: true\n"
        "    required: true\n"
        "habitat:\n"
        "  origin: test\n"
        f"  home_dir: {str(cdir).replace(chr(92), '/')}\n"
        "  persistent: true\n",
        encoding="utf-8",
    )
    return cdir


def test_save_skips_when_ephemeral(tmp_path):
    cdir = _baseline_yaml(tmp_path)
    cfg = OrganismConfig.load(str(cdir))
    original = (cdir / "ludex.yaml").read_text(encoding="utf-8")

    # Ephemeral mutation pattern (the MCP-server case)
    cfg._ephemeral = True
    cfg.brain = {"provider": "ollama", "model": "none"}
    cfg.organs["engine"]["enabled"] = False

    result = cfg.save()
    assert result is None, "ephemeral save() should return None (no write)"

    after = (cdir / "ludex.yaml").read_text(encoding="utf-8")
    assert after == original, "on-disk ludex.yaml must be unchanged"


def test_save_writes_when_not_ephemeral(tmp_path):
    """Default behavior unchanged: legitimate saves still persist."""
    cdir = _baseline_yaml(tmp_path)
    cfg = OrganismConfig.load(str(cdir))
    cfg.brain["model"] = "claude-haiku-4-5"  # legitimate edit
    out = cfg.save()
    assert out is not None
    after = (cdir / "ludex.yaml").read_text(encoding="utf-8")
    assert "claude-haiku-4-5" in after


def test_ephemeral_flag_does_not_serialize(tmp_path):
    """_ephemeral is process-only state — never written to disk."""
    cdir = _baseline_yaml(tmp_path)
    cfg = OrganismConfig.load(str(cdir))
    cfg._ephemeral = True
    d = cfg.to_dict()
    assert "_ephemeral" not in d


def test_load_does_not_inherit_ephemeral(tmp_path):
    """Fresh load() always produces a non-ephemeral config."""
    cdir = _baseline_yaml(tmp_path)
    cfg = OrganismConfig.load(str(cdir))
    assert cfg._ephemeral is False


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        test_save_skips_when_ephemeral(Path(td))
    print("  [PASS] save skipped when _ephemeral")
    with tempfile.TemporaryDirectory() as td:
        test_save_writes_when_not_ephemeral(Path(td))
    print("  [PASS] save writes when not ephemeral")
    with tempfile.TemporaryDirectory() as td:
        test_ephemeral_flag_does_not_serialize(Path(td))
    print("  [PASS] _ephemeral does not serialize")
    with tempfile.TemporaryDirectory() as td:
        test_load_does_not_inherit_ephemeral(Path(td))
    print("  [PASS] load does not inherit _ephemeral")
