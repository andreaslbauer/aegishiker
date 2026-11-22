"""Tests for the LAN mobile companion server, HTTPS TLS pairing, FieldHikeBundle, and offline PWA sync endpoints."""
import json
import ssl
import urllib.request
from src.core.database import Database
from src.core.trail_server import TrailServer, qr_png
from src.hiking.field_bundle import build_field_hike_bundle, compute_solar_darkness
from src.hiking.routing import route_from_trail_segment


def test_trail_server_pwa_pack_and_telemetry_sync(seeded_db: Database) -> None:
    srv = TrailServer(seeded_db, port=0)
    srv.start()
    try:
        assert srv.is_running
        pairing_url = srv.get_pairing_url(https=True)
        assert pairing_url.startswith("https://")
        assert qr_png(pairing_url)[:8] == b"\x89PNG\r\n\x1a\n"

        base = f"http://127.0.0.1:{srv.port}/h/{srv.token}"
        https_base = f"https://127.0.0.1:{srv.port}/h/{srv.token}"
        unverified_ssl = ssl._create_unverified_context()

        # Verify HTTPS endpoint works on the same port as HTTP
        with urllib.request.urlopen(f"{https_base}/hiker.html", timeout=15, context=unverified_ssl) as resp:
            html = resp.read().decode("utf-8")
            assert "AegisHiker Field Companion" in html
            assert "indexedDB" in html
            assert "gesturestart" in html and "map-only-mode" in html
            for hud_id in ("hudDist", "hudRem", "hudSpeed", "hudElev", "hudEta", "hudDark", "mapModeFabBtn"):
                assert hud_id in html

        # Verify /bundle over HTTPS returns landmarks, viewpoints, legs, and clipped basemap
        with urllib.request.urlopen(f"{https_base}/bundle", timeout=15, context=unverified_ssl) as resp:
            bundle_json = json.loads(resp.read().decode("utf-8"))
            assert bundle_json["id"]
            assert len(bundle_json["coords_3d"]) >= 2
            assert len(bundle_json["coords_3d"][0]) == 4  # [lon, lat, ele_m, cum_dist_m]
            assert len(bundle_json["legs"]) >= 1
            assert len(bundle_json["landmarks"]) >= 3
            assert bundle_json["basemap"]["dem"] is not None

        with urllib.request.urlopen(f"{base}/ca.cer", timeout=15) as resp:
            ca_bytes = resp.read()
            assert b"BEGIN CERTIFICATE" in ca_bytes

        with urllib.request.urlopen(f"{base}/state", timeout=15) as resp:
            state_json = json.loads(resp.read().decode("utf-8"))
            assert len(state_json["destinations"]) >= 7
            assert state_json["active_bundle"]["id"] == bundle_json["id"]

        with urllib.request.urlopen(f"{base}/pack/white_mountains_nh", timeout=15) as resp:
            pack_json = json.loads(resp.read().decode("utf-8"))
            assert pack_json["destination"]["id"] == "white_mountains_nh"
            assert len(pack_json["trails"]) >= 6

        req = urllib.request.Request(
            f"{https_base}/sync",
            data=json.dumps(
                {
                    "destination_id": "white_mountains_nh",
                    "notes": "Test mobile GPS sync",
                    "telemetry_points": [
                        {"timestamp": "2026-10-17T09:00:00", "lat": 44.1416, "lon": -71.6812, "ele_m": 535.0},
                        {"timestamp": "2026-10-17T09:10:00", "lat": 44.1430, "lon": -71.6750, "ele_m": 620.0},
                    ],
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15, context=unverified_ssl) as resp:
            out = json.loads(resp.read().decode("utf-8"))
            assert out["status"] == "ok"
            assert out["points_saved"] == 2
    finally:
        srv.stop()


def test_field_hike_bundle_and_solar_darkness(seeded_db: Database) -> None:
    pack = seeded_db.get_destination_pack("white_mountains_nh", with_map=True)
    assert pack is not None
    loop_seg = next(t for t in pack.trails if "franconia_ridge_loop" in t.id)
    route = route_from_trail_segment(loop_seg)

    bundle = build_field_hike_bundle(route, pack)
    assert bundle.name == route.name
    assert bundle.distance_m > 10_000
    assert len(bundle.legs) >= 1
    assert any(lm.kind == "peak" and "Lafayette" in lm.name for lm in bundle.landmarks)
    assert any(lm.kind == "hut" and "Greenleaf" in lm.name for lm in bundle.landmarks)
    assert bundle.basemap is not None
    assert len(bundle.basemap.contours) > 0
    assert bundle.basemap.dem is not None

    # Test solar sunset & civil dusk calculation at 2:00 PM EDT (18:00 UTC) in October in the White Mountains
    oct_afternoon_utc = 1760724000.0  # 2025-10-17 18:00:00 UTC (2:00 PM EDT)
    dark_info = compute_solar_darkness(44.1607, -71.6444, now_ts=oct_afternoon_utc)
    assert not dark_info["is_dark"]
    assert dark_info["sun_elevation_deg"] > 15.0
    assert 180.0 <= dark_info["minutes_to_sunset"] <= 300.0
    assert dark_info["minutes_to_darkness"] > dark_info["minutes_to_sunset"]


