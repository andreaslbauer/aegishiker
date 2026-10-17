"""Tests for the LAN mobile companion server and offline PWA sync endpoints."""
import json
import urllib.request
from src.core.database import Database
from src.core.trail_server import TrailServer


def test_trail_server_pwa_pack_and_telemetry_sync(seeded_db: Database) -> None:
    srv = TrailServer(seeded_db, port=0)
    srv.start()
    try:
        assert srv.is_running
        base = f"http://127.0.0.1:{srv.port}/h/{srv.token}"

        with urllib.request.urlopen(f"{base}/hiker.html", timeout=5) as resp:
            html = resp.read().decode("utf-8")
            assert "AegisHiker Field Companion" in html
            assert "indexedDB" in html

        with urllib.request.urlopen(f"{base}/state", timeout=5) as resp:
            state_json = json.loads(resp.read().decode("utf-8"))
            assert len(state_json["destinations"]) >= 7

        with urllib.request.urlopen(f"{base}/pack/white_mountains_nh", timeout=5) as resp:
            pack_json = json.loads(resp.read().decode("utf-8"))
            assert pack_json["destination"]["id"] == "white_mountains_nh"
            assert len(pack_json["trails"]) >= 6

        req = urllib.request.Request(
            f"{base}/sync",
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
        with urllib.request.urlopen(req, timeout=5) as resp:
            out = json.loads(resp.read().decode("utf-8"))
            assert out["status"] == "ok"
            assert out["points_saved"] == 2
    finally:
        srv.stop()

