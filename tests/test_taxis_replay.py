"""Taxis offline battery — the organ must reproduce the v3-validated harness
gate semantics (research/physis-mud/run.py, GATED-LIVE-v3 C1=+2.500), byte-
faithfully where the reference emits strings. This is the OFFLINE proxy for
falsification P1 (taxis-only ≈ gate-only); the live 2×2 waits for Ray's lock.
"""
from ludex.blocks.taxis import TaxisBlock, DEFAULT_K

F = "lxm/mud/testzone"


def _b():
    return TaxisBlock()


def _plateau_to_k(b, k=DEFAULT_K, sig="room A: nothing changes"):
    b.handle_observe_progress(F, sig)          # first sight: new → plateau 0
    for _ in range(k):
        b.handle_observe_progress(F, sig)      # repeats → plateau climbs to k
    return b


# ---------- plateau mechanics (reference: run.py lines 152-156) ----------

def test_plateau_counts_and_resets_on_new_state():
    b = _b()
    assert b.handle_observe_progress(F, "sig1")["plateau"] == 0     # new
    assert b.handle_observe_progress(F, "sig1")["plateau"] == 1     # repeat
    assert b.handle_observe_progress(F, "sig1")["plateau"] == 2
    assert b.handle_observe_progress(F, "sig2")["plateau"] == 0     # new → reset


def test_below_k_no_directive():
    b = _b()
    b.handle_observe_progress(F, "s")
    b.handle_observe_progress(F, "s")          # plateau 1 < 3
    d = b.handle_directive(F, frontier=[("A", "north")], here="A")
    assert d["line"] == "" and not d["fired"]


# ---------- 3-branch semantics (reference: run.py lines 158-203) ----------

def test_explore_branch_names_here_exit_byte_faithful():
    b = _plateau_to_k(_b())
    d = b.handle_directive(F, frontier=[("A", "north"), ("B", "east")], here="A")
    assert d["branch"] == "explore" and d["fired"]
    assert d["line"] == ("[Explore] Nothing new for 3 turns. "
                         "Untried exit: 'north' from where you now stand. "
                         "Go and take it now.")


def test_redirect_branch_points_back():
    b = _plateau_to_k(_b())
    d = b.handle_directive(F, frontier=[("B", "east")], here="A")   # nothing here
    assert d["branch"] == "redirect"
    assert d["line"] == ("[Explore] Nothing new for 3 turns and no "
                         "untried exits in this room. Return toward B "
                         "and take its 'east' exit.")


def test_commit_latch_fires_once_and_rearms_on_new_state():
    b = _plateau_to_k(_b())
    d1 = b.handle_directive(F, frontier=[], here="A")               # exhausted
    assert d1["branch"] == "commit" and d1["line"].startswith("[Commit]")
    b.handle_observe_progress(F, "room A: nothing changes")         # still plateaued
    d2 = b.handle_directive(F, frontier=[], here="A")
    assert d2["line"] == "" and not d2["fired"]                     # LATCHED silent
    b.handle_observe_progress(F, "room B: fresh sight")             # new state → re-arm
    _plateau_to_k(b, sig="room B: fresh sight")
    d3 = b.handle_directive(F, frontier=[], here="B")
    assert d3["branch"] == "commit" and d3["fired"]                 # new episode fires


def test_locked_exits_skipped_yields_commit():
    b = _plateau_to_k(_b())
    d = b.handle_directive(F, frontier=[("A", "locked iron door")], here="A")
    assert d["branch"] == "commit"       # locked-only frontier = exhausted (v3 rule)


def test_rotation_fresh_first_then_least_named():
    b = _plateau_to_k(_b())
    fr = [("A", "north"), ("A", "south")]
    first = b.handle_directive(F, frontier=fr, here="A")
    second = b.handle_directive(F, frontier=fr, here="A")
    assert "north" in first["line"] and "south" in second["line"]   # fresh-first
    third = b.handle_directive(F, frontier=fr, here="A")            # both named once → least-named
    assert "north" in third["line"]


def test_no_topos_degrades_gracefully():
    b = _plateau_to_k(_b())
    d = b.handle_directive(F, frontier=None, here="")
    assert d["branch"] == "commit"       # no frontier info → exhausted path, no crash


# ---------- beyond the gate: progress latching + observed sequencing ----------

def test_goal_tags_latch_in_observed_order():
    b = _b()
    b.handle_observe_progress(F, "opened sluice", goal_tag="sluice")
    b.handle_observe_progress(F, "opened sluice")                   # repeat: no re-latch
    b.handle_observe_progress(F, "found lantern", goal_tag="lantern")
    b.handle_observe_progress(F, "found lantern AGAIN", goal_tag="lantern")  # dupe tag ignored
    s = b.handle_sense(F)
    assert s["chain"] == ["sluice", "lantern"]
    pv = b.handle_plan_view(F)
    assert "sluice  (after: start)" in pv and "lantern  (after: sluice)" in pv


def test_sense_reading_and_field_isolation():
    b = _plateau_to_k(_b())
    b.handle_directive(F, frontier=[], here="A")
    s = b.handle_sense(F)
    assert s["plateau"] >= DEFAULT_K and s["phase"] == "committed" and s["fires"] == 1
    other = b.handle_sense("lxm/mud/otherzone")
    assert other["plateau"] == 0 and other["fires"] == 0            # per-field isolation


def test_reset_field_iid_hygiene():
    b = _plateau_to_k(_b())
    b.handle_directive(F, frontier=[], here="A")
    b.handle_reset_field(F)
    s = b.handle_sense(F)
    assert s["plateau"] == 0 and s["fires"] == 0 and s["phase"] == "exploring"
