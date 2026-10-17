"""Interactive 'Re-experience Hike Map' panel:
Lets the user replay any recorded/traced hike on an interactive topographic map,
scrubbing through elevation and time while viewing geotagged photos matched from
AegisPhotoVoyager along the trail corridor.
"""
from pathlib import Path
from typing import List, Optional
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.core import paths
from src.gui.app_state import AppState
from src.gui.charts.elevation_chart import ElevationChart
from src.gui.charts.map_canvas import MapCanvas
from src.gui.widgets import SectionHeader, StatCard
from src.hiking.hike_replay import build_interactive_map_data, export_interactive_hike_html
from src.hiking.models import TrailPhotoPin
from src.hiking.units import format_distance, format_elevation
from src.gui.busy import busy, run_in_background


class ReexperiencePanel(QWidget):
    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.photo_pins: List[TrailPhotoPin] = []
        self._max_dist_m: float = 1000.0

        self.play_timer = QTimer(self)
        self.play_timer.setInterval(120)
        self.play_timer.timeout.connect(self._advance_playback)

        layout = QVBoxLayout(self)
        header = SectionHeader(
            "Re-experience Hike — Interactive Trail & Photo Replay Map",
            "Scrub or play through your completed hikes and relive geotagged photos matched from AegisPhotoVoyager.",
        )

        rescan_btn = QPushButton("Rescan Voyager Photos")
        rescan_btn.clicked.connect(self.rescan_voyager_for_active_session)
        export_html_btn = QPushButton("Export Interactive HTML Map...")
        export_html_btn.clicked.connect(self.export_html_map)
        open_browser_btn = QPushButton("Open Interactive Map in Browser")
        open_browser_btn.setObjectName("PrimaryButton")
        open_browser_btn.clicked.connect(self.open_in_browser)

        header.actions_layout.addWidget(rescan_btn)
        header.actions_layout.addWidget(export_html_btn)
        header.actions_layout.addWidget(open_browser_btn)
        layout.addWidget(header)

        # Session selector row
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Recorded Hike Session:"))
        self.session_combo = QComboBox()
        self.session_combo.currentIndexChanged.connect(self._on_session_combo_changed)
        sel_row.addWidget(self.session_combo, 1)
        layout.addLayout(sel_row)

        # Summary stat cards
        cards_row = QHBoxLayout()
        self.dist_card = StatCard("Hike Distance", "0.0 km")
        self.gain_card = StatCard("Elevation Gain", "+0 m")
        self.photos_card = StatCard("Matched Voyager Photos", "0")
        self.cursor_card = StatCard("Replay Position", "0.00 km")
        cards_row.addWidget(self.dist_card)
        cards_row.addWidget(self.gain_card)
        cards_row.addWidget(self.photos_card)
        cards_row.addWidget(self.cursor_card)
        layout.addLayout(cards_row)

        # Playback controls bar
        play_row = QHBoxLayout()
        self.play_btn = QPushButton("▶ Play Hike Replay")
        self.play_btn.setObjectName("PrimaryButton")
        self.play_btn.clicked.connect(self.toggle_playback)
        play_row.addWidget(self.play_btn)

        self.scrubber = QSlider(Qt.Orientation.Horizontal)
        self.scrubber.setRange(0, 1000)
        self.scrubber.valueChanged.connect(self._on_scrubber_changed)
        play_row.addWidget(self.scrubber, 1)
        layout.addLayout(play_row)

        # Main splitter: Left (MapCanvas + ElevationChart), Right (Photo Story Cards)
        main_split = QSplitter(Qt.Orientation.Horizontal)

        left_split = QSplitter(Qt.Orientation.Vertical)
        self.map_canvas = MapCanvas()
        self.map_canvas.photo_clicked.connect(self._on_photo_pin_clicked)
        self.elev_chart = ElevationChart()
        self.elev_chart.hover_distance_changed.connect(self._set_replay_distance)
        left_split.addWidget(self.map_canvas)
        left_split.addWidget(self.elev_chart)
        left_split.setStretchFactor(0, 6)
        left_split.setStretchFactor(1, 3)

        right_frame = QFrame()
        right_frame.setObjectName("CardFrame")
        right_col = QVBoxLayout(right_frame)
        right_col.addWidget(QLabel("<b>Trail Photo Timeline (AegisPhotoVoyager)</b>"))

        self.photo_list = QListWidget()
        self.photo_list.currentRowChanged.connect(self._on_photo_row_selected)
        right_col.addWidget(self.photo_list, 1)

        self.thumb_lbl = QLabel("Select or scrub to a photo pin along the trail")
        self.thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_lbl.setMinimumHeight(150)
        self.thumb_lbl.setStyleSheet("background-color: #0B1610; border: 1px solid #2C4637; border-radius: 8px;")
        right_col.addWidget(self.thumb_lbl)

        self.photo_meta_lbl = QLabel("")
        self.photo_meta_lbl.setWordWrap(True)
        self.photo_meta_lbl.setStyleSheet("color: #98B2A1; font-size: 9pt;")
        right_col.addWidget(self.photo_meta_lbl)

        main_split.addWidget(left_split)
        main_split.addWidget(right_frame)
        main_split.setStretchFactor(0, 7)
        main_split.setStretchFactor(1, 4)
        layout.addWidget(main_split, 1)

        self.app_state.session_changed.connect(lambda _: self.refresh_all())
        self.app_state.route_changed.connect(lambda _: self.refresh_all())
        self.refresh_all()

    def refresh_all(self) -> None:
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        sessions = self.app_state.db.list_sessions()
        active_idx = 0
        for idx, s in enumerate(sessions):
            route = self.app_state.db.get_route(s.route_id) if s.route_id else None
            r_name = route.name if route else "Recorded Hike"
            date_part = (s.started_at or "")[:10]
            self.session_combo.addItem(f"{r_name} ({date_part}) — [{s.status}]", s.id)
            if s.id == self.app_state.active_session_id:
                active_idx = idx
        self.session_combo.setCurrentIndex(active_idx)
        self.session_combo.blockSignals(False)
        self._load_active_session_view()

    def _on_session_combo_changed(self) -> None:
        s_id = self.session_combo.currentData()
        if s_id:
            self.app_state.set_active_session(str(s_id))

    def _load_active_session_view(self) -> None:
        session = self.app_state.get_active_session()
        route = None
        if session and session.route_id:
            route = self.app_state.db.get_route(session.route_id)
        if not route:
            route = self.app_state.get_active_route()

        unit = str(self.app_state.settings.get("unit_system", "metric"))
        if session:
            self.photo_pins = self.app_state.db.list_photo_pins(session_id=session.id)
        elif route:
            self.photo_pins = self.app_state.db.list_photo_pins(route_id=route.id)
        else:
            self.photo_pins = []

        trails = self.app_state.db.list_trails(route.destination_id if route else self.app_state.active_destination_id)
        pois = self.app_state.db.list_pois(route.destination_id if route else self.app_state.active_destination_id)

        self.map_canvas.units = "metric" if unit == "metric" else "us"
        self.map_canvas.sync_basemap(
            self.app_state.db, route.destination_id if route else self.app_state.active_destination_id
        )
        self.map_canvas.set_scene_data(trails=trails, pois=pois, route=route, photo_pins=self.photo_pins)
        if route:
            self._max_dist_m = max(1.0, route.distance_m)
            self.elev_chart.set_profile(route.elevation_profile, photo_pins=self.photo_pins, unit_system=unit)
            self.dist_card.set_value(format_distance(route.distance_m, unit))
            self.gain_card.set_value(f"+{format_elevation(route.elevation_gain_m, unit)}")
        self.photos_card.set_value(str(len(self.photo_pins)), "Geotagged along corridor")

        self.photo_list.clear()
        for pin in self.photo_pins:
            item = QListWidgetItem(
                f"📸 {pin.caption} — {format_distance(pin.distance_along_route_m, unit)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, pin.id)
            self.photo_list.addItem(item)
        if self.photo_pins:
            self.photo_list.setCurrentRow(0)
            self._display_photo_pin(self.photo_pins[0])
        else:
            self.thumb_lbl.setPixmap(QPixmap())
            self.thumb_lbl.setText("No geotagged photos matched yet — click 'Rescan Voyager Photos'")
            self.photo_meta_lbl.setText("")

    def _display_photo_pin(self, pin: TrailPhotoPin) -> None:
        unit = str(self.app_state.settings.get("unit_system", "metric"))
        img_candidate = pin.thumbnail_path or pin.file_path
        if img_candidate and Path(img_candidate).exists():
            pix = QPixmap(img_candidate)
            if not pix.isNull():
                self.thumb_lbl.setPixmap(
                    pix.scaled(280, 170, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
            else:
                self.thumb_lbl.setText(f"📸 {pin.caption}")
        else:
            self.thumb_lbl.setPixmap(QPixmap())
            self.thumb_lbl.setText(f"📸 {pin.caption}\n({pin.lat:.4f}, {pin.lon:.4f})")

        people_str = f" • People: {', '.join(pin.people_names)}" if pin.people_names else ""
        mood_str = f" • Mood: {pin.mood}" if pin.mood else ""
        ai_str = f"\nAI Summary: {pin.ai_summary}" if pin.ai_summary else ""
        self.photo_meta_lbl.setText(
            f"Trail Mark: {format_distance(pin.distance_along_route_m, unit)} • "
            f"Elev: {format_elevation(pin.ele_m, unit)} • Taken: {pin.taken_at[:19]}"
            f"{people_str}{mood_str}{ai_str}"
        )

    def _on_photo_row_selected(self, row: int) -> None:
        if 0 <= row < len(self.photo_pins):
            pin = self.photo_pins[row]
            self._display_photo_pin(pin)
            self._set_replay_distance(pin.distance_along_route_m, update_slider=True)

    def _on_photo_pin_clicked(self, pin_id: str) -> None:
        for idx, pin in enumerate(self.photo_pins):
            if pin.id == pin_id:
                self.photo_list.setCurrentRow(idx)
                break

    def _on_scrubber_changed(self, val: int) -> None:
        dist_m = (val / 1000.0) * self._max_dist_m
        self._set_replay_distance(dist_m, update_slider=False)

    def _set_replay_distance(self, dist_m: float, update_slider: bool = True) -> None:
        unit = str(self.app_state.settings.get("unit_system", "metric"))
        self.map_canvas.set_highlight_distance(dist_m)
        self.elev_chart.set_highlight_distance(dist_m)
        self.cursor_card.set_value(format_distance(dist_m, unit))
        if update_slider and self._max_dist_m > 0:
            self.scrubber.blockSignals(True)
            self.scrubber.setValue(int(min(1000, max(0, (dist_m / self._max_dist_m) * 1000))))
            self.scrubber.blockSignals(False)

        # Highlight nearest photo pin if within 150m
        if self.photo_pins:
            nearest = min(self.photo_pins, key=lambda p: abs(p.distance_along_route_m - dist_m))
            if abs(nearest.distance_along_route_m - dist_m) <= max(150.0, self._max_dist_m * 0.04):
                self._display_photo_pin(nearest)

    def toggle_playback(self) -> None:
        if self.play_timer.isActive():
            self.play_timer.stop()
            self.play_btn.setText("▶ Play Hike Replay")
        else:
            if self.scrubber.value() >= 1000:
                self.scrubber.setValue(0)
            self.play_timer.start()
            self.play_btn.setText("⏸ Pause Replay")

    def _advance_playback(self) -> None:
        nxt = self.scrubber.value() + 15
        if nxt >= 1000:
            self.scrubber.setValue(1000)
            self.toggle_playback()
        else:
            self.scrubber.setValue(nxt)

    def rescan_voyager_for_active_session(self) -> None:
        session = self.app_state.get_active_session()
        route = self.app_state.get_active_route()
        if session and session.route_id:
            route = self.app_state.db.get_route(session.route_id) or route
        if not route:
            return
        matched = run_in_background(
            self,
            "Rescanning AegisPhotoVoyager library…",
            self.app_state.voyager_bridge.match_photos_for_route,
            title="Matching Photos",
            route=route,
            session_id=session.id if session else None,
            max_corridor_m=float(self.app_state.settings.get("photo_match_corridor_m", 250.0)),
        )
        with busy(self, "Rebuilding hike replay map…", title="Matching Photos"):
            for pin in matched:
                self.app_state.db.save_photo_pin(pin)
            if session:
                dest = self.app_state.db.get_destination(route.destination_id)
                session.interactive_map_data = build_interactive_map_data(
                    route=route,
                    session=session,
                    photos=matched,
                    destination_name=dest.name if dest else route.destination_id,
                )
                self.app_state.db.save_session(session)
            self._load_active_session_view()

    def _ensure_map_html(self) -> Optional[Path]:
        session = self.app_state.get_active_session()
        route = self.app_state.get_active_route()
        if session and session.route_id:
            route = self.app_state.db.get_route(session.route_id) or route
        if not route:
            return None
        dest = self.app_state.db.get_destination(route.destination_id)
        map_data = (
            session.interactive_map_data
            if session and session.interactive_map_data
            else build_interactive_map_data(
                route=route,
                session=session,
                photos=self.photo_pins,
                destination_name=dest.name if dest else route.destination_id,
            )
        )
        out_id = session.id if session else route.id
        html_path = paths.get_hike_maps_dir() / f"{out_id}.html"
        export_interactive_hike_html(map_data, html_path)
        return html_path

    def export_html_map(self) -> None:
        src_html = self._ensure_map_html()
        if not src_html:
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Export Interactive Hike Replay HTML Map",
            "interactive_hike_replay.html",
            "HTML Files (*.html)",
        )
        if target:
            Path(target).write_text(src_html.read_text(encoding="utf-8"), encoding="utf-8")
            self.app_state.status_message.emit(f"Exported interactive hike map to {target}")

    def open_in_browser(self) -> None:
        html_path = self._ensure_map_html()
        if html_path and html_path.exists():
            webbrowser.open(html_path.as_uri())

