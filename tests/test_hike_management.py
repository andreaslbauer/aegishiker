"""Hike chooser (grouped by year), rename/delete, and person names from AegisPhotoVoyager."""
import os
import sqlite3
from pathlib import Path

from src.core import paths
from src.core.database import Database
from src.core.settings import Settings
from src.core.voyager_bridge import VoyagerBridge
from src.gui.app_state import AppState
from src.hiking.hike_info import hike_people, hike_title, hike_trail_names
from src.hiking.models import ComposedRoute, HikePhotoPin, HikeSession, TrailSegment


def _qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _session(db, title, started, route=None, people=()):
    s = HikeSession(title=title, started_at=started, route_id=route.id if route else None,
                    destination_id="white_mountains_nh")
    db.save_session(s)
    for i, who in enumerate(people):
        db.save_photo_pin(HikePhotoPin(session_id=s.id, lat=44.1, lon=-71.6, date_taken=1000 + i,
                                       voyager_photo_id=9000 + i + hash(title) % 1000, people=list(who)))
    return s


def test_hike_info_helpers() -> None:
    route = ComposedRoute(name="Franconia Loop", notes="Segments: Falling Waters → Ridge → Ridge → Old Bridle")
    assert hike_title(HikeSession(title="Recorded Hike"), route) == "Franconia Loop"
    assert hike_title(HikeSession(title="Birthday hike"), route) == "Birthday hike"
    assert hike_trail_names(route, {}) == ["Falling Waters", "Ridge", "Old Bridle"]
    plain = ComposedRoute(name="r", trail_ids=["t1"])
    assert hike_trail_names(plain, {"t1": TrailSegment(id="t1", name="White Dot Trail")}) == ["White Dot Trail"]
    pins = [HikePhotoPin(lat=0, lon=0, people=p) for p in (["Bob"], ["Alice", "Bob"], ["Alice"], ["Bob"])]
    assert hike_people(pins) == ["Bob", "Alice"]


def test_picker_groups_by_year_search_rename_delete(seeded_db: Database, test_settings: Settings) -> None:
    _qt()
    from src.gui.dialogs.hike_picker_dialog import HikePickerDialog

    state = AppState(db=seeded_db, settings=test_settings)
    a = _session(seeded_db, "Lafayette sunrise", "2025-08-14T07:00:00", people=[["Alice"]])
    b = _session(seeded_db, "Winter Washington", "2024-02-02T08:00:00", people=[["Carol"], ["Carol", "Dan"]])
    c = _session(seeded_db, "Zealand hut trip", "2025-10-01T09:00:00")
    html_map = paths.get_hike_maps_dir() / f"{b.id}.html"
    html_map.parent.mkdir(parents=True, exist_ok=True)
    html_map.write_text("<html></html>")
    state.set_active_session(b.id)

    dlg = HikePickerDialog(state)
    years = [dlg.tree.topLevelItem(i).text(0) for i in range(dlg.tree.topLevelItemCount())]
    assert years[0].startswith("2025") and "2 hikes" in years[0] and years[1].startswith("2024")
    assert dlg.tree.currentItem().data(0, 256) == b.id, "opens on the current hike"

    dlg.search.setText("carol")
    assert dlg.count_lbl.text() == "1 of 3 hikes"
    dlg.search.setText("")

    state.rename_session(a.id, "  Lafayette at dawn ")
    assert seeded_db.get_session(a.id).title == "Lafayette at dawn"

    assert len(seeded_db.get_photo_pins(b.id)) == 2
    state.delete_session(b.id)
    assert seeded_db.get_session(b.id) is None and seeded_db.get_photo_pins(b.id) == []
    assert not html_map.exists()
    assert state.active_session_id in {a.id, c.id}, "another hike becomes current"
    dlg.populate()
    assert dlg.count_lbl.text() == "2 hikes"


