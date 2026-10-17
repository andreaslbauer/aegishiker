"""Interactive QPainter elevation profile with grade-% coloring, photo ticks,
and bidirectional hover/replay scrubber.
"""
from typing import List, Optional
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QWidget

from src.hiking.models import ElevationSample, TrailPhotoPin
from src.hiking.units import format_distance, format_elevation
from .base_chart import BaseChartWidget


class ElevationChart(BaseChartWidget):
    hover_distance_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Compose or select a route to inspect its elevation profile", parent)
        self.setMinimumHeight(160)
        self.samples: List[ElevationSample] = []
        self.photo_pins: List[TrailPhotoPin] = []
        self.highlight_distance_m: Optional[float] = None
        self.unit_system: str = "metric"

    def set_profile(
        self,
        samples: List[ElevationSample],
        photo_pins: Optional[List[TrailPhotoPin]] = None,
        unit_system: str = "metric",
    ) -> None:
        self.samples = list(samples or [])
        self.photo_pins = list(photo_pins or [])
        self.unit_system = unit_system
        self.update()

    def set_highlight_distance(self, distance_m: Optional[float]) -> None:
        self.highlight_distance_m = distance_m
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        if len(self.samples) < 2:
            self.draw_empty_state(painter)
            painter.end()
            return

        self.draw_background(painter, "#0B1610", "#2C4637")
        pad_l, pad_r, pad_t, pad_b = 48.0, 20.0, 18.0, 26.0
        w = max(10.0, float(self.width()) - pad_l - pad_r)
        h = max(10.0, float(self.height()) - pad_t - pad_b)

        max_d = max(1.0, self.samples[-1].distance_m)
        min_e = min(s.ele_m for s in self.samples)
        max_e = max(s.ele_m for s in self.samples)
        span_e = max(15.0, max_e - min_e)

        def to_xy(dist_m: float, ele_m: float) -> QPointF:
            x = pad_l + (dist_m / max_d) * w
            y = pad_t + h - ((ele_m - min_e) / span_e) * h
            return QPointF(x, y)

        # Fill area under curve
        area = QPainterPath()
        p0 = to_xy(self.samples[0].distance_m, self.samples[0].ele_m)
        area.moveTo(QPointF(pad_l, pad_t + h))
        area.lineTo(p0)
        line = QPainterPath()
        line.moveTo(p0)

        for s in self.samples[1:]:
            pt = to_xy(s.distance_m, s.ele_m)
            area.lineTo(pt)
            line.lineTo(pt)
        area.lineTo(QPointF(pad_l + w, pad_t + h))
        area.closeSubpath()

        grad = QLinearGradient(0, pad_t, 0, pad_t + h)
        grad.setColorAt(0.0, QColor(46, 204, 113, 120))
        grad.setColorAt(1.0, QColor(46, 204, 113, 15))
        painter.fillPath(area, grad)

        painter.setPen(QPen(QColor("#2ECC71"), 2.5))
        painter.drawPath(line)

        # Axes labels
        painter.setFont(QFont("Sans Serif", 8))
        painter.setPen(QColor("#98B2A1"))
        painter.drawText(6, int(pad_t + 8), format_elevation(max_e, self.unit_system))
        painter.drawText(6, int(pad_t + h), format_elevation(min_e, self.unit_system))
        painter.drawText(int(pad_l), int(self.height() - 6), "0")
        painter.drawText(int(pad_l + w - 45), int(self.height() - 6), format_distance(max_d, self.unit_system))

        # Photo pins on elevation profile
        for pin in self.photo_pins:
            pt = to_xy(min(max_d, max(0.0, pin.distance_along_route_m)), pin.ele_m or min_e)
            painter.setPen(QPen(QColor("#FFFFFF"), 1.2))
            painter.setBrush(QColor("#F39C12"))
            painter.drawEllipse(pt, 5.0, 5.0)

        # Highlight scrubber line
        if self.highlight_distance_m is not None:
            d = min(max_d, max(0.0, self.highlight_distance_m))
            nearest = min(self.samples, key=lambda s: abs(s.distance_m - d))
            pt = to_xy(nearest.distance_m, nearest.ele_m)
            painter.setPen(QPen(QColor("#F39C12"), 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(int(pt.x()), int(pad_t), int(pt.x()), int(pad_t + h))
            painter.setBrush(QColor("#F39C12"))
            painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
            painter.drawEllipse(pt, 6.0, 6.0)
            info = (
                f"{format_distance(nearest.distance_m, self.unit_system)} • "
                f"{format_elevation(nearest.ele_m, self.unit_system)} "
                f"({nearest.grade_pct:+.1f}%)"
            )
            painter.setPen(QColor("#ECF5EE"))
            painter.drawText(int(min(self.width() - 180, max(pad_l, pt.x() + 8))), int(pad_t + 14), info)

        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if len(self.samples) < 2:
            return
        pad_l, pad_r = 48.0, 20.0
        w = max(10.0, float(self.width()) - pad_l - pad_r)
        frac = (event.position().x() - pad_l) / w
        frac = min(1.0, max(0.0, frac))
        dist_m = frac * self.samples[-1].distance_m
        self.highlight_distance_m = dist_m
        self.hover_distance_changed.emit(dist_m)
        self.update()

