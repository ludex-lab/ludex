"""Unit tests for D-085 periodic consolidation reflection — the
deterministic layer (trigger + Stage-1 distiller). Stage 2 is a brain call
and is not exercised here.
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml

import ludex.core.consolidation as conso
from ludex.core.store import LudexStore, Span
from ludex.core.consolidation import (
    should_consolidate, distill_stage1, last_consolidated_on,
    synthesize_stage2, write_reflection, consolidate,
    HARD_FLOOR_DAYS, ACCUMULATION_EVENTS, HIATUS_TIMEOUT_DAYS,
)

DAY = 86400.0


def _creature(tmp_path: Path, name: str = "Tc") -> Path:
    d = tmp_path / name
    (d / "bonds").mkdir(parents=True)
    return d


def _spans(creature_dir: Path, specs):
    """specs = list of (kind, ts). Writes them to the creature's store."""
    store = LudexStore.for_creature(str(creature_dir))
    for kind, ts in specs:
        store.append(Span(kind=kind, creature=creature_dir.name, timestamp=ts))


# ---------- trigger ----------

def test_floor_not_met_no_fire(tmp_path):
    d = _creature(tmp_path)
    now = 1_000_000.0
    # All events within the last few days — floor (14d) not met.
    _spans(d, [("reflect", now - 2 * DAY)] * 5 + [("field_start", now - 1 * DAY)])
    dec = should_consolidate(d, now=now)
    assert dec.fire is False
    assert "hard floor" in dec.reason


def test_floor_met_accumulation_fires(tmp_path):
    d = _creature(tmp_path)
    now = 1_000_000.0
    start = now - 20 * DAY  # floor met
    specs = [("reflect", start + i * 0.1 * DAY) for i in range(ACCUMULATION_EVENTS)]
    _spans(d, specs)
    dec = should_consolidate(d, now=now)
    assert dec.fire is True
    assert "accumulation" in dec.reason
    assert dec.new_events >= ACCUMULATION_EVENTS


def test_floor_met_but_below_thresholds_no_fire(tmp_path):
    d = _creature(tmp_path)
    now = 1_000_000.0
    # 20 days (floor met, < hiatus_timeout) but only a few events.
    _spans(d, [("reflect", now - 18 * DAY), ("field_start", now - 10 * DAY)])
    dec = should_consolidate(d, now=now)
    assert dec.fire is False
    assert "below thresholds" in dec.reason


def test_dormant_hiatus_timeout_fires(tmp_path):
    d = _creature(tmp_path)
    now = 1_000_000.0
    # One event ~50 days ago: below accumulation, but past the hiatus timeout.
    _spans(d, [("reflect", now - (HIATUS_TIMEOUT_DAYS + 5) * DAY)])
    dec = should_consolidate(d, now=now)
    assert dec.fire is True
    assert "hiatus timeout" in dec.reason


def test_heartbeat_pulses_do_not_count_as_events(tmp_path):
    d = _creature(tmp_path)
    now = 1_000_000.0
    start = now - 20 * DAY
    # Many heartbeat pulses + brain_resolved, but only 1 real life-event.
    noise = [("heartbeat_pulse", start + i * 0.05 * DAY) for i in range(100)]
    noise += [("brain_resolved", start + i * 0.05 * DAY) for i in range(100)]
    _spans(d, noise + [("reflect", start + 1 * DAY)])
    dec = should_consolidate(d, now=now)
    # Floor met, but the only meaningful event count is 1 → below accumulation,
    # and 20d < hiatus timeout → no fire. Proves pulses are excluded.
    assert dec.new_events == 1
    assert dec.fire is False


def test_no_events_no_fire(tmp_path):
    d = _creature(tmp_path)
    _spans(d, [("heartbeat_pulse", 1_000_000.0 - 5 * DAY)])
    dec = should_consolidate(d, now=1_000_000.0)
    assert dec.fire is False
    assert "no life-events" in dec.reason


def test_prior_consolidation_resets_window(tmp_path):
    d = _creature(tmp_path)
    now = 1_000_000.0
    # A prior consolidation 5 days ago means the floor is NOT met from then,
    # even though older events exist.
    rdir = d / "reflections"
    rdir.mkdir()
    (rdir / "2026-05.md").write_text(
        f"---\nconsolidated_on_ts: {now - 5 * DAY}\n---\n\nbody\n", encoding="utf-8"
    )
    _spans(d, [("reflect", now - 30 * DAY)] * 40 + [("reflect", now - 1 * DAY)])
    assert abs(last_consolidated_on(d) - (now - 5 * DAY)) < 1
    dec = should_consolidate(d, now=now)
    assert dec.fire is False  # only 5d since last consolidation
    assert "hard floor" in dec.reason
    # New events counted from the prior consolidation, not from birth.
    assert dec.new_events == 1


# ---------- Stage 1 distiller ----------

