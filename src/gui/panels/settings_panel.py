"""Application Settings & AegisPhotoVoyager Bridge Configuration panel."""
from typing import Optional
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.themes import apply_theme
from src.gui.widgets import Card, SectionHeader, add_form_row, form_layout, page_layout


class SettingsPanel(QWidget):
    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state

        layout = page_layout(self)
        header = SectionHeader(
            "Preferences",
            "Units and look, hiking defaults, your AegisPhotoVoyager library, local AI and your emergency contact.",
        )
        layout.addWidget(header)

        display_card, display_form = self._form_card("Display", "Units and colour theme")
        hiking_card, hiking_form = self._form_card("Hiking", "Defaults for pace estimates and photo matching")
        photos_card, photos_form = self._form_card("AegisPhotoVoyager", "Where your photo library lives")
        ai_card, ai_form = self._form_card("Local AI", "Ollama on this computer — nothing leaves it")
        safety_card, safety_form = self._form_card("Safety", "Printed on the trailhead leave-behind sheet")
        form = display_form

        self.unit_combo = QComboBox()
        self.unit_combo.addItem("Metric (km, m, kg, °C)", "metric")
        self.unit_combo.addItem("Imperial (mi, ft, lb, °F)", "imperial")
        idx = self.unit_combo.findData(self.app_state.settings.get("unit_system", "metric"))
        if idx >= 0:
            self.unit_combo.setCurrentIndex(idx)
        add_form_row(display_form, "Units", self.unit_combo)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Alpine Forest Dark", "dark")
        self.theme_combo.addItem("Daylight Trail Light", "light")
        t_idx = self.theme_combo.findData(self.app_state.settings.get("theme", "dark"))
        if t_idx >= 0:
            self.theme_combo.setCurrentIndex(t_idx)
        add_form_row(display_form, "Theme", self.theme_combo)
        self.app_state.settings_changed.connect(self._sync_theme)  # changed in Configure

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(1.5, 10.0)
        self.speed_spin.setSingleStep(0.25)
        self.speed_spin.setValue(float(self.app_state.settings.get("base_speed_kmh", 4.5)))
        self.speed_spin.setSuffix(" km/h")
        add_form_row(hiking_form, "Flat walking speed", self.speed_spin)

        self.corridor_spin = QDoubleSpinBox()
        self.corridor_spin.setRange(25.0, 2500.0)
        self.corridor_spin.setSingleStep(25.0)
        self.corridor_spin.setValue(float(self.app_state.settings.get("photo_match_corridor_m", 250.0)))
        self.corridor_spin.setSuffix(" m")
        add_form_row(hiking_form, "Photo match distance", self.corridor_spin)

        voyager_row = QHBoxLayout()
        discovered = self.app_state.voyager_bridge.discover_db_path()
        self.voyager_edit = QLineEdit(
            str(self.app_state.settings.get("voyager_db_path", "") or (str(discovered) if discovered else ""))
        )
        self.voyager_edit.setPlaceholderText("Auto-discovered in ~/.AegisPhotoVoyager/voyager.db")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_voyager)
        voyager_row.addWidget(self.voyager_edit, 1)
        voyager_row.addWidget(browse_btn)
        add_form_row(photos_form, "Library database", voyager_row)

        self.ollama_url_edit = QLineEdit(str(self.app_state.settings.get("ollama_host", "http://127.0.0.1:11434")))
        add_form_row(ai_form, "Ollama host", self.ollama_url_edit)

        self.ollama_model_edit = QLineEdit(str(self.app_state.settings.get("ollama_model", "qwen2.5:7b")))
        add_form_row(ai_form, "Model", self.ollama_model_edit)

        self.emergency_edit = QLineEdit(str(self.app_state.settings.get("emergency_contact", "")))
        self.emergency_edit.setPlaceholderText("Name, phone, and overdue instructions for Leave-Behind PDF")
        add_form_row(safety_form, "Emergency contact", self.emergency_edit)

        save_btn = QPushButton("Save Preferences")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.save_settings)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        grid.addWidget(display_card, 0, 0)
        grid.addWidget(hiking_card, 0, 1)
        grid.addWidget(photos_card, 1, 0)
        grid.addWidget(ai_card, 1, 1)
        grid.addWidget(safety_card, 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(save_btn)
        layout.addLayout(save_row)

    def _sync_theme(self) -> None:
        idx = self.theme_combo.findData(self.app_state.settings.get("theme", "dark"))
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)

    def _browse_voyager(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select AegisPhotoVoyager Database",
            "",
            "SQLite DB (*.db *.sqlite);;All Files (*)",
        )
        if path:
            self.voyager_edit.setText(path)

    @staticmethod
    def _form_card(title: str, subtitle: str):
        card = Card(title, subtitle)
        form = form_layout()
        card.body.addLayout(form)
        return card, form

    def save_settings(self) -> None:
        s = self.app_state.settings
        s.set("unit_system", str(self.unit_combo.currentData()))
        s.set("theme", str(self.theme_combo.currentData()))
        s.set("base_speed_kmh", float(self.speed_spin.value()))
        s.set("photo_match_corridor_m", float(self.corridor_spin.value()))
        s.set("voyager_db_path", self.voyager_edit.text().strip())
        s.set("ollama_host", self.ollama_url_edit.text().strip())
        s.set("ollama_model", self.ollama_model_edit.text().strip())
        s.set("emergency_contact", self.emergency_edit.text().strip())
        s.save()

        self.app_state.voyager_bridge.explicit_db_path = self.voyager_edit.text().strip() or None
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, str(s.get("theme", "dark")), float(s.get("font_scale", 1.0)))
        self.app_state.settings_changed.emit()
        self.app_state.status_message.emit("Preferences saved.")

