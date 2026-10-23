"""Build a hike segment by segment along a pack's trail network.

Start on one trail, then repeatedly pick the next trail from the candidates that touch the current
segment: at its end, or forking off partway along it (the current segment is then cut at that
junction). Turning around on the current trail (out-and-back) and re-using trails already walked
are always allowed. The chain is complete when it returns to its starting point or the user ends it.

Besides the region's named trails the chain can know the basemap's other footpaths and tracks
("map paths", see `map_paths_from_features`): minor named paths and unnamed ones. With
`include_map_paths` they are offered as segments and can be clicked on the map or started on;
either way, routes to a clicked trail may use them to fill gaps.
"""
import bisect
import heapq
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

from .elevation import sample_dem, summarize_elevation
from .geo import haversine_m
from .models import ComposedRoute, DEMGrid, MapFeature, TrailSegment
from .routing import recompute_composed_route

JUNCTION_M = 25.0  # trail vertices this close are treated as the same junction
# A trail's *end* this close to another trail also connects: on open summits and at trailheads the
# mapped trails often stop tens of metres apart (Monadnock's summit: Pumpelly / Smith Summit / Dublin)
END_JUNCTION_M = 75.0
MIN_LEG_M = 40.0  # ignore stubs shorter than this
CLOSE_LOOP_M = 60.0  # back within this distance of the start = loop complete
FORK_MIN_ALONG_M = END_JUNCTION_M + 25.0  # forks right where the segment began are "a different start": use Undo
_CELL_DEG = 0.0005  # ~40-55 m grid cells for the vertex index

Place = Tuple[str, float, float]  # (name, lon, lat)

MAP_PATH_TAG = "map_path"  # tag on TrailSegments made from basemap paths (not region trails)
MAP_PATH_STEP_M = 20.0  # densify map paths so crossings with trails fall within JUNCTION_M


