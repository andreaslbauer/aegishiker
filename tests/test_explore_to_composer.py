"""Explore Regions → 'Compose / Trace This Trail' preselects that trail as the Composer's start."""
import os
from pathlib import Path

from src.core.database import Database
from src.core.settings import Settings
from src.gui.app_state import AppState


def test_compose_button_preselects_starting_trail(seeded_db: Database, tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    state = AppState(db=seeded_db, settings=Settings(settings_file=tmp_path / "settings.json"))
    win = MainWindow(app_state=state)
    win.show()
    explore, composer = win.explore_panel, win.composer_panel
    routes_before = len(seeded_db.list_routes())

    row = next(i for i, t in enumerate(explore._filtered_trails) if t.id == "wmnf_osm_crawford_path")
    explore.trail_table.selectRow(row)
    explore._compose_selected_trail()
    app.processEvents()

    builder = composer.segment_builder
    assert win.tabs.currentWidget() is composer
    assert builder.start_combo.currentData() == "wmnf_osm_crawford_path"
    assert [leg.trail.id for leg in builder.chain.legs] == ["wmnf_osm_crawford_path"]
    assert builder.cand_list.count() > 0, "next-segment options are ready"
    assert len(seeded_db.list_routes()) == routes_before, "no throwaway route is saved"
    win.hide()
    win.deleteLater()


def test_selecting_region_fits_map_to_destination(seeded_db: Database, tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.panels.explore_panel import ExplorePanel

    app = QApplication.instance() or QApplication([])
    state = AppState(db=seeded_db, settings=Settings(settings_file=tmp_path / "settings.json"))
    explore = ExplorePanel(app_state=state)
    destination = next(d for d in seeded_db.list_destinations() if d.id != state.active_destination_id)
    fitted = []
    monkeypatch.setattr(explore.map_canvas, "fit_when_shown", lambda coords: fitted.append(coords))

    explore.dest_combo.setCurrentIndex(explore.dest_combo.findData(destination.id))
    app.processEvents()

    assert state.active_destination_id == destination.id
    assert fitted == [[
        (destination.min_lat, destination.min_lon),
        (destination.max_lat, destination.max_lon),
    ]]
    explore.deleteLater()


def test_clicking_builder_trail_zooms_map(seeded_db: Database, tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    state = AppState(db=seeded_db, settings=Settings(settings_file=tmp_path / "settings.json"))
    win = MainWindow(app_state=state)
    win.show()
    composer = win.composer_panel
    win.tabs.setCurrentWidget(composer)
    builder = composer.segment_builder
    assert builder.start_on_trail("wmnf_osm_crawford_path")
    app.processEvents()
    canvas = composer.map_canvas

    def visible_span():
        return canvas.zoom, canvas.pan_offset

    before = visible_span()
    builder.cand_list.setCurrentRow(0)  # programmatic selection (as from a map click) must not zoom
    app.processEvents()
    assert visible_span() == before

    builder.cand_list.itemClicked.emit(builder.cand_list.item(0))
    app.processEvents()
    after_cand = visible_span()
    assert after_cand != before, "clicking an option zooms the map to it"
    assert canvas.zoom <= 80.0

    builder.legs_list.itemClicked.emit(builder.legs_list.item(0))
    app.processEvents()
    assert visible_span() != after_cand, "clicking a leg of the hike zooms to that leg"
    win.hide()
    win.deleteLater()
