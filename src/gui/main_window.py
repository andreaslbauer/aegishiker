"""Main application window for AegisHiker."""
from typing import Dict, Optional
from PySide6.QtCore import QByteArray, QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QColor, QFont, QIcon, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QSplitter,
    QFileDialog,
    QLabel,
    QMainWindow,
    QStatusBar,
    QTabWidget,
    QWidget,
)

from src.core import paths
from src.core.about import APP_NAME, get_version
from src.gui.app_state import AppState
from src.gui.chat_dock import ChatDock
from src.gui.dialogs.about_dialog import AboutDialog
from src.gui.dialogs.help_dialog import HelpDialog
from src.gui.dialogs.region_download_dialog import RegionDownloadDialog
from src.gui.panels.brainstorm_panel import BrainstormPanel
from src.gui.panels.composer_panel import ComposerPanel
from src.gui.panels.explore_panel import ExplorePanel
from src.gui.panels.gear_panel import GearPanel
from src.gui.panels.library_panel import LibraryPanel
from src.gui.panels.phone_sync_panel import PhoneSyncPanel
from src.gui.panels.reexperience_panel import ReexperiencePanel
from src.gui.panels.settings_panel import SettingsPanel
from src.gui.panels.telemetry_panel import TelemetryPanel
from src.gui.report import export_trailhead_pdf
from src.gui.busy import busy


# Bump when the window layout changes so stale saved dock layouts are ignored (1: sidebar layout)
WINDOW_STATE_VERSION = 1


