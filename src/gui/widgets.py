"""Reusable custom PySide6 widgets for AegisHiker."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


PAGE_MARGINS = (28, 22, 28, 20)
PAGE_SPACING = 16


def page_layout(page: QWidget) -> QVBoxLayout:
    """Root layout for a page: generous margins and spacing between its cards."""
    layout = QVBoxLayout(page)
    layout.setContentsMargins(*PAGE_MARGINS)
    layout.setSpacing(PAGE_SPACING)
    return layout


class Card(QFrame):
    """Rounded surface grouping related content, with an optional title row.

    Add content to `card.body` (a QVBoxLayout); widgets for the title row's right side go into
    `card.actions` (a QHBoxLayout).
    """

    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None,
                 margins: tuple = (18, 16, 18, 16), spacing: int = 10) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*margins)
        outer.setSpacing(spacing)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        if title or subtitle:
            head = QHBoxLayout()
            head.setSpacing(12)
            text = QVBoxLayout()
            text.setSpacing(2)
            if title:
                self.title_lbl = QLabel(title)
                self.title_lbl.setObjectName("CardTitle")
                text.addWidget(self.title_lbl)
            if subtitle:
                self.subtitle_lbl = QLabel(subtitle)
                self.subtitle_lbl.setObjectName("CardSubtitle")
                self.subtitle_lbl.setWordWrap(True)
                text.addWidget(self.subtitle_lbl)
            head.addLayout(text, 1)
            head.addLayout(self.actions)
            outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(spacing)
        outer.addLayout(self.body, 1)


class StatCard(QFrame):
    """Compact metric card: small caption, large value, optional note."""

    def __init__(self, title: str, value: str = "--", subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)

        self.title_lbl = QLabel(title.upper())
        self.title_lbl.setObjectName("StatTitle")
        self.value_lbl = QLabel(value)
        self.value_lbl.setObjectName("StatValue")
        self.sub_lbl = QLabel(subtitle)
        self.sub_lbl.setObjectName("StatSub")
        # Long values (trail and region names) wrap instead of widening the whole window
        self.value_lbl.setWordWrap(True)
        self.sub_lbl.setWordWrap(True)

        layout.addWidget(self.title_lbl)
        layout.addWidget(self.value_lbl)
        layout.addWidget(self.sub_lbl)
        self.sub_lbl.setVisible(bool(subtitle))

    def set_value(self, value: str, subtitle: str | None = None) -> None:
        self.value_lbl.setText(value)
        if subtitle is not None:
            self.sub_lbl.setText(subtitle)
            self.sub_lbl.setVisible(bool(subtitle))


class SectionHeader(QWidget):
    """Page header: title, optional subtitle, and right-aligned action widgets."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(12)
        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("SectionTitle")
        text_col.addWidget(self.title_lbl)
        if subtitle:
            self.sub_lbl = QLabel(subtitle)
            self.sub_lbl.setObjectName("SectionSubtitle")
            self.sub_lbl.setWordWrap(True)
            text_col.addWidget(self.sub_lbl)
        layout.addLayout(text_col, 1)
        self.actions_layout = QHBoxLayout()
        self.actions_layout.setSpacing(8)
        layout.addLayout(self.actions_layout)


def form_layout() -> QFormLayout:
    """Two-column form with comfortable spacing (use add_form_row to add rows)."""
    form = QFormLayout()
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(10)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form


def add_form_row(form: QFormLayout, text: str, field) -> QLabel:
    """Add a row with a plain label (a QLabel made by QFormLayout from a string would treat
    '&' as a keyboard-shortcut marker and show '_')."""
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if isinstance(field, QWidget):
        label.setMinimumHeight(field.sizeHint().height())  # centre the label on its field
    form.addRow(label, field)
    return label


def shrink_combos(root: QWidget, min_chars: int = 12) -> None:
    """Let every combo box under `root` shrink below its longest entry (the popup still shows
    full names); by default a combo's minimum width is that of its longest item."""
    for combo in root.findChildren(QComboBox):
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(min_chars)


def stat_row(*cards: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(12)
    for c in cards:
        row.addWidget(c)
    return row


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
                "background-color: #1E4532; color: #2ECC71; border: 1px solid #2ECC71; "
                "border-radius: 14px; padding: 5px 14px; font-weight: 700;"
            )
        else:
            self.setStyleSheet(
                "background-color: transparent; color: #93AD9C; border: 1px solid #2F4B3A; "
                "border-radius: 14px; padding: 5px 14px; font-weight: 600;"
            )


class EmptyStateWidget(QFrame):
    """Friendly empty-state placeholder with call-to-action."""

    def __init__(self, title: str, message: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
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

