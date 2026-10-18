"""Build a hike segment by segment along a pack's trail network.

Start on one trail, then repeatedly pick the next trail from the candidates that touch the current
segment: at its end, or forking off partway along it (the current segment is then cut at that
junction). Turning around on the current trail (out-and-back) and re-using trails already walked
are always allowed. The chain is complete when it returns to its starting point or the user ends it.
"""
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

from .elevation import summarize_elevation
from .geo import haversine_m
from .models import ComposedRoute, TrailSegment
from .routing import recompute_composed_route

JUNCTION_M = 25.0  # trail vertices this close are treated as the same junction
# A trail's *end* this close to another trail also connects: on open summits and at trailheads the
# mapped trails often stop tens of metres apart (Monadnock's summit: Pumpelly / Smith Summit / Dublin)
END_JUNCTION_M = 75.0
MIN_LEG_M = 40.0  # ignore stubs shorter than this
CLOSE_LOOP_M = 60.0  # back within this distance of the start = loop complete
FORK_MIN_ALONG_M = 60.0  # forks right where the segment began are "a different start": use Undo
_CELL_DEG = 0.0005  # ~40-55 m grid cells for the vertex index

Place = Tuple[str, float, float]  # (name, lon, lat)


@dataclass
class Leg:
    """Walk along `trail` from vertex `start` to vertex `end` (end < start = walking backwards)."""

    trail: TrailSegment
    start: int
    end: int

    @property
    def step(self) -> int:
        return 1 if self.end >= self.start else -1

    def coords(self) -> List[List[float]]:
        pts = self.trail.coords_3d
        if self.end >= self.start:
            return [list(p) for p in pts[self.start:self.end + 1]]
        return [list(p) for p in reversed(pts[self.end:self.start + 1])]

    def stats(self) -> Dict[str, float]:
        return summarize_elevation(self.coords())


@dataclass
class Candidate:
    leg: Leg  # the next segment
    cut_at: int  # index into the current last leg's coords where we switch (last index = its end)
    along_m: float  # distance along the current segment to the switch point
    at_place: Optional[str]  # named place near the switch point
    toward_place: Optional[str]  # named place near where the new segment ends
    distance_m: float
    gain_m: float
    loss_m: float
    turnaround: bool = False  # walking back the way we came on the same trail

    @property
    def trail_name(self) -> str:
        return self.leg.trail.name


