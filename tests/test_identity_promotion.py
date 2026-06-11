"""D-085 param 3 tests — [PROVISIONAL]→[SETTLED] SELF.md promotion gate."""
from __future__ import annotations

from pathlib import Path

from ludex.core.consolidation import (
    parse_identity_shifts, propose_promotions, apply_promotions,
)
from ludex.core.selfhood import (
    extract_identity_block, IDENTITY_BLOCK_START, IDENTITY_BLOCK_END,
)


def _write_reflection(creature_dir: Path, stem: str, ts: float,
                      shifts: list[str]) -> None:
    rdir = creature_dir / "reflections"
    rdir.mkdir(exist_ok=True)
    bullets = "\n".join(f"- {s}" for s in shifts) or "(none settled yet)"
    (rdir / f"{stem}.md").write_text(f"""---
window_from: 2026-01-01
window_to: 2026-01-31
consolidated_on: {stem}-28
consolidated_on_ts: {ts}
synthesizer: test (stub)
distiller: mechanical/deterministic (no LLM)
events: 50
---

## What Happened

Things happened.

## Identity Shifts

{bullets}

## Still Open

A question.
""", encoding="utf-8")


def test_parse_identity_shifts_bullets_and_placeholder():
    text = "## Identity Shifts\n\n- My pause before answering has settled into identity.\n- (none beyond that)\n"
    out = parse_identity_shifts(text)
    assert out == ["My pause before answering has settled into identity."]
    assert parse_identity_shifts("## Identity Shifts\n\n(none settled yet)\n") == []


def test_first_window_nominates_provisional(tmp_path):
    _write_reflection(tmp_path, "2026-05", 1000.0,
                      ["I rest before exploring, and that is mine."])
    proposal = propose_promotions(tmp_path)
    assert proposal["window"] == "2026-05"
    assert len(proposal["nominations"]) == 1
    assert proposal["promotions"] == []

    block = apply_promotions(tmp_path, proposal)
    assert "[PROVISIONAL] I rest before exploring" in block
    self_text = (tmp_path / "SELF.md").read_text()
    assert IDENTITY_BLOCK_START in self_text and IDENTITY_BLOCK_END in self_text


def test_second_window_promotes_to_settled(tmp_path):
    _write_reflection(tmp_path, "2026-05", 1000.0,
                      ["I rest before exploring, and that is mine."])
    apply_promotions(tmp_path, propose_promotions(tmp_path))

    _write_reflection(tmp_path, "2026-06", 2000.0,
                      ["Resting before exploring is mine — it held again."])
    proposal = propose_promotions(tmp_path)
    assert len(proposal["promotions"]) == 1
    assert proposal["nominations"] == []

    block = apply_promotions(tmp_path, proposal)
    assert "[SETTLED] I rest before exploring" in block
    assert "[PROVISIONAL] I rest before exploring" not in block


def test_same_window_rerun_is_idempotent(tmp_path):
    _write_reflection(tmp_path, "2026-05", 1000.0,
                      ["I rest before exploring, and that is mine."])
    apply_promotions(tmp_path, propose_promotions(tmp_path))
    # Re-running on the SAME window must not promote (no single-window drift)
    proposal = propose_promotions(tmp_path)
    assert proposal["promotions"] == []
    assert proposal["nominations"] == []


def test_both_windows_at_once_promotes_directly(tmp_path):
    # Candidate present in the last TWO windows with no prior provisional
    # entry → two-window survival → straight to SETTLED.
    _write_reflection(tmp_path, "2026-05", 1000.0,
                      ["My quick wit cuts through confusion."])
    _write_reflection(tmp_path, "2026-06", 2000.0,
                      ["My quick wit cuts through the confusion again."])
    proposal = propose_promotions(tmp_path)
    assert len(proposal["promotions"]) == 1


def test_settled_entries_survive_later_windows(tmp_path):
    _write_reflection(tmp_path, "2026-05", 1000.0, ["I rest before exploring."])
    apply_promotions(tmp_path, propose_promotions(tmp_path))
    _write_reflection(tmp_path, "2026-06", 2000.0, ["I rest before exploring."])
    apply_promotions(tmp_path, propose_promotions(tmp_path))
    _write_reflection(tmp_path, "2026-07", 3000.0, ["A wholly new theme arrived."])
    apply_promotions(tmp_path, propose_promotions(tmp_path))
    self_text = (tmp_path / "SELF.md").read_text()
    assert "[SETTLED] I rest before exploring" in self_text
    assert "[PROVISIONAL] A wholly new theme arrived" in self_text


def test_extract_identity_block_roundtrip():
    block = (f"{IDENTITY_BLOCK_START}\n## Identity\n- [SETTLED] x (settled: t)\n"
             f"{IDENTITY_BLOCK_END}")
    text = f"# Self\n\nreflection prose\n\n{block}\n"
    assert extract_identity_block(text) == block
    assert extract_identity_block("no block here") == ""
