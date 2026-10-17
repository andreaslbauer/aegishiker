"""QPainter bar chart comparing planned vs actual pace splits per kilometer."""
from typing import List
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from src.hiking.telemetry import SplitStat
from .base_chart import BaseChartWidget


class PaceChart(BaseChartWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Sync or simulate GPS telemetry to view kilometer pace splits", parent)
        self.setMinimumHeight(150)
        self.splits: List[SplitStat] = []

    def set_splits(self, splits: List[SplitStat]) -> None:
        self.splits = list(splits or [])
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        if not self.splits:
            self.draw_empty_state(painter)
            painter.end()
            return

        self.draw_background(painter, "#0B1610", "#2C4637")
        pad_l, pad_r, pad_t, pad_b = 40.0, 16.0, 20.0, 26.0
        w = max(10.0, float(self.width()) - pad_l - pad_r)
        h = max(10.0, float(self.height()) - pad_t - pad_b)

        max_pace = max(15.0, max((s.pace_min_per_km for s in self.splits), default=15.0))
        bar_w = max(8.0, (w / max(1, len(self.splits))) * 0.65)
        step_x = w / max(1, len(self.splits))

        painter.setFont(QFont("Sans Serif", 8))
        for idx, sp in enumerate(self.splits):
            bh = min(h, (sp.pace_min_per_km / max_pace) * h)
            bx = pad_l + idx * step_x + (step_x - bar_w) * 0.5
            by = pad_t + h - bh
            painter.setBrush(QColor("#2ECC71") if sp.elevation_change_m <= 40 else QColor("#F39C12"))
            painter.setPen(QColor("#1E8449"))
            painter.drawRoundedRect(QRectF(bx, by, bar_w, bh), 3, 3)
            painter.setPen(QColor("#98B2A1"))
            painter.drawText(int(bx), int(self.height() - 6), f"km {sp.split_index}")

        painter.end()

