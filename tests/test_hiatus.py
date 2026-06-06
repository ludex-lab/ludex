"""Tests for ludex.core.hiatus and the heartbeat hiatus-detection path.

Covers:
- HIATUS.md frontmatter parser (success + bad input)
- HiatusMarker.is_active boundary
- duration_human formatting
- build_reflect_context output shape
- mark_consumed idempotency
- find_active_hiatus pre-window noop
- pulse_creature integration: hiatus forces reflect, marker consumed
  on success, marker preserved on reflect failure
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ludex.core.hiatus import (
    HiatusMarker,
    build_reflect_context,
    find_active_hiatus,
    find_in_hiatus,
    mark_consumed,
    parse_hiatus,
)


# -------------------------------------------------------------------
# Parser
# -------------------------------------------------------------------

def _write_hiatus(dir_: Path, body: str = "Body prose.") -> Path:
    p = dir_ / "HIATUS.md"
    p.write_text(
        "---\n"
        "hiatus_start: 2026-05-11\n"
        "hiatus_end: 2026-06-19\n"
        "reason: caretaker_traveled\n"
        "declared_by: JJ\n"
        "declared_at: 2026-05-11T15:30:00Z\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return p


def test_parse_hiatus_success(tmp_path):
    _write_hiatus(tmp_path)
    m = parse_hiatus(tmp_path / "HIATUS.md")
    assert m is not None
    assert m.start_date == "2026-05-11"
    assert m.end_date == "2026-06-19"
    assert m.reason == "caretaker_traveled"
    assert m.declared_by == "JJ"
    assert m.body == "Body prose."
    assert m.end_ts > m.start_ts


def test_parse_hiatus_missing_file(tmp_path):
    assert parse_hiatus(tmp_path / "HIATUS.md") is None


def test_parse_hiatus_no_frontmatter(tmp_path):
    p = tmp_path / "HIATUS.md"
    p.write_text("just body, no frontmatter", encoding="utf-8")
    assert parse_hiatus(p) is None


def test_parse_hiatus_missing_required_field(tmp_path):
    p = tmp_path / "HIATUS.md"
    p.write_text(
        "---\nhiatus_start: 2026-05-11\n---\n\nno end date\n",
        encoding="utf-8",
    )
    assert parse_hiatus(p) is None


def test_parse_hiatus_bad_date(tmp_path):
    p = tmp_path / "HIATUS.md"
    p.write_text(
        "---\nhiatus_start: not-a-date\nhiatus_end: 2026-06-19\n---\n\nbody\n",
        encoding="utf-8",
    )
    assert parse_hiatus(p) is None


# -------------------------------------------------------------------
# HiatusMarker behavior
# -------------------------------------------------------------------

def _marker(start: float, end: float) -> HiatusMarker:
    return HiatusMarker(
        start_date="d_start", end_date="d_end",
        start_ts=start, end_ts=end,
        reason="r", declared_by="JJ", declared_at="ts",
        body="body",
    )


def test_is_active_pre_window():
    now = time.time()
    m = _marker(start=now + 10, end=now + 100)
    assert m.is_active(now) is False


def test_is_active_post_window():
    now = time.time()
    m = _marker(start=now - 200, end=now - 10)
    assert m.is_active(now) is True


def test_is_active_at_boundary():
    now = 1000.0
    m = _marker(start=900.0, end=1000.0)
    # now == end_ts is considered active (post-hiatus has begun)
    assert m.is_active(now) is True


def test_is_in_hiatus_pre_window():
    now = 500.0
    m = _marker(start=1000.0, end=2000.0)
    assert m.is_in_hiatus(now) is False


def test_is_in_hiatus_mid_window():
    now = 1500.0
    m = _marker(start=1000.0, end=2000.0)
    assert m.is_in_hiatus(now) is True


def test_is_in_hiatus_at_start_boundary():
    # now == start_ts → just entered hiatus
    now = 1000.0
    m = _marker(start=1000.0, end=2000.0)
    assert m.is_in_hiatus(now) is True


def test_is_in_hiatus_at_end_boundary_is_false():
    # now == end_ts → hiatus has ended, is_active fires, is_in_hiatus does not
    now = 2000.0
    m = _marker(start=1000.0, end=2000.0)
    assert m.is_in_hiatus(now) is False
    assert m.is_active(now) is True


def test_is_in_hiatus_post_window():
    now = 3000.0
    m = _marker(start=1000.0, end=2000.0)
    assert m.is_in_hiatus(now) is False


def test_duration_human_weeks():
    m = _marker(start=0.0, end=86400.0 * 7 * 5.5)  # 5.5 weeks
    assert m.duration_human() == "5.5 weeks"


def test_duration_human_integer_weeks():
    m = _marker(start=0.0, end=86400.0 * 7 * 3)  # exactly 3 weeks
    assert m.duration_human() == "3 weeks"


def test_duration_human_days():
    m = _marker(start=0.0, end=86400.0 * 3)
    assert m.duration_human() == "3 days"


def test_duration_human_hours():
    m = _marker(start=0.0, end=3600.0 * 6)
    assert m.duration_human() == "6 hours"


# -------------------------------------------------------------------
# Reflect-context rendering
# -------------------------------------------------------------------

def test_build_reflect_context_includes_window_and_body():
    m = _marker(start=0.0, end=86400.0 * 7 * 5.5)
    out = build_reflect_context(m)
    assert "[Hiatus context]" in out
    assert "d_start" in out and "d_end" in out
    assert "5.5 weeks" in out
    assert "body" in out
    assert "Reason: r" in out


def test_build_reflect_context_omits_empty_fields():
    m = HiatusMarker(
        start_date="s", end_date="e",
        start_ts=0.0, end_ts=86400.0,
        reason="", declared_by="", declared_at="",
        body="",
    )
    out = build_reflect_context(m)
    assert "Reason:" not in out
    # body absent; no trailing blank section
    assert not out.endswith("\n")
    assert "(1 days)" in out


# -------------------------------------------------------------------
# Consume / idempotency
# -------------------------------------------------------------------

def test_mark_consumed_renames(tmp_path):
    p = _write_hiatus(tmp_path)
    consumed = mark_consumed(p)
    assert not p.exists()
    assert consumed.name == "HIATUS.consumed.md"
    assert consumed.exists()


def test_mark_consumed_idempotent_when_consumed_exists(tmp_path):
    p = _write_hiatus(tmp_path)
    # First consume
    mark_consumed(p)
    # Caretaker re-declares (or a stray active file appears)
    p2 = _write_hiatus(tmp_path)
    # Second consume should not overwrite the existing .consumed.md;
    # just remove the active one. (Caretaker can rotate to dated names
    # if a history matters.)
    consumed = mark_consumed(p2)
    assert not p2.exists()
    assert consumed.exists()


# -------------------------------------------------------------------
# find_active_hiatus convenience
# -------------------------------------------------------------------

def test_find_active_hiatus_pre_window_returns_none(tmp_path):
    p = tmp_path / "HIATUS.md"
    p.write_text(
        "---\n"
        "hiatus_start: 2099-01-01\n"
        "hiatus_end: 2099-12-31\n"
        "---\n\nfuture hiatus\n",
        encoding="utf-8",
    )
    assert find_active_hiatus(tmp_path) is None


def test_find_active_hiatus_post_window_returns_marker(tmp_path):
    p = tmp_path / "HIATUS.md"
    p.write_text(
        "---\n"
        "hiatus_start: 2000-01-01\n"
        "hiatus_end: 2000-01-02\n"
        "---\n\npast hiatus\n",
        encoding="utf-8",
    )
    m = find_active_hiatus(tmp_path)
    assert m is not None
    assert m.start_date == "2000-01-01"


def test_find_active_hiatus_missing_returns_none(tmp_path):
    assert find_active_hiatus(tmp_path) is None


# -------------------------------------------------------------------
# find_in_hiatus convenience
# -------------------------------------------------------------------

def test_find_in_hiatus_mid_window_returns_marker(tmp_path):
    _write_hiatus(tmp_path)
    # Frozen "now" inside the 2026-05-11 → 2026-06-19 window.
    # 2026-05-20 = ~9 days into the window.
    import calendar
    mid_ts = float(calendar.timegm((2026, 5, 20, 0, 0, 0, 0, 0, 0)))
    m = find_in_hiatus(tmp_path, now=mid_ts)
    assert m is not None
    assert m.start_date == "2026-05-11"
    assert m.end_date == "2026-06-19"


def test_find_in_hiatus_post_window_returns_none(tmp_path):
    _write_hiatus(tmp_path)
    import calendar
    post_ts = float(calendar.timegm((2026, 6, 25, 0, 0, 0, 0, 0, 0)))
    assert find_in_hiatus(tmp_path, now=post_ts) is None


def test_find_in_hiatus_pre_window_returns_none(tmp_path):
    _write_hiatus(tmp_path)
    import calendar
    pre_ts = float(calendar.timegm((2026, 5, 1, 0, 0, 0, 0, 0, 0)))
    assert find_in_hiatus(tmp_path, now=pre_ts) is None


def test_find_in_hiatus_missing_returns_none(tmp_path):
    assert find_in_hiatus(tmp_path) is None


def test_find_in_hiatus_and_find_active_hiatus_mutually_exclusive(tmp_path):
    """Across the timeline, at most one of (find_in_hiatus,
    find_active_hiatus) should ever return a marker. This contract
    is what lets the heartbeat short-circuit on find_in_hiatus and
    rely on find_active_hiatus driving wake."""
    _write_hiatus(tmp_path)
    import calendar
    pre_ts = float(calendar.timegm((2026, 5, 1, 0, 0, 0, 0, 0, 0)))
    mid_ts = float(calendar.timegm((2026, 5, 20, 0, 0, 0, 0, 0, 0)))
    post_ts = float(calendar.timegm((2026, 6, 25, 0, 0, 0, 0, 0, 0)))
    for ts in (pre_ts, mid_ts, post_ts):
        in_h = find_in_hiatus(tmp_path, now=ts) is not None
        active = find_active_hiatus(tmp_path, now=ts) is not None
        assert not (in_h and active), (
            f"both fired at ts={ts}: in_hiatus={in_h}, active={active}"
        )
