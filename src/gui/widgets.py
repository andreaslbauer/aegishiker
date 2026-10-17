"""Reusable custom PySide6 widgets for AegisHiker."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class StatCard(QFrame):
    """Compact metric card showing a bold value and a muted caption."""

    def __init__(self, title: str, value: str = "--", subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardFrame")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        self.title_lbl = QLabel(title.upper())
        self.title_lbl.setStyleSheet("font-size: 9pt; color: #98B2A1; font-weight: 600;")
        self.value_lbl = QLabel(value)
        self.value_lbl.setStyleSheet("font-size: 14pt; font-weight: 700; color: #2ECC71;")
        self.sub_lbl = QLabel(subtitle)
        self.sub_lbl.setStyleSheet("font-size: 8.5pt; color: #98B2A1;")

        layout.addWidget(self.title_lbl)
        layout.addWidget(self.value_lbl)
        if subtitle:
            layout.addWidget(self.sub_lbl)

    def set_value(self, value: str, subtitle: str | None = None) -> None:
        self.value_lbl.setText(value)
        if subtitle is not None:
            self.sub_lbl.setText(subtitle)


class SectionHeader(QWidget):
    """Header row with title, optional subtitle, and right-aligned action widgets."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("SectionTitle")
        text_col.addWidget(self.title_lbl)
        if subtitle:
            self.sub_lbl = QLabel(subtitle)
            self.sub_lbl.setStyleSheet("color: #98B2A1; font-size: 9.5pt;")
            text_col.addWidget(self.sub_lbl)
        layout.addLayout(text_col)
        layout.addStretch(1)
        self.actions_layout = QHBoxLayout()
        layout.addLayout(self.actions_layout)


class BadgeLabel(QLabel):
    """Colored pill badge for difficulty or trail condition."""

    COLOR_MAP = {
        "easy": ("#1E8449", "#ECF5EE"),
        "moderate": ("#2874A6", "#ECF5EE"),
        "hard": ("#D68910", "#191003"),
        "strenuous": ("#C0392B", "#FFFFFF"),
        "good": ("#27AE60", "#09180F"),
        "muddy": ("#D68910", "#191003"),
        "icy": ("#3498DB", "#081926"),
        "overgrown": ("#8E44AD", "#FFFFFF"),
        "closed": ("#E74C3C", "#FFFFFF"),
    }

    def __init__(self, text: str, kind: str = "good", parent: QWidget | None = None) -> None:
        super().__init__(text.upper(), parent)
        self.set_kind(text, kind)

    def set_kind(self, text: str, kind: str) -> None:
        self.setText(text.upper())
        bg, fg = self.COLOR_MAP.get(kind.lower(), ("#2C4637", "#ECF5EE"))
        self.setStyleSheet(
            f"background-color: {bg}; color: {fg}; padding: 2px 8px; "
            f"border-radius: 9px; font-size: 8.5pt; font-weight: 700;"
        )


class PillToggle(QPushButton):
    """Checkable pill button for condition and difficulty filters."""

    def __init__(self, label: str, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self._update_style()
        self.toggled.connect(lambda _: self._update_style())

    def _update_style(self) -> None:
        if self.isChecked():
            self.setStyleSheet(
                "background-color: #2ECC71; color: #09180F; border-radius: 12px; "
                "padding: 4px 11px; font-weight: 700;"
            )
        else:
            self.setStyleSheet(
                "background-color: #1E3327; color: #98B2A1; border-radius: 12px; "
                "padding: 4px 11px; font-weight: 600;"
            )


class EmptyStateWidget(QFrame):
    """Friendly empty-state placeholder with call-to-action."""

    def __init__(self, title: str, message: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardFrame")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t_lbl = QLabel(title)
        t_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t_lbl.setStyleSheet("font-size: 12pt; font-weight: 700; color: #2ECC71;")
        m_lbl = QLabel(message)
        m_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m_lbl.setWordWrap(True)
        m_lbl.setStyleSheet("color: #98B2A1;")
        layout.addWidget(t_lbl)
        layout.addWidget(m_lbl)

