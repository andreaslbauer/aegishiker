"""3D hike replay: projection, chase camera, and the photo zoom-in / linger / zoom-out sequence."""
import math
import os
from pathlib import Path

from src.hiking.terrain3d import Camera, LocalFrame, build_mesh, chase_camera, route_path


def test_projection_and_chase_camera_above_terrain() -> None:
    cam = Camera(0.0, 0.0, 100.0, yaw=0.0, pitch=0.0, focal=500.0, cx=400.0, cy=300.0)
    project = cam.projector()
    sx, sy, depth = project(1000.0, 0.0, 100.0)  # straight ahead (east)
    assert (round(sx), round(sy), round(depth)) == (400, 300, 1000)
    assert project(1000.0, -100.0, 100.0)[0] > 400, "south is to the right when facing east"
    assert project(-1000.0, 0.0, 100.0) is None, "behind the camera"

    ridge = lambda x, y: 5000.0 if x < 0 else 0.0  # noqa: E731 — a wall behind the hiker
    cam2 = chase_camera((0.0, 0.0, 0.0), 0.0, 800, 600, ground_at=ridge)
    assert cam2.z >= 5000.0 + 150.0 - 1e-6 and cam2.pitch > math.radians(60)

    frame = LocalFrame(44.0, -71.0)
    mesh = build_mesh(None, frame, (-500, -500, 500, 500), cell_m=100.0, fallback_ele=300.0)
    assert mesh.height_at(0, 0) == 300.0 and len(mesh.color) == mesh.rows - 1
    path = route_path([[-71.0, 44.0, 300.0], [-70.99, 44.0, 400.0]], frame)
    assert 790 < path.length < 810 and abs(path.at(path.length / 2)[3] - 350.0) < 1.0


