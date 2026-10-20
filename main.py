#!/usr/bin/env python3
"""AegisHiker — Local AI Trail Planner, Route Composer, Field GPS Companion & Hike Replay Studio."""
import argparse
import faulthandler
import gc
import json
import logging
import os
import sqlite3
import sys
import tempfile
import urllib.request
from pathlib import Path

if sys.platform == "linux":
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    gtk_modules = os.environ.get("GTK_MODULES", "")
    if gtk_modules:
        os.environ["GTK_MODULES"] = ",".join(
            m for m in gtk_modules.split(",") if m.strip() not in ("canberra-gtk-module", "atk-bridge")
        )

import shiboken6
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

from src.core.about import APP_NAME, get_version
from src.core.database import Database
from src.core.settings import Settings
from src.gui.app_state import AppState
from src.gui.busy import busy
from src.gui.main_window import MainWindow
from src.gui.report import export_trailhead_pdf
from src.gui.themes import apply_theme
from src.hiking.hike_replay import build_interactive_map_data, export_interactive_hike_html
from src.hiking.models import HikeSession
from src.hiking.pace import estimate_pace_minutes
from src.hiking.routing import snap_and_route_waypoints

logger = logging.getLogger("aegishiker")


def setup_logging(debug: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_self_test(app: QApplication) -> int:
    """Execute end-to-end automated self-test in an isolated temporary directory."""
    with tempfile.TemporaryDirectory(prefix="aegishiker_selftest_") as tmp:
        os.environ["AEGISHIKER_DATA_DIR"] = tmp
        tmp_path = Path(tmp)

        # Create a mock AegisPhotoVoyager database with geotagged White Mountains photos
        voyager_dir = tmp_path / ".AegisPhotoVoyager"
        voyager_dir.mkdir(parents=True, exist_ok=True)
        voyager_db_path = voyager_dir / "voyager.db"
        conn = sqlite3.connect(str(voyager_db_path))
        conn.executescript(
            """
            CREATE TABLE photos (
                id INTEGER PRIMARY KEY,
                name TEXT,
                full_path TEXT,
                date_taken TEXT,
                camera_model TEXT,
                lat REAL,
                lon REAL,
                is_starred INTEGER DEFAULT 0,
                is_hidden INTEGER DEFAULT 0,
                is_deleted INTEGER DEFAULT 0
            );
            CREATE TABLE ai_analysis (
                photo_id INTEGER PRIMARY KEY,
                summary TEXT,
                tags_json TEXT,
                mood TEXT
            );
            CREATE TABLE persons (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE faces (id INTEGER PRIMARY KEY, photo_id INTEGER, person_id INTEGER, is_ignored INTEGER DEFAULT 0);
            INSERT INTO photos (id, name, full_path, date_taken, camera_model, lat, lon, is_starred)
            VALUES (101, 'franconia_ridge_summit.jpg', '/tmp/franconia_ridge_summit.jpg', '2026-10-17 11:15:00', 'Sony A7IV', 44.1485, -71.6448, 1);
            INSERT INTO ai_analysis (photo_id, summary, tags_json, mood)
            VALUES (101, 'Panoramic alpine ridge view on Mount Lafayette in the White Mountains', '["summit", "alpine", "nh"]', 'epic');
            """
        )
        conn.commit()
        conn.close()

        settings = Settings(settings_file=tmp_path / "settings.json")
        settings.set("voyager_db_path", str(voyager_db_path))
        db = Database(db_path=tmp_path / "aegishiker.db")
        state = AppState(db=db, settings=settings)

        # 1. Verify all 7 starter packs including New Hampshire White Mountains
        dests = state.db.list_destinations()
        dest_ids = {d.id for d in dests}
        assert "white_mountains_nh" in dest_ids, "Missing New Hampshire White Mountains pack"
        assert len(dests) >= 7, f"Expected >=7 packs, got {len(dests)}"
        print("SELFTEST 1_packs_and_db: OK", flush=True)

        # 2. Verify waypoint snapping, A* trail routing, and pacing models
        state.set_active_destination("white_mountains_nh")
        nh_trails = state.get_active_trails()
        assert len(nh_trails) >= 4
        by_id = {t.id: t for t in nh_trails}  # pick by id: list order is alphabetical by name
        falling_waters, old_bridle = by_id["wmnf_falling_waters"], by_id["wmnf_old_bridle_path"]
        waypoints = [
            (falling_waters.coordinates[0][0], falling_waters.coordinates[0][1]),
            (old_bridle.coordinates[0][0], old_bridle.coordinates[0][1]),  # Lafayette summit
        ]
        traced_route = snap_and_route_waypoints(
            segments=nh_trails,
            waypoints=waypoints,
            name="Franconia Ridge Traced Loop",
            destination_id="white_mountains_nh",
        )
        assert traced_route.distance_m > 500.0
        for model_name in ("naismith", "langmuir", "tobler"):
            mins = estimate_pace_minutes(
                traced_route.distance_m,
                traced_route.elevation_gain_m,
                traced_route.elevation_loss_m,
                model=model_name,
            )
            assert mins > 5.0
        state.db.upsert_route(traced_route)
        print("SELFTEST 2_routing_and_pace: OK", flush=True)

        # 3. Verify "I Hiked This Trail" + AegisPhotoVoyager matching + Interactive Map persistence
        matched_pins = state.voyager_bridge.match_photos_for_route(
            route=traced_route,
            max_corridor_m=2500.0,
        )
        assert len(matched_pins) >= 1, "Expected matched photo from mock AegisPhotoVoyager DB"
        session = HikeSession(
            route_id=traced_route.id,
            destination_id="white_mountains_nh",
            status="traced_hiked",
            actual_distance_m=traced_route.distance_m,
            actual_elevation_gain_m=traced_route.elevation_gain_m,
            actual_duration_min=traced_route.estimated_duration_min,
            notes="Hiked Franconia Ridge in New Hampshire White Mountains",
        )
        for pin in matched_pins:
            pin.session_id = session.id
        map_data = build_interactive_map_data(
            route=traced_route,
            session=session,
            photos=matched_pins,
            destination_name="New Hampshire White Mountains",
        )
        session.interactive_map_data = map_data
        state.db.save_session(session)
        for pin in matched_pins:
            state.db.save_photo_pin(pin)
        html_file = export_interactive_hike_html(map_data, tmp_path / "franconia_replay.html")
        assert html_file.exists() and html_file.stat().st_size > 1000
        print("SELFTEST 3_i_hiked_this_and_voyager: OK", flush=True)

        # 4. Verify Mobile LAN Server & Offline Pack / Telemetry Sync API
        state.trail_server.start()
        base_url = f"http://127.0.0.1:{state.trail_server.port}/h/{state.trail_server.token}"
        with urllib.request.urlopen(f"{base_url}/pack/white_mountains_nh", timeout=5) as resp:
            pack_payload = json.loads(resp.read().decode("utf-8"))
            assert pack_payload["destination"]["id"] == "white_mountains_nh"

        sync_req = urllib.request.Request(
            f"{base_url}/sync",
            data=json.dumps(
                {
                    "route_id": traced_route.id,
                    "destination_id": "white_mountains_nh",
                    "notes": "Selftest mobile sync",
                    "telemetry_points": [
                        {
                            "timestamp": "2026-10-17T10:00:00",
                            "lat": 44.1485,
                            "lon": -71.6448,
                            "ele_m": 1590.0,
                            "accuracy_m": 4.0,
                            "speed_mps": 1.2,
                        }
                    ],
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(sync_req, timeout=5) as resp:
            sync_out = json.loads(resp.read().decode("utf-8"))
            assert sync_out["status"] == "ok" and sync_out["points_saved"] == 1
        state.trail_server.stop()
        print("SELFTEST 4_mobile_server_and_pwa: OK", flush=True)

        # 5. Verify MainWindow, all 9 tabs, QPainter canvases, and PDF export
        win = MainWindow(app_state=state)
        win.show()
        for tab_idx in range(win.tabs.count()):
            win.tabs.setCurrentIndex(tab_idx)
            app.processEvents()

        pdf_path = export_trailhead_pdf(
            output_path=tmp_path / "trailhead_safety.pdf",
            route=traced_route,
            destination=state.get_active_destination(),
            session=session,
            photos=matched_pins,
        )
        assert pdf_path.exists() and pdf_path.stat().st_size > 1000
        win.close()
        print("SELFTEST 5_gui_and_pdf: OK", flush=True)

    print("AUTOMATED_TEST_COMPLETE", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} — Local AI Trail Planner & Field Companion")
    parser.add_argument("--test-mode", action="store_true", help="Run automated headless self-test and exit")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("--version", action="store_true", help="Print application version and exit")
    args, qt_args = parser.parse_known_args()

    if args.version:
        print(f"{APP_NAME} {get_version()}")
        return 0

    setup_logging(args.debug)
    # A native crash (Qt / image codecs) prints the Python stack of every thread to stderr
    faulthandler.enable(all_threads=True)

    app = QApplication([sys.argv[0]] + qt_args)
    app.setOrganizationName("Aegis")
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("aegishiker")
    app.setApplicationVersion(get_version())
    apply_theme(app, "dark", 1.0)

    if args.test_mode:
        return run_self_test(app)

    with busy(None, "Opening trail database and regional packs…", title=f"Starting {APP_NAME}", steps=2) as b:
        state = AppState()
        apply_theme(
            app,
            str(state.settings.get("theme", "dark")),
            float(state.settings.get("font_scale", 1.0)),
        )
        b.step("Building maps and views…")
        win = MainWindow(app_state=state)
        b.step()
    win.show()
    win.raise_()
    win.activateWindow()
    logger.info("%s v%s started.", APP_NAME, get_version())
    exit_code = app.exec()
    shutdown_qt(app)
    return exit_code


def shutdown_qt(app: QApplication) -> None:
    """Destroy every window while Python and Qt are both still fully alive.

    Returning straight from app.exec() left PySide's end-of-interpreter cleanup
    (destroyQCoreApplication) to delete the remaining widgets in arbitrary order, which crashed
    on quit (SIGSEGV in ~QObject, heap corruption in ~QBackingStore).
    """
    for widget in list(app.topLevelWidgets()):
        if not shiboken6.isValid(widget):
            continue
        # Skip popups (completer popups, combo dropdowns, context menus) and tooltips;
        # their lifecycles are owned by parent widgets / QCompleters and calling
        # deleteLater() on them causes double-free crashes when parents are destroyed.
        if widget.windowType() in (Qt.WindowType.Popup, Qt.WindowType.ToolTip):
            continue
        # Skip child windows whose parent is already a top-level widget that will destroy them
        if widget.parent() is not None:
            continue
        widget.hide()
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    app.shutdown()


if __name__ == "__main__":
    sys.exit(main())

