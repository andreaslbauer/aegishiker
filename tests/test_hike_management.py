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
