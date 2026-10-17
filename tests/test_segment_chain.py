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
