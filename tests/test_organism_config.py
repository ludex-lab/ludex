"""Regression tests for OrganismConfig isolation.

The original bug: loading two configs in the same process caused the second
load's values to bleed into the first, because `from_dict` did a shallow
copy of DEFAULT_ORGANS.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ludex.core.organism_config import OrganismConfig, DEFAULT_ORGANS


def _write_config(tmp: Path, name: str, system_prompt: str, provider: str = "claude_cli") -> Path:
    habitat = tmp / name
    habitat.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "brain": {"provider": provider, "model": "haiku"},
        "organs": {
            "engine": {
                "enabled": True,
                "required": True,
                "system_prompt": system_prompt,
                "max_turns": 200,
                "token_budget": 100000,
            },
        },
        "habitat": {"mode": "local", "home_dir": str(habitat), "persistent": True},
        "born_at": 1000.0,
        "session_count": 1,
    }
    (habitat / "ludex.json").write_text(json.dumps(data), encoding="utf-8")
    return habitat


def test_two_loads_do_not_bleed():
    """Loading two configs must not cross-contaminate system_prompt."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _write_config(tmp, "Primo", "I am Primo, a Haiku creature.")
        _write_config(tmp, "Spark", "I am Spark, a Flash creature.")

        primo = OrganismConfig.load(str(tmp / "Primo"))
        spark = OrganismConfig.load(str(tmp / "Spark"))

        assert primo.organs["engine"]["system_prompt"] == "I am Primo, a Haiku creature."
        assert spark.organs["engine"]["system_prompt"] == "I am Spark, a Flash creature."


def test_load_does_not_mutate_defaults():
    """DEFAULT_ORGANS must remain pristine after any number of loads."""
    original = DEFAULT_ORGANS["engine"]["system_prompt"]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _write_config(tmp, "A", "custom prompt A")
        _write_config(tmp, "B", "custom prompt B")
        OrganismConfig.load(str(tmp / "A"))
        OrganismConfig.load(str(tmp / "B"))
    assert DEFAULT_ORGANS["engine"]["system_prompt"] == original


def test_organ_mutation_does_not_affect_other_instance():
    """Mutating one loaded config's organ must not affect another."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _write_config(tmp, "A", "prompt A")
        _write_config(tmp, "B", "prompt B")

        a = OrganismConfig.load(str(tmp / "A"))
        b = OrganismConfig.load(str(tmp / "B"))

        a.organs["engine"]["system_prompt"] = "mutated A"
        assert b.organs["engine"]["system_prompt"] == "prompt B"
        assert DEFAULT_ORGANS["engine"]["system_prompt"] == ""


def test_load_resolves_home_dir_to_config_parent():
    """home_dir in memory must be the absolute path of the config's
    parent, regardless of the caller's cwd. Prevents the LxM-adapter
    failure mode where a relative "./creatures/Primo" resolved against
    the caller's cwd, creating a bogus creature dir."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d).resolve()
        habitat = _write_config(tmp, "Primo", "I am Primo.")
        # Rewrite the config with a relative (and ambiguous) home_dir
        # to mimic committed config files like `"creatures/Primo"`.
        data = json.loads((habitat / "ludex.json").read_text())
        data["habitat"]["home_dir"] = "./creatures/Primo"
        (habitat / "ludex.json").write_text(json.dumps(data))

        cwd_before = os.getcwd()
        try:
            os.chdir("/tmp")  # unrelated cwd — must not influence result
            cfg = OrganismConfig.load(str(habitat))
            assert Path(cfg.habitat.home_dir).is_absolute()
            assert Path(cfg.habitat.home_dir).resolve() == habitat.resolve()
        finally:
            os.chdir(cwd_before)


def test_save_writes_portable_dot_home_dir():
    """save() should write home_dir as "." when it matches the save
    directory, so the committed config stays cwd-independent."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d).resolve()
        habitat = _write_config(tmp, "Primo", "I am Primo.")
        cfg = OrganismConfig.load(str(habitat))
        # save() returns the actual file it wrote — ludex.yaml when
        # PyYAML is available (the default), ludex.json otherwise.
        # Read that file directly so the assertion isn't fooled by
        # the original ludex.json fixture left over from _write_config.
        saved_path = Path(cfg.save(str(habitat)))
        if saved_path.suffix == ".yaml":
            import yaml
            saved = yaml.safe_load(saved_path.read_text())
        else:
            saved = json.loads(saved_path.read_text())
        assert saved["habitat"]["home_dir"] == "."
