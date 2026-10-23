"""Segment-by-segment hike building: forks mid-segment, junctions at the end, out-and-back,
loop completion, naming and undo."""
from src.hiking.models import TrailSegment
from src.hiking.segment_chain import SegmentChain


def _line(tid, name, pts):
    return TrailSegment(id=tid, name=name, coords_3d=[[lon, lat, ele] for lon, lat, ele in pts])


def _network():
    # A runs east ~1.2 km (vertices ~120 m apart); B forks north off A at vertex 6; C returns from B's end to A's start.
    a = _line("a", "Brook Trail", [(-71.0 + i * 0.0015, 44.0, 300 + i * 10) for i in range(11)])
    b = _line("b", "Ridge Trail (via the ledges)", [(-70.991, 44.0 + i * 0.0015, 360 + i * 30) for i in range(9)])
    c_pts = [(-70.991 + (i / 10) * (-71.0 + 70.991), 44.012 - (i / 10) * 0.012, 600 - i * 30) for i in range(11)]
    c = _line("c", "Return Path", c_pts)
    return [a, b, c], [("Summit Ledge", -70.991, 44.012), ("Trailhead", -71.0, 44.0)]


def test_fork_cuts_current_segment_and_loop_completes() -> None:
    trails, places = _network()
    chain = SegmentChain(trails, places)
    chain.start(trails[0])
    cands = chain.candidates()
    fork = next(c for c in cands if c.leg.trail.id == "b" and c.leg.end > c.leg.start)
    assert 650 < fork.along_m < 800 and fork.toward_place == "Summit Ledge"  # vertex 6, ~120 m apart
    assert any(c.turnaround for c in cands), "turning around is always offered"

    chain.apply(fork)
    assert chain.legs[0].end == 6, "Brook Trail is cut at the fork"
    back = next(c for c in chain.candidates() if c.leg.trail.id == "c")
    assert back.at_place == "Summit Ledge"
    chain.apply(back)
    assert chain.is_closed() and chain.complete and chain.candidates() == []
    assert chain.shape() == "loop"
    assert chain.proposed_name() == "Brook Trail → Ridge Trail → Return Path (Loop)"

    route = chain.to_route("", "test_pack")
    assert route.trail_type == "loop" and route.trail_ids == ["a", "b", "c"]
    assert route.distance_m > 2500 and route.elevation_gain_m > 200

    chain.undo()
    assert [leg.trail.id for leg in chain.legs] == ["a", "b"] and not chain.complete
    chain.undo()
    assert [leg.trail.id for leg in chain.legs] == ["a"] and chain.legs[0].end == 10, "undo restores the cut"


def test_out_and_back_and_end_here() -> None:
    trails, places = _network()
    chain = SegmentChain(trails, places)
    chain.start(trails[0])
    turn = next(c for c in chain.candidates() if c.turnaround)
    chain.apply(turn)
    assert chain.shape() == "out_and_back"
    assert chain.proposed_name() == "Brook Trail (Out & Back)"

    chain.undo()
    chain.end_here()
    assert chain.complete and chain.shape() == "segment" and chain.candidates() == []
    assert chain.proposed_name() == "Brook Trail"


def test_reverse_start_walks_trail_backwards() -> None:
    trails, places = _network()
    chain = SegmentChain(trails, places)
    chain.start(trails[1], reverse=True)
    pts = chain.coords()
    assert pts[0][1] > pts[-1][1], "starts at the north end of Ridge Trail"


def test_map_hit_testing_for_trails_and_options() -> None:
    trails, places = _network()
    chain = SegmentChain(trails, places)
    # A click 10 m south of Brook Trail near its east end: starts there, walking it backwards
    hits = chain.trails_at(43.99991, -70.9862, tol_m=20.0)
    assert [t.id for t, _rev, _d in hits] == ["a"] and hits[0][1] is True
    assert chain.trails_at(43.99, -70.9862, tol_m=20.0) == []

    chain.start(trails[0])
    cands = chain.candidates()
    # A click on Ridge Trail well north of the fork selects that option
    idx = chain.candidates_at(cands, 44.006, -70.991, tol_m=20.0)
    assert idx and cands[idx[0]].leg.trail.id == "b"


def test_trail_ending_near_another_connects_without_duplicates() -> None:
    trails, places = _network()
    # Ledge Spur ends ~50 m short of Brook Trail's east end (mapped paths stop apart on open rock)
    spur = _line("d", "Ledge Spur", [(-70.9853 + i * 0.001, 44.00045 + i * 0.001, 420 + i * 20) for i in range(6)])
    chain = SegmentChain(trails + [spur], places)
    chain.start(trails[0])
    names = [c.trail_name for c in chain.candidates()]
    assert "Ledge Spur" in names
    assert len(names) == len(set((c.trail_name, c.leg.end, c.turnaround) for c in chain.candidates())), names