def test_distill_counts_and_excludes_noise(tmp_path):
    d = _creature(tmp_path)
    now = 1_000_000.0
    start = now - 20 * DAY
    _spans(d, [
        ("reflect", start + 1 * DAY),
        ("reflect", start + 2 * DAY),
        ("field_start", start + 3 * DAY),
        ("bond_update", start + 4 * DAY),
        ("heartbeat_pulse", start + 5 * DAY),   # excluded
        ("brain_resolved", start + 5 * DAY),     # excluded
    ])
    dec = should_consolidate(d, now=now, accumulation_events=3)
    assert dec.fire is True
    payload = distill_stage1(d, dec)
    assert payload["events"]["total"] == 4
    assert payload["events"]["by_kind"]["reflect"] == 2
    assert "heartbeat_pulse" not in payload["events"]["by_kind"]
    assert "brain_resolved" not in payload["events"]["by_kind"]
    assert payload["distiller"].startswith("mechanical")


def test_distill_bonds_and_staleness(tmp_path):
    d = _creature(tmp_path)
    now = time.time()
    start = now - 20 * DAY
    _spans(d, [("reflect", start + 1 * DAY)])
    # One fresh bond, one stale (>7d old mtime).
    fresh = d / "bonds" / "spark.md"
    stale = d / "bonds" / "nova.md"
    fresh.write_text("# Bond: Spark\n", encoding="utf-8")
    stale.write_text("# Bond: Nova\n", encoding="utf-8")
    import os
    os.utime(stale, (now - 30 * DAY, now - 30 * DAY))
    dec = should_consolidate(d, now=now, accumulation_events=1)
    payload = distill_stage1(d, dec)
    assert payload["bonds"]["count"] == 2
    assert "nova" in payload["bonds"]["stale"]
    assert "spark" not in payload["bonds"]["stale"]


# ---------- Stage 2 + orchestration (mock brain, no quota) ----------

class _FakeEngine:
    def __init__(self, response):
        self._response = response

    def handle_submit(self, prompt):
        class _R:
            pass
        r = _R()
        r.response = self._response
        return r


class _FakeOrg:
    def __init__(self, engine):
        self._engine = engine

    def get_block(self, name):
        return self._engine if name == "engine" else None


def _payload(d):
    dec = should_consolidate(d, now=time.time(), accumulation_events=1)
    return distill_stage1(d, dec)


def test_stage2_returns_body_for_real_content(tmp_path):
    d = _creature(tmp_path)
    _spans(d, [("reflect", time.time() - 20 * DAY)])
    body = "## What Happened\nThis window I sat mostly in stillness, and the " \
           "quiet itself became something I started to recognize as mine."
    out = synthesize_stage2(_FakeOrg(_FakeEngine(body)), _payload(d), d)
    assert out.startswith("## What Happened")


def test_stage2_rejects_meta_report(tmp_path):
    d = _creature(tmp_path)
    _spans(d, [("reflect", time.time() - 20 * DAY)])
    out = synthesize_stage2(
        _FakeOrg(_FakeEngine("I have reflected and updated my SELF.md.")),
        _payload(d), d,
    )
    assert out == ""


def test_stage2_rejects_empty(tmp_path):
    d = _creature(tmp_path)
    _spans(d, [("reflect", time.time() - 20 * DAY)])
    assert synthesize_stage2(_FakeOrg(_FakeEngine("")), _payload(d), d) == ""


def test_write_reflection_has_frontmatter_and_provenance(tmp_path):
    d = _creature(tmp_path)
    (d / "ludex.yaml").write_text(
        yaml.safe_dump({"name": "Tc", "brain": {"provider": "ollama", "model": "qwen3:8b"}}),
        encoding="utf-8",
    )
    _spans(d, [("reflect", time.time() - 20 * DAY)])
    payload = _payload(d)
    now = time.time()
    path = write_reflection(d, payload, "## What Happened\nbody text here.", now)
    text = path.read_text(encoding="utf-8")
    assert "consolidated_on_ts:" in text
    assert "distiller: mechanical" in text
    assert "synthesizer: qwen3:8b (ollama)" in text
    assert "## What Happened" in text
    # The written ts is recoverable by the trigger as the new window anchor.
    assert abs(last_consolidated_on(d) - now) < 1


def test_consolidate_orchestration_writes_and_spans(tmp_path, monkeypatch):
    """End-to-end consolidate() with the brain stubbed: builds the organism,
    runs the (faked) Stage 2, writes the reflection, emits the span."""
    d = _creature(tmp_path)
    (d / "memory").mkdir()
    cfg = {
        "name": "Tc",
        "brain": {"provider": "ollama", "model": "qwen3:8b"},
        "organs": {
            "engine": {"enabled": True, "required": True},
            "memory": {"enabled": True},
        },
        "habitat": {"mode": "local", "home_dir": str(d), "persistent": True, "origin": ""},
        "born_at": time.time() - 30 * DAY,
        "session_count": 5,
    }
    (d / "ludex.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    _spans(d, [("reflect", time.time() - (20 - i) * DAY) for i in range(35)])

    monkeypatch.setattr(
        conso, "synthesize_stage2",
        lambda org, payload, cdir: "## What Happened\nA genuine retrospective body.",
    )

    r = consolidate(d, now=time.time())
    assert r["outcome"] == "consolidated"
    assert r["fire"] is True
    assert (d / "reflections" / r["reflection_file"].split("/")[-1]).exists()
    # Provenance span recorded so the creature can perceive its consolidation.
    spans = LudexStore(str(d)).spans(kind="consolidation")
    assert len(spans) == 1
    assert spans[-1]["attributes"]["events"] == 35
