"""Interactive Trail Composer & Elevation Profile panel, featuring:
- Region selector & click-to-snap trail routing
- Undo, Clear, Reverse, Close Loop tools
- Pacing model selector (Naismith, Langmuir, Tobler)
- '✓ I Hiked This Trail' button that logs the hike, scans AegisPhotoVoyager for GPS photos,
  and generates an interactive hike replay map.
"""
from typing import List, Optional, Tuple
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.charts.elevation_chart import ElevationChart
from src.gui.charts.map_canvas import MapCanvas
from src.gui.panels.segment_builder import SegmentBuilder
from src.gui.dialogs.i_hiked_this_dialog import IHikedThisDialog
from src.gui.help_text import TOOLTIPS
from src.gui.widgets import Card, SectionHeader, StatCard, page_layout, stat_row
from src.hiking.edits import close_loop_route, reverse_route
from src.hiking.geojson_io import export_route_to_geojson, import_route_from_geojson
from src.hiking.gpx import export_route_to_gpx, import_route_from_gpx
from src.hiking.models import ComposedRoute
from src.hiking.pace import estimate_pace_minutes
from src.hiking.routing import snap_and_route_waypoints
from src.hiking.units import format_distance, format_duration, format_duration_min, format_elevation
from src.gui.busy import busy


