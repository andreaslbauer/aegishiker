"""Modal dialog to update trail surface conditions (Good, Muddy, Icy, Overgrown, Closed)."""
from typing import Optional
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.hiking.models import TrailCondition, TrailSegment


class ConditionDialog(QDialog):
    def __init__(self, segment: TrailSegment, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.segment = segment
        self.setWindowTitle(f"Update Trail Condition — {segment.name}")
        self.resize(380, 180)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{segment.name}</b>"))

        form = QFormLayout()
        self.cond_combo = QComboBox()
        for cond in TrailCondition:
            self.cond_combo.addItem(cond.value.capitalize(), cond.value)
        idx = self.cond_combo.findData(segment.condition.value)
        if idx >= 0:
            self.cond_combo.setCurrentIndex(idx)
        form.addRow("Trailhead Condition:", self.cond_combo)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save Condition")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def selected_condition(self) -> str:
        return str(self.cond_combo.currentData())

