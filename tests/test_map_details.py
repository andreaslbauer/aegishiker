"""Offline tests for downloaded map details: contours, OSM features, trail alignment, POI
correction, pack re-import on version change, and basemap rendering."""
import math
import os
from pathlib import Path

from src.core import map_details as md
from src.core.database import Database
from src.core.pack_manager import seed_packs_into_db
from src.hiking.models import DestinationPack, MapFeature, POI, TrailSegment


def _way(wid, coords, **tags):
    return {"type": "way", "id": wid, "tags": tags, "geometry": [{"lon": lo, "lat": la} for lo, la in coords]}


def _node(nid, lon, lat, **tags):
    return {"type": "node", "id": nid, "lon": lon, "lat": lat, "tags": tags}


def test_contours_trace_closed_rings_around_a_cone() -> None:
    n = 41
    grid = [[1000.0 - 25.0 * math.hypot(r - 20, c - 20) for c in range(n)] for r in range(n)]
    bbox = (44.0, -71.0, 44.04, -70.95)
    lines = md.compute_contours(grid, bbox, interval=100.0)
    levels = sorted({ln.ele_m for ln in lines})
    assert levels[-4:] == [600.0, 700.0, 800.0, 900.0]
    for ln in lines:
        if ln.ele_m >= 600:  # lower levels are cut by the grid edge
            assert ln.coords[0] == ln.coords[-1], "contours around a summit are closed rings"
        for lon, lat in ln.coords:
            assert -71.0 <= lon <= -70.95 and 44.0 <= lat <= 44.04
    assert md.choose_contour_interval(300, 1900, "ft") == 200 * 0.3048
    assert md.choose_contour_interval(300, 500, "m") == 10.0


def test_build_features_classifies_hiker_details() -> None:
    elements = [
        _way(1, [(-71.0, 44.0), (-71.001, 44.001), (-71.002, 44.0015)], highway="path", name="Ridge Path",
             sac_scale="demanding_mountain_hiking"),
        _way(2, [(-71.0, 44.0), (-71.001, 44.0), (-71.001, 44.001), (-71.0, 44.0)], natural="water", name="Pond"),
        _node(3, -71.0005, 44.0005, natural="spring", name="Cold Spring"),
        _node(4, -71.0006, 44.0006, natural="peak", name="Mount Test", ele="1600"),
        {"type": "way", "id": 5, "tags": {"tourism": "alpine_hut", "name": "Test Hut", "capacity": "40"},
         "center": {"lon": -71.0007, "lat": 44.0007}},
        _node(6, -71.0, 44.0, amenity="bench"),
    ]
    feats = {f.name or f.kind: f for f in md.build_features(elements, 44.0)}
    assert feats["Ridge Path"].kind == "path" and feats["Ridge Path"].geom == "line"
    assert "SAC T3" in feats["Ridge Path"].info
    assert feats["Pond"].kind == "water" and feats["Pond"].geom == "area"
    assert "treat" in feats["Cold Spring"].info
    assert feats["Mount Test"].ele_m == 1600.0
    assert feats["Test Hut"].kind == "hut" and "capacity 40" in feats["Test Hut"].info
    assert "bench" not in feats and len(feats) == 5


def test_align_trail_follows_named_osm_path_and_moves_poi() -> None:
    # Real "Falls Trail" curves north; the sketch is a straight line 150 m south of it
    real = [(-71.0 + i * 0.001, 44.0 + 0.0015 * math.sin(i / 10 * math.pi)) for i in range(11)]
    decoy = [(-71.0, 44.0), (-71.005, 43.999), (-71.01, 44.0)]
    elements = [
        _way(1, real, highway="path", name="Falls Trail"),
        _way(2, decoy, highway="path", name="Other Trail"),
        _node(3, real[-1][0], real[-1][1], natural="peak", name="Test Peak"),
    ]
    sketch = [[-71.0 + i * 0.0005, 43.9995, 500.0] for i in range(21)]  # 2 anchor legs of 10 vertices
    pack = DestinationPack(
        id="t", name="t", min_lat=43.99, max_lat=44.01, min_lon=-71.01, max_lon=-70.98,
        trails=[TrailSegment(id="t1", name="Falls Trail to Test Peak", coords_3d=sketch, distance_m=800.0)],
        pois=[POI(id="p1", name="Test Peak Summit", kind="peak", lat=43.9995, lon=-70.99)],
    )
    moved = md.correct_pois_from_osm(pack, elements, lambda la, lo: 777.0)
    assert pack.pois[0].lon == round(real[-1][0], 6) and pack.pois[0].ele_m == 777.0
    aligned, skipped = md.align_trails_to_osm(pack, elements, lambda la, lo: 500.0 + (la - 44.0) * 1e5,
                                              anchor_fixes=moved)
    assert (aligned, skipped) == (1, [])
    trail = pack.trails[0]
    assert "osm_aligned" in trail.tags
    assert max(c[1] for c in trail.coords_3d) > 44.001, "geometry now follows the curving real trail"
    assert trail.elevation_gain_m > 100


