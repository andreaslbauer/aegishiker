"""Saved Routes & Hike History Library panel with .hike archive and PDF export."""
from typing import Optional
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.report import export_trailhead_pdf
from src.gui.widgets import SectionHeader
from src.hiking.hike_file import load_hike_archive, save_hike_archive
from src.hiking.units import format_distance, format_duration, format_duration_min, format_elevation
from src.gui.busy import busy


class LibraryPanel(QWidget):
    open_route_requested = Signal(str)
    open_replay_requested = Signal(str)

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state

        layout = QVBoxLayout(self)
        header = SectionHeader(
            "Trail Library, Completed Hikes & Portable Archives",
            "Manage all composed routes and completed hikes, export PDF Trailhead Sheets, or share .hike bundles.",
        )
        layout.addWidget(header)

        self.route_table = QTableWidget(0, 5)
        self.route_table.setHorizontalHeaderLabels(["Route Name", "Region", "Distance", "Ascent", "Est. Time"])
        self.route_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.route_table, 1)

        btn_row = QHBoxLayout()
        open_btn = QPushButton("Open in Trail Composer")
        open_btn.setObjectName("PrimaryButton")
        open_btn.clicked.connect(self._open_selected_route)

        pdf_btn = QPushButton("Export Trailhead & Safety PDF...")
        pdf_btn.clicked.connect(self._export_pdf)

        save_hike_btn = QPushButton("Export .hike Bundle...")
        save_hike_btn.clicked.connect(self._export_hike_bundle)

        load_hike_btn = QPushButton("Import .hike Bundle...")
        load_hike_btn.clicked.connect(self._import_hike_bundle)

        for b in [open_btn, pdf_btn, save_hike_btn, load_hike_btn]:
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        self.app_state.route_changed.connect(lambda _: self.refresh_all())
        self.app_state.destination_changed.connect(lambda _: self.refresh_all())
        self.refresh_all()

    def refresh_all(self) -> None:
        self._routes = self.app_state.db.list_routes()
        unit = str(self.app_state.settings.get("unit_system", "metric"))
        self.route_table.setRowCount(len(self._routes))
        for idx, r in enumerate(self._routes):
            dest = self.app_state.db.get_destination(r.destination_id)
            d_name = dest.name if dest else r.destination_id
            self.route_table.setItem(idx, 0, QTableWidgetItem(r.name))
            self.route_table.setItem(idx, 1, QTableWidgetItem(d_name))
            self.route_table.setItem(idx, 2, QTableWidgetItem(format_distance(r.distance_m, unit)))
            self.route_table.setItem(idx, 3, QTableWidgetItem(f"+{format_elevation(r.elevation_gain_m, unit)}"))
            self.route_table.setItem(idx, 4, QTableWidgetItem(format_duration_min(r.estimated_duration_min)))

    def _selected_route(self):
        rows = self.route_table.selectionModel().selectedRows()
        idx = rows[0].row() if rows else 0
        if 0 <= idx < len(getattr(self, "_routes", [])):
            return self._routes[idx]
        return self.app_state.get_active_route()

    def _open_selected_route(self) -> None:
        r = self._selected_route()
        if r:
            self.app_state.set_active_route(r.id)
            self.open_route_requested.emit(r.id)

    def _export_pdf(self) -> None:
        r = self._selected_route()
        if not r:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Trailhead & Leave-Behind Safety PDF",
            f"{r.name.lower().replace(' ', '_')}_trailhead.pdf",
            "PDF Documents (*.pdf)",
        )
        if not path:
            return
        with busy(self, "Exporting trailhead safety PDF…", title="Exporting PDF"):
            dest = self.app_state.db.get_destination(r.destination_id)
            gear = self.app_state.db.list_gear(r.id)
            photos = self.app_state.db.list_photo_pins(route_id=r.id)
            export_trailhead_pdf(
                output_path=path,
                route=r,
                destination=dest,
                gear_items=gear,
                photos=photos,
                emergency_contact=str(self.app_state.settings.get("emergency_contact", "")),
                unit_system=str(self.app_state.settings.get("unit_system", "metric")),
            )
        self.app_state.status_message.emit(f"Exported PDF Trailhead Sheet to {path}")

    def _export_hike_bundle(self) -> None:
        r = self._selected_route()
        if not r:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Portable .hike Bundle",
            f"{r.name.lower().replace(' ', '_')}.hike",
            "AegisHiker Archive (*.hike)",
        )
        if not path:
            return
        with busy(self, "Saving .hike archive…", title="Saving Hike"):
            dest = self.app_state.db.get_destination(r.destination_id)
            gear = self.app_state.db.list_gear(r.id)
            photos = self.app_state.db.list_photo_pins(route_id=r.id)
            save_hike_archive(path, route=r, destination=dest, gear_items=gear, photos=photos)
        self.app_state.status_message.emit(f"Saved .hike archive to {path}")

    def _import_hike_bundle(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Portable .hike Bundle",
            "",
            "AegisHiker Archive (*.hike);;All Files (*)",
        )
        if not path:
            return
        with busy(self, "Importing .hike archive…", title="Importing Hike"):
            bundle = load_hike_archive(path)
            route = bundle["route"]
            self.app_state.db.upsert_route(route)
            if bundle.get("gear_items"):
                self.app_state.db.replace_gear_for_route(route.id, bundle["gear_items"])
            for pin in bundle.get("photos", []):
                self.app_state.db.save_photo_pin(pin)
            self.app_state.set_active_route(route.id)
        self.refresh_all()

