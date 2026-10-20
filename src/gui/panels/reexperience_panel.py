"""Interactive 'Re-experience Hike Map' panel:
Lets the user replay any recorded/traced hike on an interactive topographic map,
scrubbing through elevation and time while viewing geotagged photos matched from
AegisPhotoVoyager along the trail corridor.
"""
import html
from pathlib import Path
from typing import List, Optional
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core import paths
from src.gui.app_state import AppState
from src.gui.charts.elevation_chart import ElevationChart
from src.gui.charts.map_canvas import MapCanvas
from src.gui.charts.photo_preview import format_datetime, format_time
from src.gui.widgets import Card, SectionHeader, StatCard, page_layout, stat_row
from src.hiking.hike_replay import build_interactive_map_data, export_interactive_hike_html
from src.hiking.models import TrailPhotoPin
from src.hiking.photo_matcher import dedupe_pins, photo_keys
from src.hiking.hike_info import hike_date_label, hike_title, hike_trail_names, people_label, real_people
from src.hiking.hike_report import hike_companions, hike_details
from src.gui.dialogs.hike_details_dialog import HikeDetailsDialog
from src.gui.dialogs.hike_picker_dialog import HikePickerDialog
from src.gui.dialogs.photo_review_dialog import PhotoReviewDialog
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

        layout = page_layout(self)
        header = SectionHeader(
            "Re-experience Hike",
            "Relive a completed hike on the map, in 3D, and through the photos you took along the way.",
        )
        self.choose_btn = QPushButton("📂  Choose Hike…")
        self.choose_btn.setObjectName("PrimaryButton")
        self.choose_btn.setToolTip("Browse your hikes by year with trails, people and photos")
        self.choose_btn.clicked.connect(self.choose_hike)
        more_btn = QToolButton()
        more_btn.setText("More  ▾")
        more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more = QMenu(more_btn)
        more.addAction("Rescan AegisPhotoVoyager photos", self.rescan_voyager_for_active_session)
        more.addSeparator()
        more.addAction("Open interactive map in browser", self.open_in_browser)
        more.addAction("Export interactive HTML map…", self.export_html_map)
        more_btn.setMenu(more)
        self.site_btn = QPushButton("🌐  Create Website…")
        self.site_btn.setToolTip("Make a website about this hike (summary, Play the Hike, photos, map), "
                                 "optionally password protected, and publish it via FTP")
        self.site_btn.clicked.connect(self.create_website)
        header.actions_layout.addWidget(more_btn)
        header.actions_layout.addWidget(self.site_btn)
        header.actions_layout.addWidget(self.choose_btn)
        layout.addWidget(header)

        # Current hike: name, date, trails, people — rename / delete
        hike_card = Card(margins=(18, 14, 18, 14))
        hike_row = QHBoxLayout()
        hike_row.setSpacing(12)
        self.hike_lbl = QLabel()
        self.hike_lbl.setWordWrap(True)
        self.hike_lbl.setTextFormat(Qt.TextFormat.RichText)
        hike_row.addWidget(self.hike_lbl, 1)
        self.details_btn = QPushButton("Details…")
        self.details_btn.setObjectName("FlatButton")
        self.details_btn.setToolTip("Name, who came along, weather and notes")
        self.details_btn.clicked.connect(self.edit_details)
        self.review_btn = QPushButton("Review Photos…")
        self.review_btn.setObjectName("FlatButton")
        self.review_btn.setToolTip("Go through the hike's photos and leave out the ones you don't want")
        self.review_btn.clicked.connect(self.review_photos)
        self.rename_btn = QPushButton("Rename…")
        self.rename_btn.setObjectName("FlatButton")
        self.rename_btn.clicked.connect(self.rename_hike)
        self.delete_btn = QPushButton("Delete…")
        self.delete_btn.setObjectName("DangerButton")
        self.delete_btn.clicked.connect(self.delete_hike)
        for b in (self.details_btn, self.review_btn, self.rename_btn, self.delete_btn):
            hike_row.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        hike_card.body.addLayout(hike_row)
        layout.addWidget(hike_card)

        # Summary stat cards
        self.dist_card = StatCard("Hike Distance", "0.0 km")
        self.gain_card = StatCard("Elevation Gain", "+0 m")
        self.photos_card = StatCard("Photos", "0")
        self.cursor_card = StatCard("Replay Position", "0.00 km")
        layout.addLayout(stat_row(self.dist_card, self.gain_card, self.photos_card, self.cursor_card))

        # Playback controls
        play_card = Card(margins=(14, 10, 14, 10))
        play_row = QHBoxLayout()
        play_row.setSpacing(10)
        self.replay3d_btn = QPushButton("🎬  Play 3D Replay")
        self.replay3d_btn.setObjectName("PrimaryButton")
        self.replay3d_btn.setToolTip("Fly along the hike in 3D; photos zoom in to fill the screen where they were taken")
        self.replay3d_btn.clicked.connect(self.open_3d_replay)
        play_row.addWidget(self.replay3d_btn)
        self.play_btn = QPushButton("▶ Animate on Map")
        self.play_btn.clicked.connect(self.toggle_playback)
        play_row.addWidget(self.play_btn)
        self.scrubber = QSlider(Qt.Orientation.Horizontal)
        self.scrubber.setRange(0, 1000)
        self.scrubber.valueChanged.connect(self._on_scrubber_changed)
        play_row.addSpacing(8)
        play_row.addWidget(self.scrubber, 1)
        play_card.body.addLayout(play_row)
        layout.addWidget(play_card)

        # Main splitter: Left (MapCanvas + ElevationChart), Right (Photo Story Cards)
        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.setHandleWidth(14)

        left_split = QSplitter(Qt.Orientation.Vertical)
        left_split.setHandleWidth(12)
        self.map_canvas = MapCanvas()
        self.map_canvas.show_photo_thumbnails = True  # photos on the map; hover for a larger view
        self.map_canvas.photo_clicked.connect(self._on_photo_pin_clicked)
        self.elev_chart = ElevationChart()
        self.elev_chart.hover_distance_changed.connect(self._set_replay_distance)
        left_split.addWidget(self.map_canvas)
        left_split.addWidget(self.elev_chart)
        left_split.setStretchFactor(0, 6)
        left_split.setStretchFactor(1, 3)

        right_frame = Card("Photo timeline", margins=(14, 14, 14, 14))
        right_col = right_frame.body

        self.photo_list = QListWidget()
        self.photo_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.photo_list.currentRowChanged.connect(self._on_photo_row_selected)
        self.photo_list.itemSelectionChanged.connect(self._update_remove_btn)
        self.photo_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.photo_list.customContextMenuRequested.connect(self._photo_list_menu)
        remove_sc = QShortcut(QKeySequence.StandardKey.Delete, self.photo_list)
        remove_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        remove_sc.activated.connect(self.remove_selected_photos)
        right_col.addWidget(self.photo_list, 1)

        self.remove_photo_btn = QPushButton("Remove from Hike")
        self.remove_photo_btn.setObjectName("FlatButton")
        self.remove_photo_btn.setToolTip("Leave the selected photos out of this hike (Delete). "
                                         "The photo files and AegisPhotoVoyager are not touched.")
        self.remove_photo_btn.clicked.connect(self.remove_selected_photos)
        self.remove_photo_btn.setEnabled(False)
        right_col.addWidget(self.remove_photo_btn, 0, Qt.AlignmentFlag.AlignRight)

        self.thumb_lbl = QLabel("Select or scrub to a photo pin along the trail")
        self.thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_lbl.setWordWrap(True)
        self.thumb_lbl.setMinimumHeight(150)
        self.thumb_lbl.setStyleSheet("background-color: #0B1610; border: 1px solid #223829; border-radius: 10px;")
        right_col.addWidget(self.thumb_lbl)

        self.photo_meta_lbl = QLabel("")
        self.photo_meta_lbl.setWordWrap(True)
        self.photo_meta_lbl.setObjectName("Muted")
        right_col.addWidget(self.photo_meta_lbl)

        main_split.addWidget(left_split)
        main_split.addWidget(right_frame)
        main_split.setStretchFactor(0, 7)
        main_split.setStretchFactor(1, 3)
        # Explicit sizes: from size hints alone the map side started collapsed to zero width
        main_split.setChildrenCollapsible(False)
        left_split.setChildrenCollapsible(False)
        self.map_canvas.setMinimumWidth(320)
        right_frame.setMinimumWidth(240)
        main_split.setSizes([1000, 420])
        left_split.setSizes([620, 260])
        layout.addWidget(main_split, 1)

        self.app_state.session_changed.connect(lambda _: self.refresh_all())
        self.app_state.sessions_changed.connect(self.refresh_all)
        self.app_state.route_changed.connect(lambda _: self.refresh_all())
        self.refresh_all()

    def refresh_all(self) -> None:
        self._load_active_session_view()

    def _update_hike_header(self, session, route) -> None:
        if session is None:
            self.hike_lbl.setText("<span style='color:#98B2A1'>No recorded hike yet — use “✓ I Hiked This Trail” "
                                  "in the Trail Composer, or sync a hike from your phone.</span>")
        else:
            trails = hike_trail_names(route, {t.id: t for t in self.app_state.db.list_trails(session.pack_id or "")})
            parts = [f"<span style='font-size:12pt; font-weight:700'>{html.escape(hike_title(session, route))}</span>",
                     f"<span style='color:#93AD9C'>{html.escape(hike_date_label(session))}</span>"]
            if trails and not (len(trails) == 1 and route is not None and trails[0] == route.name):
                shown = trails if len(trails) <= 6 else trails[:5] + [f"+{len(trails) - 5} more"]
                parts.append(f"<span style='color:#93AD9C'>Trails: {html.escape(' → '.join(shown))}</span>")
            people = hike_companions(session, self.photo_pins)
            if people:
                parts.append(f"<span style='color:#2ECC71'>👥 {html.escape(people_label(people, 8))}</span>")
            weather = hike_details(session).get("weather")
            if weather:
                parts.append(f"<span style='color:#93AD9C'>⛅ {html.escape(weather)}</span>")
            self.hike_lbl.setText("<br>".join(parts))
        for b in (self.details_btn, self.review_btn, self.rename_btn, self.delete_btn, self.site_btn):
            b.setEnabled(session is not None)

    def choose_hike(self) -> None:
        dlg = HikePickerDialog(self.app_state, self)
        chosen = dlg.selected_session_id if dlg.exec() else None
        dlg.deleteLater()
        if chosen:
            self.app_state.set_active_session(chosen)
        else:
            self.refresh_all()  # hikes may have been renamed or deleted in the dialog

    def rename_hike(self) -> None:
        session = self.app_state.get_active_session()
        if session is None:
            return
        route = self.app_state.db.get_route(session.route_id) if session.route_id else None
        title, ok = QInputDialog.getText(self, "Rename Hike", "Hike name:", text=hike_title(session, route))
        if ok and title.strip():
            self.app_state.rename_session(session.id, title)

    def _active(self):
        session = self.app_state.get_active_session()
        route = self.app_state.db.get_route(session.route_id) if session and session.route_id else None
        return session, route

    def edit_details(self) -> None:
        session, _route = self._active()
        if session is None:
            return
        dlg = HikeDetailsDialog(self.app_state, session, self)
        dlg.exec()
        dlg.deleteLater()

    def review_photos(self) -> None:
        session, route = self._active()
        if session is None:
            return
        if not self.photo_pins:
            QMessageBox.information(self, "Review Photos", "This hike has no photos yet — use More ▸ Rescan "
                                                           "AegisPhotoVoyager photos to find them.")
            return
        unit = str(self.app_state.settings.get("unit_system", "metric"))
        dlg = PhotoReviewDialog(self.photo_pins, unit, hike_title(session, route), self)
        removed = dlg.removed_pins() if dlg.exec() else []
        dlg.deleteLater()
        self._remove_photos(removed)

    def remove_selected_photos(self) -> None:
        session, _route = self._active()
        rows = sorted({self.photo_list.row(i) for i in self.photo_list.selectedItems()})
        pins = [self.photo_pins[r] for r in rows if 0 <= r < len(self.photo_pins)]
        if session is None or not pins:
            return
        what = (f"“{pins[0].caption or pins[0].filename}”" if len(pins) == 1 else f"these {len(pins)} photos")
        answer = QMessageBox.question(
            self, "Remove Photos",
            f"Remove {what} from this hike?\n\nThe photo files and AegisPhotoVoyager are not touched, "
            "and a rescan will leave them out.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._remove_photos(pins)

    def _photo_list_menu(self, pos) -> None:
        item = self.photo_list.itemAt(pos)
        if item is None or self.app_state.get_active_session() is None:
            return
        if not item.isSelected():
            self.photo_list.setCurrentItem(item)
        n = len(self.photo_list.selectedItems())
        menu = QMenu(self)
        menu.addAction(f"Remove {'Photo' if n == 1 else f'{n} Photos'} from Hike…", self.remove_selected_photos)
        menu.exec(self.photo_list.viewport().mapToGlobal(pos))

    def _update_remove_btn(self) -> None:
        self.remove_photo_btn.setEnabled(
            bool(self.photo_list.selectedItems()) and self.app_state.get_active_session() is not None
        )

    def _remove_photos(self, removed: List[TrailPhotoPin]) -> None:
        session, route = self._active()
        if session is None or not removed:
            return
        with busy(self, "Updating the hike's photos…", title="Remove Photos"):
            self.app_state.db.remove_photos_from_session(session.id, removed)
            session = self.app_state.db.get_session(session.id) or session
            if route is not None:
                dest = self.app_state.db.get_destination(route.destination_id)
                session.interactive_map_data = build_interactive_map_data(
                    route=route, session=session, photos=dedupe_pins(self.app_state.db.get_photo_pins(session.id)),
                    destination_name=dest.name if dest else route.destination_id,
                )
                self.app_state.db.save_session(session)
        self.app_state.status_message.emit(
            f"Left {len(removed)} photo{'s' if len(removed) != 1 else ''} out of the hike (the files are untouched)."
        )
        self._load_active_session_view()

    def create_website(self) -> None:
        session, route = self._active()
        if session is None or route is None or len(route.coords_3d) < 2:
            QMessageBox.information(self, "Hike Website", "Choose a recorded hike with a route first.")
            return
        from src.gui.dialogs.hike_site_dialog import HikeSiteDialog

        if self.play_timer.isActive():
            self.toggle_playback()
        dlg = HikeSiteDialog(self.app_state, session, route, self.photo_pins, self)
        dlg.exec()
        dlg.deleteLater()

    def delete_hike(self) -> None:
        session = self.app_state.get_active_session()
        if session is None:
            return
        route = self.app_state.db.get_route(session.route_id) if session.route_id else None
        answer = QMessageBox.question(
            self, "Delete Hike",
            f"Delete “{hike_title(session, route)}” ({hike_date_label(session)})?\n\n"
            "This removes the hike, its replay map and its photo links. The photos themselves in "
            "AegisPhotoVoyager are not touched, and the planned route stays in the Library.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.app_state.delete_session(session.id)

    def _fill_missing_people(self) -> None:
        """Photos saved before person names were read get them from AegisPhotoVoyager now; names of
        unidentified faces ("Person 1234") saved before they were filtered are removed."""
        for pin in self.photo_pins:
            named = real_people(pin.people)
            if named != pin.people:
                pin.people = named
                if pin.session_id:
                    self.app_state.db.save_photo_pin(pin)
        missing = [p for p in self.photo_pins if not p.people and p.voyager_photo_id is not None]
        if not missing:
            return
        found = self.app_state.voyager_bridge.people_for_photo_ids([int(p.voyager_photo_id) for p in missing])
        for pin in missing:
            names = found.get(int(pin.voyager_photo_id))
            if names:
                pin.people = names
                if pin.session_id:
                    self.app_state.db.save_photo_pin(pin)

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
        self.photo_pins = dedupe_pins(self.photo_pins)  # also cleans up pins saved twice before
        self._fill_missing_people()
        self.photo_pins.sort(key=lambda p: (p.date_taken or 0, p.distance_along_route_m))  # timeline order
        self._update_hike_header(session, route)

        trails = self.app_state.db.list_trails(route.destination_id if route else self.app_state.active_destination_id)
        pois = self.app_state.db.list_pois(route.destination_id if route else self.app_state.active_destination_id)

        self.map_canvas.units = "metric" if unit == "metric" else "us"
        self.map_canvas.sync_basemap(
            self.app_state.db, route.destination_id if route else self.app_state.active_destination_id
        )
        # Only the hiked route is drawn in trail colours; the basemap still shows every path
        self.map_canvas.set_scene_data(
            trails=[] if route else trails, pois=pois, route=route, photo_pins=self.photo_pins
        )
        if route:
            # Fit the map to the hike and its photos (deferred until this tab is on screen)
            self.map_canvas.fit_when_shown(
                list(route.coordinates) + [(p.lat, p.lon) for p in self.photo_pins],
                margin_px=95.0 if self.photo_pins else 50.0,  # room for thumbnails at the edges
            )
            self._max_dist_m = max(1.0, route.distance_m)
            self.elev_chart.set_profile(route.elevation_profile, photo_pins=self.photo_pins, unit_system=unit)
            self.dist_card.set_value(format_distance(route.distance_m, unit))
            self.gain_card.set_value(f"+{format_elevation(route.elevation_gain_m, unit)}")
        self.photos_card.set_value(str(len(self.photo_pins)), "Geotagged along corridor")

        self.photo_list.clear()
        for pin in self.photo_pins:
            when = format_time(pin)
            who = f"  👥 {people_label(pin.people, 3)}" if pin.people else ""
            item = QListWidgetItem(
                f"📸 {when + ' · ' if when else ''}{pin.caption or pin.filename} — "
                f"{format_distance(pin.distance_along_route_m, unit)}{who}"
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
        if self.map_canvas.selected_photo_id != pin.id:
            self.map_canvas.selected_photo_id = pin.id
            self.map_canvas.update()
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
            f"Elev: {format_elevation(pin.ele_m, unit)} • Taken: {format_datetime(pin)}"
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

    def _replay_route(self):
        session = self.app_state.get_active_session()
        route = self.app_state.db.get_route(session.route_id) if session and session.route_id else None
        return route or self.app_state.get_active_route()

    def open_3d_replay(self) -> None:
        route = self._replay_route()
        if not route or len(route.coords_3d) < 2:
            self.app_state.status_message.emit("Select a recorded hike with a route to replay.")
            return
        if self.play_timer.isActive():
            self.toggle_playback()
        with busy(self, "Building 3D terrain for the replay…", title="3D Replay"):
            from src.gui.replay3d import Replay3DDialog

            pack = self.app_state.db.get_destination_pack(route.destination_id)
            unit = str(self.app_state.settings.get("unit_system", "metric"))
            dlg = Replay3DDialog(route, pack, self.photo_pins, "metric" if unit == "metric" else "us", self)
        dlg.exec()
        dlg.deleteLater()  # frees the terrain mesh and photos now, not when the app quits

    def toggle_playback(self) -> None:
        if self.play_timer.isActive():
            self.play_timer.stop()
            self.play_btn.setText("▶ Animate on Map")
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
        if session:  # photos the user removed from this hike stay out
            excluded = self.app_state.db.excluded_photo_keys(session.id)
            matched = [p for p in matched if not excluded.intersection(photo_keys(p))]
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

