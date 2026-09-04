"""The village has to be able to watch a substrate transition.

The renderer could already swap a head when a re-brain happened LIVE, but the
bus never scanned `substrate_transition` spans, so the sixteen already in the
ledger were invisible. That gap matters more after the longitudinal reframe
(DEVIATION 01): the pre-registered event was one generation dying and being
succeeded, and what the ledger actually holds is the whole substrate generation
turning over with nobody dead — four creatures across two lineages on 07-27.

Two properties. Same-day moves merge into one scene, because rendering them
separately shows four upgrades where the record shows a turnover. And rare
scenes survive the API's volume cap: 1086 reflect and 296 heartbeat scenes
against 5 transitions means a tail cut silently drops exactly what the timeline
exists to show, which is checklist item 8.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ludex.village.bus import _desc, _transition_scene  # noqa: E402


def _move(t, who, **attrs):
    return (t, who, attrs)


def test_same_day_moves_merge_into_one_turnover():
    run = [_move(1000.0, "Aria", axis="M"), _move(1060.0, "Verse", axis="M"),
           _move(1120.0, "Flare", axis="M"), _move(1180.0, "Spark", axis="M")]
    s = _transition_scene(run)
    assert s["kind"] == "transition"
    assert s["actors"] == ["Aria", "Verse", "Flare", "Spark"]
    assert s["cohort_sweep"] is True
    assert s["axes"] == ["M"]
    assert s["t"] == 1000.0                      # anchored on the first move


def test_a_single_move_is_not_a_sweep():
    s = _transition_scene([_move(1000.0, "Nova", axis="A")])
    assert s["cohort_sweep"] is False
    assert s["actors"] == ["Nova"]


def test_a_missing_reason_stays_missing():
    """07-27's four spans carry no reason. Absent is reported, never invented —
    the same rule that kept it out of the longitudinal amendment."""
    s = _transition_scene([_move(1000.0, "Aria", axis="M")])
    assert s["moves"][0]["reason"] is None


def test_from_and_to_render_whether_string_or_dict():
    """Nova's 07-20 span nests provider/model/auth; the others are flat strings."""
    assert _desc("gemini_cli/gemini-2.5-flash") == "gemini_cli/gemini-2.5-flash"
    assert _desc({"provider": "agy_cli", "model": "gemini-3.5-flash",
                  "auth": "subscription"}) == "agy_cli/gemini-3.5-flash/subscription"
    assert _desc(None) == ""


def test_rare_scenes_survive_the_volume_cap():
    """The API pins transitions and arrivals past the tail cut."""
    from web.server import _KEEP_ALWAYS
    scenes = ([{"t": float(i), "kind": "reflect"} for i in range(500)]
              + [{"t": 0.5, "kind": "transition"}])
    scenes.sort(key=lambda s: s["t"])
    n = 400
    tail, dropped = scenes[-n:], scenes[:-n]
    pinned = [s for s in dropped if s.get("kind") in _KEEP_ALWAYS]
    assert pinned, "the oldest transition must not be lost to a volume cap"
    assert "transition" in _KEEP_ALWAYS and "arrival" in _KEEP_ALWAYS