def _view(tmp_path: Path, n_photos: int = 1, spacing_m: float = 0.0, seconds_apart: int = 60):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtWidgets import QApplication

    from src.gui.replay3d import Replay3DView
    from src.hiking.models import DestinationPack, HikePhotoPin
    from src.hiking.routing import route_from_trail_segment

    QApplication.instance() or QApplication([])
    pack_file = Path(__file__).resolve().parent.parent / "data" / "packs" / "mount_monadnock.json"
    pack = DestinationPack.model_validate_json(pack_file.read_text(encoding="utf-8"))
    trail = next(t for t in pack.trails if t.name == "White Dot Trail")
    route = route_from_trail_segment(trail)
    img = tmp_path / "summit.jpg"
    Image.new("RGB", (600, 400), (120, 160, 200)).save(img)
    c = route.coords_3d[len(route.coords_3d) // 2]
    pins = [
        HikePhotoPin(id=f"p{i}", lat=c[1], lon=c[0], photo_path=str(img), date_taken=1755180000 + i * seconds_apart,
                     distance_along_m=route.distance_m / 2 + i * spacing_m, caption=f"Photo {i}")
        for i in range(n_photos)
    ]
    view = Replay3DView(route, pack, pins, "us")
    view.resize(800, 500)
    return view, route


def test_replay_eases_into_stop_blends_photo_and_back_out(tmp_path: Path) -> None:
    from src.gui.replay3d import APPROACH_M, BLEND_IN_S, BLEND_OUT_S, DEPART_M, FIRST_HOLD_S, MIN_SPEED_FACTOR

    view, route = _view(tmp_path)
    dup = view.stops[0].pin.model_copy(update={"id": "dup"})
    assert len(view.stops) == 1 and len(view.stops[0].items) == 1 and dup.id != view.stops[0].pin.id
    full = view.wait_for_photo(0)
    assert full is not None and full.width() == 600, "full-size photo for the stop, not the thumbnail"
    assert not view.grab().isNull()

    stop_at = view.stops[0].along_m
    view.seek(0.0)
    assert view.speed_factor() == 1.0, "full speed far from any photo"
    view.dist = stop_at - view._ease_m(APPROACH_M) / 3  # (slow-down zones shrink on short hikes)
    near = view.speed_factor()
    assert MIN_SPEED_FACTOR <= near < 0.6, "slowing down while approaching the photo"
    cam_far = view._camera(800, 500)
    view.dist = stop_at - 2.0
    assert view.speed_factor() <= MIN_SPEED_FACTOR + 0.02, "crawling, never stopped dead, right at it"

    view.dist = stop_at - 30.0
    view.phase = "playing"
    while view.phase == "playing":
        view.advance(0.25)
    assert view.phase == "blend_in" and abs(view.dist - stop_at) < 5.0
    view.advance(BLEND_IN_S * 0.5)
    assert 0.2 < view._blend() < 0.8, "the photo blends in gradually"
    assert not view.grab().isNull()
    phases = []
    for _ in range(int((BLEND_IN_S + FIRST_HOLD_S + BLEND_OUT_S) / 0.25) + 6):
        view.advance(0.25)
        phases.append(view.phase)
    assert "hold" in phases and "blend_out" in phases and phases[-1] == "playing", phases
    assert view.next_stop == 1
    view.dist = stop_at + view._ease_m(DEPART_M) / 12
    assert view.speed_factor() < 0.6, "speeding back up gradually after the photo"
    view.dist = stop_at + 400.0
    assert view.speed_factor() == 1.0

    while view.phase == "playing":
        view.advance(1.0)
    assert view.phase == "done" and abs(view.dist - view.path.length) < 1e-6
    view.seek(0.0)
    assert view.dist == 0.0 and view.next_stop == 0
    _ = cam_far
    view.shutdown()


def test_photos_close_together_share_one_stop_and_cross_fade(tmp_path: Path) -> None:
    from src.gui.replay3d import BLEND_IN_S, FIRST_HOLD_S, NEXT_HOLD_S, XFADE_S

    view, _route = _view(tmp_path, n_photos=3, spacing_m=30.0)
    assert len(view.stops) == 1 and len(view.stops[0].items) == 3
    view.dist = view.stops[0].along_m
    view.phase, view.phase_t, view.photo_k = "blend_in", 0.0, 0
    view.advance(BLEND_IN_S + 0.01)
    assert view.phase == "hold" and view.photo_k == 0
    view.advance(FIRST_HOLD_S - XFADE_S / 2)  # in the middle of the cross-fade to photo 2
    assert view.phase == "hold" and view.photo_k == 0 and not view.grab().isNull()
    view.advance(XFADE_S)
    assert view.photo_k == 1
    view.advance(NEXT_HOLD_S + 0.01)
    assert view.photo_k == 2
    # Skipping mid-blend reverses from where it is (no jump)
    view.phase, view.phase_t = "blend_in", BLEND_IN_S * 0.25
    view.skip_photo()
    assert view.phase == "blend_out" and abs(view._blend() - smooth_quarter()) < 0.05
    view.shutdown()


def smooth_quarter() -> float:
    from src.hiking.terrain3d import smoothstep

    return smoothstep(0.25)


def test_replay_plan_groups_by_distance_or_time() -> None:
    from src.hiking.models import HikePhotoPin
    from src.hiking.replay_plan import plan_photo_stops

    def pin(along, ts, star=False):
        return HikePhotoPin(lat=0, lon=0, distance_along_m=along, date_taken=ts, is_starred=star)

    pins = [pin(100, 1000), pin(150, 5000), pin(900, 1100),  # close in time but far along: separate
            pin(2000, 9000), pin(2600, 9100)]  # within 3 min but 600 m apart: span cap splits them
    plans = plan_photo_stops(pins)
    assert [len(p.all_pins) for p in plans] == [2, 1, 1, 1]
    many = [pin(5000 + i, 20000 + i * 30, star=(i == 17)) for i in range(20)]
    (big,) = plan_photo_stops(many, cap=6)
    assert len(big.shown) == 6 and big.hidden == 14 and any(p.is_starred for p in big.shown)
    assert [p.date_taken for p in big.shown] == sorted(p.date_taken for p in big.shown)


def test_map_layers_are_draped_on_the_replay_terrain() -> None:
    from src.hiking.models import DEMGrid, DestinationPack, MapFeature, TrailSegment
    from src.hiking.terrain3d import LocalFrame, build_mesh, replay_map_layers

    dem = DEMGrid(min_lat=44.0, max_lat=44.1, min_lon=-71.7, max_lon=-71.6, rows=3, cols=3,
                  elevations_m=[[300, 400, 500]] * 3)
    other = TrailSegment(id="other", name="Other Trail", coords_3d=[[-71.66, 44.04, 0], [-71.64, 44.06, 0]])
    mine = TrailSegment(id="mine", name="My Trail", coords_3d=[[-71.66, 44.05, 0], [-71.64, 44.05, 0]])
    pond = [[-71.652, 44.048], [-71.648, 44.048], [-71.648, 44.052], [-71.652, 44.052], [-71.652, 44.048]]
    pack = DestinationPack(id="p", name="P", dem=dem, trails=[other, mine], features=[
        MapFeature(kind="water", geom="area", name="Pond", coords=pond),
        MapFeature(kind="stream", geom="line", coords=[[-71.66, 44.044], [-71.64, 44.044]]),
        MapFeature(kind="path", geom="line", name="My Trail", coords=[[-71.66, 44.05], [-71.64, 44.05]]),
        MapFeature(kind="path", geom="line", coords=[[-71.656, 44.055], [-71.656, 44.06]]),
    ])
    frame = LocalFrame(44.05, -71.65, 1.5)
    mesh = build_mesh(dem, frame, (-1500, -1500, 1500, 1500), cell_m=120.0)
    layers = replay_map_layers(pack, mesh, skip_trail_ids=["mine"])
    assert {"trail", "stream", "path"} <= set(layers.kinds)
    assert layers.kinds.count("path") > 0 and "water_edge" not in layers.kinds, "pond drawn as a surface"
    # The path that is a piece of My Trail and the hike's own trail aren't drawn again
    xs_trail = [s for s, k in zip(layers.segs, layers.kinds) if k == "trail"]
    assert all(abs(s[1] - s[4]) > 0 for s in xs_trail), "only the diagonal Other Trail"
    pieces = sum(len(r) for r in layers.water.values())
    assert pieces >= 4, "the pond is cut into the terrain cells it covers"
    for (r, c), rings in layers.water.items():
        for ring in rings:
            for x, y, z in ring:  # every piece lies within its cell, on the terrain
                assert mesh.x0 + c * 120.0 - 1e-6 <= x <= mesh.x0 + (c + 1) * 120.0 + 1e-6
                assert abs(z - (mesh.height_at(x, y) + 3.0)) < 1e-6
    seg = layers.segs[0]
    assert abs(seg[2] - (mesh.height_at(seg[0], seg[1]) + 6.0)) < 1e-6, "lines lie just above the ground"
