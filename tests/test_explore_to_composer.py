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
