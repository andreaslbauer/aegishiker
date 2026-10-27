"""Who came along and what the weather was: a form used when logging a hike ('I Hiked This Trail')
and the 'Hike Details' dialog for a saved hike. Weather can be looked up from Open-Meteo (opt-in,
one request, logged)."""
from datetime import date, datetime
from typing import Callable, Dict, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.busy import run_in_background
from src.hiking.hike_info import hike_start
from src.hiking.hike_report import hike_details, set_hike_details
from src.hiking.models import HikeSession

# (lat, lon, day, start, end) of the hike, for the weather lookup
WhenWhere = Tuple[float, float, date, Optional[datetime], Optional[datetime]]


def _c_to_shown(c: str, us: bool) -> str:
    try:
        v = float(c)
    except ValueError:
        return ""
    return f"{v * 9 / 5 + 32:.0f}" if us else f"{v:.1f}".rstrip("0").rstrip(".")


def _shown_to_c(text: str, us: bool) -> str:
    try:
        v = float(text.replace(",", "."))
    except ValueError:
        return ""
    return f"{(v - 32) * 5 / 9:.1f}" if us else f"{v:.1f}"


class HikeDetailsForm(QWidget):
    """Companions and weather fields. Values are kept in metric (°C, mm, km/h) and shown in the
    user's units."""

    def __init__(self, app_state: AppState, when_where: Callable[[], Optional[WhenWhere]],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.when_where = when_where
        self.us = str(app_state.settings.get("unit_system", "metric")) != "metric"
        self._source = ""
        self._hourly = ""
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.companions = QLineEdit()
        self.companions.setPlaceholderText("e.g. Maria, Tom (people recognised in the photos are added automatically)")
        form.addRow("Hiked with:", self.companions)

        self.weather = QLineEdit()
        self.weather.setPlaceholderText("e.g. Clear sky, light breeze")
        lookup = QPushButton("Look Up Weather")
        lookup.setToolTip("Fetch the weather at the trailhead during the hike from Open-Meteo (needs internet)")
        lookup.clicked.connect(self.look_up_weather)
        wrow = QHBoxLayout()
        wrow.addWidget(self.weather, 1)
        wrow.addWidget(lookup)
        form.addRow("Weather:", wrow)

        num = QDoubleValidator(-80.0, 9999.0, 1, self)
        t_unit, p_unit, w_unit = ("°F", "in", "mph") if self.us else ("°C", "mm", "km/h")
        self.t_low, self.t_high, self.precip, self.wind = (QLineEdit() for _ in range(4))
        for w in (self.t_low, self.t_high, self.precip, self.wind):
            w.setValidator(num)
            w.setMaximumWidth(80)
        trow = QHBoxLayout()
        for label, w, unit in (("", self.t_low, "to"), ("", self.t_high, t_unit), ("   Rain", self.precip, p_unit),
                               ("   Wind up to", self.wind, w_unit)):
            if label:
                trow.addWidget(QLabel(label))
            trow.addWidget(w)
            trow.addWidget(QLabel(unit))
        trow.addStretch(1)
        form.addRow("Temperature:", trow)
        self.source_lbl = QLabel("")
        self.source_lbl.setObjectName("Muted")
        form.addRow("", self.source_lbl)

    def set_details(self, d: Dict[str, str]) -> None:
        self.companions.setText(d.get("companions", ""))
        self.weather.setText(d.get("weather", ""))
        self.t_low.setText(_c_to_shown(d.get("temp_low_c", ""), self.us))
        self.t_high.setText(_c_to_shown(d.get("temp_high_c", ""), self.us))
        precip = d.get("precip_mm", "")
        wind = d.get("wind_kmh", "")
        try:
            self.precip.setText(f"{float(precip) / 25.4:.2f}" if self.us and precip else (precip or ""))
        except ValueError:
            self.precip.setText("")
        try:
            self.wind.setText(f"{float(wind) / 1.609:.0f}" if self.us and wind else (wind or ""))
        except ValueError:
            self.wind.setText("")
        self._source = d.get("weather_source", "")
        self._hourly = d.get("weather_hourly", "")
        self.source_lbl.setText(f"Weather from {self._source}" if self._source else "")

    def details(self) -> Dict[str, str]:
        def num(w: QLineEdit, factor: float) -> str:
            try:
                return f"{float(w.text().replace(',', '.')) * factor:.1f}" if w.text().strip() else ""
            except ValueError:
                return ""
        return {
            "companions": self.companions.text().strip(),
            "weather": self.weather.text().strip(),
            "temp_low_c": _shown_to_c(self.t_low.text(), self.us) if self.t_low.text().strip() else "",
            "temp_high_c": _shown_to_c(self.t_high.text(), self.us) if self.t_high.text().strip() else "",
            "precip_mm": num(self.precip, 25.4 if self.us else 1.0),
            "wind_kmh": num(self.wind, 1.609 if self.us else 1.0),
            "weather_source": self._source if self.weather.text().strip() else "",
            "weather_hourly": self._hourly if self.weather.text().strip() else "",
        }

    def look_up_weather(self) -> None:
        ww = self.when_where()
        if ww is None:
            QMessageBox.information(self, "Weather", "This hike has no route to look up the weather for.")
            return
        from src.core.weather import fetch_hike_weather

        lat, lon, day, start, end = ww
        try:
            found = run_in_background(self, "Asking Open-Meteo for the weather on the trail…", fetch_hike_weather,
                                      lat, lon, day, start, end, title="Weather")
        except Exception as e:
            QMessageBox.warning(self, "Weather", str(e))
            return
        merged = self.details()
        merged.update(found)
        self.set_details(merged)


class HikeDetailsDialog(QDialog):
    """Edit a saved hike's name, companions, weather and notes."""

    def __init__(self, app_state: AppState, session: HikeSession, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.session = session
        self.setWindowTitle("Hike Details")
        self.resize(720, 460)
        col = QVBoxLayout(self)
        route = app_state.db.get_route(session.route_id) if session.route_id else None
        top = QFormLayout()
        self.title_edit = QLineEdit(session.title)
        top.addRow("Hike name:", self.title_edit)
        col.addLayout(top)

        def when_where() -> Optional[WhenWhere]:
            if route is None or not route.coords_3d:
                return None
            start = hike_start(session)
            try:
                end = datetime.fromisoformat(session.ended_at) if session.ended_at else None
            except ValueError:
                end = None
            day = start.date() if start else date.today()
            timed = start is not None and (start.hour or start.minute)
            lon, lat = route.coords_3d[0][0], route.coords_3d[0][1]
            return lat, lon, day, start if timed else None, end if timed else None

        self.form = HikeDetailsForm(app_state, when_where)
        self.form.set_details(hike_details(session))
        col.addWidget(self.form)
        col.addWidget(QLabel("Notes:"))
        self.notes = QTextEdit()
        self.notes.setPlainText(session.notes)
        self.notes.setPlaceholderText("Trail conditions, wildlife, the view from the top…")
        col.addWidget(self.notes, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(save)
        col.addLayout(row)
        self.title_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _save(self) -> None:
        s = self.session
        s.title = self.title_edit.text().strip() or s.title
        s.notes = self.notes.toPlainText().strip()
        set_hike_details(s, self.form.details())
        self.app_state.db.save_session(s)
        self.app_state.sessions_changed.emit()
        self.accept()
