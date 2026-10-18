"""Window geometry, tabs, splitters and user selections survive an app restart."""
import os
from pathlib import Path

from src.core.database import Database
from src.core.settings import Settings
from src.gui.app_state import AppState
from src.hiking.models import TrailCondition
from src.hiking.routing import route_from_trail_segment


def _open_window(db: Database, settings_file: Path):
    from src.gui.main_window import MainWindow

    state = AppState(db=db, settings=Settings(settings_file=settings_file))
    win = MainWindow(app_state=state)
    win.show()
    return state, win


def test_window_and_selections_restored_after_restart(seeded_db: Database, tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    settings_file = tmp_path / "settings.json"

    state, win = _open_window(seeded_db, settings_file)
    win.resize(1111, 777)
    win.move(40, 50)
    win.tabs.setCurrentIndex(2)
    state.set_active_destination("mount_monadnock")
    white_dot = next(t for t in seeded_db.list_trails("mount_monadnock") if t.name == "White Dot Trail")
    route = route_from_trail_segment(white_dot)
    seeded_db.upsert_route(route)
    state.set_active_route(route.id)
    route_id = route.id
    win.composer_panel.pace_combo.setCurrentIndex(win.composer_panel.pace_combo.findData("tobler"))
    win.composer_panel.pack_spin.setValue(9.5)
    win.explore_panel.cond_toggles[TrailCondition.ICY].setChecked(True)
    key, splitter = next(win._splitters())
    total = sum(splitter.sizes())
    splitter.setSizes([total // 4, total - total // 4])
    sizes = splitter.sizes()
    app.processEvents()
    size = (win.width(), win.height())  # actual size: the window's minimum may exceed the request
    win._save_ui_state()  # what closeEvent does, without closing the shared database

    state2, win2 = _open_window(seeded_db, settings_file)
    app.processEvents()
    assert win2.tabs.currentIndex() == 2
    win2.tabs.setCurrentIndex(0)  # splitter positions apply when their tab is first shown
    app.processEvents()
    app.processEvents()
    assert state2.active_destination_id == "mount_monadnock"
    assert state2.active_route_id == route_id
    # within a few px: the headless test platform has no real frame/screen for Qt to restore against
    assert abs(win2.width() - size[0]) <= 10 and abs(win2.height() - size[1]) <= 10
    assert win2.composer_panel.pace_combo.currentData() == "tobler"
    assert win2.composer_panel.pack_spin.value() == 9.5
    assert win2.explore_panel.cond_toggles[TrailCondition.ICY].isChecked()
    restored = dict(win2._splitters())[key].sizes()
    assert abs(restored[0] / sum(restored) - sizes[0] / sum(sizes)) < 0.02, (restored, sizes)
    for w in (win, win2):
        w.hide()
        w.deleteLater()


def test_stale_saved_region_falls_back(seeded_db: Database, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    s = Settings(settings_file=settings_file)
    s.set("active_pack_id", "no_such_pack")
    s.set("last_route_id", "rte_gone")
    s.save()
    state = AppState(db=seeded_db, settings=Settings(settings_file=settings_file))
    assert state.active_destination_id == "white_mountains_nh"
    assert state.active_route_id in {r.id for r in seeded_db.list_routes("white_mountains_nh")}
