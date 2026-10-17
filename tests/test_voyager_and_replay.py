"""Tests for the 'I Hiked This Trail' feature:
1) Region selection & trail tracing
2) Completed HikeSession persistence
3) AegisPhotoVoyager SQLite DB discovery & GPS corridor photo matching
4) Interactive hike replay map generation & persistence (JSON + standalone HTML)
"""
from pathlib import Path

from src.core.database import Database
from src.core.voyager_bridge import VoyagerBridge
from src.hiking.hike_replay import build_interactive_map_data, export_interactive_hike_html
from src.hiking.models import HikeSession
from src.hiking.routing import snap_and_route_waypoints


def test_i_hiked_this_trail_and_voyager_photo_matching(
    seeded_db: Database,
    mock_voyager_db: Path,
    tmp_path: Path,
) -> None:
    # 1. Select New Hampshire White Mountains region and trace Franconia Ridge
    trails = seeded_db.list_trails("white_mountains_nh")
    traced_route = snap_and_route_waypoints(
        segments=trails,
        waypoints=[(44.1416, -71.6812), (44.1404, -71.6445), (44.1607, -71.6409)],
        name="My Hiked Franconia Ridge Traverse",
        destination_id="white_mountains_nh",
    )
    seeded_db.upsert_route(traced_route)

    # 2. Discover AegisPhotoVoyager DB and match geotagged photos along the traced trail
    bridge = VoyagerBridge(str(mock_voyager_db))
    assert bridge.is_available()
    assert bridge.discover_db_path() == mock_voyager_db
    assert bridge.count_geotagged_photos() == 3

    matched_pins = bridge.match_photos_for_route(
        route=traced_route,
        max_corridor_m=300.0,
    )
    # Photos 501 (Little Haystack) and 502 (Mt Lafayette) are on the ridge; 503 (Miami Beach) is excluded
    assert len(matched_pins) == 2
    filenames = [p.filename for p in matched_pins]
    assert "little_haystack_ridge.jpg" in filenames
    assert "mt_lafayette_summit.jpg" in filenames
    assert "Alice" in matched_pins[0].people_names or "Bob" in matched_pins[1].people_names
    assert matched_pins[0].thumbnail_path is not None

    # 3. Create and persist the HikeSession + interactive map data + standalone HTML replay map
    session = HikeSession(
        route_id=traced_route.id,
        destination_id="white_mountains_nh",
        title=traced_route.name,
        status="traced_hiked",
        actual_distance_m=traced_route.distance_m,
        actual_elevation_gain_m=traced_route.elevation_gain_m,
        actual_duration_min=traced_route.estimated_duration_min,
        notes="Clear views across the Pemigewasset Wilderness!",
    )
    map_data = build_interactive_map_data(
        route=traced_route,
        session=session,
        photos=matched_pins,
        destination_name="New Hampshire White Mountains",
    )
    session.interactive_map_data = map_data
    seeded_db.save_session(session, photo_pins=matched_pins)

    html_out = export_interactive_hike_html(map_data, tmp_path / "franconia_replay.html")
    assert html_out.exists()
    html_text = html_out.read_text(encoding="utf-8")
    assert "My Hiked Franconia Ridge Traverse" in html_text
    assert "little_haystack_ridge.jpg" in html_text
    assert "mt_lafayette_summit.jpg" in html_text

    # 4. Verify database round-trip of persisted session and photo pins
    loaded_session = seeded_db.get_session(session.id)
    assert loaded_session is not None
    assert loaded_session.status == "traced_hiked"
    assert loaded_session.interactive_map_data is not None
    loaded_pins = seeded_db.list_photo_pins(session_id=session.id)
    assert len(loaded_pins) == 2

