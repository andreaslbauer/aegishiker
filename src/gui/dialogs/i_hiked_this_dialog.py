"""'I Hiked This Trail' dialog:
1) Confirms region & traced trail route
2) Persists a completed HikeSession record
3) Discovers AegisPhotoVoyager DB and matches geotagged photos along the trail corridor
4) Splits the photos by the day they were taken: each day becomes its own hike (the trail may
   have been hiked several times)
5) Generates and persists an interactive hike replay map per hike (JSON + standalone HTML)
"""
from datetime import date, datetime
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core import paths
from src.gui.app_state import AppState
from src.hiking.hike_replay import build_interactive_map_data, export_interactive_hike_html
from src.hiking.models import ComposedRoute, HikeSession, TrailPhotoPin
from src.hiking.hike_days import PARTIAL_COVERAGE, HikeDay, build_day_session, group_pins_by_day
from src.hiking.units import format_distance
from src.gui.busy import busy, run_in_background


class IHikedThisDialog(QDialog):
    def __init__(
        self,
        app_state: AppState,
        route: ComposedRoute,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.route = route
        self.matched_pins: List[TrailPhotoPin] = []
        self.day_groups: List[HikeDay] = []
        self.created_sessions: List[HikeSession] = []
        self.created_session: Optional[HikeSession] = None  # most recent of created_sessions

        self.setWindowTitle("I Hiked This Trail — Log Hike & Match Voyager Photos")
        self.resize(820, 680)

        layout = QVBoxLayout(self)

        dest = self.app_state.db.get_destination(route.destination_id)
        dest_name = f"{dest.name} ({dest.region})" if dest else route.destination_id

        header = QLabel(
            f"<b>Region:</b> {dest_name} &nbsp;•&nbsp; "
            f"<b>Distance:</b> {format_distance(route.distance_m)} &nbsp;•&nbsp; "
            f"<b>Ascent:</b> +{route.elevation_gain_m:.0f} m"
        )
        layout.addWidget(header)

        form = QFormLayout()
        self.name_edit = QLineEdit(route.name or "Traced Trail Hike")
        self.date_edit = QLineEdit(date.today().isoformat())
        self.date_edit.setToolTip("Used for the hike when no photo with a date is matched")

        self.corridor_spin = QDoubleSpinBox()
        self.corridor_spin.setRange(25.0, 2500.0)
        self.corridor_spin.setSingleStep(25.0)
        self.corridor_spin.setValue(float(self.app_state.settings.get("photo_match_corridor_m", 250.0)))
        self.corridor_spin.setSuffix(" m")

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Trail notes, summit weather, wildlife sightings, companions...")
        self.notes_edit.setMaximumHeight(75)

        form.addRow("Hike Title:", self.name_edit)
        form.addRow("Date Hiked (if no dated photos):", self.date_edit)
        form.addRow("GPS Corridor Radius:", self.corridor_spin)
        form.addRow("Hike Notes:", self.notes_edit)
        layout.addLayout(form)

        # AegisPhotoVoyager DB discovery bar
        voyager_row = QHBoxLayout()
        self.voyager_lbl = QLabel()
        self._update_voyager_status_label()
        browse_btn = QPushButton("Locate Voyager DB...")
        browse_btn.clicked.connect(self._browse_voyager_db)
        scan_btn = QPushButton("Scan AegisPhotoVoyager Photos")
        scan_btn.clicked.connect(self.scan_voyager_photos)
        voyager_row.addWidget(self.voyager_lbl, 1)
        voyager_row.addWidget(browse_btn)
        voyager_row.addWidget(scan_btn)
        layout.addLayout(voyager_row)

        # Matched photo table
        self.photo_table = QTableWidget(0, 5)
        self.photo_table.setHorizontalHeaderLabels(["Taken", "Caption / File", "Trail Mark", "Coordinates", "Voyager AI / Tags"])
        self.photo_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.photo_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.photo_table, 1)

        # One hike per day the photos were taken
        self.days_lbl = QLabel()
        layout.addWidget(self.days_lbl)
        self.days_list = QListWidget()
        self.days_list.setMaximumHeight(110)
        layout.addWidget(self.days_list)

        # Bottom action buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save Completed Hike(s) && Generate Interactive Maps")
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.clicked.connect(self.finalize_hike)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self.save_btn)
        layout.addLayout(btn_row)

        # Run initial scan automatically
        self.scan_voyager_photos()

    def _update_voyager_status_label(self) -> None:
        db_path = self.app_state.voyager_bridge.discover_db_path()
        if db_path:
            self.voyager_lbl.setText(f"AegisPhotoVoyager DB: {db_path}")
            self.voyager_lbl.setStyleSheet("color: #2ECC71; font-size: 9pt;")
        else:
            self.voyager_lbl.setText(
                "AegisPhotoVoyager DB not auto-detected in ~/.AegisPhotoVoyager/ (optional — you can still save or locate DB)"
            )
            self.voyager_lbl.setStyleSheet("color: #98B2A1; font-size: 9pt;")

    def _browse_voyager_db(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select AegisPhotoVoyager SQLite Database",
            str(paths.Path.home()),
            "SQLite Databases (*.db *.sqlite);;All Files (*)",
        )
        if file_path:
            self.app_state.settings.set("voyager_db_path", file_path)
            self.app_state.settings.save()
            self.app_state.voyager_bridge.explicit_db_path = file_path
            self._update_voyager_status_label()
            self.scan_voyager_photos()

    def scan_voyager_photos(self) -> None:
        corridor_m = float(self.corridor_spin.value())
        self.matched_pins = run_in_background(
            self,
            "Scanning AegisPhotoVoyager library for photos along this trail…",
            self.app_state.voyager_bridge.match_photos_for_route,
            title="Matching Photos",
            route=self.route,
            session_id=None,
            max_corridor_m=corridor_m,
            date_prefix=None,
        )
        self.matched_pins.sort(key=lambda p: (p.date_taken or 0, p.distance_along_m))
        self.photo_table.setRowCount(len(self.matched_pins))
        for idx, pin in enumerate(self.matched_pins):
            taken = datetime.fromtimestamp(pin.date_taken).strftime("%Y-%m-%d %H:%M") if pin.date_taken else "no date"
            self.photo_table.setItem(idx, 0, QTableWidgetItem(taken))
            self.photo_table.setItem(idx, 1, QTableWidgetItem(pin.caption))
            self.photo_table.setItem(idx, 2, QTableWidgetItem(f"{pin.distance_along_route_m / 1000.0:.2f} km"))
            self.photo_table.setItem(idx, 3, QTableWidgetItem(f"{pin.lat:.4f}, {pin.lon:.4f}"))
            meta = pin.ai_summary or ", ".join(pin.people_names + pin.tags) or pin.mood or "Geotagged match"
            self.photo_table.setItem(idx, 4, QTableWidgetItem(meta))
        self._populate_days()

    def _unit(self) -> str:
        return str(self.app_state.settings.get("unit_system", "metric"))

    def _populate_days(self) -> None:
        self.day_groups = group_pins_by_day(self.matched_pins)
        self.days_list.clear()
        unit = self._unit()
        for group in self.day_groups:
            if group.day is None:
                label = f"Photos without a date ({len(group.pins)}) → hike on the date entered above"
            else:
                span = f"{group.first:%H:%M}–{group.last:%H:%M}" if group.first and group.last else ""
                lo, hi = group.along_range_m
                cov = group.coverage(self.route.distance_m)
                label = (
                    f"{group.day:%a %b %d, %Y} · {len(group.pins)} photo{'s' if len(group.pins) != 1 else ''} · {span}"
                    f" · photos along {format_distance(lo, unit)}–{format_distance(hi, unit)}"
                    + (" (part of the route)" if len(group.pins) >= 2 and cov < PARTIAL_COVERAGE else "")
                )
            item = QListWidgetItem(label, self.days_list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
        dated = sum(1 for g in self.day_groups if g.day is not None)
        if dated > 1:
            self.days_lbl.setText(f"<b>Photos from {dated} different days</b> — each checked day is saved as its own hike:")
        elif dated == 1:
            self.days_lbl.setText("<b>Hike date from the photos:</b>")
        else:
            self.days_lbl.setText("No dated photos matched — the hike is saved on the date entered above.")
        self.days_list.setVisible(bool(self.day_groups))

    def _selected_groups(self) -> List[HikeDay]:
        return [
            g for i, g in enumerate(self.day_groups)
            if self.days_list.item(i) and self.days_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def finalize_hike(self) -> None:
        groups = self._selected_groups() if self.day_groups else [HikeDay(day=None)]
        if not groups:
            QMessageBox.information(self, "No Hike Selected", "Check at least one day to save as a hike.")
            return
        try:
            fallback = date.fromisoformat(self.date_edit.text().strip())
        except ValueError:
            fallback = date.today()
        unit = self._unit()
        with busy(self, "Saving hike(s) and building replay maps…", title="Saving Hike", steps=len(groups)) as b:
            self.route.name = self.name_edit.text().strip() or self.route.name
            self.app_state.db.upsert_route(self.route)
            dest = self.app_state.db.get_destination(self.route.destination_id)
            dest_name = dest.name if dest else self.route.destination_id
            notes = self.notes_edit.toPlainText().strip()

            for group in groups:
                day = group.day or fallback
                session = build_day_session(
                    self.route, group, title=f"{self.route.name} — {day:%b %d, %Y}", notes=notes,
                    fallback_day=fallback, units_fmt=lambda m: format_distance(m, unit),
                )
                session.stats.photo_count = len(group.pins)
                for pin in group.pins:
                    pin.session_id = session.id
                    pin.route_id = self.route.id
                map_data = build_interactive_map_data(
                    route=self.route, session=session, photos=group.pins, destination_name=dest_name,
                )
                session.interactive_map_data = map_data
                self.app_state.db.save_session(session)
                for pin in group.pins:
                    self.app_state.db.save_photo_pin(pin)
                export_interactive_hike_html(map_data, paths.get_hike_maps_dir() / f"{session.id}.html")
                self.created_sessions.append(session)
                b.step(f"Saved hike on {day:%b %d, %Y}")

            self.created_sessions.sort(key=lambda x: x.started_at)
            self.created_session = self.created_sessions[-1]
            self.app_state.set_active_route(self.route.id)
            self.app_state.set_active_session(self.created_session.id)
            n_photos = sum(len(g.pins) for g in groups)
            self.app_state.status_message.emit(
                f"Saved {len(self.created_sessions)} hike{'s' if len(self.created_sessions) != 1 else ''} of "
                f"'{self.route.name}' with {n_photos} matched photos!"
            )
        self.accept()