def test_monadnock_summit_connects_pumpelly_to_smith_summit() -> None:
    """Regression: on Monadnock's bare summit the mapped trails end ~50 m apart."""
    from pathlib import Path

    from src.hiking.models import DestinationPack

    pack_file = Path(__file__).resolve().parent.parent / "data" / "packs" / "mount_monadnock.json"
    pack = DestinationPack.model_validate_json(pack_file.read_text(encoding="utf-8"))
    by_name = {t.name: t for t in pack.trails}
    chain = SegmentChain(pack.trails, [])
    chain.start(by_name["Pumpelly Trail"])  # Dublin side up to the summit
    names = [c.trail_name for c in chain.candidates()]
    assert {"Smith Summit Trail", "Dublin Trail", "White Dot Trail", "White Arrow Trail"} <= set(names)
    assert names.count("Smith Summit Trail") == 1


def test_clicked_trail_offered_via_network_or_as_jump() -> None:
    trails, places = _network()
    # Pond Path: not touching anything, its west end ~120 m east of Brook Trail's east end
    pond = _line("p", "Pond Path", [(-70.9835 + i * 0.001, 44.0006, 300.0) for i in range(6)])
    chain = SegmentChain(trails + [pond], places)
    chain.start(trails[2])  # Return Path: Ridge top → trailhead (Brook Trail's start)
    assert "Pond Path" not in {c.trail_name for c in chain.candidates()}

    # Ridge Trail isn't an option from the trailhead; its lower part is reached via Brook Trail
    ridge = trails[1]
    top = ridge.coords_3d[2]
    routed = chain.candidate_to(ridge, top[1], top[0])
    assert routed is not None and routed.picked and routed.gap_m == 0
    assert routed.trail_name == "Ridge Trail (via the ledges)" and routed.via_names == ["Brook Trail"]

    chain.apply(routed)
    assert [leg.trail.id for leg in chain.legs] == ["c", "a", "b"]
    end = chain.legs[-1].coords()[-1]
    assert abs(end[1] - top[1]) < 1e-9, "the routed option ends where the user clicked"

    # From Brook Trail's east end, the unconnected Pond Path is close: offered as a jump
    chain.start(trails[0])
    p2 = pond.coords_3d[3]
    jump = chain.candidate_to(pond, p2[1], p2[0])
    assert jump is not None and 80 < jump.gap_m < 200 and jump.leg.trail.id == "p"
    chain.apply(jump)
    assert chain.legs[-1].trail.id == "p" and chain.legs[-1].end == 3, "the jump ends where the user clicked"


def test_clicked_trail_leaves_current_segment_at_the_intersection() -> None:
    trails, places = _network()
    chain = SegmentChain(trails, places)
    chain.start(trails[0])  # Brook Trail, all the way east
    ridge = trails[1]
    spot = ridge.coords_3d[4]
    cand = chain.candidate_to(ridge, spot[1], spot[0])
    assert cand is not None and cand.gap_m == 0 and not cand.via_names
    chain.apply(cand)
    assert [leg.trail.id for leg in chain.legs] == ["a", "b"]
    assert chain.legs[0].end == 6, "Brook Trail is left at the fork, not walked to its end and back"
    assert (chain.legs[1].start, chain.legs[1].end) == (0, 4), "Ridge Trail is walked to the clicked spot"


def test_clicked_option_ends_at_the_click_and_can_continue() -> None:
    trails, places = _network()
    chain = SegmentChain(trails, places)
    chain.start(trails[0])
    fork = next(c for c in chain.candidates() if c.leg.trail.id == "b" and c.leg.end > c.leg.start)
    spot = trails[1].coords_3d[3]
    short = chain.shorten_to(fork, spot[1], spot[0])
    assert short.leg.end == 3 and short.distance_m < fork.distance_m
    chain.apply(short)
    assert chain.legs[0].end == 6 and chain.legs[1].end == 3
    more = next(c for c in chain.candidates() if c.continues)
    assert more.trail_name == "Ridge Trail (via the ledges)" and more.leg.end == 8
    chain.apply(more)
    assert [leg.trail.id for leg in chain.legs] == ["a", "b"] and chain.legs[1].end == 8, "one leg, not two"


