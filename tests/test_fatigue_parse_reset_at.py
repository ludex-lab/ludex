"""Tests for ludex.blocks.adapters._fatigue.parse_reset_at.

Contract: parse_reset_at(text) -> int | None. Returns
seconds-from-now until the parsed reset moment, None when no
trigger phrase matches or every date format fails.

Verified sample (D-077, 2026-05-09): Codex stderr carries
'try again at May 12th, 2026 3:16 PM.' with no timezone hint.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from ludex.blocks.adapters._fatigue import parse_reset_at


def _future_secs(seconds_ahead: float) -> int:
    """Helper: tolerance-check a parsed delta against an expectation."""
    return int(seconds_ahead)


def test_returns_none_on_empty():
    assert parse_reset_at("") is None
    assert parse_reset_at("   \n   ") is None


def test_returns_none_when_no_trigger():
    assert parse_reset_at("ERROR: out of memory") is None
    assert parse_reset_at("just some random text without a reset hint") is None


def test_returns_none_on_unparseable_phrase():
    # Trigger present but the date portion is garbage.
    assert parse_reset_at("try again at later when you can") is None


def test_iso_with_z():
    future = datetime.utcnow() + timedelta(hours=2)
    iso = future.strftime("%Y-%m-%dT%H:%M:%SZ")
    text = f"Daily limit reached; resets at {iso}"
    result = parse_reset_at(text)
    assert result is not None
    # UTC datetime; we accept ±5 min slack for time-of-test drift.
    assert 2 * 3600 - 300 <= result <= 2 * 3600 + 300


def test_iso_local():
    future = datetime.now() + timedelta(minutes=45)
    s = future.strftime("%Y-%m-%dT%H:%M:%S")
    text = f"resets at {s}"
    result = parse_reset_at(text)
    assert result is not None
    assert 45 * 60 - 120 <= result <= 45 * 60 + 120


def test_jj_verified_format():
    """The exact format JJ verified 2026-05-09: 'try again at May 12th,
    2026 3:16 PM.' — month name, ordinal, comma year, 12h time."""
    future = datetime.now() + timedelta(hours=4)
    # Build the phrase from a real future datetime so the test is
    # date-stable.
    month = future.strftime("%B")
    day = future.day
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(
        day if day < 20 else day % 10, "th"
    )
    if day in (11, 12, 13):
        suffix = "th"
    year = future.year
    time_str = future.strftime("%I:%M %p").lstrip("0")
    phrase = f"{month} {day}{suffix}, {year} {time_str}"
    text = f"Codex usage limit reached. Try again at {phrase}."
    result = parse_reset_at(text)
    assert result is not None
    assert 4 * 3600 - 300 <= result <= 4 * 3600 + 300


def test_resets_at_iso_period():
    future = datetime.now() + timedelta(minutes=20)
    iso = future.strftime("%Y-%m-%dT%H:%M:%S")
    text = f"Quota exhausted. resets at {iso}. Please retry."
    result = parse_reset_at(text)
    assert result is not None
    assert 20 * 60 - 120 <= result <= 20 * 60 + 120


def test_available_again_at():
    future = datetime.now() + timedelta(hours=1, minutes=10)
    iso = future.strftime("%Y-%m-%dT%H:%M:%S")
    text = f"Available again at {iso}"
    result = parse_reset_at(text)
    assert result is not None
    assert 70 * 60 - 120 <= result <= 70 * 60 + 120


def test_time_only_today():
    # "resets at 23:59" — if we're earlier than 23:59 today, expect
    # a same-day delta. If after, parser should roll to tomorrow.
    now = datetime.now()
    later_today = now.replace(hour=(now.hour + 1) % 24, minute=0, second=0,
                              microsecond=0)
    s = later_today.strftime("%H:%M")
    text = f"resets at {s}"
    result = parse_reset_at(text)
    assert result is not None
    # Either same-day (positive small delta) or rolled to tomorrow
    # (within ~24h). Both are non-negative and bounded.
    assert 0 <= result < 25 * 3600


def test_past_timestamp_clamps_to_zero():
    """If a parsed timestamp is already in the past, return 0 (the
    fatigue window has effectively elapsed already). The resilience
    block should treat 0 as 'recovered'."""
    past = datetime.now() - timedelta(hours=2)
    iso = past.strftime("%Y-%m-%dT%H:%M:%S")
    text = f"resets at {iso}"
    result = parse_reset_at(text)
    # Either None (date-only that doesn't make sense) or 0; reject
    # any negative number.
    assert result is None or result == 0


def test_case_insensitive():
    future = datetime.now() + timedelta(minutes=30)
    iso = future.strftime("%Y-%m-%dT%H:%M:%S")
    for trigger in ("Try Again At", "TRY AGAIN AT", "try again at"):
        text = f"limit hit. {trigger} {iso}"
        result = parse_reset_at(text)
        assert result is not None, f"failed for trigger={trigger!r}"


def test_multiple_triggers_first_wins():
    future1 = datetime.now() + timedelta(hours=1)
    future2 = datetime.now() + timedelta(hours=5)
    iso1 = future1.strftime("%Y-%m-%dT%H:%M:%S")
    iso2 = future2.strftime("%Y-%m-%dT%H:%M:%S")
    text = f"try again at {iso1}\nor maybe resets at {iso2}"
    result = parse_reset_at(text)
    assert result is not None
    # First trigger ("try again at") wins.
    assert 3600 - 300 <= result <= 3600 + 300
