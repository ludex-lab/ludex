"""FC-wiring tool-support probe cache regression tests.

The original incident (2026-08-24 02:03): OllamaAdapter.supports_tools()
is a real /api/chat call, so every organism build loaded the entire model
into RAM to re-learn a constant. Retired Moss's 9.6GB gemma4 was loaded
at each heartbeat for a month; the night it met 1.9GB free / 0 swap, the
load collapsed the habitat's GUI session. The verdict is now cached on
the config keyed by brain identity ("<provider>:<model>"), persisted to
disk, and invalidated only by a brain change.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ludex.core.organism_config import OrganismConfig


def _write_ollama_creature(tmp: Path, name: str = "Tc",
                           model: str = "gemma-test") -> Path:
    home = tmp / name
    (home / "memory").mkdir(parents=True)
    (home / "bonds").mkdir(parents=True)
    data = {
        "name": name,
        "brain": {"provider": "ollama", "model": model},
        # Seed the D-072 capability cache so build() does not fire the
        # (network) capability probe — this test isolates the FC probe.
        "brain_capabilities": ["narrative"],
        "capability_probed_brain": f"ollama:{model}",
        "capability_probed_at": 1.0,
        "organs": {
            "engine": {"enabled": True, "required": True,
                       "max_turns": 200, "token_budget": 100000},
            "memory": {"enabled": True},
        },
        "habitat": {"mode": "local", "home_dir": str(home),
                    "persistent": True, "origin": ""},
        "born_at": 1000.0,
        "session_count": 1,
    }
    # save() writes ludex.yaml whenever PyYAML is present, so the
    # config starts as yaml too — one file, before and after.
    (home / "ludex.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return home


def _count_probes(monkeypatch):
    calls: list[str] = []
    from ludex.blocks.adapters.ollama import OllamaAdapter

    def _fake_probe(self, model, num_ctx=None):
        # num_ctx joined the signature 2026-08-26: the probe builds its own
        # request body and was still loading models at the server's default
        # context. A fake that does not accept it raises TypeError into the
        # wiring's broad except and this counter silently stays empty.
        calls.append(model)
        return False

    monkeypatch.setattr(OllamaAdapter, "supports_tools", _fake_probe)
    return calls


def test_supports_tools_probed_once_then_cached(tmp_path, monkeypatch):
    calls = _count_probes(monkeypatch)
    home = _write_ollama_creature(tmp_path)

    OrganismConfig.load(str(home)).build()
    assert calls == ["gemma-test"]

    # Verdict persisted to disk alongside the config.
    saved = yaml.safe_load((home / "ludex.yaml").read_text(encoding="utf-8"))
    assert saved["fc_probed_brain"] == "ollama:gemma-test"
    assert saved["fc_supports_tools"] is False

    # A fresh load + build must use the cache — no second model load.
    OrganismConfig.load(str(home)).build()
    assert calls == ["gemma-test"]


def test_brain_change_invalidates_fc_cache(tmp_path, monkeypatch):
    calls = _count_probes(monkeypatch)
    home = _write_ollama_creature(tmp_path)

    OrganismConfig.load(str(home)).build()
    assert calls == ["gemma-test"]

    # Re-brain: same creature, different model — stale verdict must not
    # be trusted.
    data = yaml.safe_load((home / "ludex.yaml").read_text(encoding="utf-8"))
    data["brain"]["model"] = "other-model"
    data["capability_probed_brain"] = "ollama:other-model"
    (home / "ludex.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    OrganismConfig.load(str(home)).build()
    assert calls == ["gemma-test", "other-model"]