def map_paths_from_features(
    features: Sequence[MapFeature], dem: Optional[DEMGrid], pack_id: str = ""
) -> List[TrailSegment]:
    """The basemap's footpaths and tracks as walkable segments (elevations from the DEM)."""
    out: List[TrailSegment] = []
    for i, f in enumerate(features):
        if f.geom != "line" or f.kind not in ("path", "track") or len(f.coords) < 2:
            continue
        pts: List[List[float]] = []
        for a, b in zip(f.coords, f.coords[1:]):
            d = haversine_m(a[1], a[0], b[1], b[0])
            n = max(1, int(d // MAP_PATH_STEP_M))
            for j in range(n):
                t = j / n
                pts.append([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
        pts.append(list(f.coords[-1][:2]))
        coords = [[lon, lat, round(sample_dem(dem, lat, lon), 1)] for lon, lat in pts]
        length = sum(haversine_m(a[1], a[0], b[1], b[0]) for a, b in zip(f.coords, f.coords[1:]))
        unnamed = "Unnamed path" if f.kind == "path" else "Unnamed track"
        if f.info:  # what the map says about it, e.g. "SAC T1 hiking", so it can be told apart
            unnamed += f" ({f.info.split(' · ')[0].split(';')[0][:40]})"
        out.append(TrailSegment(
            id=f"mappath_{i}", pack_id=pack_id, name=f.name or unnamed, coords_3d=coords, distance_m=round(length, 1),
            tags=[MAP_PATH_TAG] + ([] if f.name else ["unnamed"]),
            description="Path from the map (not one of the region's listed trails)",
        ))
    return out


def is_map_path(trail: TrailSegment) -> bool:
    return MAP_PATH_TAG in trail.tags


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
    extra_legs: List[Leg] = field(default_factory=list)  # further segments of a routed ("via") option
    gap_m: float = 0.0  # >0: the trail is close but not connected; the gap is walked in a straight line
    picked: bool = False  # added because the user clicked this trail on the map
    continues: bool = False  # keeps going on the current trail (the last segment stopped part-way)

    @property
    def trail_name(self) -> str:
        return (self.extra_legs[-1] if self.extra_legs else self.leg).trail.name

    def coords(self) -> List[List[float]]:
        """The whole option's path (all of a routed option's segments)."""
        out: List[List[float]] = []
        for leg in [self.leg, *self.extra_legs]:
            out.extend(leg.coords())
        return out

    @property
    def via_names(self) -> List[str]:
        """Trails walked to reach a routed option (excluding the destination trail)."""
        names: List[str] = []
        for leg in [self.leg, *self.extra_legs][:-1]:
            if not names or names[-1] != leg.trail.name:
                names.append(leg.trail.name)
        return names


@dataclass
class StopPoint:
    """A point an option can be walked to: vertex k of its last segment."""

    k: int
    lat: float
    lon: float
    kind: str  # "junction" (other trails meet here) or "end" (the option's end)
    names: List[str]  # the other trails meeting here
    along_m: float  # walking distance from where the option leaves the hike


@dataclass
class SegmentChain:
    trails: Sequence[TrailSegment]
    places: Sequence[Place] = ()
    map_paths: Sequence[TrailSegment] = ()  # the map's other paths: pickable, routable and offered too
    legs: List[Leg] = field(default_factory=list)
    ended: bool = False
    include_map_paths: bool = True  # offer minor & unnamed map paths as segments (else routing only)
    _history: List[Tuple[List[Leg], bool]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.named_trails: List[TrailSegment] = list(self.trails)
        self._n_named = len(self.named_trails)
        self.trails = self.named_trails + list(self.map_paths)
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
        # Named map paths lying on a listed trail are that trail's own OSM pieces: never offered
        self._shadows = {ti for ti in range(self._n_named, len(self.trails)) if self._is_shadow(ti)}

    SHADOW_M = 12.0  # a map path this close to a listed trail all along is that trail's own path

    def _is_shadow(self, ti: int) -> bool:
        """Is map path ti a piece of a listed trail (named or not: some listed trails are built from
        unnamed OSM ways)? Sampled at three points, all on the same listed trail."""
        t = self.trails[ti]
        if len(t.coords_3d) < 2:
            return False
        pts = t.coords_3d
        samples = [pts[int(f * (len(pts) - 1))] for f in (0.15, 0.5, 0.85)]
        for tj in range(self._n_named):
            lo_lat, lo_lon, hi_lat, hi_lon = self._bounds[tj]
            if all(lo_lat - 0.0002 <= p[1] <= hi_lat + 0.0002 and lo_lon - 0.0003 <= p[0] <= hi_lon + 0.0003 for p in samples):
                line = self.trails[tj].coords_3d
                if all(self._dist_to_line_m(p[1], p[0], line) <= self.SHADOW_M for p in samples):
                    return True
        return False

    def offered(self, ti: int) -> bool:
        """Can trail ti be offered as a next segment / named at a junction?"""
        if ti < self._n_named:
            return True
        return self.include_map_paths and ti not in self._shadows

    def minor_paths(self) -> List[TrailSegment]:
        """The map's named paths that aren't one of the region's listed trails, by name."""
        return sorted((self.trails[ti] for ti in range(self._n_named, len(self.trails))
                       if ti not in self._shadows and "unnamed" not in self.trails[ti].tags),
                      key=lambda t: t.name.lower())

    # ── building ──────────────────────────────────────────────────────────

    def start(self, trail: TrailSegment, reverse: bool = False, from_vertex: Optional[int] = None) -> None:
        """Start on `trail` at one end (or at `from_vertex`, e.g. a trailhead part-way along it),
        walking toward its last vertex, or toward its first with `reverse`."""
        last = len(trail.coords_3d) - 1
        begin = (last if reverse else 0) if from_vertex is None else max(0, min(last, from_vertex))
        end = 0 if reverse else last
        if begin == end:
            end = last - end
        self._history.clear()
        self.legs = [Leg(trail, begin, end)]
        self.ended = False

    def start_near(self, lat: float, lon: float, trail: TrailSegment) -> Tuple[Optional[int], bool]:
        """Where to start `trail` for a hike from the point (a trailhead): (vertex or None for the
        nearer end, reverse). Part-way along a trail, the longer stretch lies ahead."""
        pts = trail.coords_3d
        dists = [haversine_m(lat, lon, p[1], p[0]) for p in pts]
        v = min(range(len(pts)), key=dists.__getitem__)
        cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            cum.append(cum[-1] + haversine_m(a[1], a[0], b[1], b[0]))
        if cum[v] <= END_JUNCTION_M:
            return None, False
        if cum[-1] - cum[v] <= END_JUNCTION_M:
            return None, True
        return v, cum[v] > cum[-1] - cum[v]

    def apply(self, cand: Candidate) -> None:
        self._history.append(([replace(leg) for leg in self.legs], self.ended))
        last = self.legs[-1]
        cut_vertex = last.start + cand.cut_at * last.step
        if cut_vertex == last.start:
            self.legs.pop()
        else:
            self.legs[-1] = Leg(last.trail, last.start, cut_vertex)
        for leg in [cand.leg, *cand.extra_legs]:
            prev = self.legs[-1] if self.legs else None
            if prev and prev.trail.id == leg.trail.id and prev.end == leg.start and prev.step == leg.step:
                self.legs[-1] = Leg(prev.trail, prev.start, leg.end)  # same trail, same direction: one leg
            else:
                self.legs.append(leg)

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
                if not self.offered(ti) or self.trails[ti].id == last.trail.id:
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
                    # Forks right after the start are "a different start" (use Undo) — but the end of
                    # a short segment (a 90 m climb up to a ridge trail) is where the hike goes on
                    if cum[k] < FORK_MIN_ALONG_M and cum[-1] - cum[k] > END_JUNCTION_M:
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

        # The current segment stopped part-way (the user clicked where they went): keep going on it
        ahead = len(last.trail.coords_3d) - 1 if last.step > 0 else 0
        if ahead != last.end:
            cand = self._make(Leg(last.trail, last.end, ahead), len(leg_pts) - 1, cum)
            if cand:
                cand.continues = True
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
        are all returned so the caller can ask which one is meant. Map paths are returned only
        where no region trail is near (a listed trail usually is a map path too)."""
        hits = self._trails_at(lat, lon, tol_m, 0, self._n_named)
        if hits or not self.include_map_paths:
            return hits
        return self._trails_at(lat, lon, tol_m, self._n_named, len(self.trails))

    def _trails_at(self, lat: float, lon: float, tol_m: float, lo: int, hi: int) -> List[Tuple[TrailSegment, bool, float]]:
        hits = []
        for t, (lo_lat, lo_lon, hi_lat, hi_lon) in zip(self.trails[lo:hi], self._bounds[lo:hi]):
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
            d = self._dist_to_line_m(lat, lon, c.coords())
            if d <= tol_m:
                hits.append((d, i))
        return [i for _d, i in sorted(hits)]

    # ── reaching a trail clicked on the map ───────────────────────────────

    CLOSE_GAP_M = 300.0  # a clicked trail this close (but not connected) can be jumped to
    MAX_GAP_M = 1500.0  # beyond this an unconnected trail is not offered
    MAX_ROUTE_M = 25_000.0
    MAP_PATH_COST = 1.5  # routing weight of a metre on a map path vs. on a region trail

    def _network(self):
        """Walkable graph over every trail vertex: neighbours along a trail plus junction links
        (the same rules as the option list: 25 m anywhere, 75 m at a trail's end). Built once."""
        if getattr(self, "_graph", None) is not None:
            return self._graph
        offsets, n = [], 0
        for t in self.trails:
            offsets.append(n)
            n += len(t.coords_3d)
        adj: List[List[Tuple[int, float]]] = [[] for _ in range(n)]
        for ti, t in enumerate(self.trails):
            pts, base = t.coords_3d, offsets[ti]
            # Routes prefer the region's trails; map paths (unnamed paths, tracks) fill the gaps
            cost = 1.0 if ti < self._n_named else self.MAP_PATH_COST
            for vi in range(len(pts) - 1):
                d = cost * haversine_m(pts[vi][1], pts[vi][0], pts[vi + 1][1], pts[vi + 1][0])
                adj[base + vi].append((base + vi + 1, d))
                adj[base + vi + 1].append((base + vi, d))
        # Junction links, as in _near_vertices but with a flat-earth distance and direct grid scans
        # (map paths make this tens of thousands of vertices): 25 m between any vertices from
        # adjacent cells, 75 m when either vertex is a trail's end (searched 2 cells out)
        grid = self._grid
        ky = 110_540.0
        kx = 111_320.0 * math.cos(math.radians(self.trails[0].coords_3d[0][1])) if self.trails and self.trails[0].coords_3d else ky
        j2, e2 = JUNCTION_M ** 2, END_JUNCTION_M ** 2
        ends = {(ti, len(t.coords_3d) - 1) for ti, t in enumerate(self.trails)} | {(ti, 0) for ti in range(len(self.trails))}
        for ti, t in enumerate(self.trails):
            pts, base = t.coords_3d, offsets[ti]
            last = len(pts) - 1
            for vi, p in enumerate(pts):
                is_end = vi == 0 or vi == last
                cx, cy = int(p[0] / _CELL_DEG), int(p[1] / _CELL_DEG)
                reach = 2 if is_end else 1
                for dx in range(-reach, reach + 1):
                    for dy in range(-reach, reach + 1):
                        for tj, vj in grid.get((cx + dx, cy + dy), ()):
                            if tj == ti:
                                continue
                            q = self.trails[tj].coords_3d[vj]
                            ddx, ddy = (q[0] - p[0]) * kx, (q[1] - p[1]) * ky
                            d2 = ddx * ddx + ddy * ddy
                            if d2 <= j2 or (d2 <= e2 and (is_end or (tj, vj) in ends)):
                                # Both directions: a middle vertex only scans adjacent cells, so
                                # an end 2 cells away links back to it from its own scan
                                d = math.sqrt(d2)
                                adj[base + vi].append((offsets[tj] + vj, d))
                                adj[offsets[tj] + vj].append((base + vi, d))
        self._graph = (offsets, adj)
        return self._graph

    def _node_trail(self, offsets: List[int], node: int) -> Tuple[int, int]:
        ti = bisect.bisect_right(offsets, node) - 1
        return ti, node - offsets[ti]

    def _route_legs(self, start, goal: Tuple[int, int]) -> Optional[List[Leg]]:
        """Shortest walk over the trail network as legs, or None if not reachable. `start` is one
        (trail, vertex) or a list of them (any may be the start: the nearest one is used)."""
        offsets, adj = self._network()
        starts = start if isinstance(start, list) else [start]
        sources = {offsets[t] + v for t, v in starts}
        dst = offsets[goal[0]] + goal[1]
        dist = {src: 0.0 for src in sources}
        prev: Dict[int, int] = {}
        heap = [(0.0, src) for src in sources]
        heapq.heapify(heap)
        while heap:
            d, u = heapq.heappop(heap)
            if u == dst:
                break
            if d > dist.get(u, math.inf) or d > self.MAX_ROUTE_M:
                continue
            for v, w in adj[u]:
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
        if dst not in dist:
            return None
        path = [dst]
        while path[-1] not in sources:
            path.append(prev[path[-1]])
        path.reverse()
        legs: List[Leg] = []
        run_t, run_start, run_end = None, None, None
        for node in path:
            ti, vi = self._node_trail(offsets, node)
            if ti != run_t:
                if run_t is not None and run_end != run_start:
                    legs.append(Leg(self.trails[run_t], run_start, run_end))
                run_t, run_start = ti, vi
            run_end = vi
        if run_t is not None and run_end != run_start:
            legs.append(Leg(self.trails[run_t], run_start, run_end))
        # A hop across an overlapping trail splits one trail into two legs: join them again
        merged: List[Leg] = []
        for leg in legs:
            prev = merged[-1] if merged else None
            if prev and prev.trail.id == leg.trail.id and prev.step == leg.step:
                a = prev.trail.coords_3d[prev.end]
                b = leg.trail.coords_3d[leg.start]
                if haversine_m(a[1], a[0], b[1], b[0]) <= 100.0 and (leg.start - prev.end) * prev.step >= 0:
                    merged[-1] = Leg(prev.trail, prev.start, leg.end)
                    continue
                merged.append(leg)
            else:
                merged.append(leg)
        return merged

    def candidate_to(self, trail: TrailSegment, lat: float, lon: float) -> Optional[Candidate]:
        """An option reaching a trail the user clicked on the map although it isn't offered. The hike
        leaves the current segment where it meets that trail (or the nearest way to it) and goes to
        the clicked spot: routed over the network ("via" other trails), or, when it's close but not
        mapped as connected, a short straight jump onto it. None if neither is sensible."""
        if not self.legs or self.complete:
            return None
        last = self.legs[-1]
        leg_pts = last.coords()
        ti = next(i for i, t in enumerate(self.trails) if t.id == trail.id)
        last_ti = next(i for i, t in enumerate(self.trails) if t.id == last.trail.id)
        pts = trail.coords_3d
        click_v = min(range(len(pts)), key=lambda i: haversine_m(lat, lon, pts[i][1], pts[i][0]))

        legs = None
        if trail.id != last.trail.id:
            # Every point of the current segment (but its very start) can be where the hike turns off
            starts = [(last_ti, last.start + k * last.step) for k in range(1, len(leg_pts))]
            legs = self._route_legs(starts, (ti, click_v))
        # Nearest straight-line way across, for the jump
        best = (math.inf, len(leg_pts) - 1, 0)
        for k in range(1, len(leg_pts)):
            p = leg_pts[k]
            for v in range(len(pts)):
                d = haversine_m(p[1], p[0], pts[v][1], pts[v][0])
                if d < best[0]:
                    best = (d, k, v)
        gap, gap_k, entry_v = best
        if legs:
            routed = sum(leg.stats()["distance_m"] for leg in legs)
            lo, hi = sorted((entry_v, click_v))
            jump_total = gap + Leg(trail, lo, hi).stats()["distance_m"]  # across, then along to the click
            if gap <= self.CLOSE_GAP_M and routed > max(3 * jump_total, jump_total + 800.0):
                legs = None  # a long detour around to something right next to us: offer the jump
        if legs:
            first = legs[0]
            if first.trail.id == last.trail.id:
                # The route starts by walking on along the current trail: turn off at its last vertex
                k = (first.end - last.start) * last.step
                legs = legs[1:]
            else:
                # The route's first vertex is on another trail; it was reached from the leg vertex at
                # the same junction: find that vertex on the current segment
                start_pt = first.trail.coords_3d[first.start]
                k = min(range(1, len(leg_pts)),
                        key=lambda i: haversine_m(start_pt[1], start_pt[0], leg_pts[i][1], leg_pts[i][0]))
            if legs and 0 < k:
                return self._multi_candidate(legs, gap_m=0.0, cut_at=min(k, len(leg_pts) - 1))
        if gap > self.MAX_GAP_M:
            return None
        # Jump: walk the clicked trail from its nearest point in the direction of the click
        if click_v == entry_v:
            target = len(pts) - 1 if (len(pts) - 1 - entry_v) >= entry_v else 0
        else:
            target = click_v
        if target == entry_v:
            return None
        return self._multi_candidate([Leg(trail, entry_v, target)], gap_m=gap, cut_at=gap_k)

    def shorten_to(self, cand: Candidate, lat: float, lon: float) -> Candidate:
        """The option ending at the point the user clicked on it, instead of at its trail's end."""
        pts = ([cand.leg, *cand.extra_legs][-1]).coords()
        k = min(range(len(pts)), key=lambda i: haversine_m(lat, lon, pts[i][1], pts[i][0]))
        return self.shorten_at(cand, k)

    def shorten_at(self, cand: Candidate, k: int) -> Candidate:
        """The option ending at vertex k of its last segment (see stop_points)."""
        legs = [cand.leg, *cand.extra_legs]
        final = legs[-1]
        n = len(final.coords())
        if k <= 0 or k >= n - 1:
            return cand
        legs[-1] = Leg(final.trail, final.start, final.start + k * final.step)
        coords: List[List[float]] = []
        for leg in legs:
            coords.extend(leg.coords())
        stats = summarize_elevation(coords)
        end = coords[-1]
        return replace(
            cand, leg=legs[0], extra_legs=legs[1:], distance_m=stats["distance_m"] + cand.gap_m,
            gain_m=stats["elevation_gain_m"], loss_m=stats["elevation_loss_m"],
            toward_place=self.place_near(end[0], end[1], 350.0),
        )

    def stop_points(self, cand: Candidate) -> List[StopPoint]:
        """Where the hike can stop on an option: each junction with another listed trail along its
        last segment, and its end. Each is the vertex closest to where the trails meet."""
        legs = [cand.leg, *cand.extra_legs]
        final = legs[-1]
        pts = final.coords()
        before = cand.gap_m + sum(leg.stats()["distance_m"] for leg in legs[:-1])
        cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            cum.append(cum[-1] + haversine_m(a[1], a[0], b[1], b[0]))
        # Where each other listed trail touches this segment (k -> distance), like candidates()
        touches: Dict[str, Dict[int, float]] = defaultdict(dict)
        for k in range(1, len(pts)):
            for ti, _vi, d in self._near_vertices(pts[k][0], pts[k][1]):
                other = self.trails[ti]
                if not self.offered(ti) or other.id == final.trail.id:
                    continue
                if d < touches[other.name].get(k, math.inf):
                    touches[other.name][k] = d
        found: List[Tuple[int, str]] = []
        for name, by_k in touches.items():
            ks = sorted(by_k)
            clusters: List[List[int]] = [[ks[0]]]
            for k in ks[1:]:
                if k - clusters[-1][-1] <= 2:
                    clusters[-1].append(k)
                else:
                    clusters.append([k])
            for cl in clusters:
                on = [k for k in cl if by_k[k] <= 12.0]  # actually on the same path
                if len(on) >= 2 and cum[on[-1]] - cum[on[0]] >= 60.0:
                    found += [(on[0], name), (on[-1], name)]  # a shared stretch: where it begins and ends
                else:
                    found.append((min(cl, key=by_k.__getitem__), name))  # a crossing: closest point
        out: List[StopPoint] = []
        for k, name in sorted(found):
            if cum[k] <= END_JUNCTION_M:
                continue  # the junction the option starts from
            if out and cum[k] - (out[-1].along_m - before) <= 40.0:
                if name not in out[-1].names:
                    out[-1].names.append(name)
                continue
            out.append(StopPoint(k, pts[k][1], pts[k][0], "junction", [name], before + cum[k]))
        last = len(pts) - 1
        if out and out[-1].k >= last - 1:
            out[-1] = replace(out[-1], k=last, lat=pts[last][1], lon=pts[last][0], kind="end", along_m=before + cum[last])
        elif last > 0:
            out.append(StopPoint(last, pts[last][1], pts[last][0], "end", [], before + cum[last]))
        return out

    def _multi_candidate(self, legs: List[Leg], gap_m: float, cut_at: Optional[int] = None) -> Optional[Candidate]:
        last = self.legs[-1]
        leg_pts = last.coords()
        cut = len(leg_pts) - 1 if cut_at is None else cut_at
        along = summarize_elevation(leg_pts[:cut + 1])["distance_m"] if cut > 0 else 0.0
        coords: List[List[float]] = []
        for leg in legs:
            coords.extend(leg.coords())
        stats = summarize_elevation(coords) if len(coords) > 1 else {"distance_m": 0.0, "elevation_gain_m": 0.0, "elevation_loss_m": 0.0}
        end = coords[-1]
        start = coords[0]
        return Candidate(
            leg=legs[0], extra_legs=legs[1:], cut_at=cut, along_m=along,
            at_place=self.place_near(start[0], start[1], 200.0),
            toward_place=self.place_near(end[0], end[1], 350.0),
            distance_m=stats["distance_m"] + gap_m, gain_m=stats["elevation_gain_m"],
            loss_m=stats["elevation_loss_m"], gap_m=gap_m, picked=True,
        )

    # ── result ────────────────────────────────────────────────────────────

    def segment_names(self) -> List[str]:
        """Trail names in walking order, consecutive repeats merged, parenthetical asides dropped."""
        names: List[str] = []
        for leg in self.legs:
            if "unnamed" in leg.trail.tags:
                continue
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
            if leg.trail.id not in trail_ids and not is_map_path(leg.trail):
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
        return recompute_composed_route(route, self.named_trails)
