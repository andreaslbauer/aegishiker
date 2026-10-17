"""Opt-in Desktop Region Downloader dialog using Overpass API + Open-Elevation."""
from typing import Optional
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.pack_downloader import download_custom_region_pack
from src.gui.busy import run_in_background
from src.gui.app_state import AppState


class RegionDownloadDialog(QDialog):
    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.setWindowTitle("Download Custom Offline Region Pack (OSM Overpass + DEM)")
        self.resize(480, 320)

        layout = QVBoxLayout(self)
        info = QLabel(
            "Download a hiking trail network and elevation profile for any geographic bounding box "
            "and save it permanently for 100% offline desktop & mobile use."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #98B2A1;")
        layout.addWidget(info)

        form = QFormLayout()
        self.name_edit = QLineEdit("Custom Alpine Region")
        self.region_edit = QLineEdit("New England / Custom")
        self.lat_spin = QDoubleSpinBox()
        self.lat_spin.setRange(-85.0, 85.0)
        self.lat_spin.setDecimals(4)
        self.lat_spin.setValue(44.2706)
        self.lon_spin = QDoubleSpinBox()
        self.lon_spin.setRange(-180.0, 180.0)
        self.lon_spin.setDecimals(4)
        self.lon_spin.setValue(-71.3033)
        self.span_spin = QDoubleSpinBox()
        self.span_spin.setRange(0.02, 0.35)
        self.span_spin.setDecimals(3)
        self.span_spin.setValue(0.08)

        form.addRow("Destination Name:", self.name_edit)
        form.addRow("State / Region:", self.region_edit)
        form.addRow("Center Latitude:", self.lat_spin)
        form.addRow("Center Longitude:", self.lon_spin)
        form.addRow("Bounding Box Span (°):", self.span_spin)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        dl_btn = QPushButton("Download & Index Pack")
        dl_btn.setObjectName("PrimaryButton")
        dl_btn.clicked.connect(self._on_download)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(dl_btn)
        layout.addLayout(btn_row)

    def _on_download(self) -> None:
        lat = self.lat_spin.value()
        lon = self.lon_spin.value()
        half = self.span_spin.value() / 2.0
        bbox = (lat - half, lon - half, lat + half, lon + half)
        try:
            pack = run_in_background(
                self,
                "Downloading trails, terrain and map details…",
                download_custom_region_pack,
                title="Downloading Region",
                pass_progress=True,
                name=self.name_edit.text().strip() or "Custom Region",
                region=self.region_edit.text().strip() or "Custom",
                bbox=bbox,
                summary="User-downloaded custom offline trail pack.",
            )
            self.app_state.db.import_destination_pack(pack)
            self.app_state.set_active_destination(pack.destination.id)
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Region Download Failed", f"Could not download region: {exc}")

