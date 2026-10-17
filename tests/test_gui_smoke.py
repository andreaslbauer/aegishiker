"""Headless PySide6 GUI smoke tests for MainWindow, panels, and PDF export."""
import os
from pathlib import Path
from PySide6.QtWidgets import QApplication

from src.core.database import Database
from src.core.settings import Settings
from src.gui.app_state import AppState
from src.gui.main_window import MainWindow
from src.gui.report import export_trailhead_pdf


def test_gui_main_window_and_pdf_export(
    seeded_db: Database,
    test_settings: Settings,
    mock_voyager_db: Path,
    tmp_path: Path,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    test_settings.set("voyager_db_path", str(mock_voyager_db))
    state = AppState(db=seeded_db, settings=test_settings)
    win = MainWindow(app_state=state)
    win.show()

    assert win.tabs.count() == 9
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        app.processEvents()

    route = state.get_active_route()
    assert route is not None
    pdf_out = export_trailhead_pdf(
        output_path=tmp_path / "test_sheet.pdf",
        route=route,
        destination=state.get_active_destination(),
    )
    assert pdf_out.exists() and pdf_out.stat().st_size > 1000
    win.close()

