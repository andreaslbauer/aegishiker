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
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.charts.elevation_chart import ElevationChart
from src.gui.charts.map_canvas import MapCanvas
from src.gui.panels.segment_builder import SegmentBuilder
from src.gui.dialogs.i_hiked_this_dialog import IHikedThisDialog
from src.gui.help_text import TOOLTIPS
from src.gui.widgets import SectionHeader, StatCard
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

        layout = QVBoxLayout(self)
        header = SectionHeader(
            "Trail Composer & Elevation Studio",
            "Build a hike trail by trail on the left, or click the map to trace one. Inspect elevation grades, or click '✓ I Hiked This Trail'.",
        )

        # Prominent "✓ I Hiked This Trail" button
        self.i_hiked_btn = QPushButton("✓ I Hiked This Trail")
        self.i_hiked_btn.setObjectName("PrimaryButton")
        self.i_hiked_btn.setToolTip(TOOLTIPS["i_hiked_this"])
        self.i_hiked_btn.clicked.connect(self.open_i_hiked_this_wizard)
        header.actions_layout.addWidget(self.i_hiked_btn)
        layout.addWidget(header)

        # Top control bar: Region selector, Route name, Pacing model, Pack weight
        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("Region:"))
        self.region_combo = QComboBox()
        self.region_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.region_combo.setMinimumContentsLength(20)
        self.region_combo.currentIndexChanged.connect(self._on_region_combo_changed)
        ctrl_row.addWidget(self.region_combo, 1)

        ctrl_row.addWidget(QLabel("Saved Routes:"))
        self.route_combo = QComboBox()
        self.route_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.route_combo.setMinimumContentsLength(22)
        self.route_combo.currentIndexChanged.connect(self._on_route_combo_changed)
        ctrl_row.addWidget(self.route_combo, 1)

        ctrl_row.addWidget(QLabel("Route Name:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("My Custom Trail Loop")
        ctrl_row.addWidget(self.name_edit, 1)

        ctrl_row.addWidget(QLabel("Pace Rule:"))
        self.pace_combo = QComboBox()
        for model_name in ["naismith", "langmuir", "tobler"]:
            self.pace_combo.addItem(model_name.capitalize(), model_name)
        self.pace_combo.currentIndexChanged.connect(self._recalculate_pace)
        ctrl_row.addWidget(self.pace_combo)

        ctrl_row.addWidget(QLabel("Pack:"))
        self.pack_spin = QDoubleSpinBox()
        self.pack_spin.setRange(0.0, 35.0)
        self.pack_spin.setValue(float(self.app_state.settings.get("default_pack_weight_kg", 6.0)))
        self.pack_spin.setSuffix(" kg")
        self.pack_spin.valueChanged.connect(self._recalculate_pace)
        ctrl_row.addWidget(self.pack_spin)
        layout.addLayout(ctrl_row)

        # Live metric stat cards
        cards_row = QHBoxLayout()
        self.dist_card = StatCard("Total Distance", "0.0 km")
        self.gain_card = StatCard("Elevation Gain", "+0 m")
        self.loss_card = StatCard("Elevation Loss", "-0 m")
        self.time_card = StatCard("Estimated Time", "0m")
        self.grade_card = StatCard("Max / Avg Grade", "0.0%")
        cards_row.addWidget(self.dist_card)
        cards_row.addWidget(self.gain_card)
        cards_row.addWidget(self.loss_card)
        cards_row.addWidget(self.time_card)
        cards_row.addWidget(self.grade_card)
        layout.addLayout(cards_row)

        # Vertical splitter: Interactive MapCanvas on top, ElevationChart on bottom
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.map_canvas = MapCanvas()
        self.map_canvas.waypoint_clicked.connect(self._on_map_waypoint_clicked)
        self.elev_chart = ElevationChart()
        self.elev_chart.hover_distance_changed.connect(self.map_canvas.set_highlight_distance)

        splitter.addWidget(self.map_canvas)
        splitter.addWidget(self.elev_chart)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 3)

        # Left: segment-by-segment hike builder; right: map + elevation profile
        self.segment_builder = SegmentBuilder(self.app_state)
        self.segment_builder.draft_changed.connect(self._on_draft_changed)
        self.segment_builder.preview_changed.connect(self.map_canvas.set_preview)
        self.segment_builder.route_saved.connect(self._on_builder_saved)
        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(self.segment_builder)
        main_split.addWidget(splitter)
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([380, 1000])
        layout.addWidget(main_split, 1)

        # Bottom toolbar: Undo, Clear, Close Loop, Reverse, Save, GPX/GeoJSON Import/Export
        tool_row = QHBoxLayout()
        undo_btn = QPushButton("Undo Waypoint")
        undo_btn.clicked.connect(self.undo_waypoint)
        clear_btn = QPushButton("Clear Trace")
        clear_btn.clicked.connect(self.clear_trace)
        loop_btn = QPushButton("Close Loop")
        loop_btn.clicked.connect(self.close_loop)
        rev_btn = QPushButton("Reverse Route")
        rev_btn.clicked.connect(self.reverse_current_route)
        save_btn = QPushButton("Save Route")
        save_btn.clicked.connect(self.save_current_route)
        gpx_in_btn = QPushButton("Import GPX/GeoJSON...")
        gpx_in_btn.clicked.connect(self.import_file)
        gpx_out_btn = QPushButton("Export GPX...")
        gpx_out_btn.clicked.connect(self.export_gpx_file)

        for b in [undo_btn, clear_btn, loop_btn, rev_btn, save_btn, gpx_in_btn, gpx_out_btn]:
            tool_row.addWidget(b)
        layout.addLayout(tool_row)

        self.app_state.destination_changed.connect(lambda _: self.refresh_all())
        self.app_state.route_changed.connect(lambda _: self.refresh_from_active_route())
        self.app_state.settings_changed.connect(self.refresh_from_active_route)
        self.refresh_all()

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
        if route is not None or had_draft:
            self.refresh_from_active_route()
        if route is not None:
            self.map_canvas.zoom_to_coordinates(route.coordinates)

    def _on_builder_saved(self, route_id: str) -> None:
        self._draft = None
        self.app_state.set_active_route(route_id)
        self._populate_route_combo()

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

        if route:
            self.name_edit.setText(route.name)
            self.elev_chart.set_profile(route.elevation_profile, photo_pins=pins, unit_system=unit)
            self._update_metrics(route)
        else:
            self.elev_chart.set_profile([], unit_system=unit)

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

    def _on_map_waypoint_clicked(self, lat: float, lon: float) -> None:
        if self._draft is not None:
            self.app_state.status_message.emit(
                "Building a hike by segments: save or clear it before tracing by clicking the map."
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
        if dlg.exec() and dlg.created_session:
            self.hike_logged_and_mapped.emit(dlg.created_session.id)

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

