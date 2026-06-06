"""Tests for emit_lxm_match_experience — joint spec §A.4."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ludex.core.organism_config import OrganismConfig
from ludex.core.trace import (
    KIND_LXM_MATCH_EXPERIENCE,
    emit_lxm_match_experience,
)


def _build_minimal_creature(tmp: Path, name: str = "Test"):
    """Spin up a throwaway creature with memory organ enabled."""
    habitat = tmp / name
    habitat.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "brain": {"provider": "claude_cli", "model": "haiku"},
        "organs": {
            "engine": {"enabled": True, "required": True,
                       "system_prompt": f"I am {name}.",
                       "max_turns": 10, "token_budget": 1000},
            "memory": {"enabled": True, "auto_capture": False},
            "tracking": {"enabled": True},
        },
        "habitat": {"mode": "local", "home_dir": str(habitat),
                    "persistent": True},
        "born_at": 1000.0,
        "session_count": 1,
    }
    (habitat / "ludex.json").write_text(json.dumps(data), encoding="utf-8")
    cfg = OrganismConfig.load(str(habitat))
    return cfg.build(), habitat


def test_emit_writes_span_and_memory():
    """A successful emit produces (a) a memory entry tagged lxm+match_id+
    distilled and (b) a store span of kind lxm.match_experience."""
    with tempfile.TemporaryDirectory() as d:
        org, habitat = _build_minimal_creature(Path(d))
        match_id = "m2_primo_vs_spark_01"

        mem_id = emit_lxm_match_experience(
            organism=org,
            match_id=match_id,
            summary="I cooperated each round; opponent mirrored; we reached steady trust by round 4.",
            moves_count=10,
            outcome="win",
            meta={"opponent": "Spark", "game": "trust", "condition": "A"},
        )

        assert mem_id is not None, "expected a memory id to be returned"
        assert mem_id.startswith("mem_"), f"unexpected id format: {mem_id}"

        # Memory entry exists and carries expected tags + type
        mem_block = org.get_block("memory")
        assert mem_block is not None
        mem = mem_block._memories[mem_id]
        assert mem.memory_type == "semantic"
        assert "lxm" in mem.tags
        assert match_id in mem.tags
        assert "distilled" in mem.tags
        assert match_id in mem.content
        assert "win" in mem.content
        assert mem.importance >= 0.7

        # Store span appended with kind=lxm.match_experience
        spans_file = habitat / "store" / "spans.jsonl"
        assert spans_file.exists(), "expected spans.jsonl to exist"
        lines = [json.loads(line) for line in spans_file.read_text().splitlines() if line.strip()]
        match_spans = [s for s in lines if s.get("kind") == KIND_LXM_MATCH_EXPERIENCE]
        assert len(match_spans) == 1
        span = match_spans[0]
        assert span["attributes"]["match_id"] == match_id
        assert span["attributes"]["outcome"] == "win"
        assert span["attributes"]["moves_count"] == 10
        assert span["attributes"]["meta"]["opponent"] == "Spark"


def test_emit_returns_none_when_no_memory_block():
    """If the organism has no memory organ, emit still writes the span
    but returns None (graceful degradation — adapter should not crash
    against memory-less test doubles)."""

    class StubOrganism:
        name = "Stub"
        config = None  # _store_for returns None → span also no-op

        def get_block(self, _name):
            return None

    result = emit_lxm_match_experience(
        organism=StubOrganism(),
        match_id="m_stub",
        summary="stub run",
        moves_count=0,
        outcome="draw",
    )
    assert result is None


def test_summary_truncation_at_400():
    """Long summaries are truncated on the span attribute but stored
    in full in the memory entry (the full content is the creature's,
    and the span is for provenance/indexing)."""
    with tempfile.TemporaryDirectory() as d:
        org, habitat = _build_minimal_creature(Path(d))
        long_summary = "x" * 1000
        mem_id = emit_lxm_match_experience(
            organism=org,
            match_id="m_long",
            summary=long_summary,
            moves_count=5,
            outcome="draw",
        )
        assert mem_id is not None

        # Span attribute: truncated
        spans_file = habitat / "store" / "spans.jsonl"
        spans = [json.loads(l) for l in spans_file.read_text().splitlines() if l.strip()]
        span = [s for s in spans if s.get("kind") == KIND_LXM_MATCH_EXPERIENCE][0]
        assert len(span["attributes"]["summary"]) == 400

        # Memory content: full summary retained (wrapped with header)
        mem = org.get_block("memory")._memories[mem_id]
        assert long_summary in mem.content