def test_seed_reimports_pack_when_version_changes(temp_app_dir: Path, monkeypatch) -> None:
    from src.core import paths

    packs_dir = temp_app_dir / "bundled"
    packs_dir.mkdir()
    monkeypatch.setattr(paths, "BUNDLED_PACKS_DIR", packs_dir)
    monkeypatch.setattr(paths, "PACKS_USER_DIR", temp_app_dir / "none")
    pack = DestinationPack(id="vtest", name="V", trails=[TrailSegment(id="v1", name="T",
                           coords_3d=[[-71, 44, 1], [-71.01, 44, 1]])])
    (packs_dir / "v.json").write_text(pack.model_dump_json())
    db = Database(temp_app_dir / "v.db")
    seed_packs_into_db(db)
    assert db.get_destination_pack("vtest").features == []

    pack.pack_version = "1.0-map2"
    pack.features = [MapFeature(kind="peak", name="P", coords=[[-71.0, 44.0]], ele_m=1000.0)]
    pack.contour_interval_m = 60.96
    (packs_dir / "v.json").write_text(pack.model_dump_json())
    seed_packs_into_db(db)
    stored = db.get_destination_pack("vtest")
    assert stored.pack_version == "1.0-map2"
    assert stored.features[0].name == "P" and stored.contour_interval_m == 60.96
    db.close()


def test_map_canvas_renders_bundled_basemap(seeded_db: Database) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.charts.map_canvas import MapCanvas

    app = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.sync_basemap(seeded_db, "white_mountains_nh")
    assert canvas.basemap_pack_id == "white_mountains_nh"
    assert canvas._relief is not None and canvas._point_features and "path" in canvas._line_paths
    canvas.set_scene_data(seeded_db.list_trails("white_mountains_nh"), seeded_db.list_pois("white_mountains_nh"))
    lat, lon = 44.15, -71.66
    assert all(abs(a - b) < 1e-9 for a, b in zip(canvas.unproject(canvas.project(lat, lon)), (lat, lon)))
    assert not canvas.grab().isNull()
    app.processEvents()


def test_map_reuses_its_rendered_base_while_panning() -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pathlib import Path

    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    from src.gui.charts.map_canvas import MapCanvas
    from src.hiking.models import DestinationPack

    app = QApplication.instance() or QApplication([])
    pack_file = Path(__file__).resolve().parent.parent / "data" / "packs" / "mount_monadnock.json"
    pack = DestinationPack.model_validate_json(pack_file.read_text(encoding="utf-8"))
    canvas = MapCanvas()
    canvas.resize(800, 500)
    canvas.show()
    canvas.set_basemap(pack)
    canvas.set_scene_data(trails=pack.trails, pois=pack.pois)
    renders = []
    original = canvas._render_base
    canvas._render_base = lambda xf, key: (renders.append(1), original(xf, key))
    target = QPixmap(canvas.size())
    canvas.render(target)
    assert len(renders) == 1
    canvas.set_preview([(42.86, -72.11), (42.87, -72.10)])  # overlays only: no re-render
    canvas.render(target)
    assert len(renders) == 1
    canvas.pan_offset += QPointF(30, 12)
    canvas._interacted()  # dragging: the cached map is shown moved
    canvas.render(target)
    assert len(renders) == 1
    canvas._settled()  # the view settled: drawn sharp once
    canvas.render(target)
    assert len(renders) == 2
    canvas.excluded_conditions = {"muddy"}  # what the base shows changed
    canvas.render(target)
    assert len(renders) == 3
    canvas.deleteLater()
    app.processEvents()
