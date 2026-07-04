"""Topos cognitive map (memory-systems step 3, 2026-07-04).

The spatial memory the GIVEN-MAP probe proved causal: place graph with
visited/frontier state, persisted per-field, emitted map-shaped +
frontier-explicit (the probe-validated format, no prose).
"""
from ludex.core.organism_config import OrganismConfig

FIELD = "lxm/mud/testzone"


def _org(tmp_path):
    cfg = OrganismConfig.from_preset("full", name="mapper")
    cfg.habitat.home_dir = str(tmp_path)
    cfg._ephemeral = True
    return cfg.build()


def test_observe_builds_graph_and_binds_edges(tmp_path):
    topos = _org(tmp_path).get_block("topos")
    # arrive at Study (start): exits seen but unwalked
    topos.handle_observe_place(FIELD, "Study", exits=["down", "east"])
    assert set(dict(topos.handle_frontier(FIELD))) == {"Study"}
    # walk down → Landing: the Study.down edge binds, leaves the frontier
    topos.handle_observe_place(FIELD, "Landing", exits=["up", "west"],
                               moved_from="Study", moved_dir="down")
    frontier = topos.handle_frontier(FIELD)
    assert ("Study", "down") not in frontier
    assert ("Study", "east") in frontier          # still unwalked (locked door case)
    assert ("Landing", "up") in frontier and ("Landing", "west") in frontier


def test_map_persists_across_block_instances(tmp_path):
    org1 = _org(tmp_path)
    org1.get_block("topos").handle_observe_place(FIELD, "Study", exits=["down"])
    # a fresh organism over the same habitat sees the same graph
    org2 = _org(tmp_path)
    view = org2.get_block("topos").handle_map_view(FIELD)
    assert "Study" in view and "down→?" in view


def test_map_view_is_map_shaped_and_frontier_explicit(tmp_path):
    topos = _org(tmp_path).get_block("topos")
    topos.handle_observe_place(FIELD, "Study", exits=["down", "east"])
    topos.handle_observe_place(FIELD, "Landing", exits=["up"],
                               moved_from="Study", moved_dir="down")
    view = topos.handle_map_view(FIELD)
    assert view.startswith("[Map] 2 places known")
    assert "you are at: Landing" in view
    assert "Study: down→Landing · east→?" in view
    # the frontier line is current-position-relative (reply13 phrasing)
    assert "Exits you have not walked from where you now stand: up" in view
    assert "east from Study" in view              # elsewhere-frontier listed too
    # no prose: every line is structural
    assert "you should" not in view.lower()


def test_empty_and_complete_frontiers(tmp_path):
    topos = _org(tmp_path).get_block("topos")
    assert topos.handle_map_view(FIELD) == ""     # nothing observed yet
    topos.handle_observe_place(FIELD, "Cell", exits=["north"])
    topos.handle_observe_place(FIELD, "Hall", exits=[], moved_from="Cell", moved_dir="north")
    view = topos.handle_map_view(FIELD)
    assert "Frontier: none" in view               # every seen exit walked


def test_mud_obs_parse():
    import importlib.util
    spec = importlib.util.spec_from_file_location("mudrun", "research/physis-mud/run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    room, exits = m._parse_mud_obs(
        "=== The Spiral Landing ===\nA curved stair.\n\nExits: up, west, east\nYou see: dust")
    assert room == "The Spiral Landing"
    assert exits == ["up", "west", "east"]
