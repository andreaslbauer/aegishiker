"""'Build by Trail Segments' sidebar for the Trail Composer.

Pick a starting trail, then keep choosing the next trail from those that branch off the current
segment (or meet it at its end), turn around for an out-and-back, and finish when back at the start
or with 'End Here'. The growing hike is shown live on the Composer's map, stats and elevation chart.
"""
import math
from typing import List, Optional, Tuple

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
from src.hiking.geo import haversine_m
from src.hiking.models import ComposedRoute, DEMGrid, DestinationPack
from src.hiking.segment_chain import (
    Candidate,
    SegmentChain,
    StopPoint,
    is_bushwhack,
    is_map_path,
    is_road_path,
    map_paths_from_features,
)
from src.hiking.units import format_distance, format_elevation

PLACE_KINDS = {"peak", "hut", "shelter", "trailhead", "parking", "waterfall", "viewpoint", "campsite", "saddle", "spring"}


class SegmentBuilder(QWidget):
    draft_changed = Signal(object)  # Optional[ComposedRoute] — the hike built so far
    preview_changed = Signal(object)  # Optional[List[(lat, lon)]] — highlighted candidate
    candidates_changed = Signal(object)  # List[List[(lat, lon)]] — pickable segments for the map
    route_saved = Signal(str)  # route_id
    focus_requested = Signal(object)  # List[(lat, lon)] — the user clicked a trail: zoom the map to it
    stops_changed = Signal(object)  # List[dict] — points the next segment can be walked to (see _update_stops)

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.chain: Optional[SegmentChain] = None
        self.pack_id: Optional[str] = None
        self._candidates: List[Candidate] = []
        self._picked: List[Candidate] = []  # options added by clicking trails on the map
        self._name_edited = False
        self._stops: List[tuple] = []  # (option index, StopPoint)
        self._hover_stop: Optional[tuple] = None
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
        self.minor_chk = QCheckBox("Include minor && unnamed paths")
        self.minor_chk.setChecked(bool(self.app_state.settings.get("include_minor_paths", True)))
        self.minor_chk.setToolTip(
            "Offer the map's smaller paths too — side paths, herd paths and unnamed tracks that aren't "
            "among the region's listed trails. Off: only listed trails (paths still bridge gaps)."
        )
        self.minor_chk.toggled.connect(self._on_minor_toggled)
        col.addWidget(self.minor_chk)

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

        self.next_lbl = self._step("2 · Next segment — click a trail on the map, then a ● where you turned or stopped")
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
        self.undo_btn = QPushButton("Remove Last Segment")
        self.undo_btn.setToolTip("Take the last segment off the hike")
        self.undo_btn.clicked.connect(self.undo)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        for b in (self.add_btn, self.end_btn):
            btn_row.addWidget(b)
        col.addLayout(btn_row)
        edit_row = QHBoxLayout()
        edit_row.addWidget(self.undo_btn, 1)
        edit_row.addWidget(self.clear_btn)
        col.addLayout(edit_row)

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
        """Load the region's trails (keeps the current hike if the region is unchanged or overlaps
        the current hike endpoint)."""
        pack_id = pack.id if pack else None
        if pack_id == self.pack_id and self.chain is not None:
            return
        old_legs = list(self.chain.legs) if self.chain and self.chain.legs else []
        old_ended = self.chain.ended if self.chain else False
        old_history = list(self.chain._history) if self.chain else []
        self.pack_id = pack_id
        self._dem: Optional[DEMGrid] = pack.dem if pack else None
        self._loaded_adj_ids: Tuple[str, ...] = ()
        self.start_combo.clear()
        self.chain = None
        if pack:
            places = [(f.name, f.coords[0][0], f.coords[0][1])
                      for f in pack.features if f.geom == "point" and f.name and f.kind in PLACE_KINDS]
            places += [(p.name, p.lon, p.lat) for p in pack.pois]
            trails = sorted(pack.trails, key=lambda t: t.name.lower())
            # The map's other footpaths, tracks, and roads: pickable on the map and used to fill gaps
            map_paths = map_paths_from_features(pack.features, pack.dem, pack.id)
            self.chain = SegmentChain(trails, places, map_paths=map_paths,
                                      include_map_paths=self.minor_chk.isChecked())
            if old_legs:
                end_lon, end_lat = old_legs[-1].coords()[-1][:2]
                by_id = {t.id: t for t in trails}
                if (
                    (pack.min_lat <= end_lat <= pack.max_lat and pack.min_lon <= end_lon <= pack.max_lon)
                    or old_legs[-1].trail.id in by_id
                ):
                    from dataclasses import replace as _dc_replace
                    self.chain.legs = [
                        _dc_replace(lg, trail=by_id[lg.trail.id]) if lg.trail.id in by_id else lg
                        for lg in old_legs
                    ]
                    self.chain.ended = old_ended
                    self.chain._history = old_history
            self._fill_start_combo()
        self._refresh()

    def _ensure_adjacent_packs_for_bbox(self, bbox: Tuple[float, float, float, float]) -> bool:
        """If `bbox` (the hike or trail being composed) approaches or crosses `self.pack_id`'s
        boundary, pull trails, POIs, and map paths from the adjacent area pack(s) into `self.chain`."""
        if not self.pack_id or not self.chain or not hasattr(self.app_state.db, "adjacent_pack_ids"):
            return False
        needed = tuple(self.app_state.db.adjacent_pack_ids(self.pack_id, bbox=bbox, margin_deg=0.035))
        if not (set(needed) - set(getattr(self, "_loaded_adj_ids", ()))):
            return False
        union_adj = tuple(sorted(set(getattr(self, "_loaded_adj_ids", ())) | set(needed)))
        comp = self.app_state.db.get_composite_pack(self.pack_id, bbox=bbox, with_map=True, margin_deg=0.035)
        if comp is None:
            return False
        self._loaded_adj_ids = union_adj
        self._dem = comp.dem
        old_legs = list(self.chain.legs)
        old_ended = self.chain.ended
        old_history = list(self.chain._history)
        places = [(f.name, f.coords[0][0], f.coords[0][1])
                  for f in comp.features if f.geom == "point" and f.name and f.kind in PLACE_KINDS]
        places += [(p.name, p.lon, p.lat) for p in comp.pois]
        trails = sorted(comp.trails, key=lambda t: t.name.lower())
        map_paths = map_paths_from_features(comp.features, comp.dem, comp.id)
        self.chain = SegmentChain(trails, places, map_paths=map_paths,
                                  include_map_paths=self.minor_chk.isChecked())
        if old_legs:
            from dataclasses import replace as _dc_replace
            by_id = {t.id: t for t in trails}
            self.chain.legs = [
                _dc_replace(lg, trail=by_id[lg.trail.id]) if lg.trail.id in by_id else lg
                for lg in old_legs
            ]
            self.chain.ended = old_ended
            self.chain._history = old_history
        self._fill_start_combo()
        return True

    def _fill_start_combo(self) -> None:
        """The region's trails, then (when included) the map's minor named paths. Unnamed paths are
        too many to list: click one on the map to start there."""
        current = self.start_combo.currentData()
        self.start_combo.clear()
        if not self.chain:
            return
        unit = self._unit()
        for t in self.chain.named_trails:
            self.start_combo.addItem(f"{t.name}  ({format_distance(t.distance_m, unit)})", t.id)
        if self.chain.include_map_paths:
            minor = self.chain.minor_paths()
            if minor:
                self.start_combo.insertSeparator(self.start_combo.count())
            for t in minor:
                self.start_combo.addItem(f"{t.name}  ({format_distance(t.distance_m, unit)}, minor path)", t.id)
        idx = self.start_combo.findData(current)
        if idx >= 0:
            self.start_combo.setCurrentIndex(idx)

    def _on_minor_toggled(self, on: bool) -> None:
        self.app_state.settings.set("include_minor_paths", on)
        self.app_state.settings.save()
        if self.chain:
            self.chain.include_map_paths = on
            self._fill_start_combo()
            self._picked = [c for c in self._picked if on or not is_map_path(c.leg.trail)]
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

    def _disambiguate_candidate_hits(self, hits: List[int], lat: float, lon: float) -> Optional[int]:
        """Pick the best candidate hit at `(lat, lon)`, only prompting with `_choose` when multiple
        candidates genuinely share the clicked path away from the junction."""
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]
        assert self.chain is not None
        top_cand = self._candidates[hits[0]]
        top_tier = 1 if is_map_path(top_cand.leg.trail) else 0
        top_pts = top_cand.coords()
        top_body = top_pts[min(2, len(top_pts) - 1):] if len(top_pts) > 2 else top_pts
        top_bd = self.chain._dist_to_line_m(lat, lon, top_body)
        close_hits: List[int] = [hits[0]]
        for h in hits[1:]:
            c = self._candidates[h]
            tier = 1 if is_map_path(c.leg.trail) else 0
            if tier > top_tier:
                continue
            pts = c.coords()
            body = pts[min(2, len(pts) - 1):] if len(pts) > 2 else pts
            bd = self.chain._dist_to_line_m(lat, lon, body)
            if abs(bd - top_bd) <= 8.0:
                close_hits.append(h)
        if len(close_hits) == 1:
            return close_hits[0]
        return self._choose(
            [(self._candidate_text(self._candidates[h], self._unit()).split("\n")[0], h) for h in close_hits]
        )

    def pick_at(self, lat: float, lon: float, tol_m: float) -> bool:
        """Handle a map click. Returns True when the click belongs to the builder (no tracing)."""
        if not self.pick_chk.isChecked() or not self.chain:
            return False
        if self.has_draft():
            if self.chain.complete:
                self.app_state.status_message.emit("The hike is complete: save it, or Remove Last Segment to keep building.")
                return True
            stop = self._stop_at(lat, lon, max(18.0, tol_m * 1.5))
            if stop is not None:
                self.add_to_stop(*stop)
                return True
            hits = self.chain.candidates_at(self._candidates, lat, lon, max(16.0, tol_m * 1.25))
            if hits:
                top_cand = self._candidates[hits[0]]
                routed = self.chain.candidate_to(top_cand.leg.trail, lat, lon)
                if routed is not None and routed.via_names:
                    # A shorter multi-trail path reaches this spot via other connecting trails
                    hits = []
            if not hits:
                self._offer_clicked_trail(lat, lon, max(20.0, tol_m * 1.4))
                return True
            current_row = self.cand_list.currentRow()
            if current_row in hits:
                # Clicking an already-selected candidate adds it (up to the nearest stop point or click)
                cand_stops = [sp for i, sp in self._stops if i == current_row]
                if len(cand_stops) == 1:
                    self.add_to_stop(current_row, cand_stops[0])
                elif cand_stops:
                    nearest_sp = min(cand_stops, key=lambda sp: haversine_m(lat, lon, sp.lat, sp.lon))
                    if haversine_m(lat, lon, nearest_sp.lat, nearest_sp.lon) <= max(45.0, tol_m * 3.0):
                        self.add_to_stop(current_row, nearest_sp)
                    else:
                        short = self.chain.shorten_to(self._candidates[current_row], lat, lon)
                        self.chain.apply(short)
                        self._refresh()
                        self.app_state.status_message.emit(f"Added {short.trail_name}.")
                else:
                    self.add_selected()
                return True
            idx = self._disambiguate_candidate_hits(hits, lat, lon)
            if idx is not None:
                # Clicked an option: select it and show where it can be walked to
                self.cand_list.setCurrentRow(idx)
                n = sum(1 for i, _sp in self._stops if i == idx)
                self.app_state.status_message.emit(
                    f"{self._candidates[idx].trail_name}: click one of its {n} points (●) where you turned "
                    "or stopped (or click the trail again / Add Segment)."
                )
            return True
        trails = self.chain.trails_at(lat, lon, max(18.0, tol_m * 1.3))
        if not trails:
            self.app_state.status_message.emit("Click on a trail to start the hike there.")
            return True
        close_trails = [trails[0]] + [t for t in trails[1:] if t[2] <= trails[0][2] + 8.0 and t[0].name != trails[0][0].name]
        pick = 0 if len(close_trails) == 1 else self._choose(
            [(f"Start on {t.name}", n) for n, (t, _rev, _d) in enumerate(close_trails)]
        )
        if pick is not None:
            trail, reverse, _d = close_trails[pick]
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
        """When the user clicks a point that is NOT immediately on the next trail, find the
        shortest path of connecting trails leading there and immediately fill in all those segments.
        Only when no trail or road is near the click, offers an off-trail 'Bushwhack' segment."""
        assert self.chain is not None
        all_pts = [pt for leg in self.chain.legs for pt in leg.coords()]
        if all_pts:
            lats = [p[1] for p in all_pts] + [lat]
            lons = [p[0] for p in all_pts] + [lon]
            self._ensure_adjacent_packs_for_bbox((min(lats), max(lats), min(lons), max(lons)))
        trails = self.chain.trails_at(lat, lon, tol_m)
        if not trails:
            bw = self.chain.bushwhack_to(lat, lon, getattr(self, "_dem", None), self.pack_id or "")
            if bw is None:
                self.app_state.status_message.emit("Click a trail, or one of the dashed yellow options, to add it.")
                return
            self._picked = [c for c in self._picked if not is_bushwhack(c.leg.trail)] + [bw]
            self._show_candidates(select=bw)
            self.app_state.status_message.emit(
                f"No trail here — offered off-trail {bw.trail_name} ({format_distance(bw.distance_m, self._unit())}). "
                "Click its ● endpoint or Add Segment to bushwhack there."
            )
            return
        close_trails = [trails[0]] + [t for t in trails[1:] if t[2] <= trails[0][2] + 8.0 and t[0].name != trails[0][0].name]
        pick = 0 if len(close_trails) == 1 else self._choose(
            [(f"Go to {t.name}", n) for n, (t, _rev, _d) in enumerate(close_trails)]
        )
        if pick is None:
            return
        trail = close_trails[pick][0]
        with busy(self, "Finding shortest path to that trail…", show_dialog=False):
            cand = self.chain.candidate_to(trail, lat, lon)
        if cand is None:
            self.app_state.status_message.emit(
                f"{trail.name} can't be reached from here on the mapped trails, and is too far to jump to."
            )
            return
        # Immediately fill in all connecting trail segments leading to the clicked point
        unit = self._unit()
        via_names = list(cand.via_names)
        dist_str = format_distance(cand.distance_m, unit)
        gap_m = cand.gap_m
        self.chain.apply(cand)
        self._refresh()
        if gap_m:
            how = f"{format_distance(gap_m, unit)} off-trail jump, {dist_str}"
        elif via_names:
            how = f"via {' → '.join(via_names)}, {dist_str}"
        else:
            how = dist_str
        self.app_state.status_message.emit(f"Added shortest path to {trail.name} ({how}).")

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
            stop = self._stop_at(lat, lon, tol_m * 1.5)
            hits = [] if stop else self.chain.candidates_at(self._candidates, lat, lon, tol_m)
            hover = stop if stop else None
            if hover != self._hover_stop:
                self._hover_stop = hover
                self._emit_stops()
            if stop:
                idx, sp = stop
                cand = self.chain.shorten_at(self._candidates[idx], sp.k)
                self.preview_changed.emit([(p[1], p[0]) for p in cand.coords()])
                msg = f"Click to add: {self._stop_text(self._candidates[idx], sp)}"
            elif hits:
                self.preview_changed.emit([(p[1], p[0]) for p in self._candidates[hits[0]].coords()])
                msg = (f"Click to select {self._candidates[hits[0]].trail_name} and see where it goes"
                       + (f" (+{len(hits) - 1} more here)" if len(hits) > 1 else ""))
            else:
                trails = self.chain.trails_at(lat, lon, max(20.0, tol_m * 1.4))
                cand = self.chain.candidate_to(trails[0][0], lat, lon) if trails else None
                if cand is not None:
                    self.preview_changed.emit([(p[1], p[0]) for p in cand.coords()])
                    via = f" via {' → '.join(cand.via_names)}" if cand.via_names else ""
                    msg = (
                        f"Click to add shortest path to {cand.trail_name}{via} "
                        f"({format_distance(cand.distance_m, self._unit())})"
                    )
                else:
                    self._on_candidate_highlighted(self.cand_list.currentRow())
                    msg = ""
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
        if row != getattr(self, "_stops_row", None):
            self._stops_row = row
            self._update_stops()

    # ── stop points: where the next segment can be walked to ─────────────

    def _update_stops(self) -> None:
        """Junctions and ends of every option (the selected turnaround's too). Where several options
        reach the same spot, the shortest walk there is kept."""
        stops: List[tuple] = []
        chain = self.chain
        selected = self.cand_list.currentRow()
        if chain and chain.legs and not chain.complete:
            for i, cand in enumerate(self._candidates):
                if cand.turnaround and i != selected:
                    continue
                for sp in chain.stop_points(cand):
                    dup = next((n for n, (_j, o) in enumerate(stops)
                                if abs(o.lat - sp.lat) * 110540 < 15 and abs(o.lon - sp.lon) * 80000 < 15), None)
                    if dup is None:
                        stops.append((i, sp))
                    elif i == selected or (stops[dup][0] != selected and sp.along_m < stops[dup][1].along_m):
                        stops[dup] = (i, sp)
        self._stops = stops
        self._hover_stop = None
        self._emit_stops()

    def _emit_stops(self) -> None:
        selected = self.cand_list.currentRow()
        unit = self._unit()
        self.stops_changed.emit([
            {
                "lat": sp.lat, "lon": sp.lon, "kind": sp.kind, "selected": i == selected,
                "hover": self._hover_stop is not None and self._hover_stop[1] is sp,
                "label": (", ".join(sp.names[:2]) + (f" +{len(sp.names) - 2}" if len(sp.names) > 2 else "")
                          if sp.names else f"end · {format_distance(sp.along_m, unit)}"),
            }
            for i, sp in self._stops
        ])

    def _stop_at(self, lat: float, lon: float, tol_m: float) -> Optional[tuple]:
        """The stop point under the cursor (the selected option's first), or None."""
        selected = self.cand_list.currentRow()
        best, best_key = None, None
        for i, sp in self._stops:
            d = haversine_m(lat, lon, sp.lat, sp.lon)
            if d <= tol_m:
                key = (i != selected, d)
                if best_key is None or key < best_key:
                    best, best_key = (i, sp), key
        return best

    def _stop_text(self, cand: Candidate, sp: StopPoint) -> str:
        unit = self._unit()
        where = (f"the junction with {', '.join(sp.names[:2])}" if sp.kind == "junction"
                 else (f"its end at {', '.join(sp.names[:2])}" if sp.names else "its end"))
        return f"{cand.trail_name}, {format_distance(sp.along_m, unit)} to {where}"

    def add_to_stop(self, idx: int, sp: StopPoint) -> None:
        """Add the stretch of option `idx` that leads to the stop point to the hike."""
        if not self.chain or not (0 <= idx < len(self._candidates)):
            return
        cand = self._candidates[idx]
        text = self._stop_text(cand, sp)
        self.chain.apply(self.chain.shorten_at(cand, sp.k))
        self._refresh()
        self.app_state.status_message.emit(f"Added {text}.")

    # ── view ─────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        chain = self.chain
        unit = self._unit()
        self.legs_list.clear()
        self.cand_list.clear()
        self._candidates = []
        draft: Optional[ComposedRoute] = None

        if chain and chain.legs:
            all_pts = [pt for leg in chain.legs for pt in leg.coords()]
            if all_pts:
                lats = [p[1] for p in all_pts]
                lons = [p[0] for p in all_pts]
                if self._ensure_adjacent_packs_for_bbox((min(lats), max(lats), min(lons), max(lons))):
                    chain = self.chain
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
                self.status_lbl.setText("✓ Back at the starting point — the hike is complete. Name it and save, or Remove Last Segment to change it.")
            elif chain.ended:
                self.status_lbl.setText("✓ Hike ends here. Name it and save, or Remove Last Segment to keep going.")
            elif not self._candidates:
                self.status_lbl.setText("No listed trails continue from here: click any path on the map to go on, or End Here / Remove Last Segment.")
            else:
                self.status_lbl.setText(
                    "Click a dashed yellow trail, then the dot (●) where you turned or stopped. "
                    "Double-click an option to add all of it."
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
        self._stops_row = self.cand_list.currentRow()
        self._update_stops()
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
        self._update_stops()
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
        t = (c.extra_legs[-1] if c.extra_legs else c.leg).trail
        if c.picked:
            stats = (f"{format_distance(c.distance_m, unit)} · +{format_elevation(c.gain_m, unit)}"
                     f" / -{format_elevation(c.loss_m, unit)}")
            toward = f" toward {c.toward_place}" if c.toward_place else ""
            if is_bushwhack(t):
                return f"🧭 {c.trail_name}\n    {stats}"
            if c.gap_m:
                return (f"🧭 {c.trail_name}{toward} — {format_distance(c.gap_m, unit)} off-trail bushwhack\n"
                        f"    {stats}")
            via = f"\n    via {' → '.join(c.via_names)}" if c.via_names else ""
            prefix = "🛣" if is_road_path(t) else "📍"
            return f"{prefix} {c.trail_name}{toward} (picked on the map){via}\n    {stats}"
        at = c.at_place or f"{format_distance(c.along_m, unit)} along"
        toward = f" toward {c.toward_place}" if c.toward_place else ""
        stats = (f"{format_distance(c.distance_m, unit)} · +{format_elevation(c.gain_m, unit)}"
                 f" / -{format_elevation(c.loss_m, unit)}")
        if c.turnaround:
            return f"↩ Turn around: back on {c.trail_name}{toward}\n    {stats}"
        if c.continues:
            return f"↑ Continue on {c.trail_name}{toward}\n    {stats}"
        prefix = "🛣" if is_road_path(t) else "→"
        return f"{prefix} {c.trail_name}{toward}\n    at {at} · {stats}"

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