class MainWindow(QMainWindow):
    def __init__(self, app_state: Optional[AppState] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state or AppState()

        self.setWindowTitle(f"{APP_NAME} v{get_version()} — Local AI Trail Planner & Field Companion")
        self.resize(1360, 860)

        icon_path = paths.get_assets_dir() / "appicon.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # Pages (a QTabWidget with its tab bar hidden; the sidebar navigates between them)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("Pages")
        self.tabs.tabBar().hide()
        self.explore_panel = ExplorePanel(self.app_state)
        self.brainstorm_panel = BrainstormPanel(self.app_state)
        self.composer_panel = ComposerPanel(self.app_state)
        self.reexperience_panel = ReexperiencePanel(self.app_state)
        self.phone_sync_panel = PhoneSyncPanel(self.app_state)
        self.telemetry_panel = TelemetryPanel(self.app_state)
        self.gear_panel = GearPanel(self.app_state)
        self.library_panel = LibraryPanel(self.app_state)
        self.settings_panel = SettingsPanel(self.app_state)
        for panel, name in (
            (self.explore_panel, "Explore Regions"),
            (self.brainstorm_panel, "AI Brainstorm"),
            (self.composer_panel, "Trail Composer"),
            (self.reexperience_panel, "Re-experience Hike"),
            (self.phone_sync_panel, "Mobile Companion"),
            (self.telemetry_panel, "Telemetry & Splits"),
            (self.gear_panel, "Gear & Hydration"),
            (self.library_panel, "Trail Library"),
            (self.settings_panel, "Preferences"),
        ):
            self.tabs.addTab(panel, name)

        # AI Trail Guide dock: out of the way until asked for (sidebar button / View menu)
        self.chat_dock = ChatDock(self.app_state, self)
        self.chat_dock.setObjectName("TrailGuideDock")  # needed for saveState/restoreState
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.chat_dock)
        self.chat_dock.hide()

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_sidebar())
        row.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        # Cross-panel navigation signals
        self.explore_panel.compose_trail_requested.connect(self._compose_trail)
        self.explore_panel.start_at_place_requested.connect(self._start_hike_at_place)
        self.explore_panel.open_route_requested.connect(self._open_route_in_composer)
        self.brainstorm_panel.route_selected_for_composer.connect(self._switch_to_composer)
        self.library_panel.open_route_requested.connect(self._switch_to_composer)
        self.library_panel.open_replay_requested.connect(self._switch_to_reexperience)
        self.composer_panel.hike_logged_and_mapped.connect(self._switch_to_reexperience)

        self._build_menus()
        self._build_status_bar()
        self._restore_ui_state()

    # (sidebar label, icon, page index); None = group heading
    NAV = [
        ("PLAN", None, None),
        ("Explore Regions", "🗺", 0),
        ("Trail Composer", "🥾", 2),
        ("AI Brainstorm", "✨", 1),
        ("Gear & Hydration", "🎒", 6),
        ("ON THE TRAIL", None, None),
        ("Mobile Companion", "📱", 4),
        ("REMEMBER", None, None),
        ("Re-experience Hike", "🎬", 3),
        ("Telemetry & Splits", "📈", 5),
        ("Trail Library", "📚", 7),
    ]

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(224)
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 18, 0, 14)
        col.setSpacing(2)
        title = QLabel(f"⛰  {APP_NAME}")
        title.setObjectName("AppTitle")
        sub = QLabel("Plan · Hike · Remember")
        sub.setObjectName("AppSubtitle")
        for lbl in (title, sub):
            lbl.setContentsMargins(18, 0, 12, 0)
            col.addWidget(lbl)
        col.addSpacing(10)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav_rows: Dict[int, int] = {}  # page index -> list row
        heading_font = QFont()
        heading_font.setPointSizeF(max(7.5, heading_font.pointSizeF() * 0.8))
        heading_font.setBold(True)
        for label, icon, page in self.NAV:
            if page is None:
                item = QListWidgetItem(label)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setFont(heading_font)
                item.setForeground(QColor("#5E7868"))
                item.setSizeHint(QSize(0, 34))
            else:
                item = QListWidgetItem(f"{icon}   {label}")
                item.setData(Qt.ItemDataRole.UserRole, page)
                item.setSizeHint(QSize(0, 38))
                self._nav_rows[page] = self.nav.count()
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav_row)
        self.tabs.currentChanged.connect(self._sync_nav)
        col.addWidget(self.nav, 1)

        self.guide_btn = QPushButton("💬   AI Trail Guide")
        self.guide_btn.setObjectName("NavButton")
        self.guide_btn.setCheckable(True)
        self.guide_btn.setToolTip("Show or hide the AI Trail Guide chat")
        self.guide_btn.toggled.connect(self.chat_dock.setVisible)
        self.chat_dock.visibilityChanged.connect(self.guide_btn.setChecked)
        self.settings_btn = QPushButton("⚙   Preferences")
        self.settings_btn.setObjectName("NavButton")
        self.settings_btn.setCheckable(True)
        self.settings_btn.clicked.connect(lambda: self.tabs.setCurrentWidget(self.settings_panel))
        col.addWidget(self.guide_btn)
        col.addWidget(self.settings_btn)
        self._sync_nav(self.tabs.currentIndex())
        return side

    def _on_nav_row(self, row: int) -> None:
        item = self.nav.item(row) if row >= 0 else None
        page = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if page is not None and page != self.tabs.currentIndex():
            self.tabs.setCurrentIndex(int(page))

    def _sync_nav(self, page: int) -> None:
        self.nav.blockSignals(True)
        if page in self._nav_rows:
            self.nav.setCurrentRow(self._nav_rows[page])
        else:
            self.nav.clearSelection()
            self.nav.setCurrentRow(-1)
        self.nav.blockSignals(False)
        self.settings_btn.setChecked(self.tabs.currentWidget() is self.settings_panel)

    @staticmethod
    def _run_dialog(dlg) -> None:
        dlg.exec()
        dlg.deleteLater()

    def _compose_trail(self, trail_id: str) -> None:
        """Explore → Composer: start a segment-by-segment hike on the chosen trail."""
        if self.composer_panel.start_hike_on_trail(trail_id):
            self.tabs.setCurrentWidget(self.composer_panel)

    def _start_hike_at_place(self, lat: float, lon: float, name: str) -> None:
        """Explore → Composer: start a hike at a trailhead / parking lot."""
        if self.composer_panel.start_hike_at_place(lat, lon, name):
            self.tabs.setCurrentWidget(self.composer_panel)

    def _open_route_in_composer(self, route_id: str) -> None:
        """Explore → Composer: show a saved hike of the region."""
        self.composer_panel.open_route(route_id)
        self.tabs.setCurrentWidget(self.composer_panel)

    def _switch_to_composer(self, _route_id: str) -> None:
        self.tabs.setCurrentWidget(self.composer_panel)

    def _switch_to_reexperience(self, _session_id: str) -> None:
        self.reexperience_panel.refresh_all()
        self.tabs.setCurrentWidget(self.reexperience_panel)

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        dl_act = QAction("Download Custom Offline Region...", self)
        dl_act.triggered.connect(lambda: self._run_dialog(RegionDownloadDialog(self.app_state, self)))
        file_menu.addAction(dl_act)

        i_hiked_act = QAction("✓ I Hiked This Trail...", self)
        i_hiked_act.triggered.connect(self.composer_panel.open_i_hiked_this_wizard)
        file_menu.addAction(i_hiked_act)

        pdf_act = QAction("Export Trailhead & Safety PDF...", self)
        pdf_act.triggered.connect(self._export_active_pdf)
        file_menu.addAction(pdf_act)

        file_menu.addSeparator()
        quit_act = QAction("E&xit", self)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        view_menu = menubar.addMenu("&View")
        toggle_chat_act = self.chat_dock.toggleViewAction()
        toggle_chat_act.setText("AI Trail Guide")
        view_menu.addAction(toggle_chat_act)

        help_menu = menubar.addMenu("&Help")
        guide_act = QAction("User Guide & Leave No Trace...", self)
        guide_act.triggered.connect(lambda: self._run_dialog(HelpDialog("getting_started", self)))
        help_menu.addAction(guide_act)

        about_act = QAction(f"About {APP_NAME}...", self)
        about_act.triggered.connect(lambda: self._run_dialog(AboutDialog(self)))
        help_menu.addAction(about_act)

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        self.setStatusBar(bar)
        self.status_lbl = QLabel("Ready — 7 offline regional packs loaded (including New Hampshire White Mountains)")
        bar.addWidget(self.status_lbl, 1)

        voyager_db = self.app_state.voyager_bridge.discover_db_path()
        v_text = f"Voyager DB: {voyager_db.name}" if voyager_db else "Voyager DB: Ready"
        self.voyager_pill = QLabel(v_text)
        self.voyager_pill.setStyleSheet("color: #2ECC71; padding-right: 10px;")
        bar.addPermanentWidget(self.voyager_pill)

        self.app_state.status_message.connect(self.status_lbl.setText)

    def _export_active_pdf(self) -> None:
        route = self.app_state.get_active_route()
        if not route:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Trailhead & Leave-Behind Safety PDF",
            f"{route.name.lower().replace(' ', '_')}_trailhead.pdf",
            "PDF Documents (*.pdf)",
        )
        if not path:
            return
        with busy(self, "Exporting trailhead safety PDF…", title="Exporting PDF"):
            dest = self.app_state.get_active_destination()
            gear = self.app_state.db.list_gear(route.id)
            photos = self.app_state.get_active_route_photos()
            export_trailhead_pdf(
                output_path=path,
                route=route,
                destination=dest,
                gear_items=gear,
                photos=photos,
                emergency_contact=str(self.app_state.settings.get("emergency_contact", "")),
                unit_system=str(self.app_state.settings.get("unit_system", "metric")),
            )
        self.status_lbl.setText(f"Exported PDF to {path}")

    # ── Window / UI state persisted between launches ──────────────────────────

    def _splitters(self):
        """Every splitter keyed by its panel and position (stable across launches)."""
        for panel in (self.explore_panel, self.composer_panel, self.reexperience_panel, self.telemetry_panel):
            for idx, splitter in enumerate(panel.findChildren(QSplitter)):
                yield f"{type(panel).__name__}.{idx}", splitter

    def _panels_with_state(self):
        return {"explore": self.explore_panel, "composer": self.composer_panel}

    def _restore_ui_state(self) -> None:
        s = self.app_state.settings
        geometry = str(s.get("window_geometry", "") or "")
        if geometry:
            # Qt moves the window back onto a visible screen if the monitor setup changed
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        state = str(s.get("window_state", "") or "")
        if state:
            self.restoreState(QByteArray.fromBase64(state.encode("ascii")), WINDOW_STATE_VERSION)
        tab = int(s.get("active_tab", 0) or 0)
        if 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)
        ui = s.get("ui_state") or {}
        self._pending_splitters = dict(ui.get("splitters") or {})
        self._arm_splitter_restore()
        for key, panel in self._panels_with_state().items():
            if isinstance(ui.get(key), dict):
                panel.restore_ui_state(ui[key])

    def _arm_splitter_restore(self) -> None:
        """Apply saved splitter positions when each splitter is first shown with its real size.
        Splitters in hidden tabs keep a stale width until shown, so restoring earlier squeezes them."""
        self._splitter_keys = {}
        for key, splitter in self._splitters():
            if key in self._pending_splitters:
                self._splitter_keys[splitter] = key
                splitter.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.Show and obj in getattr(self, "_splitter_keys", {}):
            key = self._splitter_keys.pop(obj)
            obj.removeEventFilter(self)
            QTimer.singleShot(0, lambda sp=obj, k=key: self._apply_splitter(sp, k))
        return super().eventFilter(obj, event)

    def _apply_splitter(self, splitter: QSplitter, key: str) -> None:
        saved = self._pending_splitters.pop(key, None)
        if not isinstance(saved, list) or len(saved) != splitter.count() or sum(saved) <= 0:
            return
        total = sum(splitter.sizes()) or sum(saved)
        splitter.setSizes([round(total * v / sum(saved)) for v in saved])  # same proportions

    def _save_ui_state(self) -> None:
        s = self.app_state.settings
        s.set("window_geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))
        s.set("window_state", bytes(self.saveState(WINDOW_STATE_VERSION).toBase64()).decode("ascii"))
        s.set("active_tab", self.tabs.currentIndex())
        ui = {"splitters": {key: sp.sizes() for key, sp in self._splitters()}}
        # Keep positions of splitters never shown this session (still pending) as they were
        for key, sizes in getattr(self, "_pending_splitters", {}).items():
            ui["splitters"][key] = sizes
        for key, panel in self._panels_with_state().items():
            ui[key] = panel.ui_state()
        s.set("ui_state", ui)
        s.save()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_ui_state()
        self.app_state.close()
        super().closeEvent(event)

