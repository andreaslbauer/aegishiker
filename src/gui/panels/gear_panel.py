"""10 Essentials, Gear Weight, Hydration & Calorie Calculator panel."""
from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.widgets import SectionHeader, StatCard
from src.hiking.gear import (
    estimate_calories_kcal,
    estimate_hydration_ml,
    generate_gear_checklist,
    total_pack_weight_g,
)
from src.hiking.models import GearItem


class GearPanel(QWidget):
    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.items: List[GearItem] = []

        layout = QVBoxLayout(self)
        header = SectionHeader(
            "Gear Checklist, 10 Essentials & Hydration Planner",
            "Compute pack weight, water requirements, and energy expenditure for your active route.",
        )
        layout.addWidget(header)

        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("Template:"))
        self.tpl_combo = QComboBox()
        for key, label in [
            ("day_hike", "Day Hike (3-Season)"),
            ("winter_alpine", "Winter / Alpine Above Treeline"),
            ("overnight_backpacking", "Overnight Backpacking"),
            ("trail_running", "Fastpacking / Trail Running"),
        ]:
            self.tpl_combo.addItem(label, key)
        ctrl_row.addWidget(self.tpl_combo)

        ctrl_row.addWidget(QLabel("Ambient Temp:"))
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(-25.0, 45.0)
        self.temp_spin.setValue(18.0)
        self.temp_spin.setSuffix(" °C")
        self.temp_spin.valueChanged.connect(self._recompute_cards)
        ctrl_row.addWidget(self.temp_spin)

        load_btn = QPushButton("Load Template for Active Route")
        load_btn.setObjectName("PrimaryButton")
        load_btn.clicked.connect(self.load_template_for_route)
        ctrl_row.addWidget(load_btn)
        ctrl_row.addStretch(1)
        layout.addLayout(ctrl_row)

        cards_row = QHBoxLayout()
        self.weight_card = StatCard("Checked Pack Weight", "0.00 kg")
        self.water_card = StatCard("Recommended Water", "0.0 L")
        self.cal_card = StatCard("Estimated Burn", "0 kcal")
        cards_row.addWidget(self.weight_card)
        cards_row.addWidget(self.water_card)
        cards_row.addWidget(self.cal_card)
        layout.addLayout(cards_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Packed", "Item Name", "Category", "Weight (g)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_table_item_changed)
        layout.addWidget(self.table, 1)

        self.app_state.route_changed.connect(lambda _: self.refresh_from_db())
        self.refresh_from_db()

    def refresh_from_db(self) -> None:
        r_id = self.app_state.active_route_id
        if not r_id:
            return
        self.items = self.app_state.db.list_gear(r_id)
        if not self.items:
            self.load_template_for_route()
            return
        self._populate_table()
        self._recompute_cards()

    def load_template_for_route(self) -> None:
        r_id = self.app_state.active_route_id
        if not r_id:
            return
        tpl_key = str(self.tpl_combo.currentData() or "day_hike")
        self.items = generate_gear_checklist(r_id, tpl_key)
        self.app_state.db.replace_gear_for_route(r_id, self.items)
        self._populate_table()
        self._recompute_cards()

    def _populate_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.items))
        for idx, item in enumerate(self.items):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk_item.setCheckState(Qt.CheckState.Checked if item.checked else Qt.CheckState.Unchecked)
            self.table.setItem(idx, 0, chk_item)
            self.table.setItem(idx, 1, QTableWidgetItem(item.name))
            self.table.setItem(idx, 2, QTableWidgetItem(item.category.capitalize()))
            self.table.setItem(idx, 3, QTableWidgetItem(f"{item.weight_g:.0f}"))
        self.table.blockSignals(False)

    def _on_table_item_changed(self, tbl_item: QTableWidgetItem) -> None:
        row = tbl_item.row()
        if 0 <= row < len(self.items) and tbl_item.column() == 0:
            self.items[row].checked = tbl_item.checkState() == Qt.CheckState.Checked
            if self.app_state.active_route_id:
                self.app_state.db.replace_gear_for_route(self.app_state.active_route_id, self.items)
            self._recompute_cards()

    def _recompute_cards(self) -> None:
        route = self.app_state.get_active_route()
        w_g = total_pack_weight_g(self.items, checked_only=True)
        self.weight_card.set_value(f"{w_g / 1000.0:.2f} kg", f"{w_g:.0f} g total")
        if route:
            water_ml = estimate_hydration_ml(
                duration_min=route.estimated_duration_min,
                elevation_gain_m=route.elevation_gain_m,
                temp_c=float(self.temp_spin.value()),
            )
            kcal = estimate_calories_kcal(
                distance_m=route.distance_m,
                elevation_gain_m=route.elevation_gain_m,
                hiker_weight_kg=float(self.app_state.settings.get("hiker_weight_kg", 75.0)),
                pack_weight_kg=w_g / 1000.0,
            )
            self.water_card.set_value(f"{water_ml / 1000.0:.1f} L", f"{water_ml} mL recommended")
            self.cal_card.set_value(f"{kcal} kcal", "Estimated trail burn")

