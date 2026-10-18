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


def test_replay_runs_through_photo_sequence(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtWidgets import QApplication

    from src.gui.replay3d import LINGER_S, ZOOM_S, Replay3DView
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
    pin = HikePhotoPin(id="p", lat=c[1], lon=c[0], photo_path=str(img), date_taken=1755180000,
                       distance_along_m=route.distance_m / 2, caption="Halfway")
    dup = pin.model_copy(update={"id": "p-dup"})  # same photo saved twice: shown once
    view = Replay3DView(route, pack, [pin, dup], "us")
    view.resize(800, 500)
    assert len(view.stops) == 1 and view.stops[0].pixmap is not None
    assert not view.grab().isNull()

    view.phase = "playing"
    while view.phase == "playing":
        view.advance(0.5)
    assert view.phase == "zoom_in" and abs(view.dist - route.distance_m / 2) < view.speed_mps * 0.5 + 1
    phases = []
    for _ in range(int((2 * ZOOM_S + LINGER_S) / 0.25) + 4):
        view.advance(0.25)
        phases.append(view.phase)
    assert "linger" in phases and phases[-1] == "playing", phases
    assert sum(1 for ph in phases if ph == "linger") * 0.25 >= LINGER_S - 0.5
    assert not view.grab().isNull()

    while view.phase == "playing":
        view.advance(1.0)
    assert view.phase == "done" and abs(view.dist - view.path.length) < 1e-6
    view.seek(0.0)
    assert view.dist == 0.0 and view.next_stop == 0
