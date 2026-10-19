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
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
from src.gui.dialogs.hike_details_dialog import HikeDetailsForm
from src.gui.dialogs.photo_review_dialog import PhotoReviewDialog
from src.hiking.hike_replay import build_interactive_map_data, export_interactive_hike_html
from src.hiking.models import ComposedRoute, HikeSession, TrailPhotoPin
from src.hiking.hike_days import (
    PARTIAL_COVERAGE,
    HikeDay,
    build_day_session,
    build_multi_day_session,
    group_pins_by_day,
    looks_multi_day,
    overnight_candidates,
    plan_stages,
)
from src.hiking.hike_report import named_places, set_hike_details
from src.hiking.photo_matcher import photo_keys
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
        self.left_out: set = set()  # ids of matched photos the user left out
        self.day_groups: List[HikeDay] = []
        self.created_sessions: List[HikeSession] = []
        self.created_session: Optional[HikeSession] = None  # most recent of created_sessions

        self.setWindowTitle("I Hiked This Trail — Log Hike & Match Voyager Photos")
        self.resize(900, 780)

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
        self.details_form = HikeDetailsForm(self.app_state, self._when_where)
        layout.addWidget(self.details_form)

        # AegisPhotoVoyager DB discovery bar
        voyager_row = QHBoxLayout()
        self.voyager_lbl = QLabel()
        self._update_voyager_status_label()
        browse_btn = QPushButton("Locate Voyager DB...")
        browse_btn.clicked.connect(self._browse_voyager_db)
        scan_btn = QPushButton("Scan AegisPhotoVoyager Photos")
        scan_btn.clicked.connect(self.scan_voyager_photos)
        self.review_btn = QPushButton("Review Photos…")
        self.review_btn.setToolTip("Look through the matched photos and leave out the ones that don't belong")
        self.review_btn.clicked.connect(self.review_photos)
        voyager_row.addWidget(self.voyager_lbl, 1)
        voyager_row.addWidget(browse_btn)
        voyager_row.addWidget(scan_btn)
        voyager_row.addWidget(self.review_btn)
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
        self.days_list.itemChanged.connect(lambda _item: self._update_multi_day())
        layout.addWidget(self.days_list)

        # Several consecutive days on the trail: one multi-day hike with nights at huts / tent sites
        self.multi_chk = QCheckBox("These days are one multi-day hike (nights at huts, shelters or tent sites)")
        self.multi_chk.setToolTip("Save the checked consecutive days as a single hike with a stage per day")
        self.multi_chk.toggled.connect(lambda _on: self._update_multi_day(keep_choice=True))
        layout.addWidget(self.multi_chk)
        self.nights_box = QWidget()
        self.nights_form = QFormLayout(self.nights_box)
        self.nights_form.setContentsMargins(24, 0, 0, 0)
        layout.addWidget(self.nights_box)
        self.night_combos: List[QComboBox] = []
        self._places = None
        self.multi_chk.hide()
        self.nights_box.hide()

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
        self.left_out = {pid for pid in self.left_out if any(p.id == pid for p in self.matched_pins)}
        self._fill_table()

    def _fill_table(self) -> None:
        self.photo_table.setRowCount(len(self.matched_pins))
        for idx, pin in enumerate(self.matched_pins):
            taken = datetime.fromtimestamp(pin.date_taken).strftime("%Y-%m-%d %H:%M") if pin.date_taken else "no date"
            self.photo_table.setItem(idx, 0, QTableWidgetItem(taken))
            self.photo_table.setItem(idx, 1, QTableWidgetItem(pin.caption))
            self.photo_table.setItem(idx, 2, QTableWidgetItem(f"{pin.distance_along_route_m / 1000.0:.2f} km"))
            self.photo_table.setItem(idx, 3, QTableWidgetItem(f"{pin.lat:.4f}, {pin.lon:.4f}"))
            meta = pin.ai_summary or ", ".join(pin.people_names + pin.tags) or pin.mood or "Geotagged match"
            if pin.id in self.left_out:
                meta = "✕ Left out — " + meta
            self.photo_table.setItem(idx, 4, QTableWidgetItem(meta))
            if pin.id in self.left_out:
                for c in range(5):
                    self.photo_table.item(idx, c).setForeground(QColor("#7E8F84"))
        self.review_btn.setEnabled(bool(self.matched_pins))
        self._populate_days()

    def _kept_pins(self) -> List[TrailPhotoPin]:
        return [p for p in self.matched_pins if p.id not in self.left_out]

    def review_photos(self) -> None:
        if not self.matched_pins:
            return
        dlg = PhotoReviewDialog(self.matched_pins, self._unit(), self.name_edit.text().strip(), self)
        dlg.set_kept({p.id for p in self._kept_pins()})
        if dlg.exec():
            self.left_out = {p.id for p in dlg.removed_pins()}
            self._fill_table()
        dlg.deleteLater()

    def _when_where(self):
        """Trailhead and time of the hike, for the weather lookup."""
        if not self.route.coords_3d:
            return None
        groups = self._selected_groups() if self.day_groups else []
        dated = [g for g in groups if g.day is not None]
        try:
            fallback = date.fromisoformat(self.date_edit.text().strip())
        except ValueError:
            fallback = date.today()
        g = dated[0] if dated else None
        lon, lat = self.route.coords_3d[0][0], self.route.coords_3d[0][1]
        start = g.first.replace(tzinfo=None) if g and g.first else None
        end = g.last.replace(tzinfo=None) if g and g.last else None
        return lat, lon, (g.day if g else fallback), start, end

    def _unit(self) -> str:
        return str(self.app_state.settings.get("unit_system", "metric"))

    def _saved_photo_keys(self) -> set:
        """Identities of photos already attached to any saved hike."""
        keys: set = set()
        for session in self.app_state.db.list_sessions():
            for pin in self.app_state.db.get_photo_pins(session.id):
                keys.update(photo_keys(pin))
        return keys

    def _populate_days(self) -> None:
        self.day_groups = group_pins_by_day(self._kept_pins())
        self.days_list.clear()
        unit = self._unit()
        saved = self._saved_photo_keys()
        for group in self.day_groups:
            already = sum(1 for p in group.pins if any(k in saved for k in photo_keys(p)))
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
            all_saved = bool(group.pins) and already == len(group.pins)
            if all_saved:
                label += " — already saved as a hike"
            elif already:
                label += f" — {already} of these photos are already in a saved hike"
            item = QListWidgetItem(label, self.days_list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # Don't log the same day twice by default
            item.setCheckState(Qt.CheckState.Unchecked if all_saved else Qt.CheckState.Checked)
        dated = sum(1 for g in self.day_groups if g.day is not None)
        if dated > 1:
            self.days_lbl.setText(f"<b>Photos from {dated} different days</b> — each checked day is saved as its own hike:")
        elif dated == 1:
            self.days_lbl.setText("<b>Hike date from the photos:</b>")
        else:
            self.days_lbl.setText("No dated photos matched — the hike is saved on the date entered above.")
        self.days_list.setVisible(bool(self.day_groups))
        self._update_multi_day(default=looks_multi_day(self.day_groups))

    # ── multi-day hikes ──────────────────────────────────────────────────

    def _region_places(self):
        if self._places is None:
            self._places = named_places(self.app_state.db.get_destination_pack(self.route.destination_id))
        return self._places

    def _consecutive_days(self) -> List[HikeDay]:
        """The checked dated days, if there are several and they follow each other."""
        dated = sorted((g for g in self._selected_groups() if g.day is not None), key=lambda g: g.day)
        if len(dated) < 2 or any((b.day - a.day).days != 1 for a, b in zip(dated, dated[1:])):
            return []
        return dated

    def _is_multi_day(self) -> bool:
        return not self.multi_chk.isHidden() and self.multi_chk.isChecked() and bool(self._consecutive_days())

    def _update_multi_day(self, default: Optional[bool] = None, keep_choice: bool = False) -> None:
        days = self._consecutive_days()
        self.multi_chk.setVisible(bool(days))
        if default is not None:
            self.multi_chk.blockSignals(True)
            self.multi_chk.setChecked(default)
            self.multi_chk.blockSignals(False)
        while self.nights_form.rowCount():
            self.nights_form.removeRow(0)
        self.night_combos = []
        multi = bool(days) and self.multi_chk.isChecked()
        self.nights_box.setVisible(multi)
        self.save_btn.setText("Save Multi-Day Hike && Generate Interactive Map" if multi
                              else "Save Completed Hike(s) && Generate Interactive Maps")
        dated = sum(1 for g in self.day_groups if g.day is not None)
        if multi:
            self.days_lbl.setText(f"<b>Photos from {len(days)} consecutive days</b> — saved as one multi-day hike, "
                                  "a stage per day. Where did you spend the nights?")
        elif dated > 1:
            self.days_lbl.setText(f"<b>Photos from {dated} different days</b> — each checked day is saved as its own hike:")
        if not multi:
            return
        unit = self._unit()
        stages = plan_stages(self.route, days, self._region_places())
        for n, st in enumerate(stages[:-1], 1):
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.lineEdit().setPlaceholderText("Hut, shelter or campsite (type any name)")
            for name, d in overnight_candidates(self.route, st.to_m, self._region_places()):
                combo.addItem(f"{name}  ({format_distance(d, unit)} from the trail)", name)
            combo.addItem("Not recorded", "")
            idx = combo.findData(st.overnight)
            combo.setCurrentIndex(idx if idx >= 0 else combo.count() - 1)
            self.nights_form.addRow(f"Night {n} · {st.day:%a %b %d}:", combo)
            self.night_combos.append(combo)

    def _overnights(self) -> List[str]:
        out = []
        for combo in self.night_combos:
            text = combo.currentText().strip()
            idx = combo.findText(text)
            out.append(str(combo.itemData(idx)) if idx >= 0 else text)
        return out

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
        if self._is_multi_day():
            self._save_multi_day()
            return
        with busy(self, "Saving hike(s) and building replay maps…", title="Saving Hike", steps=len(groups)) as b:
            self.route.name = self.name_edit.text().strip() or self.route.name
            self.app_state.db.upsert_route(self.route)
            dest = self.app_state.db.get_destination(self.route.destination_id)
            dest_name = dest.name if dest else self.route.destination_id
            notes = self.notes_edit.toPlainText().strip()
            details = self.details_form.details()
            left_out = [p for p in self.matched_pins if p.id in self.left_out]
            left_out_by_day = {g.day: g.pins for g in group_pins_by_day(left_out)}

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
                set_hike_details(session, details)
                self.app_state.db.save_session(session)
                for pin in group.pins:
                    self.app_state.db.save_photo_pin(pin)
                # Photos left out in the review stay out when the hike is rescanned later
                removed = left_out_by_day.get(group.day) or (left_out if len(groups) == 1 else [])
                if removed:
                    self.app_state.db.remove_photos_from_session(session.id, removed)
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

    def _save_multi_day(self) -> None:
        """Save the checked consecutive days as one hike with a stage per day and its overnights."""
        days = self._consecutive_days()
        stages = plan_stages(self.route, days, self._region_places())
        for st, name in zip(stages, self._overnights()):
            st.overnight = name
        with busy(self, "Saving the multi-day hike and building its replay map…", title="Saving Hike"):
            self.route.name = self.name_edit.text().strip() or self.route.name
            self.app_state.db.upsert_route(self.route)
            dest = self.app_state.db.get_destination(self.route.destination_id)
            first, last = days[0].day, days[-1].day
            span = f"{first:%b %d}–{last:%d, %Y}" if first.month == last.month else f"{first:%b %d}–{last:%b %d, %Y}"
            session = build_multi_day_session(self.route, days, stages, title=f"{self.route.name} — {span}",
                                              notes=self.notes_edit.toPlainText().strip())
            set_hike_details(session, self.details_form.details())
            pins = [p for g in days for p in g.pins]
            session.stats.photo_count = len(pins)
            for pin in pins:
                pin.session_id = session.id
                pin.route_id = self.route.id
            session.interactive_map_data = build_interactive_map_data(
                route=self.route, session=session, photos=pins,
                destination_name=dest.name if dest else self.route.destination_id,
            )
            self.app_state.db.save_session(session)
            for pin in pins:
                self.app_state.db.save_photo_pin(pin)
            left_out = [p for p in self.matched_pins if p.id in self.left_out]
            if left_out:
                self.app_state.db.remove_photos_from_session(session.id, left_out)
            export_interactive_hike_html(session.interactive_map_data, paths.get_hike_maps_dir() / f"{session.id}.html")
            self.created_sessions = [session]
            self.created_session = session
            self.app_state.set_active_route(self.route.id)
            self.app_state.set_active_session(session.id)
            nights = [st.overnight for st in stages[:-1] if st.overnight]
            self.app_state.status_message.emit(
                f"Saved a {len(days)}-day hike of '{self.route.name}' with {len(pins)} photos"
                + (f" (nights at {', '.join(nights)})" if nights else "") + "."
            )
        self.accept()
