"""'I Hiked This Trail': photos from different days become separate hikes."""
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.core.database import Database
from src.core.settings import Settings
from src.gui.app_state import AppState
from src.hiking.hike_days import build_day_session, group_pins_by_day
from src.hiking.models import ComposedRoute, HikePhotoPin

UTC = timezone.utc


def _pin(ts, along):
    return HikePhotoPin(lat=44.15, lon=-71.64, date_taken=ts, distance_along_m=along)


def _ts(y, m, d, hh, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=UTC).timestamp())


def test_photos_grouped_per_day_with_times_and_coverage() -> None:
    pins = [
        _pin(_ts(2025, 8, 14, 9), 100), _pin(_ts(2025, 8, 14, 13, 30), 11_000),
        _pin(_ts(2026, 7, 2, 10), 500), _pin(_ts(2026, 7, 2, 11), 2_000),
        _pin(None, 3_000),
    ]
    groups = group_pins_by_day(pins, tz=UTC)
    assert [g.day for g in groups] == [date(2025, 8, 14), date(2026, 7, 2), None]
    assert len(groups[0].pins) == 2 and groups[0].first.hour == 9 and groups[0].last.hour == 13

    route = ComposedRoute(name="Franconia Loop", pack_id="white_mountains_nh", distance_m=12_000,
                          estimated_duration_min=420)
    full = build_day_session(route, groups[0], title="Full")
    assert full.started_at == "2025-08-14T09:00:00" and full.ended_at == "2025-08-14T13:30:00"
    assert "part of it" not in full.notes, "photos span most of the route"
    partial = build_day_session(route, groups[1], title="Partial")
    assert "possibly only part of it was hiked" in partial.notes
    undated = build_day_session(route, groups[2], title="Undated", fallback_day=date(2024, 6, 1))
    assert undated.started_at == "2024-06-01T08:00:00"
    assert datetime.fromisoformat(undated.ended_at) - datetime.fromisoformat(undated.started_at) == timedelta(minutes=420)


def test_dialog_saves_one_hike_per_photo_day(
    seeded_db: Database, test_settings: Settings, mock_voyager_db: Path, tmp_path: Path
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from src.gui.dialogs.i_hiked_this_dialog import IHikedThisDialog
    from src.hiking.routing import route_from_trail_segment

    app = QApplication.instance() or QApplication([])
    # A second visit to Little Haystack, three days after the mock library's Franconia photos
    conn = sqlite3.connect(str(mock_voyager_db))
    conn.execute(
        "INSERT INTO photos (id, name, full_path, date_taken, camera_model, lat, lon) VALUES (?,?,?,?,?,?,?)",
        (504, "haystack_again.jpg", "/photos/haystack_again.jpg", 1760700000 + 3 * 86400, "Pixel 9", 44.1405, -71.6446),
    )
    conn.commit()
    conn.close()
    test_settings.set("voyager_db_path", str(mock_voyager_db))
    state = AppState(db=seeded_db, settings=test_settings)
    trail = next(t for t in seeded_db.list_trails("white_mountains_nh") if t.id == "wmnf_franconia_ridge_loop")
    route = route_from_trail_segment(trail)
    sessions_before = len(seeded_db.list_sessions())

    dlg = IHikedThisDialog(state, route)
    app.processEvents()
    dated = [g for g in dlg.day_groups if g.day is not None]
    assert len(dated) == 2, [g.day for g in dlg.day_groups]
    assert dlg.days_list.count() == 2 and dlg.days_list.item(0).checkState() == Qt.CheckState.Checked

    dlg.finalize_hike()
    assert len(dlg.created_sessions) == 2
    assert len(seeded_db.list_sessions()) == sessions_before + 2
    first, second = dlg.created_sessions
    assert first.started_at[:10] != second.started_at[:10]
    assert dlg.created_session is second and state.active_session_id == second.id
    # each hike holds exactly its own day's photos
    assert {p.voyager_photo_id for p in seeded_db.list_photo_pins(session_id=first.id)} == {
        p.voyager_photo_id for p in dated[0].pins}
    assert {p.voyager_photo_id for p in seeded_db.list_photo_pins(session_id=second.id)} == {504}


def test_unchecked_days_are_skipped(
    seeded_db: Database, test_settings: Settings, mock_voyager_db: Path
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from src.gui.dialogs.i_hiked_this_dialog import IHikedThisDialog
    from src.hiking.routing import route_from_trail_segment

    QApplication.instance() or QApplication([])
    test_settings.set("voyager_db_path", str(mock_voyager_db))
    state = AppState(db=seeded_db, settings=test_settings)
    trail = next(t for t in seeded_db.list_trails("white_mountains_nh") if t.id == "wmnf_franconia_ridge_loop")
    dlg = IHikedThisDialog(state, route_from_trail_segment(trail))
    assert dlg.days_list.count() == 1
    dlg.days_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    before = len(seeded_db.list_sessions())
    from unittest.mock import patch

    with patch("src.gui.dialogs.i_hiked_this_dialog.QMessageBox.information") as info:
        dlg.finalize_hike()
    assert info.called and len(seeded_db.list_sessions()) == before
