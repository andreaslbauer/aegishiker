"""Alpine Forest Dark and Daylight Trail Light palettes and QSS stylesheet generator."""
from typing import Dict
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

DARK_COLORS: Dict[str, str] = {
    "bg": "#0F1B14",
    "panel": "#16261D",
    "panel_alt": "#1E3327",
    "text": "#ECF5EE",
    "muted": "#98B2A1",
    "accent": "#2ECC71",
    "accent_hover": "#27AE60",
    "warn": "#F39C12",
    "danger": "#E74C3C",
    "info": "#3498DB",
    "border": "#2C4637",
    "input_bg": "#111E17",
}

LIGHT_COLORS: Dict[str, str] = {
    "bg": "#F4F7F4",
    "panel": "#FFFFFF",
    "panel_alt": "#E7EFE9",
    "text": "#16261D",
    "muted": "#4F6B5A",
    "accent": "#1E8449",
    "accent_hover": "#196F3D",
    "warn": "#D68910",
    "danger": "#C0392B",
    "info": "#2874A6",
    "border": "#C5D6CA",
    "input_bg": "#FFFFFF",
}


def get_theme_colors(theme: str = "dark") -> Dict[str, str]:
    return LIGHT_COLORS if str(theme).lower() == "light" else DARK_COLORS


def build_stylesheet(theme: str = "dark", font_scale: float = 1.0) -> str:
    c = get_theme_colors(theme)
    raw_scale = float(font_scale or 1.0)
    scale = raw_scale / 100.0 if raw_scale > 10.0 else raw_scale
    scale = max(0.75, min(2.0, scale))
    base_pt = max(9, int(round(10 * scale)))
    h1_pt = max(12, int(round(15 * scale)))
    return f"""
    QWidget {{
        background-color: {c['bg']};
        color: {c['text']};
        font-size: {base_pt}pt;
    }}
    QMainWindow, QDialog {{
        background-color: {c['bg']};
    }}
    QTabWidget::pane {{
        border: 1px solid {c['border']};
        background-color: {c['bg']};
        border-radius: 6px;
    }}
    QTabBar::tab {{
        background-color: {c['panel']};
        color: {c['muted']};
        border: 1px solid {c['border']};
        padding: 7px 14px;
        margin-right: 3px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        font-weight: 600;
    }}
    QTabBar::tab:selected {{
        background-color: {c['panel_alt']};
        color: {c['accent']};
        border-bottom-color: {c['accent']};
    }}
    QGroupBox, QFrame#CardFrame {{
        background-color: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 8px;
    }}
    QPushButton {{
        background-color: {c['panel_alt']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 6px 12px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        border-color: {c['accent']};
    }}
    QPushButton#PrimaryButton {{
        background-color: {c['accent']};
        color: #09180F;
        border-color: {c['accent']};
        font-weight: 700;
    }}
    QPushButton#PrimaryButton:hover {{
        background-color: {c['accent_hover']};
    }}
    QPushButton#WarnButton {{
        background-color: {c['warn']};
        color: #1A1002;
        border-color: {c['warn']};
        font-weight: 700;
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {c['input_bg']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 5px 8px;
    }}
    QListWidget, QTableWidget, QTreeWidget {{
        background-color: {c['panel']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 6px;
    }}
    QHeaderView::section {{
        background-color: {c['panel_alt']};
        color: {c['muted']};
        border: 1px solid {c['border']};
        padding: 5px;
        font-weight: 600;
    }}
    QLabel#SectionTitle {{
        font-size: {h1_pt}pt;
        font-weight: 700;
        color: {c['accent']};
    }}
    QStatusBar {{
        background-color: {c['panel']};
        color: {c['muted']};
        border-top: 1px solid {c['border']};
    }}
    """


def apply_theme(app: QApplication, theme: str = "dark", font_scale: float = 1.0) -> None:
    c = get_theme_colors(theme)
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(c["input_bg"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c["panel_alt"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(c["panel_alt"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#09180F"))
    app.setPalette(pal)
    app.setStyleSheet(build_stylesheet(theme, font_scale))

