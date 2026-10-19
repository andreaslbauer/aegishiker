"""'Build by Trail Segments' sidebar for the Trail Composer.

Pick a starting trail, then keep choosing the next trail from those that branch off the current
segment (or meet it at its end), turn around for an out-and-back, and finish when back at the start
or with 'End Here'. The growing hike is shown live on the Composer's map, stats and elevation chart.
"""
import math
from typing import List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QCursor, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.busy import busy
from src.hiking.models import ComposedRoute, DestinationPack
from src.hiking.segment_chain import Candidate, SegmentChain, map_paths_from_features
from src.hiking.units import format_distance, format_elevation

PLACE_KINDS = {"peak", "hut", "shelter", "trailhead", "parking", "waterfall", "viewpoint", "campsite", "saddle", "spring"}


class SegmentBuilder(QWidget):
    draft_changed = Signal(object)  # Optional[ComposedRoute] — the hike built so far
    preview_changed = Signal(object)  # Optional[List[(lat, lon)]] — highlighted candidate
    candidates_changed = Signal(object)  # List[List[(lat, lon)]] — pickable segments for the map
    route_saved = Signal(str)  # route_id
    focus_requested = Signal(object)  # List[(lat, lon)] — the user clicked a trail: zoom the map to it

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.chain: Optional[SegmentChain] = None
        self.pack_id: Optional[str] = None
        self._candidates: List[Candidate] = []
        self._picked: List[Candidate] = []  # options added by clicking trails on the map
        self._name_edited = False
        self.setMinimumWidth(320)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)

        self.pick_chk = QCheckBox("Pick trails on the map")
        self.pick_chk.setChecked(True)
        self.pick_chk.setToolTip(
            "Click a trail on the map to start there, then click the dashed yellow options to add "
            "segments. Turn off to trace a route by clicking waypoints instead."
        )
        col.addWidget(self.pick_chk)

        col.addWidget(self._step("1 · Starting trail — or click one on the map"))
        self.start_combo = QComboBox()
        self.start_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.start_combo.setMinimumContentsLength(24)
        self.start_combo.view().setMinimumWidth(460)  # long trail names readable in the popup
        self.start_combo.activated.connect(self._on_start_trail_chosen)
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

        col.addWidget(self._step("Hike so far"))
        self.legs_list = QListWidget()
        self.legs_list.setObjectName("Inset")
        self.legs_list.setMaximumHeight(130)
        self.legs_list.setWordWrap(True)
        self.legs_list.itemClicked.connect(self._on_leg_clicked)
        col.addWidget(self.legs_list)

        self.next_lbl = self._step("2 · Next segment — double-click, or click any trail on the map")
        col.addWidget(self.next_lbl)
        self.cand_list = QListWidget()
        self.cand_list.setObjectName("Inset")
        self.cand_list.setWordWrap(True)
        self.cand_list.currentRowChanged.connect(self._on_candidate_highlighted)
        self.cand_list.itemClicked.connect(self._on_candidate_clicked)
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

        col.addWidget(self._step("3 · Name"))
        name_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Proposed from the segment names")
        self.name_edit.textEdited.connect(self._on_name_edited)
        propose_btn = QPushButton("↺")
        propose_btn.setToolTip("Use the proposed name built from the segment names")
        propose_btn.setObjectName("IconButton")
        propose_btn.clicked.connect(self._reset_name)
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(propose_btn)
        col.addLayout(name_row)
        self.save_btn = QPushButton("Save Hike")
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.clicked.connect(self.save)
        col.addWidget(self.save_btn)

        self._sync_ui()

    @staticmethod
    def _step(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("StatTitle")
        lbl.setWordWrap(True)
        return lbl

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
            # The map's other footpaths and tracks: pickable on the map and used to fill gaps
            map_paths = map_paths_from_features(pack.features, pack.dem, pack.id)
            self.chain = SegmentChain(trails, places, map_paths=map_paths)
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

    def start_on_trail(self, trail_id: str) -> bool:
        """Preselect a starting trail (e.g. from Explore Regions) and start the hike on it.
        Asks before replacing an unsaved multi-segment hike. Returns False if cancelled/unknown."""
        idx = self.start_combo.findData(trail_id)
        if idx < 0:
            return False
        if self.chain and len(self.chain.legs) > 1:
            answer = QMessageBox.question(
                self, "Replace Hike?",
                "Start a new hike on this trail? The unsaved hike you are building will be discarded.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self.start_combo.setCurrentIndex(idx)
        self.reverse_chk.setChecked(False)
        self.start_chain()
        return True

    TRAILHEAD_REACH_M = 300.0  # trails this close to a chosen trailhead can start the hike

    def start_at_place(self, lat: float, lon: float, name: str = "") -> bool:
        """Start the hike at a trailhead / parking lot / place: on the nearest trail (asking when
        several start there), from the point nearest the place."""
        if not self.chain:
            return False
        hits = self.chain.trails_at(lat, lon, self.TRAILHEAD_REACH_M)
        if not hits:
            self.app_state.status_message.emit(
                f"No trail within {format_distance(self.TRAILHEAD_REACH_M, self._unit())} of {name or 'that place'}."
            )
            return False
        # Several different trails right at the place: let the user choose
        nearest = hits[0][2]
        close = []
        for t, _rev, d in hits:
            if d <= nearest + 80.0 and all(c.name != t.name for c in close):
                close.append(t)
        trail = close[0]
        if len(close) > 1:
            pick = self._choose([(f"Start on {t.name}", n) for n, t in enumerate(close)])
            if pick is None:
                return False
            trail = close[pick]
        if self.chain.legs and len(self.chain.legs) > 1:
            answer = QMessageBox.question(
                self, "Replace Hike?",
                "Start a new hike here? The unsaved hike you are building will be discarded.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        vertex, reverse = self.chain.start_near(lat, lon, trail)
        idx = self.start_combo.findData(trail.id)
        if idx >= 0:
            self.start_combo.setCurrentIndex(idx)
        self.reverse_chk.setChecked(reverse)
        self.chain.start(trail, reverse=reverse, from_vertex=vertex)
        self._name_edited = False
        self._refresh()
        self._focus([(p[1], p[0]) for p in self.chain.legs[0].coords()] + [(lat, lon)])
        where = f" at {name}" if name else ""
        self.app_state.status_message.emit(
            f"Hike started{where} on {trail.name}. Pick the next segment where you turned, or End Here."
        )
        return True

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
        """Handle a map click. Returns True when the click belongs to the builder (no tracing)."""
        if not self.pick_chk.isChecked() or not self.chain:
            return False
        if self.has_draft():
            if self.chain.complete:
                self.app_state.status_message.emit("The hike is complete: save it, or Undo to keep building.")
                return True
            hits = self.chain.candidates_at(self._candidates, lat, lon, tol_m)
            if not hits:
                self._offer_clicked_trail(lat, lon, tol_m)
                return True
            idx = hits[0] if len(hits) == 1 else self._choose(
                [(self._candidate_text(self._candidates[h], self._unit()).split("\n")[0], h) for h in hits]
            )
            if idx is not None:
                # Clicked on the map: the hike turns off at the junction and goes to the clicked spot
                cand = self._candidates[idx]
                if not cand.turnaround:
                    cand = self.chain.shorten_to(cand, lat, lon)
                self.chain.apply(cand)
                self._refresh()
            return True
        trails = self.chain.trails_at(lat, lon, tol_m)
        if not trails:
            self.app_state.status_message.emit("Click on a trail to start the hike there.")
            return True
        pick = 0 if len(trails) == 1 else self._choose(
            [(f"Start on {t.name}", n) for n, (t, _rev, _d) in enumerate(trails)]
        )
        if pick is not None:
            trail, reverse, _d = trails[pick]
            idx = self.start_combo.findData(trail.id)
            if idx >= 0:
                self.start_combo.setCurrentIndex(idx)
                self.reverse_chk.setChecked(reverse)
                self.start_chain()
            else:  # a map path: not in the list of region trails
                self.chain.start(trail, reverse=reverse)
                self._name_edited = False
                self._refresh()
        return True

    def _offer_clicked_trail(self, lat: float, lon: float, tol_m: float) -> None:
        """A trail clicked on the map that isn't an option yet becomes one: routed over the
        connecting trails, or (close but not mapped as connected) a short jump onto it. It is added
        to the list, selected and previewed; the user confirms with Add Segment / double-click."""
        assert self.chain is not None
        trails = self.chain.trails_at(lat, lon, tol_m)
        if not trails:
            self.app_state.status_message.emit("Click a trail, or one of the dashed yellow options, to add it.")
            return
        pick = 0 if len(trails) == 1 else self._choose(
            [(f"Go to {t.name}", n) for n, (t, _rev, _d) in enumerate(trails)]
        )
        if pick is None:
            return
        trail = trails[pick][0]
        with busy(self, "Finding a way to that trail…", show_dialog=False):
            cand = self.chain.candidate_to(trail, lat, lon)
        if cand is None:
            self.app_state.status_message.emit(
                f"{trail.name} can't be reached from here on the mapped trails, and is too far to jump to."
            )
            return
        self._picked = [c for c in self._picked if c.trail_name != cand.trail_name] + [cand]
        self._show_candidates(select=cand)
        how = (f"{format_distance(cand.gap_m, self._unit())} away, not connected on the map" if cand.gap_m
               else (f"via {' → '.join(cand.via_names)}" if cand.via_names else "from here"))
        self.app_state.status_message.emit(f"Added {trail.name} ({how}) to the options — Add Segment to use it.")

    def _choose(self, options: List[tuple]) -> Optional[int]:
        """Several trails share the clicked spot: let the user say which one is meant."""
        menu = QMenu(self)
        menu.addSection("Several trails here:")
        actions = {menu.addAction(label): value for label, value in options}
        chosen = menu.exec(QCursor.pos())
        return actions.get(chosen) if chosen else None

    def hover_at(self, lat: float, lon: float, tol_m: float) -> None:
        """Preview the trail or option under the cursor and say what a click would do."""
        if not self.pick_chk.isChecked() or not self.chain:
            return
        if self.has_draft():
            if self.chain.complete:
                return
            hits = self.chain.candidates_at(self._candidates, lat, lon, tol_m)
            if hits and hits[0] != self.cand_list.currentRow():
                self.cand_list.setCurrentRow(hits[0])
            msg = (f"Click to walk {self._candidates[hits[0]].trail_name} to this spot" + (f" (+{len(hits) - 1} more here)" if len(hits) > 1 else "")) if hits else ""
        else:
            trails = self.chain.trails_at(lat, lon, tol_m)
            self.preview_changed.emit([(p[1], p[0]) for p in trails[0][0].coords_3d] if trails else None)
            msg = (f"Click to start on {trails[0][0].name}" + (f" (+{len(trails) - 1} more here)" if len(trails) > 1 else "")) if trails else ""
        if msg != getattr(self, "_hover_msg", ""):
            self._hover_msg = msg
            self.app_state.status_message.emit(msg)

    def _on_name_edited(self, _text: str) -> None:
        self._name_edited = True

    def _reset_name(self) -> None:
        self._name_edited = False
        self.name_edit.setText(self.chain.proposed_name() if self.chain else "")

    FOCUS_MIN_SPAN_M = 400.0

    def _focus(self, coords: List[tuple]) -> None:
        """Ask the map to show these (lat, lon) points; short segments keep some surroundings
        (at least FOCUS_MIN_SPAN_M across) instead of zooming in to a few metres."""
        if not coords:
            return
        lats = [c[0] for c in coords]
        lons = [c[1] for c in coords]
        clat, clon = 0.5 * (min(lats) + max(lats)), 0.5 * (min(lons) + max(lons))
        half_lat = self.FOCUS_MIN_SPAN_M / 2 / 110540.0
        half_lon = half_lat / max(0.1, math.cos(math.radians(clat)))
        self.focus_requested.emit(list(coords) + [(clat - half_lat, clon - half_lon), (clat + half_lat, clon + half_lon)])

    # Zoom only on clicks in the lists: rows selected programmatically (e.g. by clicking the map)
    # must not move the map under the user's cursor
    def _on_start_trail_chosen(self, index: int) -> None:
        trail = next((t for t in (self.chain.trails if self.chain else []) if t.id == self.start_combo.itemData(index)), None)
        if trail is not None:
            coords = [(p[1], p[0]) for p in trail.coords_3d]
            if not (self.chain and self.chain.legs):
                self.preview_changed.emit(coords)
            self._focus(coords)

    def _on_leg_clicked(self, item: QListWidgetItem) -> None:
        row = self.legs_list.row(item)
        if self.chain and 0 <= row < len(self.chain.legs):
            self._focus([(p[1], p[0]) for p in self.chain.legs[row].coords()])

    def _on_candidate_clicked(self, item: QListWidgetItem) -> None:
        row = self.cand_list.row(item)
        if 0 <= row < len(self._candidates):
            self._focus([(p[1], p[0]) for p in self._candidates[row].coords()])

    def _on_candidate_highlighted(self, row: int) -> None:
        if 0 <= row < len(self._candidates):
            self.preview_changed.emit([(p[1], p[0]) for p in self._candidates[row].coords()])
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
            self._picked = []  # the hike changed: clicked options are recomputed on demand
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
                self.status_lbl.setText("No listed trails continue from here: click any path on the map to go on, or End Here / Undo.")
            else:
                self.status_lbl.setText(
                    "Pick where to go next. Trails are listed from the end of the current segment backwards. "
                    "Missing one? Click it on the map, even an unnamed path, and the gap is filled in."
                )
        else:
            self.summary_lbl.setText("")
            self.status_lbl.setText("Choose a starting trail and click 'Start Hike Here'." if chain else "")
            self.name_edit.clear()

        self._fit_rows(self.legs_list)
        self._fit_rows(self.cand_list)
        self._sync_ui()
        self.preview_changed.emit(None)
        self._emit_candidate_paths()
        self.draft_changed.emit(draft)

    def _emit_candidate_paths(self) -> None:
        chain = self.chain
        # Turning around runs back over the hike itself: listed, previewed on hover, but not drawn
        self.candidates_changed.emit(
            [] if not chain or chain.complete
            else [[(p[1], p[0]) for p in c.coords()] for c in self._candidates if not c.turnaround]
        )

    def hike_legs(self) -> List[tuple]:
        """(trail name, [(lat, lon), ...]) of each segment of the hike being built, in order."""
        if not self.chain:
            return []
        return [(leg.trail.name, [(p[1], p[0]) for p in leg.coords()]) for leg in self.chain.legs]

    def _show_candidates(self, select: Optional[Candidate] = None) -> None:
        """Rebuild the option list: map-picked options first, then the computed ones."""
        assert self.chain is not None
        computed = [c for c in self._candidates if not c.picked]
        self._candidates = list(self._picked) + computed
        self.cand_list.blockSignals(True)
        self.cand_list.clear()
        unit = self._unit()
        for c in self._candidates:
            self.cand_list.addItem(self._candidate_text(c, unit))
        self.cand_list.blockSignals(False)
        self._fit_rows(self.cand_list)
        self._sync_ui()
        self._emit_candidate_paths()
        if select is not None:
            self.cand_list.setCurrentRow(self._candidates.index(select))

    @staticmethod
    def _fit_rows(lst: QListWidget) -> None:
        """Size each row to its wrapped text so nothing is cut off with '…'."""
        fm = lst.fontMetrics()
        width = max(60, lst.viewport().width() - 28)  # minus the theme's item padding
        for i in range(lst.count()):
            item = lst.item(i)
            rect = fm.boundingRect(0, 0, width, 10_000, int(Qt.TextFlag.TextWordWrap), item.text())
            item.setSizeHint(QSize(width, rect.height() + 16))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_rows(self.legs_list)
        self._fit_rows(self.cand_list)

    def _candidate_text(self, c: Candidate, unit: str) -> str:
        if c.picked:
            stats = (f"{format_distance(c.distance_m, unit)} · +{format_elevation(c.gain_m, unit)}"
                     f" / -{format_elevation(c.loss_m, unit)}")
            toward = f" toward {c.toward_place}" if c.toward_place else ""
            if c.gap_m:
                return (f"↷ {c.trail_name}{toward} — {format_distance(c.gap_m, unit)} away, "
                        f"not connected on the map (walk across)\n    {stats}")
            via = f"\n    via {' → '.join(c.via_names)}" if c.via_names else ""
            return f"📍 {c.trail_name}{toward} (picked on the map){via}\n    {stats}"
        at = c.at_place or f"{format_distance(c.along_m, unit)} along"
        toward = f" toward {c.toward_place}" if c.toward_place else ""
        stats = (f"{format_distance(c.distance_m, unit)} · +{format_elevation(c.gain_m, unit)}"
                 f" / -{format_elevation(c.loss_m, unit)}")
        if c.turnaround:
            return f"↩ Turn around: back on {c.trail_name}{toward}\n    {stats}"
        if c.continues:
            return f"↑ Continue on {c.trail_name}{toward}\n    {stats}"
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
