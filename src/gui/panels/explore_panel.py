"""Destination Pack Explorer & Trail Network Browser panel."""
from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.charts.map_canvas import MapCanvas
from src.gui.dialogs.condition_dialog import ConditionDialog
from src.gui.dialogs.region_download_dialog import RegionDownloadDialog
from src.gui.widgets import PillToggle, SectionHeader, StatCard
from src.hiking.filters import TrailFilterCriteria, filter_trail_segments
from src.hiking.models import TrailCondition, TrailDifficulty
from src.hiking.routing import route_from_trail_segment
from src.hiking.units import format_distance, format_elevation


class ExplorePanel(QWidget):
    open_in_composer_requested = Signal(str)  # route_id

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state

        layout = QVBoxLayout(self)
        header = SectionHeader(
            "Explore Regional Trail Packs",
            "Browse offline regional trail networks, filter trailhead conditions, or download new OSM regions.",
        )
        dl_btn = QPushButton("Download Custom Region...")
        dl_btn.clicked.connect(self._open_download_dialog)
        header.actions_layout.addWidget(dl_btn)
        layout.addWidget(header)

        # Destination selector + FTS search bar
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Destination Pack:"))
        self.dest_combo = QComboBox()
        self.dest_combo.currentIndexChanged.connect(self._on_dest_combo_changed)
        top_bar.addWidget(self.dest_combo, 1)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search trails, peaks, waterfalls, shelters (FTS5)...")
        self.search_edit.textChanged.connect(self.refresh_trails)
        top_bar.addWidget(self.search_edit, 1)
        layout.addLayout(top_bar)

        # Condition filter chips
        chip_row = QHBoxLayout()
        chip_row.addWidget(QLabel("Avoid Conditions:"))
        self.cond_toggles: dict[TrailCondition, PillToggle] = {}
        for cond in [TrailCondition.MUDDY, TrailCondition.ICY, TrailCondition.OVERGROWN, TrailCondition.CLOSED]:
            pill = PillToggle(f"No {cond.value.capitalize()}", checked=(cond == TrailCondition.CLOSED))
            pill.toggled.connect(lambda _: self.refresh_trails())
            self.cond_toggles[cond] = pill
            chip_row.addWidget(pill)
        chip_row.addStretch(1)
        layout.addLayout(chip_row)

        # Summary stat cards
        cards_row = QHBoxLayout()
        self.trails_card = StatCard("Available Trails", "0")
        self.pois_card = StatCard("Points of Interest", "0")
        self.elev_card = StatCard("Base Elevation", "--")
        cards_row.addWidget(self.trails_card)
        cards_row.addWidget(self.pois_card)
        cards_row.addWidget(self.elev_card)
        layout.addLayout(cards_row)

        # Splitter with Trail Table + Map Preview
        splitter = QSplitter()
        left_widget = QWidget()
        left_col = QVBoxLayout(left_widget)
        left_col.setContentsMargins(0, 0, 0, 0)

        self.trail_table = QTableWidget(0, 5)
        self.trail_table.setHorizontalHeaderLabels(["Trail Segment", "Difficulty", "Condition", "Length", "Ascent"])
        # Name takes the remaining width; short value columns hug their contents
        header = self.trail_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.trail_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.trail_table.verticalHeader().setVisible(False)
        self.trail_table.setWordWrap(True)  # very long names wrap rather than being elided
        self.trail_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        header.sectionResized.connect(lambda idx, _old, _new: idx == 0 and self._fit_name_rows())
        self.trail_table.itemSelectionChanged.connect(self._on_trail_row_selected)
        left_col.addWidget(self.trail_table, 1)

        action_row = QHBoxLayout()
        cond_btn = QPushButton("Update Trailhead Condition...")
        cond_btn.clicked.connect(self._edit_selected_condition)
        compose_btn = QPushButton("Compose / Trace This Trail →")
        compose_btn.setObjectName("PrimaryButton")
        compose_btn.clicked.connect(self._compose_selected_trail)
        action_row.addWidget(cond_btn)
        action_row.addWidget(compose_btn)
        left_col.addLayout(action_row)

        self.map_canvas = MapCanvas()
        splitter.addWidget(left_widget)
        splitter.addWidget(self.map_canvas)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        self.app_state.destination_changed.connect(lambda _: self.refresh_all())
        self.app_state.trails_updated.connect(self.refresh_trails)
        self.app_state.settings_changed.connect(self.refresh_all)
        self.refresh_all()

    def ui_state(self) -> dict:
        return {"avoid_conditions": [c.value for c, pill in self.cond_toggles.items() if pill.isChecked()]}

    def restore_ui_state(self, state: dict) -> None:
        avoid = state.get("avoid_conditions")
        if not isinstance(avoid, list):
            return
        for cond, pill in self.cond_toggles.items():
            pill.blockSignals(True)
            pill.setChecked(cond.value in avoid)
            pill.blockSignals(False)
            pill._update_style()
        self.refresh_trails()

    def refresh_all(self) -> None:
        self.dest_combo.blockSignals(True)
        self.dest_combo.clear()
        destinations = self.app_state.db.list_destinations()
        active_idx = 0
        for idx, d in enumerate(destinations):
            self.dest_combo.addItem(f"{d.name} — {d.region}", d.id)
            if d.id == self.app_state.active_destination_id:
                active_idx = idx
        self.dest_combo.setCurrentIndex(active_idx)
        self.dest_combo.blockSignals(False)
        self.refresh_trails()

    def _on_dest_combo_changed(self) -> None:
        dest_id = self.dest_combo.currentData()
        if dest_id:
            self.app_state.set_active_destination(str(dest_id))

    def refresh_trails(self) -> None:
        dest = self.app_state.get_active_destination()
        unit = str(self.app_state.settings.get("unit_system", "metric"))
        query = self.search_edit.text().strip()
        if query:
            raw_trails = self.app_state.db.search_trails(query, self.app_state.active_destination_id)
        else:
            raw_trails = self.app_state.get_active_trails()

        excluded = {cond for cond, pill in self.cond_toggles.items() if pill.isChecked()}
        criteria = TrailFilterCriteria(
            allowed_difficulties=set(TrailDifficulty),
            excluded_conditions=excluded,
        )
        self._filtered_trails = filter_trail_segments(raw_trails, criteria)
        pois = self.app_state.get_active_pois()

        self.trails_card.set_value(str(len(self._filtered_trails)), f"of {len(raw_trails)} segments")
        self.pois_card.set_value(str(len(pois)), "Peaks, viewpoints & water")
        if dest:
            self.elev_card.set_value(format_elevation(dest.base_elevation_m, unit), dest.region)

        self.trail_table.setRowCount(len(self._filtered_trails))
        for row_idx, seg in enumerate(self._filtered_trails):
            name_item = QTableWidgetItem(seg.name)
            name_item.setToolTip(f"<b>{seg.name}</b><br>{seg.description}" if seg.description else seg.name)
            self.trail_table.setItem(row_idx, 0, name_item)
            self.trail_table.setItem(row_idx, 1, QTableWidgetItem(seg.difficulty.value.capitalize()))
            self.trail_table.setItem(row_idx, 2, QTableWidgetItem(seg.condition.value.capitalize()))
            self.trail_table.setItem(row_idx, 3, QTableWidgetItem(format_distance(seg.length_m, unit)))
            self.trail_table.setItem(row_idx, 4, QTableWidgetItem(f"+{format_elevation(seg.elevation_gain_m, unit)}"))
        self._fit_name_rows()

        self.map_canvas.excluded_conditions = {c.value for c in excluded}
        self.map_canvas.units = "metric" if unit == "metric" else "us"
        self.map_canvas.sync_basemap(self.app_state.db, self.app_state.active_destination_id)
        self.map_canvas.set_scene_data(
            trails=raw_trails,
            pois=pois,
            route=self.app_state.get_active_route(),
        )

    def _fit_name_rows(self) -> None:
        """Size rows so long trail names wrap onto extra lines instead of being cut off.
        Measured with a safety margin: Qt's own size hint can judge a name fits on one line
        while the painted cell (with padding) elides it."""
        fm = self.trail_table.fontMetrics()
        width = max(40, self.trail_table.columnWidth(0) - 16)
        base = fm.height() + 8
        for row in range(self.trail_table.rowCount()):
            item = self.trail_table.item(row, 0)
            if item is None:
                continue
            rect = fm.boundingRect(0, 0, width, 10_000, int(Qt.TextFlag.TextWordWrap), item.text())
            self.trail_table.setRowHeight(row, max(base, rect.height() + 8))

    def _selected_segment(self):
        rows = self.trail_table.selectionModel().selectedRows()
        if not rows:
            return self._filtered_trails[0] if getattr(self, "_filtered_trails", None) else None
        idx = rows[0].row()
        if 0 <= idx < len(self._filtered_trails):
            return self._filtered_trails[idx]
        return None

    def _on_trail_row_selected(self) -> None:
        seg = self._selected_segment()
        if not seg:
            return
        preview = route_from_trail_segment(seg)
        self.map_canvas.set_scene_data(
            trails=self.app_state.get_active_trails(),
            pois=self.app_state.get_active_pois(),
            route=preview,
        )
        self.map_canvas.zoom_to_coordinates(seg.coordinates)

    def _edit_selected_condition(self) -> None:
        seg = self._selected_segment()
        if not seg:
            return
        dlg = ConditionDialog(seg, self)
        if dlg.exec():
            new_cond = dlg.selected_condition()
            self.app_state.db.update_trail_condition(seg.id, new_cond)
            self.app_state.trails_updated.emit()

    def _compose_selected_trail(self) -> None:
        seg = self._selected_segment()
        if not seg:
            return
        route = route_from_trail_segment(seg)
        self.app_state.db.upsert_route(route)
        self.app_state.set_active_route(route.id)
        self.open_in_composer_requested.emit(route.id)

    def _open_download_dialog(self) -> None:
        dlg = RegionDownloadDialog(self.app_state, self)
        dlg.exec()

