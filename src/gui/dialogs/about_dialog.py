"""About AegisHiker dialog."""
from typing import Optional
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from src.core.about import APP_AUTHOR, APP_DESCRIPTION, APP_LICENSE, APP_NAME, get_version


class AboutDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.resize(460, 260)

        layout = QVBoxLayout(self)
        title = QLabel(f"<h2>{APP_NAME} v{get_version()}</h2>")
        layout.addWidget(title)

        desc = QLabel(APP_DESCRIPTION)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        meta = QLabel(f"<b>Author:</b> {APP_AUTHOR}<br><b>License:</b> {APP_LICENSE}")
        meta.setStyleSheet("color: #98B2A1;")
        layout.addWidget(meta)
        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("PrimaryButton")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