def test_person_names_skip_placeholders_hidden_and_resolve_merges(tmp_path: Path) -> None:
    db_file = tmp_path / "voyager.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(
        """
        CREATE TABLE photos (id INTEGER PRIMARY KEY, name TEXT, full_path TEXT, date_taken INTEGER,
            camera_model TEXT, lat REAL, lon REAL, is_starred INTEGER DEFAULT 0, is_hidden INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0);
        CREATE TABLE persons (id INTEGER PRIMARY KEY, name TEXT, is_merged_into INTEGER, is_hidden INTEGER DEFAULT 0);
        CREATE TABLE faces (id INTEGER PRIMARY KEY, photo_id INTEGER, person_id INTEGER, is_ignored INTEGER DEFAULT 0);
        INSERT INTO persons (id, name, is_merged_into, is_hidden) VALUES
            (1, 'Alice', NULL, 0), (2, 'Person 4711', NULL, 0), (3, 'Ali', 1, 0), (4, 'Secret', NULL, 1);
        INSERT INTO faces (photo_id, person_id) VALUES (10, 1), (10, 2), (11, 3), (11, 4), (12, 2);
        """
    )
    conn.commit()
    conn.close()
    bridge = VoyagerBridge(str(db_file))
    assert bridge.people_for_photo_ids([10, 11, 12]) == {10: ["Alice"], 11: ["Alice"]}


def test_reexperience_fills_missing_people(seeded_db: Database, test_settings: Settings, mock_voyager_db) -> None:
    _qt()
    from src.gui.panels.reexperience_panel import ReexperiencePanel

    test_settings.set("voyager_db_path", str(mock_voyager_db))
    state = AppState(db=seeded_db, settings=test_settings)
    s = HikeSession(title="Old hike", started_at="2025-10-17T08:00:00", destination_id="white_mountains_nh")
    seeded_db.save_session(s)
    seeded_db.save_photo_pin(HikePhotoPin(session_id=s.id, voyager_photo_id=501, lat=44.1405, lon=-71.6446,
                                          date_taken=1760700000, people=[]))  # saved before names were read
    state.set_active_session(s.id)
    panel = ReexperiencePanel(state)
    assert panel.photo_pins[0].people == ["Alice"]
    assert seeded_db.get_photo_pins(s.id)[0].people == ["Alice"], "persisted"
    assert "Alice" in panel.hike_lbl.text()


def test_reexperience_removes_selected_photos(seeded_db: Database, test_settings: Settings, monkeypatch) -> None:
    _qt()
    from PySide6.QtWidgets import QMessageBox

    from src.gui.panels.reexperience_panel import ReexperiencePanel

    state = AppState(db=seeded_db, settings=test_settings)
    s = _session(seeded_db, "Photo hike", "2025-07-04T08:00:00", people=[["A"], ["B"], ["C"]])
    state.set_active_session(s.id)
    panel = ReexperiencePanel(state)
    assert panel.photo_list.count() == 3
    assert panel.remove_photo_btn.isEnabled(), "the first photo is selected on load"
    panel.photo_list.clearSelection()
    assert not panel.remove_photo_btn.isEnabled()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    panel.photo_list.item(0).setSelected(True)
    panel.photo_list.item(2).setSelected(True)
    assert panel.remove_photo_btn.isEnabled()
    panel.remove_selected_photos()
    assert len(seeded_db.get_photo_pins(s.id)) == 3, "declining keeps the photos"

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    panel.photo_list.clearSelection()
    panel.photo_list.item(0).setSelected(True)
    panel.photo_list.item(2).setSelected(True)
    panel.remove_selected_photos()
    left = seeded_db.get_photo_pins(s.id)
    assert [p.people for p in left] == [["B"]]
    assert panel.photo_list.count() == 1 and seeded_db.get_session(s.id).stats.photo_count == 1
    assert len(seeded_db.excluded_photo_keys(s.id)) >= 2, "a rescan leaves them out"


def test_library_shows_times_hiked_and_last_hiked(seeded_db: Database, test_settings: Settings) -> None:
    _qt()
    from src.gui.panels.library_panel import LibraryPanel

    hiked = seeded_db.save_route(ComposedRoute(name="Hiked twice", pack_id="white_mountains_nh"))
    never = seeded_db.save_route(ComposedRoute(name="Never hiked", pack_id="white_mountains_nh"))
    _session(seeded_db, "First", "2025-06-01T08:00:00", route=hiked)
    _session(seeded_db, "Second", "2026-09-14T07:30:00", route=hiked)
    planned = HikeSession(title="Plan", started_at="2026-12-01T08:00:00", route_id=hiked.id, status="planned")
    seeded_db.save_session(planned)

    assert seeded_db.route_hike_stats()[hiked.id] == (2, "2026-09-14T07:30:00")
    assert never.id not in seeded_db.route_hike_stats()

    panel = LibraryPanel(AppState(db=seeded_db, settings=test_settings))
    rows = {panel.route_table.item(r, 0).text(): r for r in range(panel.route_table.rowCount())}
    cell = lambda name, col: panel.route_table.item(rows[name], col).text()  # noqa: E731
    assert (cell("Hiked twice", 5), cell("Hiked twice", 6)) == ("2×", "2026-09-14")
    assert (cell("Never hiked", 5), cell("Never hiked", 6)) == ("—", "Never")


