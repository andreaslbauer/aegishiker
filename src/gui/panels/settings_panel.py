"""Application Settings & AegisPhotoVoyager Bridge Configuration panel."""
from typing import Optional
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.themes import apply_theme
from src.gui.widgets import SectionHeader


class SettingsPanel(QWidget):
    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state

        layout = QVBoxLayout(self)
        header = SectionHeader(
            "Preferences, Units, Local AI & AegisPhotoVoyager Integration",
            "Configure metric/imperial units, theme, pacing defaults, local Ollama AI, and PhotoVoyager DB location.",
        )
        layout.addWidget(header)

        box = QGroupBox("AegisHiker Preferences")
        form = QFormLayout(box)

        self.unit_combo = QComboBox()
        self.unit_combo.addItem("Metric (km, m, kg, °C)", "metric")
        self.unit_combo.addItem("Imperial (mi, ft, lb, °F)", "imperial")
        idx = self.unit_combo.findData(self.app_state.settings.get("unit_system", "metric"))
        if idx >= 0:
            self.unit_combo.setCurrentIndex(idx)
        form.addRow("Unit System:", self.unit_combo)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Alpine Forest Dark", "dark")
        self.theme_combo.addItem("Daylight Trail Light", "light")
        t_idx = self.theme_combo.findData(self.app_state.settings.get("theme", "dark"))
        if t_idx >= 0:
            self.theme_combo.setCurrentIndex(t_idx)
        form.addRow("Color Theme:", self.theme_combo)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(1.5, 10.0)
        self.speed_spin.setSingleStep(0.25)
        self.speed_spin.setValue(float(self.app_state.settings.get("base_speed_kmh", 4.5)))
        self.speed_spin.setSuffix(" km/h")
        form.addRow("Default Flat Hiking Speed:", self.speed_spin)

        self.corridor_spin = QDoubleSpinBox()
        self.corridor_spin.setRange(25.0, 2500.0)
        self.corridor_spin.setSingleStep(25.0)
        self.corridor_spin.setValue(float(self.app_state.settings.get("photo_match_corridor_m", 250.0)))
        self.corridor_spin.setSuffix(" m")
        form.addRow("Photo GPS Corridor Radius:", self.corridor_spin)

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
        form.addRow("AegisPhotoVoyager DB Path:", voyager_row)

        self.ollama_url_edit = QLineEdit(str(self.app_state.settings.get("ollama_host", "http://127.0.0.1:11434")))
        form.addRow("Local Ollama Host:", self.ollama_url_edit)

        self.ollama_model_edit = QLineEdit(str(self.app_state.settings.get("ollama_model", "qwen2.5:7b")))
        form.addRow("Ollama Model:", self.ollama_model_edit)

        self.emergency_edit = QLineEdit(str(self.app_state.settings.get("emergency_contact", "")))
        self.emergency_edit.setPlaceholderText("Name, phone, and overdue instructions for Leave-Behind PDF")
        form.addRow("Emergency Contact:", self.emergency_edit)

        save_btn = QPushButton("Save Preferences")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.save_settings)
        form.addRow("", save_btn)

        layout.addWidget(box)
        layout.addStretch(1)

    def _browse_voyager(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select AegisPhotoVoyager Database",
            "",
            "SQLite DB (*.db *.sqlite);;All Files (*)",
        )
        if path:
            self.voyager_edit.setText(path)

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

