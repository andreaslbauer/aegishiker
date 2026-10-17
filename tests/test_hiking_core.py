"""Unit tests for pure-Python hiking domain logic (src/hiking/)."""
from src.hiking.edits import close_loop_route, reverse_route
from src.hiking.elevation import build_elevation_profile, compute_gain_loss, summarize_elevation
from src.hiking.filters import TrailFilterCriteria, filter_trails
from src.hiking.gear import (
    estimate_calories_kcal,
    estimate_hydration_ml,
    generate_gear_checklist,
    total_pack_weight_g,
)
from src.hiking.geo import cross_track_distance_m, haversine_m, project_point_onto_polyline
from src.hiking.geojson_io import export_route_to_geojson, import_route_from_geojson
from src.hiking.gpx import export_route_to_gpx, import_route_from_gpx
from src.hiking.hike_file import load_hike_archive, save_hike_archive
from src.hiking.models import TelemetryPoint, TrailCondition, TrailDifficulty, TrailSegment
from src.hiking.pace import estimate_hike_pace, estimate_pace_minutes
from src.hiking.routing import route_from_trail_segment, snap_and_route_waypoints
from src.hiking.telemetry import compute_splits, detect_off_trail, summarize_telemetry
from src.hiking.units import format_distance, format_duration, format_elevation, m_to_ft, m_to_miles


def test_units_and_geodesy() -> None:
    assert abs(m_to_miles(1609.344) - 1.0) < 1e-4
    assert abs(m_to_ft(100.0) - 328.084) < 1e-2
    assert "km" in format_distance(5000.0, "metric")
    assert "mi" in format_distance(5000.0, "us")
    assert "ft" in format_elevation(1000.0, "us")
    assert format_duration(3660) == "1h 01m"

    d = haversine_m(44.1416, -71.6812, 44.1607, -71.6409)
    assert 3500.0 < d < 4500.0

    line = [[-71.6812, 44.1416, 535.0], [-71.6409, 44.1607, 1600.0]]
    ct_m = cross_track_distance_m(44.1416, -71.6812, line)
    assert ct_m < 5.0
    cross, along, _, _, ele = project_point_onto_polyline(44.1607, -71.6409, line)
    assert cross < 5.0
    assert along > 3500.0
    assert ele > 1500.0


def test_elevation_and_pacing_models() -> None:
    coords = [
        [-71.68, 44.14, 500.0],
        [-71.66, 44.15, 950.0],
        [-71.64, 44.16, 1450.0],
        [-71.65, 44.14, 600.0],
    ]
    gain, loss = compute_gain_loss([c[2] for c in coords], hysteresis_m=3.0)
    assert gain == 950.0
    assert loss == 850.0

    prof = build_elevation_profile(coords)
    assert len(prof) == 4
    summary = summarize_elevation(coords)
    assert summary["elevation_gain_m"] == 950.0

    est = estimate_hike_pace(coords, summary["distance_m"], gain, max_ele_m=1450.0)
    assert est.naismith_seconds > 3600.0
    assert est.langmuir_seconds > 3600.0
    assert est.tobler_seconds > 3600.0
    assert est.water_liters >= 0.5
    assert est.calories_kcal > 400.0

    for m in ("naismith", "langmuir", "tobler"):
        assert estimate_pace_minutes(8000.0, 600.0, 600.0, model=m) > 60.0


def test_routing_edits_and_filters() -> None:
    seg1 = TrailSegment(
        id="seg_1",
        pack_id="white_mountains_nh",
        name="Falling Waters",
        difficulty=TrailDifficulty.HARD,
        condition_status=TrailCondition.OPEN,
        distance_m=5100.0,
        elevation_gain_m=920.0,
        coords_3d=[[-71.6812, 44.1416, 535.0], [-71.6600, 44.1410, 1000.0], [-71.6445, 44.1404, 1457.0]],
    )
    seg2 = TrailSegment(
        id="seg_2",
        pack_id="white_mountains_nh",
        name="Muddy Closed Cutoff",
        difficulty=TrailDifficulty.MODERATE,
        condition_status=TrailCondition.CLOSED,
        distance_m=2000.0,
        elevation_gain_m=150.0,
        coords_3d=[[-71.6812, 44.1416, 535.0], [-71.6445, 44.1404, 1457.0]],
    )
    filtered = filter_trails([seg1, seg2], TrailFilterCriteria(exclude_closed=True))
    assert len(filtered) == 1 and filtered[0].id == "seg_1"

    route = snap_and_route_waypoints(
        [seg1, seg2],
        [(44.1416, -71.6812), (44.1404, -71.6445)],
        name="Test Climb",
        destination_id="white_mountains_nh",
    )
    assert route.distance_m > 2500.0
    rev = reverse_route(route)
    assert rev.coords_3d[0] == route.coords_3d[-1]
    looped = close_loop_route(route, [seg1])
    assert looped.distance_m >= route.distance_m


def test_gpx_geojson_gear_and_hike_bundle(tmp_path) -> None:
    seg = TrailSegment(
        id="seg_test",
        pack_id="white_mountains_nh",
        name="Franconia Ridge Test",
        difficulty=TrailDifficulty.STRENUOUS,
        distance_m=6500.0,
        elevation_gain_m=700.0,
        coords_3d=[[-71.68, 44.14, 550.0], [-71.65, 44.15, 1250.0]],
    )
    route = route_from_trail_segment(seg)
    gpx_xml = export_route_to_gpx(route)
    roundtrip_gpx = import_route_from_gpx(gpx_xml, destination_id="white_mountains_nh")
    assert roundtrip_gpx.name == route.name
    assert len(roundtrip_gpx.coords_3d) == 2

    gj_str = export_route_to_geojson(route)
    roundtrip_gj = import_route_from_geojson(gj_str, destination_id="white_mountains_nh")
    assert roundtrip_gj.name == route.name

    gear = generate_gear_checklist(route)
    assert len(gear) >= 10
    assert total_pack_weight_g(gear, checked_only=True) > 500.0
    assert estimate_hydration_ml(240.0, 700.0, 22.0) >= 1500
    assert estimate_calories_kcal(8000.0, 700.0) > 600

    archive_file = tmp_path / "test_route.hike"
    save_hike_archive(archive_file, route=route, gear_items=gear)
    loaded = load_hike_archive(archive_file)
    assert loaded["route"].name == route.name


def test_telemetry_splits_and_off_trail() -> None:
    pts = [
        TelemetryPoint(seq=0, timestamp="2026-10-17T08:00:00", lat=44.1400, lon=-71.6800, gps_ele_m=540.0, speed_mps=1.3),
        TelemetryPoint(seq=1, timestamp="2026-10-17T08:15:00", lat=44.1450, lon=-71.6700, gps_ele_m=720.0, speed_mps=1.2),
        TelemetryPoint(seq=2, timestamp="2026-10-17T08:30:00", lat=44.1500, lon=-71.6600, gps_ele_m=910.0, speed_mps=1.1),
    ]
    summary = summarize_telemetry(pts)
    assert summary["distance_m"] > 1500.0
    assert summary["elevation_gain_m"] == 370.0
    splits = compute_splits(pts)
    assert len(splits) >= 1

    route_coords = [(44.1400, -71.6800, 540.0), (44.1500, -71.6600, 910.0)]
    on_trail = detect_off_trail((44.1450, -71.6700), route_coords)
    assert not on_trail["is_off_trail"]
    off_trail = detect_off_trail((44.2000, -71.7500), route_coords)
    assert off_trail["is_off_trail"]

