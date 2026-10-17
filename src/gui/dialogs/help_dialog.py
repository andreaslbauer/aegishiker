"""Offline HTML Help & Leave No Trace documentation viewer."""
from typing import Optional
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.core import paths
from src.gui.help_text import HELP_TOPICS


class HelpDialog(QDialog):
    def __init__(self, initial_topic: str = "getting_started", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AegisHiker Help & Mountain Safety Guide")
        self.resize(680, 480)

        layout = QVBoxLayout(self)
        top_row = QHBoxLayout()
        self.topic_combo = QComboBox()
        for key, filename in HELP_TOPICS.items():
            self.topic_combo.addItem(key.replace("_", " ").title(), filename)
        self.topic_combo.currentIndexChanged.connect(self._load_current)
        top_row.addWidget(self.topic_combo, 1)
        layout.addLayout(top_row)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        layout.addWidget(self.browser, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        idx = list(HELP_TOPICS.keys()).index(initial_topic) if initial_topic in HELP_TOPICS else 0
        self.topic_combo.setCurrentIndex(idx)
        self._load_current()

    def _load_current(self) -> None:
        filename = str(self.topic_combo.currentData())
        help_file = paths.get_help_dir() / filename
        if help_file.exists():
            self.browser.setHtml(help_file.read_text(encoding="utf-8"))
        else:
            self.browser.setHtml("<h2>Help topic not found</h2>")

