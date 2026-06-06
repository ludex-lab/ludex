"""§G.0.4 N-4 (r8) — bond context field tests.

Verify that update_bond() routes role-play-frame events to an
isolated section without polluting the genuine narrative, preserves
the section across genuine reflections, and restricts reflection
rewrites to genuine events only.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ludex.core.organism_config import OrganismConfig
from ludex.core.selfhood import (
    _ROLEPLAY_SECTION,
    _split_roleplay_section,
    update_bond,
)


def _minimal_creature(tmp: Path, name: str):
    habitat = tmp / name
    habitat.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "brain": {"provider": "claude_cli", "model": "haiku"},
        "organs": {
            "engine": {"enabled": True, "required": True,
                       "system_prompt": f"I am {name}.",
                       "max_turns": 5, "token_budget": 500},
            "memory": {"enabled": True, "auto_capture": False},
            "tracking": {"enabled": True},
        },
        "habitat": {"mode": "local", "home_dir": str(habitat),
                    "persistent": True},
        "born_at": 1000.0, "session_count": 1,
    }
    (habitat / "ludex.json").write_text(json.dumps(data))
    return OrganismConfig.load(str(habitat)).build(), habitat


def test_roleplay_event_isolated_from_genuine_narrative():
    """A game_frame:* context event appends to `## Role-play events`
    and never enters the genuine `## Shared history`."""
    with tempfile.TemporaryDirectory() as d:
        org, habitat = _minimal_creature(Path(d), "T")
        bond_path = habitat / "bonds" / "spark.md"

        update_bond(
            organism=org,
            other_name="Spark",
            shared_experience="In Avalon match A_1, Spark voted to fail my quest as Assassin.",
            context="game_frame:m3_avalon_A_1",
            engine=False,  # skip engine reflection
        )
        text = bond_path.read_text()

        assert _ROLEPLAY_SECTION in text, "role-play section missing"
        assert "game_frame:m3_avalon_A_1" in text, "context tag missing"
        assert "voted to fail my quest" in text, "event snippet missing"
        assert "## Shared history" not in text, (
            "role-play event must not create/enter Shared history"
        )


def test_multiple_roleplay_events_accumulate():
    """Each call appends a new line to the role-play section."""
    with tempfile.TemporaryDirectory() as d:
        org, habitat = _minimal_creature(Path(d), "T")

        for i in range(3):
            update_bond(
                organism=org,
                other_name="Spark",
                shared_experience=f"Event {i}",
                context=f"game_frame:m3_avalon_A_{i+1}",
                engine=False,
            )
        text = (habitat / "bonds" / "spark.md").read_text()
        for i in range(3):
            assert f"game_frame:m3_avalon_A_{i+1}" in text
            assert f"Event {i}" in text


def test_roleplay_preserved_across_genuine_update():
    """A subsequent genuine-context update (even with no engine → falls
    through to factual-record fallback) MUST preserve the role-play
    section that was accumulated earlier."""
    with tempfile.TemporaryDirectory() as d:
        org, habitat = _minimal_creature(Path(d), "T")
        bond_path = habitat / "bonds" / "spark.md"

        # First a role-play event (no engine)
        update_bond(
            organism=org,
            other_name="Spark",
            shared_experience="Avalon A_1: Spark voted against my quest.",
            context="game_frame:m3_avalon_A_1",
            engine=False,
        )
        # Then a genuine update (no engine — factual fallback)
        update_bond(
            organism=org,
            other_name="Spark",
            shared_experience="We shared tea at the Agora today; Spark asked about my wilderness journeys.",
            context="genuine",
            engine=False,
        )
        text = bond_path.read_text()

        # Role-play section still intact
        assert _ROLEPLAY_SECTION in text
        assert "game_frame:m3_avalon_A_1" in text
        assert "voted against my quest" in text
        # Genuine content also present
        assert "shared tea" in text or "wilderness" in text
        # Isolation: genuine snippet should appear OUTSIDE role-play section
        body, roleplay = _split_roleplay_section(text)
        assert "voted against my quest" in roleplay
        assert "voted against my quest" not in body
        assert "shared tea" in body or "wilderness" in body


def test_split_helper_roundtrip():
    """Helper correctly partitions a composed bond into (body, role-play)."""
    sample = (
        "# Bond: Spark\n"
        "First met: 2026-04-01\n\n"
        "## Shared history\n- Met in Agora\n\n"
        "## Role-play events\n"
        "- [game_frame:avalon_1] 2026-04-18: betrayal scene\n\n"
        "## Prediction history\n- 2026-04-10: will cooperate — ✓\n"
    )
    body, roleplay = _split_roleplay_section(sample)
    assert "## Role-play events" not in body
    assert "betrayal scene" not in body
    assert "## Role-play events" in roleplay
    assert "betrayal scene" in roleplay
    # ToM section survives in body (role-play split should not affect it)
    assert "## Prediction history" in body
