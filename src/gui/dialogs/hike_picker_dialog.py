"""Choose a recorded hike: grouped by year, with name, date, trails, people and photos.
Hikes can be renamed and deleted from here."""
from typing import Dict, List, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.busy import busy
from src.gui.charts.photo_preview import photo_pixmap
from src.hiking.hike_info import hike_date_label, hike_people, hike_start, hike_title, hike_trail_names, people_label
from src.hiking.models import HikePhotoPin, HikeSession, TrailSegment
from src.hiking.photo_matcher import dedupe_pins
from src.hiking.units import format_distance, format_elevation

THUMB_W, THUMB_H = 84, 62
MAX_THUMBS = 6
ROLE_SESSION = Qt.ItemDataRole.UserRole
ROLE_YEAR = Qt.ItemDataRole.UserRole + 1


def pick_photos(pins: List[HikePhotoPin], count: int = MAX_THUMBS) -> List[HikePhotoPin]:
    """A representative handful: starred photos first, then spread evenly over the hike."""
    starred = [p for p in pins if p.is_starred]
    rest = [p for p in pins if not p.is_starred]
    chosen = starred[:count]
    need = count - len(chosen)
    if need > 0 and rest:
        step = len(rest) / need
        chosen += [rest[int(i * step)] for i in range(min(need, len(rest)))]
    return sorted(chosen, key=lambda p: (p.date_taken or 0, p.distance_along_m))


