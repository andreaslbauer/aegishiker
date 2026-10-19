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


def test_left_out_photos_and_details_are_saved_with_the_hike(
    seeded_db: Database, test_settings: Settings, mock_voyager_db: Path
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.dialogs.i_hiked_this_dialog import IHikedThisDialog
    from src.hiking.hike_report import hike_details
    from src.hiking.photo_matcher import photo_keys
    from src.hiking.routing import route_from_trail_segment

    QApplication.instance() or QApplication([])
    conn = sqlite3.connect(str(mock_voyager_db))  # a second photo the same day
    conn.execute(
        "INSERT INTO photos (id, name, full_path, date_taken, camera_model, lat, lon) VALUES (?,?,?,?,?,?,?)",
        (505, "blurry.jpg", "/photos/blurry.jpg", 1760700000 + 900, "Pixel 9", 44.1405, -71.6446),
    )
    conn.commit()
    conn.close()
    test_settings.set("voyager_db_path", str(mock_voyager_db))
    state = AppState(db=seeded_db, settings=test_settings)
    trail = next(t for t in seeded_db.list_trails("white_mountains_nh") if t.id == "wmnf_franconia_ridge_loop")
    dlg = IHikedThisDialog(state, route_from_trail_segment(trail))
    assert len(dlg.matched_pins) >= 2
    dropped = dlg.matched_pins[0]
    dlg.left_out = {dropped.id}
    dlg._fill_table()
    dlg.details_form.companions.setText("Maria, Tom")
    dlg.details_form.weather.setText("Overcast")
    dlg.finalize_hike()

    session = dlg.created_session
    saved = seeded_db.list_photo_pins(session_id=session.id)
    assert dropped.voyager_photo_id not in {p.voyager_photo_id for p in saved}
    assert len(saved) == len(dlg.matched_pins) - 1
    assert seeded_db.excluded_photo_keys(session.id).intersection(photo_keys(dropped)), "a rescan keeps it out"
    details = hike_details(seeded_db.get_session(session.id))
    assert details["companions"] == "Maria, Tom" and details["weather"] == "Overcast"


def _route_with_hut() -> tuple:
    """A 30 km traverse north along one line; a hut beside it at 10 km, a tent site at 20 km."""
    from src.hiking.models import ComposedRoute

    coords = [[-71.6, 44.0 + i * 0.0009, 400.0 + i * 2] for i in range(301)]  # ~100 m steps
    route = ComposedRoute(name="Presidential Traverse", pack_id="white_mountains_nh", coords_3d=coords,
                          distance_m=30_000, estimated_duration_min=900)
    places = [("Greenleaf Hut", "hut", -71.5995, 44.09), ("Ethan Pond Campsite", "campsite", -71.601, 44.18),
              ("Some Summit", "peak", -71.6, 44.05)]
    return route, places


def _pin_at(route, idx, ts):
    lon, lat, _e = route.coords_3d[idx]
    return HikePhotoPin(lat=lat, lon=lon, date_taken=ts, distance_along_m=idx * 100.0)


def test_consecutive_days_moving_on_are_one_multi_day_hike() -> None:
    from src.hiking.hike_days import build_multi_day_session, looks_multi_day, plan_stages, stages_of
    from src.hiking.hike_info import hike_date_label

    route, places = _route_with_hut()
    pins = [_pin_at(route, 5, _ts(2026, 8, 1, 13)), _pin_at(route, 90, _ts(2026, 8, 1, 18)),
            _pin_at(route, 110, _ts(2026, 8, 2, 13)), _pin_at(route, 190, _ts(2026, 8, 2, 19)),
            _pin_at(route, 210, _ts(2026, 8, 3, 13)), _pin_at(route, 295, _ts(2026, 8, 3, 18))]
    groups = group_pins_by_day(pins, tz=UTC)
    assert looks_multi_day(groups)
    stages = plan_stages(route, groups, places)
    assert [st.overnight for st in stages] == ["Greenleaf Hut", "Ethan Pond Campsite", ""]
    assert stages[0].from_m == 0 and abs(stages[0].to_m - 10_000) < 150 and stages[-1].to_m > 29_000

    session = build_multi_day_session(route, groups, stages, title="Traverse")
    assert session.actual_duration_min == 5 * 60 + 6 * 60 + 5 * 60, "time walking, not the nights"
    assert [st.overnight for st in stages_of(session)] == ["Greenleaf Hut", "Ethan Pond Campsite", ""]
    assert hike_date_label(session).endswith("· 3 days")

    # The same trail hiked on two separate weekends is not one trip
    again = group_pins_by_day([_pin_at(route, 5, _ts(2026, 8, 1, 13)), _pin_at(route, 5, _ts(2026, 8, 8, 13))], tz=UTC)
    assert not looks_multi_day(again)
    # …nor two days that both start from the beginning
    twice = group_pins_by_day([_pin_at(route, 5, _ts(2026, 8, 1, 13)), _pin_at(route, 200, _ts(2026, 8, 1, 18)),
                               _pin_at(route, 5, _ts(2026, 8, 2, 13))], tz=UTC)
    assert not looks_multi_day(twice)


def test_dialog_saves_multi_day_hike_with_overnights(
    seeded_db: Database, test_settings: Settings, mock_voyager_db: Path
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.dialogs.i_hiked_this_dialog import IHikedThisDialog
    from src.hiking.hike_days import stages_of
    from src.hiking.hike_report import build_hike_summary

    QApplication.instance() or QApplication([])
    trail = next(t for t in seeded_db.list_trails("white_mountains_nh") if t.id == "wmnf_franconia_ridge_loop")
    from src.hiking.routing import route_from_trail_segment

    route = route_from_trail_segment(trail)
    pts = route.coords_3d
    conn = sqlite3.connect(str(mock_voyager_db))
    conn.execute("DELETE FROM photos")
    base = int(datetime(2026, 8, 1, 14, tzinfo=UTC).timestamp())
    for i, (frac, day, hour) in enumerate(((0.05, 0, 0), (0.4, 0, 4), (0.55, 1, 0), (0.95, 1, 4))):
        lon, lat, _e = pts[int(frac * (len(pts) - 1))]
        conn.execute("INSERT INTO photos (id, name, full_path, date_taken, camera_model, lat, lon) VALUES (?,?,?,?,?,?,?)",
                     (700 + i, f"p{i}.jpg", f"/photos/p{i}.jpg", base + day * 86400 + hour * 3600, "Pixel", lat, lon))
    conn.commit()
    conn.close()
    test_settings.set("voyager_db_path", str(mock_voyager_db))
    state = AppState(db=seeded_db, settings=test_settings)
    dlg = IHikedThisDialog(state, route)
    assert not dlg.multi_chk.isHidden() and dlg.multi_chk.isChecked(), "two days moving on along the route"
    assert len(dlg.night_combos) == 1
    dlg.night_combos[0].setEditText("Greenleaf Hut")
    dlg.finalize_hike()

    assert len(dlg.created_sessions) == 1, "one hike, not one per day"
    session = seeded_db.get_session(dlg.created_session.id)
    stages = stages_of(session)
    assert len(stages) == 2 and stages[0].overnight == "Greenleaf Hut"
    assert len(seeded_db.list_photo_pins(session_id=session.id)) == 4
    summary = build_hike_summary(session, route, seeded_db.get_destination_pack("white_mountains_nh"), [])
    assert [d["overnight"] for d in summary["days"]] == ["Greenleaf Hut", ""]
    assert summary["elapsed_min"] == 8 * 60