class ComposerPanel(QWidget):
    hike_logged_and_mapped = Signal(str)  # session_id

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.waypoints: List[Tuple[float, float]] = []
        self._draft: Optional[ComposedRoute] = None  # hike being built segment by segment

        layout = page_layout(self)
        header = SectionHeader(
            "Trail Composer",
            "Build a hike trail by trail, or click the map to trace one — then check its climbs and timing.",
        )
        import_btn = QPushButton("Import…")
        import_btn.setObjectName("FlatButton")
        import_btn.setToolTip("Import a GPX or GeoJSON route")
        import_btn.clicked.connect(self.import_file)
        export_btn = QPushButton("Export GPX…")
        export_btn.setObjectName("FlatButton")
        export_btn.clicked.connect(self.export_gpx_file)
        self.play3d_btn = QPushButton("▶  3D Play Hike")
        self.play3d_btn.setToolTip("Fly along this hike over 3D terrain")
        self.play3d_btn.clicked.connect(self.play_hike_3d)
        self.i_hiked_btn = QPushButton("✓  I Hiked This Trail")
        self.i_hiked_btn.setObjectName("PrimaryButton")
        self.i_hiked_btn.setToolTip(TOOLTIPS["i_hiked_this"])
        self.i_hiked_btn.clicked.connect(self.open_i_hiked_this_wizard)
        for b in (import_btn, export_btn, self.play3d_btn, self.i_hiked_btn):
            header.actions_layout.addWidget(b)
        layout.addWidget(header)

        # Route card: region, saved route, name + save, and the tracing tools in a menu
        route_card = Card(margins=(16, 12, 16, 12))
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(10)
        self.region_combo = QComboBox()
        self.region_combo.setToolTip("Region")
        self.region_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.region_combo.setMinimumContentsLength(18)
        self.region_combo.currentIndexChanged.connect(self._on_region_combo_changed)
        self.route_combo = QComboBox()
        self.route_combo.setToolTip("Saved routes in this region")
        self.route_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.route_combo.setMinimumContentsLength(20)
        self.route_combo.currentIndexChanged.connect(self._on_route_combo_changed)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Route name")
        save_btn = QPushButton("Save Route")
        save_btn.clicked.connect(self.save_current_route)
        tools_btn = QToolButton()
        tools_btn.setText("Route tools  ▾")
        tools_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tools = QMenu(tools_btn)
        tools.addAction("Undo last waypoint", self.undo_waypoint)
        tools.addAction("Clear traced waypoints", self.clear_trace)
        tools.addSeparator()
        tools.addAction("Close the loop back to the start", self.close_loop)
        tools.addAction("Reverse the route", self.reverse_current_route)
        tools_btn.setMenu(tools)
        for label, widget, stretch in (("Region", self.region_combo, 3), ("Saved route", self.route_combo, 3)):
            cap = QLabel(label)
            cap.setObjectName("Muted")
            ctrl_row.addWidget(cap)
            ctrl_row.addWidget(widget, stretch)
            ctrl_row.addSpacing(6)
        ctrl_row.addWidget(self.name_edit, 3)
        ctrl_row.addWidget(save_btn)
        ctrl_row.addWidget(tools_btn)
        route_card.body.addLayout(ctrl_row)
        layout.addWidget(route_card)

        # Live stats + pacing settings
        self.dist_card = StatCard("Total Distance", "0.0 km")
        self.gain_card = StatCard("Elevation Gain", "+0 m")
        self.loss_card = StatCard("Elevation Loss", "-0 m")
        self.time_card = StatCard("Estimated Time", "0m")
        self.grade_card = StatCard("Max / Avg Grade", "0.0%")
        pace_card = Card(margins=(16, 10, 16, 10), spacing=6)
        pace_title = QLabel("PACING")
        pace_title.setObjectName("StatTitle")
        pace_card.body.addWidget(pace_title)
        self.pace_combo = QComboBox()
        self.pace_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.pace_combo.setMinimumContentsLength(8)
        for model_name in ["naismith", "langmuir", "tobler"]:
            self.pace_combo.addItem(f"{model_name.capitalize()} rule", model_name)
        self.pace_combo.currentIndexChanged.connect(self._recalculate_pace)
        self.pack_spin = QDoubleSpinBox()
        self.pack_spin.setRange(0.0, 35.0)
        self.pack_spin.setValue(float(self.app_state.settings.get("default_pack_weight_kg", 6.0)))
        self.pack_spin.setPrefix("Pack ")
        self.pack_spin.setSuffix(" kg")
        self.pack_spin.valueChanged.connect(self._recalculate_pace)
        pace_row = QHBoxLayout()
        pace_row.setSpacing(6)
        pace_row.addWidget(self.pace_combo, 1)
        pace_row.addWidget(self.pack_spin, 1)
        pace_card.body.addLayout(pace_row)
        stats = stat_row(self.dist_card, self.gain_card, self.loss_card, self.time_card, self.grade_card)
        stats.addWidget(pace_card, 2)
        layout.addLayout(stats)

        # Vertical splitter: Interactive MapCanvas on top, ElevationChart on bottom
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(12)
        self.map_canvas = MapCanvas()
        self.map_canvas.waypoint_clicked.connect(self._on_map_waypoint_clicked)
        self.elev_chart = ElevationChart()
        self.elev_chart.hover_distance_changed.connect(self.map_canvas.set_highlight_distance)

        splitter.addWidget(self.map_canvas)
        splitter.addWidget(self.elev_chart)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 3)

        # Left: segment-by-segment hike builder (card); right: map + elevation profile
        self.segment_builder = SegmentBuilder(self.app_state)
        self.segment_builder.draft_changed.connect(self._on_draft_changed)
        self.segment_builder.preview_changed.connect(self.map_canvas.set_preview)
        self.segment_builder.focus_requested.connect(lambda coords: self.map_canvas.fit_when_shown(coords, 70.0))
        self.segment_builder.candidates_changed.connect(self.map_canvas.set_candidates)
        self.segment_builder.stops_changed.connect(self.map_canvas.set_stop_points)
        self.map_canvas.hover_geo.connect(
            lambda lat, lon: self.segment_builder.hover_at(lat, lon, self._pick_tolerance_m())
        )
        self.segment_builder.route_saved.connect(self._on_builder_saved)
        builder_card = Card("Build by trail segments", margins=(16, 14, 16, 14))
        builder_card.body.addWidget(self.segment_builder)
        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.setHandleWidth(14)
        main_split.addWidget(builder_card)
        main_split.addWidget(splitter)
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([400, 1000])
        layout.addWidget(main_split, 1)

        self.app_state.destination_changed.connect(lambda _: self.refresh_all())
        self.app_state.route_changed.connect(lambda _: self.refresh_from_active_route())
        self.app_state.settings_changed.connect(self.refresh_from_active_route)
        self.refresh_all()

    def ui_state(self) -> dict:
        return {"pace_model": self.pace_combo.currentData(), "pack_weight_kg": self.pack_spin.value()}

    def restore_ui_state(self, state: dict) -> None:
        idx = self.pace_combo.findData(state.get("pace_model"))
        if idx >= 0:
            self.pace_combo.setCurrentIndex(idx)
        if isinstance(state.get("pack_weight_kg"), (int, float)):
            self.pack_spin.setValue(float(state["pack_weight_kg"]))

    def refresh_all(self) -> None:
        self.region_combo.blockSignals(True)
        self.region_combo.clear()
        destinations = self.app_state.db.list_destinations()
        active_idx = 0
        for idx, d in enumerate(destinations):
            self.region_combo.addItem(f"{d.name} ({d.region})", d.id)
            if d.id == self.app_state.active_destination_id:
                active_idx = idx
        self.region_combo.setCurrentIndex(active_idx)
        self.region_combo.blockSignals(False)

        self._populate_route_combo()
        dest_id = self.app_state.active_destination_id
        self.segment_builder.set_pack(self.app_state.db.get_destination_pack(dest_id) if dest_id else None)
        self.refresh_from_active_route()

    def _on_draft_changed(self, route: Optional[ComposedRoute]) -> None:
        had_draft = self._draft is not None
        self._draft = route
        self.map_canvas.set_hike_legs(self.segment_builder.hike_legs() if route is not None else [])
        if route is not None or had_draft:
            self.refresh_from_active_route()
        if route is not None and not had_draft:
            # Fit the hike when it starts; adding segments keeps the user's zoom and position
            self.map_canvas.zoom_to_coordinates(route.coordinates)

    def start_hike_on_trail(self, trail_id: str) -> bool:
        """Preselect `trail_id` as the starting trail and start the hike there."""
        dest_id = self.app_state.active_destination_id
        self.segment_builder.set_pack(self.app_state.db.get_destination_pack(dest_id) if dest_id else None)
        return self.segment_builder.start_on_trail(trail_id)

    def start_hike_at_place(self, lat: float, lon: float, name: str = "") -> bool:
        """Start a segment-by-segment hike at a trailhead / place (from Explore Regions)."""
        dest_id = self.app_state.active_destination_id
        self.segment_builder.set_pack(self.app_state.db.get_destination_pack(dest_id) if dest_id else None)
        return self.segment_builder.start_at_place(lat, lon, name)

    def open_route(self, route_id: str) -> None:
        """Show a saved hike (from Explore Regions); it replaces the unsaved draft."""
        self.waypoints.clear()
        self.segment_builder.clear()
        self.app_state.set_active_route(route_id)
        self._populate_route_combo()

    def _on_builder_saved(self, route_id: str) -> None:
        self._draft = None
        self.app_state.set_active_route(route_id)
        self._populate_route_combo()
        route = self.app_state.get_active_route()
        answer = QMessageBox.question(
            self, "Hike Saved",
            f"Saved “{route.name if route else 'the hike'}”.\n\nDid you hike it? Log it now as a completed hike "
            "and find your photos from AegisPhotoVoyager along the way?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.open_i_hiked_this_wizard()

    def _populate_route_combo(self) -> None:
        self.route_combo.blockSignals(True)
        self.route_combo.clear()
        routes = self.app_state.db.list_routes(self.app_state.active_destination_id)
        active_idx = 0
        for idx, r in enumerate(routes):
            self.route_combo.addItem(r.name, r.id)
            if r.id == self.app_state.active_route_id:
                active_idx = idx
        self.route_combo.setCurrentIndex(active_idx)
        self.route_combo.blockSignals(False)

    def _on_region_combo_changed(self) -> None:
        dest_id = self.region_combo.currentData()
        if dest_id:
            self.waypoints.clear()
            self.app_state.set_active_destination(str(dest_id))

    def _on_route_combo_changed(self) -> None:
        r_id = self.route_combo.currentData()
        if r_id:
            self.waypoints.clear()
            self.segment_builder.clear()  # showing a saved route replaces the unsaved draft
            self.app_state.set_active_route(str(r_id))

    def refresh_from_active_route(self) -> None:
        route = self._draft or self.app_state.get_active_route()
        trails = self.app_state.get_active_trails()
        pois = self.app_state.get_active_pois()
        pins = self.app_state.get_active_route_photos()
        unit = str(self.app_state.settings.get("unit_system", "metric"))

        self.map_canvas.waypoints = list(self.waypoints)
        self.map_canvas.units = "metric" if unit == "metric" else "us"
        self.map_canvas.sync_basemap(self.app_state.db, self.app_state.active_destination_id)
        self.map_canvas.set_scene_data(trails=trails, pois=pois, route=route, photo_pins=pins)

        if route is not None and self._draft is None and route.id != getattr(self, "_fitted_route_id", None):
            self._fitted_route_id = route.id
            self.map_canvas.fit_when_shown(route.coordinates)
        if route:
            self.name_edit.setText(route.name)
            self.elev_chart.set_profile(route.elevation_profile, photo_pins=pins, unit_system=unit)
            self._update_metrics(route)
        else:
            self.elev_chart.set_profile([], unit_system=unit)
        self.play3d_btn.setEnabled(route is not None and len(route.coords_3d) >= 2)

    def play_hike_3d(self) -> None:
        """3D replay of the hike shown: the draft being built, else the saved route (with the
        photos of its recorded hikes)."""
        route = self._draft or self.app_state.get_active_route()
        if route is None or len(route.coords_3d) < 2:
            self.app_state.status_message.emit("Build or pick a hike to play it in 3D.")
            return
        unit = str(self.app_state.settings.get("unit_system", "metric"))
        show = bool(self.app_state.settings.get("replay_show_photos", True))
        with busy(self, "Building 3D terrain for the replay…", title="3D Replay"):
            from src.gui.replay3d import Replay3DDialog

            photos = [] if self._draft is not None else self.app_state.replay_photos(
                self.app_state.db.list_photo_pins(route_id=route.id))
            pack = self.app_state.db.get_destination_pack(route.destination_id)
            dlg = Replay3DDialog(route, pack, photos, "metric" if unit == "metric" else "us", self,
                                 show_photos=show, speed=float(self.app_state.settings.get("replay_speed", 1.0)))
        dlg.exec()
        if dlg.photos_check.isEnabled() and dlg.photos_check.isChecked() != show:  # remember the choice
            self.app_state.settings.set("replay_show_photos", dlg.photos_check.isChecked())
            self.app_state.settings.save()
        dlg.deleteLater()  # frees the terrain mesh now, not when the app quits

    def _update_metrics(self, route: ComposedRoute) -> None:
        unit = str(self.app_state.settings.get("unit_system", "metric"))
        model = str(self.pace_combo.currentData() or "naismith")
        dur_min = estimate_pace_minutes(
            distance_m=route.distance_m,
            elevation_gain_m=route.elevation_gain_m,
            elevation_loss_m=route.elevation_loss_m,
            model=model,
            base_speed_kmh=float(self.app_state.settings.get("base_speed_kmh", 4.5)),
            pack_weight_kg=float(self.pack_spin.value()),
        )
        route.estimated_duration_min = dur_min

        max_grade = max((abs(s.grade_pct) for s in route.elevation_profile), default=0.0)
        avg_grade = (
            sum(abs(s.grade_pct) for s in route.elevation_profile) / max(1, len(route.elevation_profile))
            if route.elevation_profile
            else 0.0
        )

        self.dist_card.set_value(format_distance(route.distance_m, unit))
        self.gain_card.set_value(f"+{format_elevation(route.elevation_gain_m, unit)}")
        self.loss_card.set_value(f"-{format_elevation(route.elevation_loss_m, unit)}")
        self.time_card.set_value(format_duration_min(dur_min), f"{model.capitalize()} rule")
        self.grade_card.set_value(f"{max_grade:.1f}% max", f"{avg_grade:.1f}% avg")

    def _recalculate_pace(self) -> None:
        route = self._draft or self.app_state.get_active_route()
        if route:
            self._update_metrics(route)

    def _pick_tolerance_m(self) -> float:
        return 12.0 * self.map_canvas.metres_per_pixel()  # ~12 px on screen at any zoom

    def _on_map_waypoint_clicked(self, lat: float, lon: float) -> None:
        if self.segment_builder.pick_at(lat, lon, self._pick_tolerance_m()):
            return
        if self._draft is not None:
            self.app_state.status_message.emit(
                "Building a hike by segments: save or clear it, or turn on 'Pick trails on the map'."
            )
            return
        self.waypoints.append((lat, lon))
        self._rebuild_route_from_waypoints()

    def _rebuild_route_from_waypoints(self) -> None:
        trails = self.app_state.get_active_trails()
        if not self.waypoints or not trails:
            return
        name = self.name_edit.text().strip() or "Traced Trail Route"
        composed = snap_and_route_waypoints(
            segments=trails,
            waypoints=self.waypoints,
            name=name,
            destination_id=self.app_state.active_destination_id or "",
        )
        self.app_state.db.upsert_route(composed)
        self.app_state.set_active_route(composed.id)
        self._populate_route_combo()
        self.refresh_from_active_route()

    def undo_waypoint(self) -> None:
        if self.waypoints:
            self.waypoints.pop()
            if self.waypoints:
                self._rebuild_route_from_waypoints()
            else:
                self.refresh_from_active_route()

    def clear_trace(self) -> None:
        self.waypoints.clear()
        self.map_canvas.waypoints = []
        self.map_canvas.update()

    def close_loop(self) -> None:
        route = self.app_state.get_active_route()
        if not route:
            return
        looped = close_loop_route(route, self.app_state.get_active_trails())
        self.app_state.db.upsert_route(looped)
        self.app_state.set_active_route(looped.id)

    def reverse_current_route(self) -> None:
        route = self.app_state.get_active_route()
        if not route:
            return
        rev = reverse_route(route)
        self.app_state.db.upsert_route(rev)
        self.app_state.set_active_route(rev.id)

    def save_current_route(self) -> None:
        route = self.app_state.get_active_route()
        if not route:
            return
        route.name = self.name_edit.text().strip() or route.name
        self.app_state.db.upsert_route(route)
        self._populate_route_combo()
        self.app_state.status_message.emit(f"Saved route '{route.name}'.")

    def open_i_hiked_this_wizard(self) -> None:
        route = self.app_state.get_active_route()
        if not route:
            trails = self.app_state.get_active_trails()
            if not trails:
                return
            from src.hiking.routing import route_from_trail_segment
            route = route_from_trail_segment(trails[0])
            self.app_state.db.upsert_route(route)
            self.app_state.set_active_route(route.id)

        route.name = self.name_edit.text().strip() or route.name
        dlg = IHikedThisDialog(self.app_state, route, self)
        created = dlg.created_session if dlg.exec() else None
        dlg.deleteLater()
        if created:
            self.hike_logged_and_mapped.emit(created.id)

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import GPX or GeoJSON Route",
            "",
            "Trail Files (*.gpx *.geojson *.json);;All Files (*)",
        )
        if not path:
            return
        with busy(self, "Importing route…", title="Importing Route"):
            content = open(path, "r", encoding="utf-8").read()
            dest_id = self.app_state.active_destination_id or ""
            if path.lower().endswith(".gpx"):
                route = import_route_from_gpx(content, destination_id=dest_id)
            else:
                route = import_route_from_geojson(content, destination_id=dest_id)
            self.app_state.db.upsert_route(route)
            self.app_state.set_active_route(route.id)
        self._populate_route_combo()

    def export_gpx_file(self) -> None:
        route = self.app_state.get_active_route()
        if not route:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Route as GPX 1.1 or GeoJSON",
            f"{route.name.lower().replace(' ', '_')}.gpx",
            "GPX 1.1 (*.gpx);;GeoJSON (*.geojson)",
        )
        if not path:
            return
        if path.lower().endswith(".geojson"):
            open(path, "w", encoding="utf-8").write(export_route_to_geojson(route))
        else:
            open(path, "w", encoding="utf-8").write(export_route_to_gpx(route))
        self.app_state.status_message.emit(f"Exported route to {path}")

