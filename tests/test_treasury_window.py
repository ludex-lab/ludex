"""Regression tests for the village treasury rollup.

The original bug: `collect()` filtered spans with `if ts < start` and had no
upper bound. Invisible while the ledger only ever asked about the current
week, wrong the moment it asked about any other — everything after the week
spilled in, and a future-dated span always landed in the current one.

Found by porting the workshop bench tool (Ember, `treasury_tally.py`), which
had both bounds from the start. These tests exist because the port shipped
without any, while the bench tool it came from shipped with seven.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ludex.village import treasury


def _week(label: str) -> tuple[float, float]:
    start = treasury.week_from_label(label)
    return start, start + 7 * 86400


def _span(ts: float, *, provider: str = "claude_cli", source: str = "estimated",
          tin: int = 100, tout: int = 40, outcome: str = "ok") -> dict:
    return {"kind": "brain_call", "timestamp": ts,
            "attributes": {"provider": provider, "token_source": source,
                           "tokens_in": tin, "tokens_out": tout,
                           "outcome": outcome}}


@pytest.fixture()
def village(tmp_path: Path, monkeypatch) -> Path:
    """A creatures/ tree with one resident, wired into the treasury module."""
    creatures = tmp_path / "creatures"
    cdir = creatures / "Probe"
    (cdir / "store").mkdir(parents=True)
    (cdir / "ludex.yaml").write_text("name: Probe\n", encoding="utf-8")
    monkeypatch.setattr(treasury, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(treasury, "creature_dirs", lambda root, habitat: [cdir])
    return cdir


def _write(cdir: Path, spans: list[dict]) -> None:
    (cdir / "store" / "spans.jsonl").write_text(
        "\n".join(json.dumps(s) for s in spans), encoding="utf-8")


def test_span_after_the_week_is_not_counted(village):
    """The bug itself: without an upper bound, later weeks spill in."""
    start, end = _week("2026-W34")
    _write(village, [_span(start + 3600), _span(end + 3600)])

    d = treasury.collect("", week="2026-W34")
    assert d["providers"]["claude_cli"]["calls"] == 1


def test_future_dated_span_does_not_land_in_the_current_week(village):
    """A span timestamped next month must not be counted as this week's."""
    now = time.time()
    _write(village, [_span(now), _span(now + 30 * 86400)])

    d = treasury.collect("", now=now)
    assert d["providers"]["claude_cli"]["calls"] == 1


def test_span_before_the_week_is_not_counted(village):
    start, _ = _week("2026-W34")
    _write(village, [_span(start - 3600), _span(start + 3600)])

    d = treasury.collect("", week="2026-W34")
    assert d["providers"]["claude_cli"]["calls"] == 1


def test_measured_and_estimated_are_never_summed(village):
    """The ledger's headline was 63% estimated presented as one figure."""
    start, _ = _week("2026-W34")
    _write(village, [_span(start + 1, source="measured", tout=100),
                     _span(start + 2, source="estimated", tout=25)])

    row = treasury.collect("", week="2026-W34")["providers"]["claude_cli"]
    assert row["out_measured"] == 100
    assert row["out_estimated"] == 25
    assert row["tok_out"] == 125


def test_missing_token_source_counts_as_estimated(village):
    """An absent label is not a measurement — it must never read as one."""
    start, _ = _week("2026-W34")
    span = _span(start + 1)
    del span["attributes"]["token_source"]
    _write(village, [span])

    row = treasury.collect("", week="2026-W34")["providers"]["claude_cli"]
    assert row["out_measured"] == 0
    assert row["out_estimated"] == 40


def test_error_rate_counts_failed_calls(village):
    start, _ = _week("2026-W34")
    _write(village, [_span(start + 1, outcome="error"),
                     _span(start + 2), _span(start + 3), _span(start + 4)])

    row = treasury.collect("", week="2026-W34")["providers"]["claude_cli"]
    assert row["err"] == 1
    assert treasury._err_cell(row) == "1/4 (25.0%)"


def test_error_cell_on_an_empty_row_does_not_divide_by_zero(village):
    assert treasury._err_cell({"calls": 0, "err": 0}) == "—"


def test_week_from_label_lands_on_monday_midnight(village):
    lt = time.localtime(treasury.week_from_label("2026-W34"))
    assert (lt.tm_wday, lt.tm_hour, lt.tm_min) == (0, 0, 0)


def test_headline_splits_measured_from_estimated(village):
    """The reported line must show the split, not one blended total."""
    start, _ = _week("2026-W34")
    _write(village, [_span(start + 1, source="measured", tout=100),
                     _span(start + 2, source="estimated", tout=25)])
    treasury.LEDGER_DIR = village / "ledger"

    line = treasury.write_week_ledger("", week="2026-W34")
    assert "실측 100 + 추정 25" in line
