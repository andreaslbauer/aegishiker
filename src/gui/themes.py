"""Alpine Forest Dark and Daylight Trail Light palettes and QSS stylesheet generator.

Layout language: pages sit on the window background; content is grouped into rounded cards
(QFrame#Card / #CardFrame, see widgets.Card) with generous padding; a sidebar (QListWidget#NavList)
navigates between pages.
"""
from typing import Dict
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

DARK_COLORS: Dict[str, str] = {
    "bg": "#0D1712",  # window / page background
    "sidebar": "#0A130E",
    "panel": "#14231A",  # cards
    "panel_alt": "#1B2E23",  # raised controls, hover
    "text": "#ECF5EE",
    "muted": "#93AD9C",
    "faint": "#5E7868",
    "accent": "#2ECC71",
    "accent_hover": "#27AE60",
    "accent_soft": "#1E4532",  # selection / subtle accent fill
    "on_accent": "#08170E",
    "warn": "#F39C12",
    "danger": "#E74C3C",
    "info": "#3498DB",
    "border": "#223829",
    "border_strong": "#2F4B3A",
    "input_bg": "#0F1C15",
}

LIGHT_COLORS: Dict[str, str] = {
    "bg": "#F3F6F3",
    "sidebar": "#E8EFE9",
    "panel": "#FFFFFF",
    "panel_alt": "#EDF3EE",
    "text": "#16261D",
    "muted": "#4F6B5A",
    "faint": "#8BA394",
    "accent": "#1E8449",
    "accent_hover": "#196F3D",
    "accent_soft": "#D5EDDD",
    "on_accent": "#FFFFFF",
    "warn": "#D68910",
    "danger": "#C0392B",
    "info": "#2874A6",
    "border": "#DCE6DE",
    "border_strong": "#C5D6CA",
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
    h1_pt = max(13, int(round(17 * scale)))
    card_pt = max(10, int(round(11 * scale)))
    small_pt = max(8, int(round(8.5 * scale)))
    return f"""
    /* Plain widgets have no background of their own: they show the surface they sit on
       (window, sidebar or card). Only windows, cards and controls paint a background. */
    QWidget {{
        color: {c['text']};
        font-size: {base_pt}pt;
    }}
    QMainWindow, QDialog, QMessageBox {{
        background-color: {c['bg']};
    }}
    QLabel, QCheckBox, QRadioButton, QScrollArea, QStackedWidget {{
        background: transparent;
    }}
    QToolTip {{
        background-color: {c['panel_alt']};
        color: {c['text']};
        border: 1px solid {c['border_strong']};
        border-radius: 6px;
        padding: 6px 8px;
    }}

    /* ── Sidebar navigation ── */
    QFrame#Sidebar {{
        background-color: {c['sidebar']};
        border: none;
        border-right: 1px solid {c['border']};
    }}
    QLabel#AppTitle {{
        font-size: {h1_pt - 2}pt;
        font-weight: 800;
        color: {c['accent']};
    }}
    QLabel#AppSubtitle {{
        color: {c['muted']};
        font-size: {small_pt}pt;
    }}
    QLabel#NavGroup {{
        color: {c['faint']};
        font-size: {small_pt}pt;
        font-weight: 700;
        padding: 10px 14px 2px 14px;
    }}
    QListWidget#NavList {{
        background: transparent;
        border: none;
        outline: 0;
    }}
    QListWidget#NavList::item {{
        color: {c['muted']};
        padding: 8px 12px;
        margin: 1px 8px;
        border-radius: 8px;
    }}
    QListWidget#NavList::item:hover {{
        background-color: {c['panel_alt']};
        color: {c['text']};
    }}
    QListWidget#NavList::item:selected {{
        background-color: {c['accent_soft']};
        color: {c['accent']};
        font-weight: 700;
    }}
    QPushButton#NavButton {{
        background: transparent;
        border: none;
        color: {c['muted']};
        text-align: left;
        padding: 8px 14px;
        margin: 1px 8px;
        border-radius: 8px;
        font-weight: 600;
    }}
    QPushButton#NavButton:hover {{
        background-color: {c['panel_alt']};
        color: {c['text']};
    }}
    QPushButton#NavButton:checked {{
        background-color: {c['accent_soft']};
        color: {c['accent']};
    }}
    QTabWidget#Pages > QTabBar {{
        height: 0px;
    }}
    QTabWidget#Pages::pane {{
        border: none;
    }}

    /* ── Tabs (inside pages) ── */
    QTabWidget::pane {{
        border: 1px solid {c['border']};
        background-color: {c['bg']};
        border-radius: 10px;
    }}
    QTabBar::tab {{
        background-color: transparent;
        color: {c['muted']};
        border: none;
        border-bottom: 2px solid transparent;
        padding: 8px 16px;
        font-weight: 600;
    }}
    QTabBar::tab:selected {{
        color: {c['accent']};
        border-bottom-color: {c['accent']};
    }}

    /* ── Cards ── */
    QFrame#Card, QFrame#CardFrame, QGroupBox {{
        background-color: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 12px;
    }}
    QGroupBox {{
        margin-top: 14px;
        padding: 18px 14px 12px 14px;
        font-weight: 700;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 6px;
        color: {c['muted']};
    }}
    QLabel#CardTitle {{
        font-size: {card_pt}pt;
        font-weight: 700;
        color: {c['text']};
    }}
    QLabel#CardSubtitle, QLabel#Muted {{
        color: {c['muted']};
        font-size: {small_pt + 0.5}pt;
    }}
    QLabel#StatTitle {{
        color: {c['muted']};
        font-size: {small_pt}pt;
        font-weight: 700;
    }}
    QLabel#StatValue {{
        color: {c['accent']};
        font-size: {base_pt + 6}pt;
        font-weight: 800;
    }}
    QLabel#StatSub {{
        color: {c['faint']};
        font-size: {small_pt}pt;
    }}
    QLabel#SectionTitle {{
        font-size: {h1_pt}pt;
        font-weight: 800;
        color: {c['text']};
    }}
    QLabel#SectionSubtitle {{
        color: {c['muted']};
    }}

    /* ── Buttons ── */
    QPushButton, QToolButton {{
        background-color: {c['panel_alt']};
        color: {c['text']};
        border: 1px solid {c['border_strong']};
        border-radius: 8px;
        padding: 7px 14px;
        min-height: 18px;
        font-weight: 600;
    }}
    QPushButton:hover, QToolButton:hover {{
        border-color: {c['accent']};
        color: {c['text']};
    }}
    QPushButton:pressed {{
        background-color: {c['accent_soft']};
    }}
    QPushButton:disabled {{
        color: {c['faint']};
        border-color: {c['border']};
        background-color: {c['panel']};
    }}
    QPushButton#PrimaryButton {{
        background-color: {c['accent']};
        color: {c['on_accent']};
        border-color: {c['accent']};
        font-weight: 700;
    }}
    QPushButton#PrimaryButton:hover {{
        background-color: {c['accent_hover']};
        border-color: {c['accent_hover']};
    }}
    QPushButton#PrimaryButton:disabled {{
        background-color: {c['accent_soft']};
        border-color: {c['accent_soft']};
        color: {c['faint']};
    }}
    QPushButton#FlatButton {{
        background-color: transparent;
        border-color: transparent;
        color: {c['muted']};
    }}
    QPushButton#FlatButton:hover {{
        background-color: {c['panel_alt']};
        color: {c['text']};
    }}
    QPushButton#WarnButton {{
        background-color: {c['warn']};
        color: #1A1002;
        border-color: {c['warn']};
        font-weight: 700;
    }}
    QPushButton#DangerButton {{
        background-color: transparent;
        color: {c['danger']};
        border-color: {c['border_strong']};
    }}
    QPushButton#DangerButton:hover {{
        border-color: {c['danger']};
    }}

    QPushButton#IconButton {{
        padding: 4px;
        min-width: 28px;
    }}
    QCheckBox {{
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {c['border_strong']};
        border-radius: 4px;
        background-color: {c['input_bg']};
    }}
    QCheckBox::indicator:hover {{
        border-color: {c['accent']};
    }}
    QCheckBox::indicator:checked {{
        background-color: {c['accent']};
        border-color: {c['accent']};
    }}

    /* ── Inputs ── */
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {c['input_bg']};
        color: {c['text']};
        border: 1px solid {c['border_strong']};
        border-radius: 8px;
        padding: 6px 10px;
        min-height: 18px;
        selection-background-color: {c['accent_soft']};
        selection-color: {c['text']};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {c['accent']};
    }}
    /* Step buttons render as dark squares under the stylesheet; values are typed, scrolled or
       stepped with the arrow keys instead */
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
        width: 0px;
        border: none;
    }}
    QComboBox::drop-down {{
        border: none;
        width: 22px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {c['panel']};
        border: 1px solid {c['border_strong']};
        selection-background-color: {c['accent_soft']};
        selection-color: {c['text']};
        padding: 4px;
    }}

    /* ── Lists, tables, trees ── */
    QListWidget, QTableWidget, QTreeWidget, QTableView, QListView, QTreeView {{
        background-color: {c['panel']};
        alternate-background-color: {c['panel_alt']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        gridline-color: transparent;
        selection-background-color: {c['accent_soft']};
        selection-color: {c['text']};
        outline: 0;
    }}
    QFrame#Card QListWidget, QFrame#Card QTableWidget, QFrame#Card QTreeWidget,
    QFrame#CardFrame QListWidget, QFrame#CardFrame QTableWidget {{
        border: none;
    }}
    QListWidget#Inset, QFrame#Card QListWidget#Inset {{
        background-color: {c['input_bg']};
        border: 1px solid {c['border']};
        border-radius: 8px;
    }}
    QTableView::item, QListView::item, QTreeView::item {{
        padding: 6px 6px;
        border: none;
    }}
    QTableView::item:selected, QListView::item:selected, QTreeView::item:selected {{
        background-color: {c['accent_soft']};
        color: {c['text']};
    }}
    QHeaderView {{
        background-color: transparent;
        border: none;
    }}
    QHeaderView::section {{
        background-color: transparent;
        color: {c['muted']};
        border: none;
        border-bottom: 1px solid {c['border_strong']};
        padding: 8px 6px;
        font-weight: 700;
        font-size: {small_pt + 0.5}pt;
    }}
    QTableCornerButton::section {{
        background: transparent;
        border: none;
    }}

    /* ── Scrollbars, splitters, misc ── */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
        background: {c['border_strong']};
        border-radius: 4px;
        min-height: 28px;
        min-width: 28px;
    }}
    QScrollBar::handle:hover {{
        background: {c['faint']};
    }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
        background: none;
        height: 0px;
        width: 0px;
    }}
    QSplitter::handle {{
        background: transparent;
    }}
    QSplitter::handle:hover {{
        background: {c['border_strong']};
    }}
    QSlider::groove:horizontal {{
        height: 4px;
        background: {c['border_strong']};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {c['accent']};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {c['text']};
        border: 2px solid {c['accent']};
        width: 14px;
        height: 14px;
        margin: -7px 0;
        border-radius: 9px;
    }}
    QProgressBar {{
        background-color: {c['panel_alt']};
        border: none;
        border-radius: 6px;
        text-align: center;
        min-height: 12px;
    }}
    QProgressBar::chunk {{
        background-color: {c['accent']};
        border-radius: 6px;
    }}
    QMenuBar {{
        background-color: {c['sidebar']};
        color: {c['muted']};
        padding: 2px 6px;
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 4px 10px;
        border-radius: 6px;
    }}
    QMenuBar::item:selected {{
        background-color: {c['panel_alt']};
        color: {c['text']};
    }}
    QMenu {{
        background-color: {c['panel']};
        border: 1px solid {c['border_strong']};
        border-radius: 8px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 6px 18px;
        border-radius: 6px;
    }}
    QMenu::item:selected {{
        background-color: {c['accent_soft']};
    }}
    QDockWidget {{
        color: {c['muted']};
        font-weight: 700;
    }}
    QDockWidget::title {{
        background-color: {c['sidebar']};
        padding: 8px 12px;
        border-bottom: 1px solid {c['border']};
    }}
    QStatusBar {{
        background-color: {c['sidebar']};
        color: {c['muted']};
        border-top: 1px solid {c['border']};
    }}
    QStatusBar QLabel {{
        padding: 0 8px;
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
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent_soft"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["panel_alt"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
    app.setPalette(pal)
    app.setStyleSheet(build_stylesheet(theme, font_scale))