class HikeCard(QFrame):
    def __init__(self, title: str, date_text: str, stats: str, trails: List[str], people: List[str],
                 photos: List[HikePhotoPin], photo_total: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("HikeCard { background: transparent; } QLabel { background: transparent; }")
        col = QVBoxLayout(self)
        col.setContentsMargins(8, 6, 8, 6)
        col.setSpacing(2)
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("font-size: 11pt; font-weight: 700;")
        col.addWidget(self.title_lbl)
        meta = QLabel(f"{date_text}{'  ·  ' + stats if stats else ''}")
        meta.setStyleSheet("color: #98B2A1;")
        col.addWidget(meta)
        if trails:
            tl = QLabel("Trails: " + " → ".join(trails))
            tl.setWordWrap(True)
            col.addWidget(tl)
        if people:
            pl = QLabel("👥 With: " + people_label(people, 6))
            pl.setStyleSheet("color: #2ECC71;")
            col.addWidget(pl)
        if photos:
            strip = QHBoxLayout()
            strip.setSpacing(4)
            for pin in photos:
                lbl = QLabel()
                lbl.setFixedSize(THUMB_W, THUMB_H)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet("border: 1px solid #2C4637; border-radius: 3px;")
                pix = photo_pixmap(pin, 160)
                if pix is not None:
                    lbl.setPixmap(pix.scaled(THUMB_W, THUMB_H, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                             Qt.TransformationMode.SmoothTransformation).copy(0, 0, THUMB_W, THUMB_H))
                else:
                    lbl.setText("📷")
                lbl.setToolTip(pin.caption or pin.filename)
                strip.addWidget(lbl)
            if photo_total > len(photos):
                more = QLabel(f"+{photo_total - len(photos)} more")
                more.setStyleSheet("color: #98B2A1;")
                strip.addWidget(more)
            strip.addStretch(1)
            col.addLayout(strip)
        elif photo_total == 0:
            none = QLabel("No photos matched")
            none.setStyleSheet("color: #5D6D7E; font-style: italic;")
            col.addWidget(none)


class HikePickerDialog(QDialog):
    """Returns the chosen hike in `selected_session_id` (accepted) — rename/delete happen in place."""

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.selected_session_id: Optional[str] = None
        self._search_text: Dict[str, str] = {}
        self.setWindowTitle("Choose a Hike")
        self.resize(820, 680)

        col = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search hikes, trails, people or year…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.count_lbl = QLabel()
        self.count_lbl.setStyleSheet("color: #98B2A1;")
        top.addWidget(self.search, 1)
        top.addWidget(self.count_lbl)
        col.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(False)
        # Subtle selection: the app's bright green highlight would hide the cards' green/grey text
        self.tree.setStyleSheet(
            "QTreeWidget::item:selected, QTreeWidget::item:selected:active {"
            " background: #1E4532; border: 1px solid #2ECC71; border-radius: 6px; }"
            "QTreeWidget::item:hover:!selected { background: #16271D; }"
            "QTreeWidget::branch:selected { background: transparent; border: none; }"
        )
        self.tree.itemDoubleClicked.connect(lambda item, _c: self._open(item))
        self.tree.currentItemChanged.connect(lambda *_: self._sync_buttons())
        col.addWidget(self.tree, 1)

        btns = QHBoxLayout()
        self.rename_btn = QPushButton("Rename…")
        self.rename_btn.clicked.connect(self.rename_selected)
        self.delete_btn = QPushButton("Delete…")
        self.delete_btn.clicked.connect(self.delete_selected)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.open_btn = QPushButton("Open Hike")
        self.open_btn.setObjectName("PrimaryButton")
        self.open_btn.setDefault(True)
        self.open_btn.clicked.connect(lambda: self._open(self.tree.currentItem()))
        for b in (self.rename_btn, self.delete_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(self.open_btn)
        col.addLayout(btns)

        self.populate()

    # ── building ─────────────────────────────────────────────────────────

    def populate(self) -> None:
        db = self.app_state.db
        unit = str(self.app_state.settings.get("unit_system", "metric"))
        self.tree.clear()
        self._search_text.clear()
        trails_cache: Dict[str, Dict[str, TrailSegment]] = {}
        years: Dict[str, QTreeWidgetItem] = {}
        current: Optional[QTreeWidgetItem] = None
        with busy(self, "Loading hikes…", show_dialog=False):
            sessions = sorted(db.list_sessions(), key=lambda s: s.started_at or "", reverse=True)
            for session in sessions:
                route = db.get_route(session.route_id) if session.route_id else None
                pack_id = session.pack_id or (route.pack_id if route else "")
                if pack_id not in trails_cache:
                    trails_cache[pack_id] = {t.id: t for t in db.list_trails(pack_id)} if pack_id else {}
                pins = dedupe_pins(db.get_photo_pins(session.id))
                pins.sort(key=lambda p: (p.date_taken or 0, p.distance_along_m))
                title = hike_title(session, route)
                trails = hike_trail_names(route, trails_cache[pack_id])
                people = hike_people(pins)
                start = hike_start(session)
                year = str(start.year) if start else "Undated"
                dist = session.stats.actual_distance_m or (route.distance_m if route else 0.0)
                gain = session.stats.actual_gain_m or (route.elevation_gain_m if route else 0.0)
                stats = f"{format_distance(dist, unit)} · +{format_elevation(gain, unit)}" if dist else ""
                if pins:
                    stats += f"{' · ' if stats else ''}{len(pins)} photo{'s' if len(pins) != 1 else ''}"

                if year not in years:
                    years[year] = QTreeWidgetItem(self.tree)
                    years[year].setFlags(Qt.ItemFlag.ItemIsEnabled)
                    years[year].setData(0, ROLE_YEAR, year)
                    years[year].setExpanded(True)
                item = QTreeWidgetItem(years[year])
                item.setData(0, ROLE_SESSION, session.id)
                card = HikeCard(title, hike_date_label(session), stats, trails, people, pick_photos(pins), len(pins))
                self.tree.setItemWidget(item, 0, card)
                item.setSizeHint(0, QSize(0, card.sizeHint().height() + 4))
                self._search_text[session.id] = " ".join([title, year, hike_date_label(session), *trails, *people]).lower()
                if session.id == self.app_state.active_session_id:
                    current = item
        for item in years.values():
            self._update_year_label(item)
        if current is not None:
            self.tree.setCurrentItem(current)
            self.tree.scrollToItem(current)
        self._apply_filter(self.search.text())

    @staticmethod
    def _update_year_label(year_item: QTreeWidgetItem) -> None:
        n = sum(1 for i in range(year_item.childCount()) if not year_item.child(i).isHidden())
        year_item.setText(0, f"{year_item.data(0, ROLE_YEAR)}  ·  {n} hike{'s' if n != 1 else ''}")
        font = year_item.font(0)
        font.setBold(True)
        year_item.setFont(0, font)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        shown = 0
        for i in range(self.tree.topLevelItemCount()):
            year_item = self.tree.topLevelItem(i)
            visible = 0
            for j in range(year_item.childCount()):
                child = year_item.child(j)
                match = not needle or needle in self._search_text.get(child.data(0, ROLE_SESSION), "")
                child.setHidden(not match)
                visible += match
            year_item.setHidden(visible == 0)
            shown += visible
            self._update_year_label(year_item)
        total = len(self._search_text)
        self.count_lbl.setText(f"{shown} of {total} hikes" if needle else f"{total} hike{'s' if total != 1 else ''}")
        self._sync_buttons()

    # ── actions ──────────────────────────────────────────────────────────

    def _current_session(self) -> Optional[HikeSession]:
        item = self.tree.currentItem()
        sid = item.data(0, ROLE_SESSION) if item is not None else None
        return self.app_state.db.get_session(sid) if sid else None

    def _sync_buttons(self) -> None:
        has = self._current_session() is not None
        for b in (self.open_btn, self.rename_btn, self.delete_btn):
            b.setEnabled(has)

    def _open(self, item: Optional[QTreeWidgetItem]) -> None:
        sid = item.data(0, ROLE_SESSION) if item is not None else None
        if sid:
            self.selected_session_id = sid
            self.accept()

    def rename_selected(self) -> None:
        session = self._current_session()
        if session is None:
            return
        route = self.app_state.db.get_route(session.route_id) if session.route_id else None
        title, ok = QInputDialog.getText(self, "Rename Hike", "Hike name:", text=hike_title(session, route))
        if ok and title.strip():
            self.app_state.rename_session(session.id, title)
            self.populate()

    def delete_selected(self) -> None:
        session = self._current_session()
        if session is None:
            return
        route = self.app_state.db.get_route(session.route_id) if session.route_id else None
        n_photos = len(self.app_state.db.get_photo_pins(session.id))
        answer = QMessageBox.question(
            self, "Delete Hike",
            f"Delete “{hike_title(session, route)}” ({hike_date_label(session)})?\n\n"
            f"This removes the hike, its replay map and its links to {n_photos} photo(s). "
            "The photos themselves in AegisPhotoVoyager are not touched, and the planned route stays in the Library.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.app_state.delete_session(session.id)
            self.populate()
