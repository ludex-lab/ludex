"""Tests for tools/validate_creature_data.py (structural data validator)."""

import json
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from validate_creature_data import validate_creature, main


def _write_creature(parent: Path, name: str = "Tc",
                    substrate_status: str | None = None) -> Path:
    habitat = parent / name
    (habitat / "store").mkdir(parents=True)
    (habitat / "snapshots" / "2026-06-11-test").mkdir(parents=True)
    brain = {"provider": "ollama", "model": "llama3.1:8b"}
    if substrate_status:
        brain["substrate_status"] = substrate_status
    (habitat / "ludex.yaml").write_text(yaml.safe_dump(
        {"name": name, "brain": brain}, sort_keys=False))
    (habitat / "store" / "spans.jsonl").write_text(json.dumps({
        "kind": "tick", "creature": name,
        "timestamp": time.time(), "attributes": {},
    }) + "\n")
    (habitat / "snapshots" / "2026-06-11-test" / "snapshot.json").write_text(
        json.dumps({"reason": "test"}))
    return habitat


def test_clean_creature_passes(tmp_path):
    habitat = _write_creature(tmp_path, substrate_status="wind-down")
    errors, stats = validate_creature(habitat)
    assert errors == []
    assert stats == {"spans": 1, "snapshots": 1}


def test_catches_bad_span_line_and_missing_fields(tmp_path):
    habitat = _write_creature(tmp_path)
    spans = habitat / "store" / "spans.jsonl"
    spans.write_text(spans.read_text()
                     + "not json\n"
                     + json.dumps({"kind": "tick"}) + "\n")
    errors, _ = validate_creature(habitat)
    assert any("not JSON" in e for e in errors)
    assert any("missing 'creature'" in e for e in errors)


def test_catches_unknown_substrate_status(tmp_path):
    habitat = _write_creature(tmp_path, substrate_status="zombie")
    errors, _ = validate_creature(habitat)
    assert any("unknown substrate_status" in e for e in errors)


def test_catches_missing_yaml_and_snapshot_meta(tmp_path):
    habitat = _write_creature(tmp_path)
    (habitat / "ludex.yaml").unlink()
    (habitat / "snapshots" / "2026-06-11-test" / "snapshot.json").unlink()
    errors, _ = validate_creature(habitat)
    assert any("no ludex.yaml" in e for e in errors)
    assert any("no snapshot.json" in e for e in errors)


def test_main_exit_codes(tmp_path, capsys):
    _write_creature(tmp_path, name="Good")
    assert main(["--root", str(tmp_path)]) == 0
    bad = _write_creature(tmp_path, name="Bad")
    (bad / "ludex.yaml").unlink()
    assert main(["--root", str(tmp_path)]) == 1
    assert "ERRORS" in capsys.readouterr().out
