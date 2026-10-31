"""Configure dialog: preferences in tabs (General, 3D Walkthrough), saved to settings.json."""
from typing import Optional
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.replay3d import REPLAY_SPEEDS
from src.gui.themes import apply_theme
from src.gui.widgets import add_form_row, form_layout


def _note(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #98B2A1; font-size: 8.5pt;")
    return lbl


class ConfigureDialog(QDialog):
    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        s = app_state.settings
        self.setWindowTitle("Configure")
        self.resize(520, 360)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._general_tab(str(s.get("theme", "dark"))), "General")
        self.tabs.addTab(self._walkthrough_tab(), "3D Walkthrough")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("PrimaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        col = QVBoxLayout(self)
        col.addWidget(self.tabs, 1)
        col.addWidget(buttons)

    def _general_tab(self, theme: str) -> QWidget:
        page = QWidget()
        form = form_layout()
        self.dark_radio = QRadioButton("Dark")
        self.light_radio = QRadioButton("Light")
        self.theme_group = QButtonGroup(page)
        for btn in (self.dark_radio, self.light_radio):
            self.theme_group.addButton(btn)
        (self.light_radio if theme == "light" else self.dark_radio).setChecked(True)
        row = QHBoxLayout()
        row.setSpacing(24)
        row.addWidget(self.dark_radio)
        row.addWidget(self.light_radio)
        row.addStretch(1)
        add_form_row(form, "Appearance", row)
        col = QVBoxLayout(page)
        col.addLayout(form)
        col.addStretch(1)
        return page

    def _walkthrough_tab(self) -> QWidget:
        s = self.app_state.settings
        page = QWidget()
        form = form_layout()

        self.people_chk = QCheckBox("Include photos with people in them")
        self.people_chk.setToolTip("Photos with faces AegisPhotoVoyager recognised, or AI tags naming a person")
        self.people_chk.setChecked(bool(s.get("replay_include_people", True)))
        add_form_row(form, "People", self.people_chk)

        self.cluster_chk = QCheckBox("Only one photo per spot with many similar photos")
        self.cluster_chk.setChecked(bool(s.get("replay_one_per_cluster", False)))
        add_form_row(form, "Similar photos", self.cluster_chk)
        voyager = self.app_state.voyager_bridge.is_available()
        form.addRow("", _note(
            "Uses AegisPhotoVoyager's visual similarity and quality scores to keep the best of each "
            "burst (starred photos win)."
            + ("" if voyager else " AegisPhotoVoyager's library was not found, so every photo is shown.")
        ))

        self.speed_combo = QComboBox()
        for label, val in REPLAY_SPEEDS:
            self.speed_combo.addItem(label, val)
        self.speed_combo.setCurrentIndex(max(0, self.speed_combo.findData(float(s.get("replay_speed", 1.0)))))
        add_form_row(form, "Default playback speed", self.speed_combo)

        col = QVBoxLayout(page)
        col.addLayout(form)
        col.addStretch(1)
        return page

    def save(self) -> None:
        s = self.app_state.settings
        theme = "light" if self.light_radio.isChecked() else "dark"
        theme_changed = theme != s.get("theme", "dark")
        s.set("theme", theme)
        s.set("replay_include_people", self.people_chk.isChecked())
        s.set("replay_one_per_cluster", self.cluster_chk.isChecked())
        s.set("replay_speed", float(self.speed_combo.currentData()))
        s.save()
        app = QApplication.instance()
        if theme_changed and isinstance(app, QApplication):
            apply_theme(app, theme, float(s.get("font_scale", 1.0)))
        self.app_state.settings_changed.emit()

    def accept(self) -> None:
        self.save()
        super().accept()