def test_map_paths_fill_gaps_but_are_not_options() -> None:
    from src.hiking.models import MapFeature
    from src.hiking.segment_chain import map_paths_from_features

    # Brook Trail ends at its east end; an unnamed map path continues east to "Far Trail", which
    # is ~1.8 km away and not connected to any listed trail
    a = _line("a", "Brook Trail", [(-71.0 + i * 0.0015, 44.0, 300) for i in range(11)])
    far = _line("far", "Far Trail", [(-70.962 + i * 0.0015, 44.0, 320) for i in range(6)])
    feats = [MapFeature(kind="path", geom="line", name="", coords=[[-70.985, 44.0], [-70.962, 44.0]])]
    paths = map_paths_from_features(feats, None, "p")
    assert len(paths) == 1 and len(paths[0].coords_3d) > 50, "densified for junctions"

    chain = SegmentChain([a, far], [], map_paths=paths)
    chain.start(a)
    offered = {c.trail_name for c in chain.candidates()}
    assert "Unnamed path" in offered, "smaller paths off the trail are offered too"
    assert chain.trails_at(44.0, -70.975, 30)[0][0].name == "Unnamed path", "clickable where no trail is"
    assert chain.trails_at(44.0, -70.99, 30)[0][0].id == "a", "listed trails win where both are"

    cand = chain.candidate_to(far, 44.0, -70.956)
    assert cand is not None and cand.gap_m == 0, "routed over the map path, no straight-line jump"
    assert cand.trail_name == "Far Trail" and cand.via_names == ["Unnamed path"]
    chain.apply(cand)
    assert chain.proposed_name() == "Brook Trail → Far Trail", "unnamed paths stay out of the name"
    assert chain.to_route("", "p").trail_ids == ["a", "far"]


def test_stop_points_are_junctions_and_ends() -> None:
    trails, places = _network()
    chain = SegmentChain(trails, places)
    chain.start(trails[2])  # Return Path, from the ridge top down to the trailhead
    turn = next(c for c in chain.candidates() if c.turnaround)
    assert [sp.kind for sp in chain.stop_points(turn)] == ["end"]
    chain.start(trails[1], reverse=True)  # Ridge Trail down to Brook Trail
    brook = next(c for c in chain.candidates() if c.leg.trail.id == "a" and c.leg.end == 10)
    stops = chain.stop_points(brook)
    assert stops[-1].kind == "end" and stops[-1].k == len(brook.leg.coords()) - 1
    short = chain.shorten_at(brook, stops[-1].k - 2)
    assert short.leg.end == 8 and short.distance_m < brook.distance_m


def test_map_path_copies_of_listed_trails_are_not_offered_twice() -> None:
    from src.hiking.models import MapFeature
    from src.hiking.segment_chain import map_paths_from_features

    a = _line("a", "Brook Trail", [(-71.0 + i * 0.0015, 44.0, 300) for i in range(11)])
    b = _line("b", "Ridge Trail", [(-70.991, 44.0 + i * 0.0015, 360 + i * 30) for i in range(9)])
    feats = [
        MapFeature(kind="path", geom="line", name="Ridge Trail", coords=[[-70.991, 44.0], [-70.991, 44.012]]),
        MapFeature(kind="path", geom="line", name="", info="SAC T1 hiking",
                   coords=[[-70.994, 44.0], [-70.994, 43.995]]),
    ]
    chain = SegmentChain([a, b], [], map_paths=map_paths_from_features(feats, None, "p"))
    chain.start(a)
    names = [c.trail_name for c in chain.candidates() if not c.turnaround]
    assert names.count("Ridge Trail") == 1, names
    assert "Unnamed path (SAC T1 hiking)" in names, "unnamed side paths are offered, told apart by their map info"


def test_minor_and_unnamed_paths_can_be_switched_off_and_started_on() -> None:
    from src.hiking.models import MapFeature
    from src.hiking.segment_chain import map_paths_from_features

    a = _line("a", "Brook Trail", [(-71.0 + i * 0.0015, 44.0, 300) for i in range(11)])
    feats = [
        MapFeature(kind="path", geom="line", name="Herd Path", coords=[[-70.991, 44.0], [-70.991, 44.006]]),
        MapFeature(kind="path", geom="line", name="", coords=[[-70.994, 44.0], [-70.994, 43.995]]),
    ]
    paths = map_paths_from_features(feats, None, "p")
    assert 640 < paths[0].distance_m < 700, "map paths know their length"
    chain = SegmentChain([a], [], map_paths=paths)
    assert [t.name for t in chain.minor_paths()] == ["Herd Path"], "named minor paths can be listed to start on"
    chain.start(a)
    names = {c.trail_name for c in chain.candidates()}
    assert {"Herd Path", "Unnamed path"} <= names

    chain.include_map_paths = False
    names = {c.trail_name for c in chain.candidates()}
    assert "Herd Path" not in names and "Unnamed path" not in names, "only listed trails"
    assert chain.trails_at(44.004, -70.991, 30) == [], "map paths not clickable when left out"

    chain.include_map_paths = True
    chain.start(chain.minor_paths()[0], reverse=True)  # down the herd path to Brook Trail
    assert chain.legs[0].trail.name == "Herd Path" and any(c.leg.trail.id == "a" for c in chain.candidates())
