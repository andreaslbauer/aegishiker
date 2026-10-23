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

    explore.state_combo.setCurrentIndex(explore.state_combo.findData(""))  # all states
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


def test_trail_search_type_ahead(seeded_db: Database, tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.panels.explore_panel import ExplorePanel

    app = QApplication.instance() or QApplication([])
    state = AppState(db=seeded_db, settings=Settings(settings_file=tmp_path / "settings.json"))
    state.set_active_destination("white_mountains_nh")
    explore = ExplorePanel(app_state=state)
    completer = explore.trail_completer

    completer.setCompletionPrefix("crawf")  # matches anywhere in the name, any case
    suggestions = [completer.completionModel().index(i, 0).data() for i in range(completer.completionCount())]
    assert "Crawford Path" in suggestions

    explore._on_trail_suggestion("Crawford Path")
    app.processEvents()
    assert explore.search_edit.text() == "Crawford Path"
    assert explore._selected_segment().name == "Crawford Path"
    assert explore.trail_table.selectionModel().selectedRows(), "the picked trail is selected"
    explore.deleteLater()


def test_map_clicks_select_an_option_then_add_it_up_to_a_stop_point(seeded_db: Database, tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    state = AppState(db=seeded_db, settings=Settings(settings_file=tmp_path / "settings.json"))
    state.set_active_destination("fitchburg_state_forest")
    win = MainWindow(app_state=state)
    win.show()
    composer = win.composer_panel
    builder = composer.segment_builder
    canvas = composer.map_canvas
    assert builder.start_on_trail("fitchburg_state_forest_crow_hill_trail")
    app.processEvents()
    assert canvas.stop_points, "junctions and ends of the options are shown on the map"

    by = {t.name: t for t in builder.chain.trails}
    mid = by["Crow Hill Ledges North Climb"].coords_3d[2]
    assert builder.pick_at(mid[1], mid[0], 12.0)  # click the climb's line: selects it, adds nothing
    assert len(builder.chain.legs) == 1
    assert builder._candidates[builder.cand_list.currentRow()].trail_name == "Crow Hill Ledges North Climb"
    selected = [sp for sp in canvas.stop_points if sp["selected"]]
    assert selected and selected[-1]["kind"] == "end" and "Midstate" in selected[-1]["label"]

    top = selected[-1]
    builder.hover_at(top["lat"], top["lon"], 12.0)
    assert canvas.preview_coords, "hovering a stop point previews the stretch that would be added"
    assert builder.pick_at(top["lat"], top["lon"], 12.0)  # click its end point: added
    cht, nc = builder.chain.legs
    assert cht.end < len(cht.trail.coords_3d) - 1, "Crow Hill Trail is left at the junction, not walked to its end"
    assert nc.trail.name == "Crow Hill Ledges North Climb" and nc.end == len(nc.trail.coords_3d) - 1
    app.processEvents()
    assert [name for name, _pts in canvas.hike_legs] == ["Crow Hill Trail", "Crow Hill Ledges North Climb"]
    assert any("Midstate" in c.trail_name for c in builder._candidates), "the ridge trail is offered next"
    win.hide()
    win.deleteLater()


def test_regions_are_chosen_by_state(seeded_db: Database, tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.panels.explore_panel import ExplorePanel

    app = QApplication.instance() or QApplication([])
    state = AppState(db=seeded_db, settings=Settings(settings_file=tmp_path / "settings.json"))
    state.set_active_destination("white_mountains_nh")
    explore = ExplorePanel(app_state=state)
    monkeypatch.setattr(explore.map_canvas, "fit_when_shown", lambda coords: None)
    regions = lambda: [explore.dest_combo.itemData(i) for i in range(explore.dest_combo.count())]  # noqa: E731

    assert explore.state_combo.currentData() == "New Hampshire", "opens on the current region's state"
    assert set(regions()) == {"white_mountains_nh", "mount_monadnock"}
    assert explore.state_combo.currentText().startswith("New Hampshire (2")

    # Choosing a state opens its first region (by name); a park across state lines is in each state
    explore.state_combo.setCurrentIndex(explore.state_combo.findData("Massachusetts"))
    app.processEvents()
    assert regions() == ["acton_boxborough_harvard", "fitchburg_state_forest", "pioneer_valley"]
    assert state.active_destination_id == "acton_boxborough_harvard"
    explore.state_combo.setCurrentIndex(explore.state_combo.findData("North Carolina"))
    assert regions() == ["great_smoky_mountains"] and state.active_destination_id == "great_smoky_mountains"
    explore.state_combo.setCurrentIndex(explore.state_combo.findData("Tennessee"))
    assert state.active_destination_id == "great_smoky_mountains", "already in the state: region kept"

    explore.state_combo.setCurrentIndex(explore.state_combo.findData(""))
    assert len(regions()) == len(seeded_db.list_destinations())
    assert explore.ui_state()["region_state"] == ""
    explore.deleteLater()


def test_pick_existing_hike_in_region_opens_it_in_composer(seeded_db: Database, tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.main_window import MainWindow
    from src.hiking.models import ComposedRoute

    app = QApplication.instance() or QApplication([])
    state = AppState(db=seeded_db, settings=Settings(settings_file=tmp_path / "settings.json"))
    trail = state.get_active_trails()[0]
    hike = seeded_db.save_route(ComposedRoute(
        pack_id=state.active_destination_id, name="Zz Test Ridge Loop",
        trail_ids=[trail.id], coords_3d=trail.coords_3d, distance_m=trail.length_m,
    ))
    other = next(d for d in seeded_db.list_destinations() if d.id != state.active_destination_id)
    seeded_db.save_route(ComposedRoute(pack_id=other.id, name="Elsewhere Hike"))
    win = MainWindow(app_state=state)
    win.show()
    explore, composer = win.explore_panel, win.composer_panel

    names = [r.name for r in explore._hikes]
    assert "Zz Test Ridge Loop" in names and "Elsewhere Hike" not in names, "only this region's hikes"
    explore.search_edit.setText("ridge loop")
    assert [r.id for r in explore._hikes] == [hike.id]

    explore.list_tabs.setCurrentIndex(1)
    explore.hike_table.selectRow(0)
    explore._open_selected_hike()
    app.processEvents()

    assert win.tabs.currentWidget() is composer
    assert state.active_route_id == hike.id
    assert composer.route_combo.currentData() == hike.id
    win.hide()
    win.deleteLater()


def test_composer_plays_the_shown_hike_in_3d(seeded_db: Database, tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui import replay3d
    from src.gui.panels.composer_panel import ComposerPanel

    app = QApplication.instance() or QApplication([])
    state = AppState(db=seeded_db, settings=Settings(settings_file=tmp_path / "settings.json"))
    composer = ComposerPanel(state)
    played = []
    monkeypatch.setattr(replay3d.Replay3DDialog, "exec", lambda dlg: played.append(dlg.windowTitle()) or 0)

    assert composer.segment_builder.start_on_trail("wmnf_osm_crawford_path")
    app.processEvents()
    assert composer.play3d_btn.isEnabled()
    composer.play3d_btn.click()
    assert played == [f"3D Hike Replay — {composer._draft.name}"], "the draft being built is played"
    composer.deleteLater()


def test_builder_lists_minor_paths_to_start_on_when_included(seeded_db: Database, tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.panels.composer_panel import ComposerPanel

    QApplication.instance() or QApplication([])
    settings = Settings(settings_file=tmp_path / "settings.json")
    state = AppState(db=seeded_db, settings=settings)
    builder = ComposerPanel(state).segment_builder
    combo = builder.start_combo
    assert builder.minor_chk.isChecked(), "included by default"
    with_minor = combo.count()
    minor = builder.chain.minor_paths()
    assert minor and combo.findData(minor[0].id) >= 0

    builder.minor_chk.setChecked(False)
    assert combo.count() < with_minor and combo.findData(minor[0].id) < 0
    assert not builder.chain.include_map_paths and settings.get("include_minor_paths") is False

    builder.minor_chk.setChecked(True)
    combo.setCurrentIndex(combo.findData(minor[0].id))
    builder.start_chain()
    assert builder.chain.legs[0].trail.id == minor[0].id
