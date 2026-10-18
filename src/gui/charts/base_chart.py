"""Base QPainter chart widget with anti-aliasing and empty-state rendering."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget


class BaseChartWidget(QWidget):
    def __init__(self, empty_text: str = "No data loaded", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.empty_text = empty_text
        self.setMinimumHeight(140)
        self.setMouseTracking(True)

    def draw_background(self, painter: QPainter, bg_hex: str = "#0A130E", border_hex: str = "#223829") -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QColor(border_hex))
        painter.setBrush(QColor(bg_hex))
        painter.drawRoundedRect(rect, 12, 12)  # same rounding as the theme's cards

    def draw_empty_state(self, painter: QPainter) -> None:
        self.draw_background(painter)
        painter.setPen(QColor("#98B2A1"))
        painter.setFont(QFont("Sans Serif", 10))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)

    def paintEvent(self, event: QPaintEvent) -> None:  # pragma: no cover - overridden by subclasses
        painter = QPainter(self)
        self.draw_empty_state(painter)
        painter.end()

