"""'I Hiked This Trail' dialog:
1) Confirms region & traced trail route
2) Persists a completed HikeSession record
3) Discovers AegisPhotoVoyager DB and matches geotagged photos along the trail corridor
4) Generates and persists an interactive hike replay map (JSON + standalone HTML)
"""
from datetime import date, datetime
from typing import List, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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
        self.created_session: Optional[HikeSession] = None

        self.setWindowTitle("I Hiked This Trail — Log Hike & Match Voyager Photos")
        self.resize(740, 560)

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
        self.filter_date_chk = QCheckBox("Only match photos taken on this date (uncheck to match any date along corridor)")
        self.filter_date_chk.setChecked(False)

        self.corridor_spin = QDoubleSpinBox()
        self.corridor_spin.setRange(25.0, 2500.0)
        self.corridor_spin.setSingleStep(25.0)
        self.corridor_spin.setValue(float(self.app_state.settings.get("photo_match_corridor_m", 250.0)))
        self.corridor_spin.setSuffix(" m")

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Trail notes, summit weather, wildlife sightings, companions...")
        self.notes_edit.setMaximumHeight(75)

        form.addRow("Hike Title:", self.name_edit)
        form.addRow("Date Hiked (YYYY-MM-DD):", self.date_edit)
        form.addRow("Date Filter:", self.filter_date_chk)
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
        self.photo_table = QTableWidget(0, 4)
        self.photo_table.setHorizontalHeaderLabels(["Caption / File", "Trail Mark", "Coordinates", "Voyager AI / Tags"])
        self.photo_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.photo_table, 1)

        # Bottom action buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save Completed Hike & Generate Interactive Map")
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
        date_prefix = self.date_edit.text().strip() if self.filter_date_chk.isChecked() else None
        corridor_m = float(self.corridor_spin.value())
        self.matched_pins = run_in_background(
            self,
            "Scanning AegisPhotoVoyager library for photos along this trail…",
            self.app_state.voyager_bridge.match_photos_for_route,
            title="Matching Photos",
            route=self.route,
            session_id=None,
            max_corridor_m=corridor_m,
            date_prefix=date_prefix or None,
        )
        self.photo_table.setRowCount(len(self.matched_pins))
        for idx, pin in enumerate(self.matched_pins):
            self.photo_table.setItem(idx, 0, QTableWidgetItem(pin.caption))
            self.photo_table.setItem(idx, 1, QTableWidgetItem(f"{pin.distance_along_route_m / 1000.0:.2f} km"))
            self.photo_table.setItem(idx, 2, QTableWidgetItem(f"{pin.lat:.4f}, {pin.lon:.4f}"))
            meta = pin.ai_summary or ", ".join(pin.people_names + pin.tags) or pin.mood or "Geotagged match"
            self.photo_table.setItem(idx, 3, QTableWidgetItem(meta))

    def finalize_hike(self) -> None:
        with busy(self, "Saving hike and building replay map…", title="Saving Hike"):
            self.route.name = self.name_edit.text().strip() or self.route.name
            self.app_state.db.upsert_route(self.route)

            dest = self.app_state.db.get_destination(self.route.destination_id)
            dest_name = dest.name if dest else self.route.destination_id

            hiked_date = self.date_edit.text().strip() or datetime.now().date().isoformat()
            session = HikeSession(
                route_id=self.route.id,
                destination_id=self.route.destination_id,
                started_at=f"{hiked_date}T08:00:00",
                ended_at=f"{hiked_date}T13:00:00",
                status="traced_hiked",
                actual_distance_m=self.route.distance_m,
                actual_elevation_gain_m=self.route.elevation_gain_m,
                actual_duration_min=self.route.estimated_duration_min,
                notes=self.notes_edit.toPlainText().strip(),
            )

            for pin in self.matched_pins:
                pin.session_id = session.id
                pin.route_id = self.route.id
            map_data = build_interactive_map_data(
                route=self.route,
                session=session,
                photos=self.matched_pins,
                destination_name=dest_name,
            )
            session.interactive_map_data = map_data
            self.app_state.db.save_session(session)

            for pin in self.matched_pins:
                self.app_state.db.save_photo_pin(pin)

            html_out = paths.get_hike_maps_dir() / f"{session.id}.html"
            export_interactive_hike_html(map_data, html_out)

            self.created_session = session
            self.app_state.set_active_route(self.route.id)
            self.app_state.set_active_session(session.id)
            self.app_state.status_message.emit(
                f"Saved completed hike '{self.route.name}' with {len(self.matched_pins)} matched photos!"
            )
        self.accept()

