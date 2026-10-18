"""'Build by Trail Segments' sidebar for the Trail Composer.

Pick a starting trail, then keep choosing the next trail from those that branch off the current
segment (or meet it at its end), turn around for an out-and-back, and finish when back at the start
or with 'End Here'. The growing hike is shown live on the Composer's map, stats and elevation chart.
"""
from typing import List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.hiking.models import ComposedRoute, DestinationPack
from src.hiking.segment_chain import Candidate, SegmentChain
from src.hiking.units import format_distance, format_elevation

PLACE_KINDS = {"peak", "hut", "shelter", "trailhead", "parking", "waterfall", "viewpoint", "campsite", "saddle", "spring"}


class SegmentBuilder(QWidget):
    draft_changed = Signal(object)  # Optional[ComposedRoute] — the hike built so far
    preview_changed = Signal(object)  # Optional[List[(lat, lon)]] — highlighted candidate
    candidates_changed = Signal(object)  # List[List[(lat, lon)]] — pickable segments for the map
    route_saved = Signal(str)  # route_id

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.chain: Optional[SegmentChain] = None
        self.pack_id: Optional[str] = None
        self._candidates: List[Candidate] = []
        self._name_edited = False
        self.setMinimumWidth(340)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 6, 0)
        title = QLabel("<b>Build Hike by Trail Segments</b>")
        col.addWidget(title)

        self.pick_chk = QCheckBox("Pick trails on the map")
        self.pick_chk.setChecked(True)
        self.pick_chk.setToolTip(
            "Click a trail on the map to start there, then click the dashed yellow options to add "
            "segments. Turn off to trace a route by clicking waypoints instead."
        )
        col.addWidget(self.pick_chk)

        col.addWidget(QLabel("1. Starting trail (or click one on the map):"))
        self.start_combo = QComboBox()
        self.start_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.start_combo.setMinimumContentsLength(24)
        self.start_combo.view().setMinimumWidth(460)  # long trail names readable in the popup
        col.addWidget(self.start_combo)
        start_row = QHBoxLayout()
        self.reverse_chk = QCheckBox("Start from the far end")
        self.reverse_chk.setToolTip("Walk the starting trail in the opposite direction")
        start_btn = QPushButton("Start Hike Here")
        start_btn.setObjectName("PrimaryButton")
        start_btn.clicked.connect(self.start_chain)
        start_row.addWidget(self.reverse_chk)
        start_row.addStretch(1)
        start_row.addWidget(start_btn)
        col.addLayout(start_row)

        col.addWidget(QLabel("Hike so far:"))
        self.legs_list = QListWidget()
        self.legs_list.setMaximumHeight(130)
        self.legs_list.setWordWrap(True)
        col.addWidget(self.legs_list)

        self.next_lbl = QLabel("2. Next segment (double-click, or click it on the map):")
        col.addWidget(self.next_lbl)
        self.cand_list = QListWidget()
        self.cand_list.setWordWrap(True)
        self.cand_list.setAlternatingRowColors(True)
        self.cand_list.currentRowChanged.connect(self._on_candidate_highlighted)
        self.cand_list.itemDoubleClicked.connect(lambda _item: self.add_selected())
        col.addWidget(self.cand_list, 1)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("Add Segment")
        self.add_btn.clicked.connect(self.add_selected)
        self.end_btn = QPushButton("End Here")
        self.end_btn.setToolTip("Finish the hike at the end of the current segment")
        self.end_btn.clicked.connect(self.end_here)
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.clicked.connect(self.undo)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        for b in (self.add_btn, self.end_btn, self.undo_btn, self.clear_btn):
            btn_row.addWidget(b)
        col.addLayout(btn_row)

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        col.addWidget(self.status_lbl)
        self.summary_lbl = QLabel("")
        self.summary_lbl.setWordWrap(True)
        self.summary_lbl.setStyleSheet("color: #2ECC71;")
        col.addWidget(self.summary_lbl)

        col.addWidget(QLabel("3. Name:"))
        name_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Proposed from the segment names")
        self.name_edit.textEdited.connect(self._on_name_edited)
        propose_btn = QPushButton("↺")
        propose_btn.setToolTip("Use the proposed name built from the segment names")
        propose_btn.setFixedWidth(32)
        propose_btn.clicked.connect(self._reset_name)
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(propose_btn)
        col.addLayout(name_row)
        self.save_btn = QPushButton("Save Hike")
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.clicked.connect(self.save)
        col.addWidget(self.save_btn)

        self._sync_ui()

    # ── region ───────────────────────────────────────────────────────────

    def set_pack(self, pack: Optional[DestinationPack]) -> None:
        """Load the region's trails (keeps the current hike if the region is unchanged)."""
        pack_id = pack.id if pack else None
        if pack_id == self.pack_id and self.chain is not None:
            return
        self.pack_id = pack_id
        self.start_combo.clear()
        self.chain = None
        if pack:
            places = [(f.name, f.coords[0][0], f.coords[0][1])
                      for f in pack.features if f.geom == "point" and f.name and f.kind in PLACE_KINDS]
            places += [(p.name, p.lon, p.lat) for p in pack.pois]
            trails = sorted(pack.trails, key=lambda t: t.name.lower())
            self.chain = SegmentChain(trails, places)
            unit = self._unit()
            for t in trails:
                self.start_combo.addItem(f"{t.name}  ({format_distance(t.distance_m, unit)})", t.id)
        self._refresh()

    def _unit(self) -> str:
        return str(self.app_state.settings.get("unit_system", "metric"))

    # ── actions ──────────────────────────────────────────────────────────

    def start_chain(self) -> None:
        if not self.chain:
            return
        trail_id = self.start_combo.currentData()
        trail = next((t for t in self.chain.trails if t.id == trail_id), None)
        if trail is None:
            return
        self.chain.start(trail, reverse=self.reverse_chk.isChecked())
        self._name_edited = False
        self._refresh()

    def add_selected(self) -> None:
        row = self.cand_list.currentRow()
        if self.chain and 0 <= row < len(self._candidates):
            self.chain.apply(self._candidates[row])
            self._refresh()

    def end_here(self) -> None:
        if self.chain and self.chain.legs and not self.chain.complete:
            self.chain.end_here()
            self._refresh()

    def undo(self) -> None:
        if self.chain:
            self.chain.undo()
            self._refresh()

    def clear(self) -> None:
        if self.chain and self.chain.legs:
            self.chain.clear()
            self._name_edited = False
            self._refresh()

    def has_draft(self) -> bool:
        return bool(self.chain and self.chain.legs)

    def save(self) -> None:
        if not self.has_draft() or not self.pack_id:
            return
        assert self.chain is not None
        route = self.chain.to_route(self.name_edit.text().strip(), self.pack_id)
        self.app_state.db.upsert_route(route)
        self.chain.clear()
        self._name_edited = False
        self._refresh()
        self.app_state.status_message.emit(f"Saved hike '{route.name}'.")
        self.route_saved.emit(route.id)

    # ── map picking ──────────────────────────────────────────────────────

    def pick_at(self, lat: float, lon: float, tol_m: float) -> bool:
        """Handle a map click. Returns True when it picked something (or should not trace)."""
        if not self.pick_chk.isChecked() or not self.chain:
            return False
        if self.has_draft():
            if self.chain.complete:
                self.app_state.status_message.emit("The hike is complete: save it, or Undo to keep building.")
                return True
            idx = self.chain.candidate_index_at(self._candidates, lat, lon, tol_m)
            if idx is None:
                self.app_state.status_message.emit("Click one of the dashed yellow trail options to add it.")
                return True
            self.cand_list.setCurrentRow(idx)
            self.add_selected()
            return True
        hit = self.chain.trail_at(lat, lon, tol_m)
        if hit is None:
            self.app_state.status_message.emit("Click on a trail to start the hike there.")
            return True
        trail, reverse = hit
        self.start_combo.setCurrentIndex(self.start_combo.findData(trail.id))
        self.reverse_chk.setChecked(reverse)
        self.start_chain()
        return True

    def hover_at(self, lat: float, lon: float, tol_m: float) -> None:
        """Preview the trail or option under the cursor."""
        if not self.pick_chk.isChecked() or not self.chain:
            return
        if self.has_draft():
            if self.chain.complete:
                return
            idx = self.chain.candidate_index_at(self._candidates, lat, lon, tol_m)
            if idx is not None and idx != self.cand_list.currentRow():
                self.cand_list.setCurrentRow(idx)
            return
        hit = self.chain.trail_at(lat, lon, tol_m)
        self.preview_changed.emit([(p[1], p[0]) for p in hit[0].coords_3d] if hit else None)

    def _on_name_edited(self, _text: str) -> None:
        self._name_edited = True

    def _reset_name(self) -> None:
        self._name_edited = False
        self.name_edit.setText(self.chain.proposed_name() if self.chain else "")

    def _on_candidate_highlighted(self, row: int) -> None:
        if 0 <= row < len(self._candidates):
            leg = self._candidates[row].leg
            self.preview_changed.emit([(p[1], p[0]) for p in leg.coords()])
        else:
            self.preview_changed.emit(None)

    # ── view ─────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        chain = self.chain
        unit = self._unit()
        self.legs_list.clear()
        self.cand_list.clear()
        self._candidates = []
        draft: Optional[ComposedRoute] = None

        if chain and chain.legs:
            for i, leg in enumerate(chain.legs, 1):
                st = leg.stats()
                QListWidgetItem(
                    f"{i}. {leg.trail.name} — {format_distance(st['distance_m'], unit)}, "
                    f"+{format_elevation(st['elevation_gain_m'], unit)} / -{format_elevation(st['elevation_loss_m'], unit)}",
                    self.legs_list,
                )
            self.legs_list.scrollToBottom()
            self._candidates = chain.candidates()
            for c in self._candidates:
                self.cand_list.addItem(self._candidate_text(c, unit))
            draft = chain.to_route(self.name_edit.text().strip(), self.pack_id or "")
            if not self._name_edited:
                self.name_edit.setText(chain.proposed_name())
                draft.name = self.name_edit.text()
            shape = {"loop": "Loop", "out_and_back": "Out & back", "segment": "One way"}[chain.shape()]
            self.summary_lbl.setText(
                f"<b>{len(chain.legs)} segment{'s' if len(chain.legs) != 1 else ''} · {shape}</b><br>"
                f"{format_distance(draft.distance_m, unit)} · +{format_elevation(draft.elevation_gain_m, unit)} / "
                f"-{format_elevation(draft.elevation_loss_m, unit)} · high point {format_elevation(draft.max_ele_m, unit)}"
                + (f"<br><span style='color:#F39C12'>⚠ {'; '.join(draft.condition_warnings)}</span>" if draft.condition_warnings else "")
            )
            if chain.is_closed():
                self.status_lbl.setText("✓ Back at the starting point — the hike is complete. Name it and save, or Undo to change it.")
            elif chain.ended:
                self.status_lbl.setText("✓ Hike ends here. Name it and save, or Undo to keep going.")
            elif not self._candidates:
                self.status_lbl.setText("No trails continue from here — End Here or Undo.")
            else:
                self.status_lbl.setText("Pick where to go next. Trails are listed from the end of the current segment backwards.")
        else:
            self.summary_lbl.setText("")
            self.status_lbl.setText("Choose a starting trail and click 'Start Hike Here'." if chain else "")
            self.name_edit.clear()

        self._fit_rows(self.legs_list)
        self._fit_rows(self.cand_list)
        self._sync_ui()
        self.preview_changed.emit(None)
        self.candidates_changed.emit(
            [] if not chain or chain.complete else [[(p[1], p[0]) for p in c.leg.coords()] for c in self._candidates]
        )
        self.draft_changed.emit(draft)

    @staticmethod
    def _fit_rows(lst: QListWidget) -> None:
        """Size each row to its wrapped text so nothing is cut off with '…'."""
        fm = lst.fontMetrics()
        width = max(60, lst.viewport().width() - 14)
        for i in range(lst.count()):
            item = lst.item(i)
            rect = fm.boundingRect(0, 0, width, 10_000, int(Qt.TextFlag.TextWordWrap), item.text())
            item.setSizeHint(QSize(width, rect.height() + 10))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_rows(self.legs_list)
        self._fit_rows(self.cand_list)

    def _candidate_text(self, c: Candidate, unit: str) -> str:
        at = c.at_place or f"{format_distance(c.along_m, unit)} along"
        toward = f" toward {c.toward_place}" if c.toward_place else ""
        stats = (f"{format_distance(c.distance_m, unit)} · +{format_elevation(c.gain_m, unit)}"
                 f" / -{format_elevation(c.loss_m, unit)}")
        if c.turnaround:
            return f"↩ Turn around: back on {c.trail_name}{toward}\n    {stats}"
        return f"→ {c.trail_name}{toward}\n    at {at} · {stats}"

    def _sync_ui(self) -> None:
        has = self.has_draft()
        complete = bool(self.chain and self.chain.complete)
        self.add_btn.setEnabled(has and not complete and bool(self._candidates))
        self.end_btn.setEnabled(has and not complete)
        self.undo_btn.setEnabled(has)
        self.clear_btn.setEnabled(has)
        self.save_btn.setEnabled(has)
        self.next_lbl.setEnabled(has and not complete)
        self.cand_list.setEnabled(has and not complete)