@dataclass
class SegmentChain:
    trails: Sequence[TrailSegment]
    places: Sequence[Place] = ()
    legs: List[Leg] = field(default_factory=list)
    ended: bool = False
    _history: List[Tuple[List[Leg], bool]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._grid: Dict[Tuple[int, int], List[Tuple[int, int]]] = defaultdict(list)
        # (min_lat, min_lon, max_lat, max_lon) from geometry: stored trail bboxes may be unset
        self._bounds = [
            (min(p[1] for p in t.coords_3d), min(p[0] for p in t.coords_3d),
             max(p[1] for p in t.coords_3d), max(p[0] for p in t.coords_3d)) if t.coords_3d else (0.0, 0.0, 0.0, 0.0)
            for t in self.trails
        ]
        for ti, t in enumerate(self.trails):
            for vi, p in enumerate(t.coords_3d):
                self._grid[(int(p[0] / _CELL_DEG), int(p[1] / _CELL_DEG))].append((ti, vi))

    # ── building ──────────────────────────────────────────────────────────

    def start(self, trail: TrailSegment, reverse: bool = False) -> None:
        last = len(trail.coords_3d) - 1
        self._history.clear()
        self.legs = [Leg(trail, last, 0) if reverse else Leg(trail, 0, last)]
        self.ended = False

    def apply(self, cand: Candidate) -> None:
        self._history.append(([replace(leg) for leg in self.legs], self.ended))
        last = self.legs[-1]
        cut_vertex = last.start + cand.cut_at * last.step
        if cut_vertex == last.start:
            self.legs.pop()
        else:
            self.legs[-1] = Leg(last.trail, last.start, cut_vertex)
        self.legs.append(cand.leg)

    def end_here(self) -> None:
        self._history.append(([replace(leg) for leg in self.legs], self.ended))
        self.ended = True

    def undo(self) -> None:
        if self._history:
            self.legs, self.ended = self._history.pop()
        elif self.legs:
            self.legs = []
            self.ended = False

    def clear(self) -> None:
        self.legs = []
        self.ended = False
        self._history.clear()

    # ── state ─────────────────────────────────────────────────────────────

    def coords(self) -> List[List[float]]:
        out: List[List[float]] = []
        for leg in self.legs:
            pts = leg.coords()
            out.extend(pts[1:] if out and pts and out[-1][:2] == pts[0][:2] else pts)
        return out

    def is_closed(self) -> bool:
        pts = self.coords()
        if len(pts) < 3:
            return False
        dist = summarize_elevation(pts)["distance_m"]
        return dist > 2 * CLOSE_LOOP_M and haversine_m(pts[0][1], pts[0][0], pts[-1][1], pts[-1][0]) <= CLOSE_LOOP_M

    @property
    def complete(self) -> bool:
        return self.ended or self.is_closed()

    def place_near(self, lon: float, lat: float, max_m: float) -> Optional[str]:
        best, best_d = None, max_m
        for name, plon, plat in self.places:
            d = haversine_m(lat, lon, plat, plon)
            if d <= best_d:
                best, best_d = name, d
        return best

    def _near_vertices(self, lon: float, lat: float) -> List[Tuple[int, int, float]]:
        """Other trails' vertices that connect here: any vertex within JUNCTION_M, or a trail's
        first/last vertex within END_JUNCTION_M."""
        cx, cy = int(lon / _CELL_DEG), int(lat / _CELL_DEG)
        out = []
        for dx in (-2, -1, 0, 1, 2):  # 5x5 cells of ~40-55 m cover END_JUNCTION_M
            for dy in (-2, -1, 0, 1, 2):
                for ti, vi in self._grid.get((cx + dx, cy + dy), ()):
                    p = self.trails[ti].coords_3d[vi]
                    d = haversine_m(lat, lon, p[1], p[0])
                    is_end = vi == 0 or vi == len(self.trails[ti].coords_3d) - 1
                    if d <= JUNCTION_M or (is_end and d <= END_JUNCTION_M):
                        out.append((ti, vi, d))
        return out

    def candidates(self) -> List[Candidate]:
        """Trails to continue on: forks along the current segment, trails at its end, and turning
        around. Empty once the chain is complete."""
        if not self.legs or self.complete:
            return []
        last = self.legs[-1]
        leg_pts = last.coords()
        cum = [0.0]
        for a, b in zip(leg_pts, leg_pts[1:]):
            cum.append(cum[-1] + haversine_m(a[1], a[0], b[1], b[0]))

        # Where does each other trail touch the current segment? (k = index along the leg)
        touches: Dict[int, Dict[int, Tuple[int, float]]] = defaultdict(dict)  # trail -> k -> (vertex, d)
        for k in range(1, len(leg_pts)):
            for ti, vi, d in self._near_vertices(leg_pts[k][0], leg_pts[k][1]):
                if self.trails[ti].id == last.trail.id:
                    continue
                prev = touches[ti].get(k)
                if prev is None or d < prev[1]:
                    touches[ti][k] = (vi, d)

        out: List[Candidate] = []
        raw: List[Tuple[float, Candidate]] = []  # (junction distance, candidate)
        for ti, by_k in touches.items():
            trail = self.trails[ti]
            ks = sorted(by_k)
            # Consecutive touches = one junction (a crossing) or a shared stretch (overlap);
            # a shared stretch can be left at either end.
            clusters: List[List[int]] = [[ks[0]]]
            for k in ks[1:]:
                if k - clusters[-1][-1] <= 2:
                    clusters[-1].append(k)
                else:
                    clusters.append([k])
            for cl in clusters:
                reps = [cl[0]] if cum[cl[-1]] - cum[cl[0]] < 50.0 else [cl[0], cl[-1]]
                for k in reps:
                    if cum[k] < FORK_MIN_ALONG_M:
                        continue
                    vi = by_k[k][0]
                    for target in (len(trail.coords_3d) - 1, 0):
                        if target == vi:
                            continue
                        leg = Leg(trail, vi, target)
                        if self._runs_along(leg, leg_pts):
                            continue  # just shadows the segment we're already on
                        cand = self._make(leg, k, cum)
                        if cand:
                            raw.append((by_k[k][1], cand))

        # The same option is often reachable from several nearby points of the current segment
        # (a trail end within END_JUNCTION_M touches it at many vertices): keep the closest one.
        for _d, cand in sorted(raw, key=lambda r: r[0]):
            start = cand.leg.trail.coords_3d[cand.leg.start]
            duplicate = any(
                o.leg.trail.id == cand.leg.trail.id and o.leg.end == cand.leg.end
                and haversine_m(start[1], start[0], o.leg.trail.coords_3d[o.leg.start][1], o.leg.trail.coords_3d[o.leg.start][0]) <= 60.0
                for o in out
            )
            if not duplicate:
                out.append(cand)

        # Out-and-back: turn around at the end of the current segment
        back_target = 0 if last.step > 0 else len(last.trail.coords_3d) - 1
        if back_target != last.end:
            cand = self._make(Leg(last.trail, last.end, back_target), len(leg_pts) - 1, cum)
            if cand:
                cand.turnaround = True
                out.append(cand)

        out.sort(key=lambda c: (-c.along_m, c.turnaround, c.trail_name))  # junctions at the end first
        return out

    def _runs_along(self, leg: Leg, leg_pts: List[List[float]]) -> bool:
        """True when the new leg's first ~80 m lie on the current segment (overlapping trail)."""
        pts = leg.coords()
        dist, probe = 0.0, None
        for a, b in zip(pts, pts[1:]):
            dist += haversine_m(a[1], a[0], b[1], b[0])
            if dist >= 80.0:
                probe = b
                break
        if probe is None:
            return False
        return any(haversine_m(probe[1], probe[0], p[1], p[0]) <= JUNCTION_M for p in leg_pts)

    def _make(self, leg: Leg, k: int, cum: List[float]) -> Optional[Candidate]:
        stats = leg.stats()
        if stats["distance_m"] < MIN_LEG_M:
            return None
        pts = leg.coords()
        return Candidate(
            leg=leg,
            cut_at=k,
            along_m=cum[k],
            at_place=self.place_near(pts[0][0], pts[0][1], 200.0),
            toward_place=self.place_near(pts[-1][0], pts[-1][1], 350.0),
            distance_m=stats["distance_m"],
            gain_m=stats["elevation_gain_m"],
            loss_m=stats["elevation_loss_m"],
        )

    # ── picking on the map ────────────────────────────────────────────────

    @staticmethod
    def _dist_to_line_m(lat: float, lon: float, pts: Sequence[Sequence[float]]) -> float:
        """Distance (m) from a point to a [lon, lat, ...] polyline (local flat approximation)."""
        kx = 111_320.0 * math.cos(math.radians(lat))
        ky = 110_540.0
        best = math.inf
        for a, b in zip(pts, pts[1:]):
            ax, ay = (a[0] - lon) * kx, (a[1] - lat) * ky
            bx, by = (b[0] - lon) * kx, (b[1] - lat) * ky
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
        return best

    def trails_at(self, lat: float, lon: float, tol_m: float) -> List[Tuple[TrailSegment, bool, float]]:
        """Trails within `tol_m` of the point, nearest first, as (trail, reverse, distance).
        `reverse` starts the trail at the end nearer the click. Trails sharing a stretch of path
        are all returned so the caller can ask which one is meant."""
        hits = []
        for t, (lo_lat, lo_lon, hi_lat, hi_lon) in zip(self.trails, self._bounds):
            if len(t.coords_3d) < 2 or not (lo_lat - 0.01 <= lat <= hi_lat + 0.01 and lo_lon - 0.01 <= lon <= hi_lon + 0.01):
                continue
            d = self._dist_to_line_m(lat, lon, t.coords_3d)
            if d <= tol_m:
                first, last = t.coords_3d[0], t.coords_3d[-1]
                reverse = haversine_m(lat, lon, last[1], last[0]) < haversine_m(lat, lon, first[1], first[0])
                hits.append((t, reverse, d))
        return sorted(hits, key=lambda h: h[2])

    def candidates_at(self, cands: Sequence[Candidate], lat: float, lon: float, tol_m: float) -> List[int]:
        """Indices of candidates whose segment passes within `tol_m` of the point, nearest first."""
        hits = []
        for i, c in enumerate(cands):
            d = self._dist_to_line_m(lat, lon, c.leg.coords())
            if d <= tol_m:
                hits.append((d, i))
        return [i for _d, i in sorted(hits)]

    # ── result ────────────────────────────────────────────────────────────

    def segment_names(self) -> List[str]:
        """Trail names in walking order, consecutive repeats merged, parenthetical asides dropped."""
        names: List[str] = []
        for leg in self.legs:
            short = re.sub(r"\s*\([^)]*\)", "", leg.trail.name).strip() or leg.trail.name
            if not names or names[-1] != short:
                names.append(short)
        return names

    def shape(self) -> str:
        pts = self.coords()
        if not self.is_closed():
            return "segment"
        # Out-and-back if the way back retraces the way out (quarter points coincide)
        n = len(pts)
        q1, q3 = pts[n // 4], pts[(3 * n) // 4]
        retraced = haversine_m(q1[1], q1[0], q3[1], q3[0]) <= 3 * JUNCTION_M
        return "out_and_back" if retraced else "loop"

    def proposed_name(self) -> str:
        names = self.segment_names()
        if not names:
            return ""
        shape = self.shape()
        if shape == "out_and_back":
            # A → B → A is presented as the way out
            half = names[: (len(names) + 1) // 2]
            return " → ".join(half) + " (Out & Back)"
        return " → ".join(names) + (" (Loop)" if shape == "loop" else "")

    def to_route(self, name: str, pack_id: str) -> ComposedRoute:
        trail_ids: List[str] = []
        for leg in self.legs:
            if leg.trail.id not in trail_ids:
                trail_ids.append(leg.trail.id)
        route = ComposedRoute(
            name=name or self.proposed_name() or "Composed Hike",
            pack_id=pack_id,
            coords_3d=self.coords(),
            trail_ids=trail_ids,
            trail_type=self.shape(),  # type: ignore[arg-type]
            tags=["segment_builder"],
            notes="Segments: " + " → ".join(leg.trail.name for leg in self.legs),
        )
        return recompute_composed_route(route, list(self.trails))