def test_library_opens_a_hiked_route_in_reexperience(seeded_db, tmp_path, monkeypatch) -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.core.settings import Settings
    from src.gui.app_state import AppState
    from src.gui.main_window import MainWindow
    from src.hiking.models import HikeSession
    from src.hiking.routing import route_from_trail_segment

    app = QApplication.instance() or QApplication([])
    trail = seeded_db.list_trails("white_mountains_nh")[0]
    hiked, never = route_from_trail_segment(trail), route_from_trail_segment(trail)
    never.name = "Not hiked yet"
    for r in (hiked, never):
        seeded_db.upsert_route(r)
    first = HikeSession(route_id=hiked.id, title="First time", started_at="2025-07-01T09:00:00", status="completed")
    seeded_db.save_session(first)
    state = AppState(db=seeded_db, settings=Settings(settings_file=tmp_path / "settings.json"))
    win = MainWindow(app_state=state)
    win.show()
    lib = win.library_panel
    win.tabs.setCurrentWidget(lib)
    lib.refresh_all()
    row = {r.id: i for i, r in enumerate(lib._routes)}
    lib.route_table.selectRow(row[never.id])
    assert not lib.replay_btn.isEnabled(), "nothing to relive on a route never hiked"
    lib.route_table.selectRow(row[hiked.id])
    assert lib.replay_btn.isEnabled()
    lib.replay_btn.click()
    app.processEvents()
    assert win.tabs.currentWidget() is win.reexperience_panel and state.active_session_id == first.id

    # Hiked twice: the user picks which hike (here the older one)
    second = HikeSession(route_id=hiked.id, title="Second time", started_at="2026-07-01T09:00:00", status="completed")
    seeded_db.save_session(second)
    win.tabs.setCurrentWidget(lib)
    lib.refresh_all()
    lib.route_table.selectRow({r.id: i for i, r in enumerate(lib._routes)}[hiked.id])
    offered = []

    def pick_oldest(route, hikes):
        offered.extend(h.title for h in hikes)
        return hikes[-1]

    monkeypatch.setattr(lib, "_choose_hike", pick_oldest)
    lib.replay_btn.click()
    app.processEvents()
    assert offered == ["Second time", "First time"], "the route's hikes, newest first"
    assert state.active_session_id == first.id and win.tabs.currentWidget() is win.reexperience_panel
    win.hide()
    win.deleteLater()


def test_reexperience_never_shows_another_route_for_a_hike(seeded_db: Database, test_settings: Settings) -> None:
    """A hike whose route was deleted shows its own stored track, not the Composer's active route."""
    _qt()
    from src.gui.panels.reexperience_panel import ReexperiencePanel

    state = AppState(db=seeded_db, settings=test_settings)
    hiked = [[-71.30, 44.30, 400.0], [-71.29, 44.31, 700.0], [-71.28, 44.32, 1100.0]]
    other = seeded_db.save_route(ComposedRoute(name="Unrelated Plan", pack_id="white_mountains_nh",
                                               coords_3d=[[-71.6, 44.1, 300.0], [-71.59, 44.1, 310.0]]))
    s = HikeSession(title="Ridge day", started_at="2025-09-05T07:00:00", destination_id="white_mountains_nh",
                    interactive_map_data={"title": "Ridge day", "coords_3d": hiked, "distance_m": 2900.0,
                                          "elevation_gain_m": 700.0})
    seeded_db.save_session(s)  # route_id None: its route was deleted
    state.set_active_route(other.id)
    state.set_active_session(s.id)
    panel = ReexperiencePanel(state)

    assert [list(c) for c in panel.map_canvas.active_route.coords_3d] == hiked
    assert [round(p.ele_m) for p in panel.elev_chart.samples][-1] == 1100, "chart = the hike's own track"
    assert panel._replay_route().coords_3d == hiked

    seeded_db.save_session(HikeSession(id=s.id, title="Ridge day", started_at=s.started_at,
                                       destination_id="white_mountains_nh"))  # no track at all
    panel.refresh_all()
    assert panel.map_canvas.active_route is None
    assert panel.elev_chart.samples == [], "no stale profile from the previous hike"
