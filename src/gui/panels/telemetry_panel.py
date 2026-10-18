"""Post-Hike GPS Telemetry & Pace Split Analyzer panel."""
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.charts.pace_chart import PaceChart
from src.gui.widgets import Card, SectionHeader, StatCard, page_layout, stat_row
from src.hiking.telemetry import compute_splits, detect_off_trail, summarize_telemetry
from src.hiking.hike_info import hike_title
from src.hiking.units import format_distance, format_duration, format_duration_min, format_elevation


class TelemetryPanel(QWidget):
    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state

        layout = page_layout(self)
        header = SectionHeader(
            "Telemetry & Splits",
            "Compare the plan with what you actually hiked: pace per kilometre, climbs and how far you strayed from the trail.",
        )
        layout.addWidget(header)

        sel_card = Card(margins=(16, 12, 16, 12))
        sel_row = QHBoxLayout()
        sel_row.setSpacing(12)
        cap = QLabel("Hike")
        cap.setObjectName("Muted")
        sel_row.addWidget(cap)
        self.session_combo = QComboBox()
        self.session_combo.currentIndexChanged.connect(self._on_session_changed)
        sel_row.addWidget(self.session_combo, 1)
        sel_card.body.addLayout(sel_row)
        layout.addWidget(sel_card)

        self.dist_card = StatCard("Actual Distance", "0.0 km")
        self.gain_card = StatCard("Actual Ascent", "+0 m")
        self.time_card = StatCard("Moving Time", "0m")
        self.dev_card = StatCard("Max Off-Trail", "0 m")
        layout.addLayout(stat_row(self.dist_card, self.gain_card, self.time_card, self.dev_card))

        splitter = QSplitter()
        splitter.setHandleWidth(14)
        self.pace_chart = PaceChart()
        splits_card = Card("Splits", margins=(14, 14, 14, 14))
        self.split_table = QTableWidget(0, 4)
        self.split_table.setHorizontalHeaderLabels(["Split", "Distance", "Elev Δ", "Pace (min/km)"])
        self.split_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.split_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.split_table.verticalHeader().setVisible(False)
        self.split_table.setShowGrid(False)
        splits_card.body.addWidget(self.split_table)

        splitter.addWidget(self.pace_chart)
        splitter.addWidget(splits_card)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter, 1)

        self.app_state.session_changed.connect(lambda _: self.refresh_all())
        self.app_state.sessions_changed.connect(self.refresh_all)
        self.refresh_all()

    def refresh_all(self) -> None:
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        sessions = self.app_state.db.list_sessions()
        active_idx = 0
        for idx, s in enumerate(sessions):
            route = self.app_state.db.get_route(s.route_id) if s.route_id else None
            self.session_combo.addItem(f"{hike_title(s, route)} ({(s.started_at or '')[:10]})", s.id)
            if s.id == self.app_state.active_session_id:
                active_idx = idx
        self.session_combo.setCurrentIndex(active_idx)
        self.session_combo.blockSignals(False)
        self._load_telemetry()

    def _on_session_changed(self) -> None:
        s_id = self.session_combo.currentData()
        if s_id:
            self.app_state.set_active_session(str(s_id))

    def _load_telemetry(self) -> None:
        session = self.app_state.get_active_session()
        unit = str(self.app_state.settings.get("unit_system", "metric"))
        if not session:
            self.pace_chart.set_splits([])
            self.split_table.setRowCount(0)
            return

        points = self.app_state.db.list_telemetry_points(session.id)
        route = self.app_state.db.get_route(session.route_id) if session.route_id else None
        if points:
            summary = summarize_telemetry(points)
            splits = compute_splits(points)
            dev_info = (
                detect_off_trail((points[-1].lat, points[-1].lon), route.coordinates)
                if route and route.coordinates
                else {"deviation_m": 0.0}
            )
            self.dist_card.set_value(format_distance(summary["distance_m"], unit))
            self.gain_card.set_value(f"+{format_elevation(summary['elevation_gain_m'], unit)}")
            self.time_card.set_value(format_duration_min(summary["duration_min"]))
            self.dev_card.set_value(f"{dev_info['deviation_m']:.1f} m")
            self.pace_chart.set_splits(splits)
            self.split_table.setRowCount(len(splits))
            for idx, sp in enumerate(splits):
                self.split_table.setItem(idx, 0, QTableWidgetItem(f"Split {sp.split_index}"))
                self.split_table.setItem(idx, 1, QTableWidgetItem(format_distance(sp.distance_m, unit)))
                self.split_table.setItem(idx, 2, QTableWidgetItem(f"{sp.elevation_change_m:+.0f} m"))
                self.split_table.setItem(idx, 3, QTableWidgetItem(f"{sp.pace_min_per_km:.1f} min/km"))
        else:
            self.dist_card.set_value(format_distance(session.actual_distance_m, unit))
            self.gain_card.set_value(f"+{format_elevation(session.actual_elevation_gain_m, unit)}")
            self.time_card.set_value(format_duration_min(session.actual_duration_min))
            self.dev_card.set_value("0.0 m (Traced)")
            self.pace_chart.set_splits([])
            self.split_table.setRowCount(0)

