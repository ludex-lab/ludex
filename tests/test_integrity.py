"""Ecosystem-integrity tests (D-090, layer 1: ephemeral load).

The invariant under test is the one today's incident violated: tooling that
loads a creature must not be able to mutate the LIVE creature. We mock
OrganismConfig.load so no real creature is needed, and have the fake loader
*write into* whatever path it is handed — then assert the contamination landed
in the throwaway copy, never the source, and the copy was cleaned up.
"""
from __future__ import annotations

import os

from ludex.core.organism_config import OrganismConfig
from ludex.core.integrity import ephemeral_creature


def _make_creature(root):
    os.makedirs(os.path.join(root, "store"))
    with open(os.path.join(root, "store", "spans.jsonl"), "w") as f:
        f.write("original\n")
    os.makedirs(os.path.join(root, "snapshots"))
    with open(os.path.join(root, "snapshots", "big.json"), "w") as f:
        f.write("x" * 1000)


def test_ephemeral_isolates_writes_and_cleans_up(tmp_path, monkeypatch):
    src = tmp_path / "Pulsar"
    _make_creature(str(src))
    seen = {}

    def fake_load(path):
        seen["path"] = path
        # simulate play writing into the loaded creature
        with open(os.path.join(path, "store", "spans.jsonl"), "a") as f:
            f.write("CONTAMINATION\n")
        return f"cfg@{path}"

    monkeypatch.setattr(OrganismConfig, "load", staticmethod(fake_load))

    with ephemeral_creature(str(src)) as cfg:
        assert cfg == f"cfg@{seen['path']}"
        assert seen["path"] != str(src)                      # loaded from a COPY
        assert os.path.exists(seen["path"])                  # copy live during the block
        assert not os.path.exists(os.path.join(seen["path"], "snapshots"))  # snapshots excluded

    # the live creature is byte-for-byte untouched; the copy is gone
    assert (src / "store" / "spans.jsonl").read_text() == "original\n"
    assert not os.path.exists(seen["path"])


def test_ephemeral_can_keep_snapshots(tmp_path, monkeypatch):
    src = tmp_path / "Pulsar"
    _make_creature(str(src))
    monkeypatch.setattr(OrganismConfig, "load", staticmethod(lambda path: path))
    with ephemeral_creature(str(src), keep_snapshots=True) as copy_path:
        assert os.path.exists(os.path.join(copy_path, "snapshots", "big.json"))
