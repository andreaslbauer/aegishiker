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
    assert chain.legs[-1].trail.id == "p" and chain.legs[-1].end == 5
