"""Trail network graph routing (A* shortest path across connected trail junctions),
nearest-trail snapping, and route composition helpers.
"""
import heapq
from typing import Dict, List, Optional, Tuple

from .elevation import sample_dem, summarize_elevation
from .geo import haversine_m
from .models import ComposedRoute, DEMGrid, RouteWaypoint, TrailSegment


def _node_key(lon: float, lat: float) -> Tuple[int, int]:
    """Quantize coordinates (~1.1 m grid) so shared trail junction endpoints merge cleanly."""
    return (round(lon * 100_000), round(lat * 100_000))


class TrailRouter:
    """Builds a connected undirected graph from a regional pack's TrailSegments."""

    def __init__(self, trails: List[TrailSegment], dem: Optional[DEMGrid] = None):
        self.trails = list(trails)
        self.trails_by_id: Dict[str, TrailSegment] = {t.id: t for t in self.trails}
        self.dem = dem
        self.nodes: Dict[Tuple[int, int], List[float]] = {}  # key -> [lon, lat, ele_m]
        # key -> list of (neighbor_key, edge_distance_m, edge_coords_3d, trail_id)
        self.adj: Dict[Tuple[int, int], List[Tuple[Tuple[int, int], float, List[List[float]], str]]] = {}
        self._build_graph()

    def _build_graph(self) -> None:
        for trail in self.trails:
            if trail.condition_status == "closed":
                continue
            pts = trail.coords_3d
            if len(pts) < 2:
                continue
            for pt in pts:
                k = _node_key(pt[0], pt[1])
                if k not in self.nodes:
                    ele = pt[2] if len(pt) > 2 else sample_dem(self.dem, pt[1], pt[0], 0.0)
                    self.nodes[k] = [pt[0], pt[1], ele]
                    self.adj[k] = []

            # Add edges between consecutive vertices along the trail so waypoints can snap anywhere
            for i in range(1, len(pts)):
                p1, p2 = pts[i - 1], pts[i]
                k1 = _node_key(p1[0], p1[1])
                k2 = _node_key(p2[0], p2[1])
                if k1 == k2:
                    continue
                d = haversine_m(p1[1], p1[0], p2[1], p2[0])
                c1 = [p1[0], p1[1], p1[2] if len(p1) > 2 else 0.0]
                c2 = [p2[0], p2[1], p2[2] if len(p2) > 2 else 0.0]
                self.adj[k1].append((k2, d, [c1, c2], trail.id))
                self.adj[k2].append((k1, d, [c2, c1], trail.id))

        # Also bridge nearby trail endpoints within 35 meters so slightly offset junctions connect
        endpoint_keys: List[Tuple[int, int]] = []
        for trail in self.trails:
            if trail.condition_status == "closed" or len(trail.coords_3d) < 2:
                continue
            for pt in (trail.coords_3d[0], trail.coords_3d[-1]):
                k = _node_key(pt[0], pt[1])
                if k not in endpoint_keys:
                    endpoint_keys.append(k)

        for i in range(len(endpoint_keys)):
            k1 = endpoint_keys[i]
            p1 = self.nodes[k1]
            for j in range(i + 1, len(endpoint_keys)):
                k2 = endpoint_keys[j]
                p2 = self.nodes[k2]
                d = haversine_m(p1[1], p1[0], p2[1], p2[0])
                if 0.1 < d <= 35.0:
                    self.adj[k1].append((k2, d, [list(p1), list(p2)], "connector"))
                    self.adj[k2].append((k1, d, [list(p2), list(p1)], "connector"))

    def snap_to_nearest_node(
        self,
        lat: float,
        lon: float,
        max_snap_m: float = 500.0,
    ) -> Tuple[Optional[Tuple[int, int]], float, List[float], Optional[str]]:
        """Find nearest graph vertex within `max_snap_m`.

        Returns (node_key_or_None, distance_m, [lon, lat, ele_m], trail_id_or_None).
        """
        best_key: Optional[Tuple[int, int]] = None
        best_dist = float("inf")
        best_trail_id: Optional[str] = None

        for trail in self.trails:
            if trail.condition_status == "closed":
                continue
            for pt in trail.coords_3d:
                d = haversine_m(lat, lon, pt[1], pt[0])
                if d < best_dist:
                    best_dist = d
                    best_key = _node_key(pt[0], pt[1])
                    best_trail_id = trail.id

        if best_key is not None and best_dist <= max_snap_m:
            return (best_key, best_dist, list(self.nodes[best_key]), best_trail_id)

        ele = sample_dem(self.dem, lat, lon, 0.0)
        return (None, best_dist, [lon, lat, ele], None)

    def shortest_path(
        self,
        start_key: Tuple[int, int],
        goal_key: Tuple[int, int],
    ) -> Tuple[List[List[float]], List[str]]:
        """A* search between two graph nodes. Returns (coords_3d, trail_ids_used)."""
        if start_key not in self.nodes or goal_key not in self.nodes:
            return ([], [])
        if start_key == goal_key:
            return ([list(self.nodes[start_key])], [])

        goal_pt = self.nodes[goal_key]
        open_heap: List[Tuple[float, float, Tuple[int, int]]] = []
        heapq.heappush(open_heap, (0.0, 0.0, start_key))
        came_from: Dict[Tuple[int, int], Tuple[Tuple[int, int], List[List[float]], str]] = {}
        g_score: Dict[Tuple[int, int], float] = {start_key: 0.0}

        while open_heap:
            _, current_g, current = heapq.heappop(open_heap)
            if current == goal_key:
                path_coords: List[List[float]] = []
                trail_ids: List[str] = []
                cur = goal_key
                segments: List[Tuple[List[List[float]], str]] = []
                while cur in came_from:
                    prev, edge_coords, tid = came_from[cur]
                    segments.append((edge_coords, tid))
                    cur = prev
                segments.reverse()
                for idx, (edge_coords, tid) in enumerate(segments):
                    if idx == 0:
                        path_coords.extend(edge_coords)
                    else:
                        path_coords.extend(edge_coords[1:])
                    if tid != "connector" and tid not in trail_ids:
                        trail_ids.append(tid)
                return (path_coords, trail_ids)

            if current_g > g_score.get(current, float("inf")):
                continue

            for neighbor, edge_d, edge_coords, tid in self.adj.get(current, []):
                tentative_g = current_g + edge_d
                if tentative_g < g_score.get(neighbor, float("inf")):
                    g_score[neighbor] = tentative_g
                    came_from[neighbor] = (current, edge_coords, tid)
                    n_pt = self.nodes[neighbor]
                    h = haversine_m(n_pt[1], n_pt[0], goal_pt[1], goal_pt[0])
                    heapq.heappush(open_heap, (tentative_g + h, tentative_g, neighbor))

        return ([], [])

    def build_route_from_waypoints(
        self,
        waypoints: List[RouteWaypoint],
        snap_to_trails: bool = True,
        max_snap_m: float = 600.0,
    ) -> Tuple[List[List[float]], List[str]]:
        """Connect ordered waypoints via trail network A* (when snapped) or direct DEM segments."""
        if not waypoints:
            return ([], [])
        if len(waypoints) == 1:
            wp = waypoints[0]
            ele = wp.ele_m or sample_dem(self.dem, wp.lat, wp.lon, 0.0)
            return ([[wp.lon, wp.lat, ele]], [wp.snapped_trail_id] if wp.snapped_trail_id else [])

        all_coords: List[List[float]] = []
        used_trail_ids: List[str] = []

        for i in range(len(waypoints) - 1):
            w1, w2 = waypoints[i], waypoints[i + 1]
            seg_coords: List[List[float]] = []
            seg_trails: List[str] = []

            if snap_to_trails and self.nodes:
                k1, _, pt1, tid1 = self.snap_to_nearest_node(w1.lat, w1.lon, max_snap_m=max_snap_m)
                k2, _, pt2, tid2 = self.snap_to_nearest_node(w2.lat, w2.lon, max_snap_m=max_snap_m)
                if k1 is not None and k2 is not None:
                    seg_coords, seg_trails = self.shortest_path(k1, k2)
                    if not seg_coords and tid1 and tid1 not in seg_trails:
                        seg_trails.append(tid1)

            if not seg_coords:
                # Freehand / direct interpolation with intermediate DEM samples every ~150 m
                seg_coords = _interpolate_freehand_segment(w1, w2, self.dem)

            if not all_coords:
                all_coords.extend(seg_coords)
            else:
                all_coords.extend(seg_coords[1:])

            for tid in seg_trails:
                if tid and tid not in used_trail_ids:
                    used_trail_ids.append(tid)

        return (all_coords, used_trail_ids)


def _interpolate_freehand_segment(
    w1: RouteWaypoint,
    w2: RouteWaypoint,
    dem: Optional[DEMGrid],
) -> List[List[float]]:
    dist = haversine_m(w1.lat, w1.lon, w2.lat, w2.lon)
    steps = max(1, min(40, int(dist // 150.0) + 1))
    coords: List[List[float]] = []
    e1 = w1.ele_m if w1.ele_m != 0.0 else sample_dem(dem, w1.lat, w1.lon, 0.0)
    e2 = w2.ele_m if w2.ele_m != 0.0 else sample_dem(dem, w2.lat, w2.lon, e1)
    for s in range(steps + 1):
        t = s / steps
        lat = w1.lat + t * (w2.lat - w1.lat)
        lon = w1.lon + t * (w2.lon - w1.lon)
        if dem is not None:
            ele = sample_dem(dem, lat, lon, e1 + t * (e2 - e1))
        else:
            ele = round(e1 + t * (e2 - e1), 1)
        coords.append([round(lon, 6), round(lat, 6), round(ele, 1)])
    return coords


def recompute_composed_route(
    route: ComposedRoute,
    trails: List[TrailSegment],
    dem: Optional[DEMGrid] = None,
    hysteresis_m: float = 3.0,
) -> ComposedRoute:
    """Recompute 3D path, distance, elevation stats, difficulty, and condition warnings on `route`."""
    router = TrailRouter(trails, dem=dem)
    if route.waypoints:
        coords, trail_ids = router.build_route_from_waypoints(
            route.waypoints,
            snap_to_trails=route.snap_to_trails,
        )
        route.coords_3d = coords
        if trail_ids:
            route.trail_ids = trail_ids
    elif route.trail_ids and not route.coords_3d:
        # Build directly from referenced trail_ids
        combined: List[List[float]] = []
        for tid in route.trail_ids:
            seg = router.trails_by_id.get(tid)
            if seg and seg.coords_3d:
                if not combined:
                    combined.extend(seg.coords_3d)
                else:
                    combined.extend(seg.coords_3d[1:])
        route.coords_3d = combined

    summary = summarize_elevation(route.coords_3d, hysteresis_m=hysteresis_m, dem=dem)
    route.distance_m = summary["distance_m"]
    route.elevation_gain_m = summary["elevation_gain_m"]
    route.elevation_loss_m = summary["elevation_loss_m"]
    route.min_ele_m = summary["min_ele_m"]
    route.max_ele_m = summary["max_ele_m"]
    route.max_grade_pct = summary["max_grade_pct"]

    # Determine difficulty, dog policy, water availability, and condition warnings from used trails
    warnings: List[str] = []
    dogs_ok = True
    water_ok = False
    for tid in route.trail_ids:
        t = router.trails_by_id.get(tid)
        if not t:
            continue
        if not t.dogs_allowed:
            dogs_ok = False
        if t.water_available:
            water_ok = True
        if t.condition_status != "open":
            msg = f"{t.name}: {t.condition_status.upper()}"
            if t.condition_note:
                msg += f" — {t.condition_note}"
            warnings.append(msg)

    route.dogs_allowed = dogs_ok
    route.water_available = water_ok
    route.condition_warnings = warnings

    # Classify route difficulty
    from .models import TrailDifficulty

    effort_score = (route.distance_m / 1000.0) + (route.elevation_gain_m / 100.0)
    if effort_score >= 22.0 or route.max_grade_pct >= 28.0:
        route.difficulty = TrailDifficulty.EXPERT
    elif effort_score >= 13.0 or route.max_grade_pct >= 18.0:
        route.difficulty = TrailDifficulty.STRENUOUS
    elif effort_score >= 6.0 or route.max_grade_pct >= 10.0:
        route.difficulty = TrailDifficulty.MODERATE
    else:
        route.difficulty = TrailDifficulty.EASY

    # Detect loop vs out-and-back
    if len(route.coords_3d) >= 4:
        start_pt = route.coords_3d[0]
        end_pt = route.coords_3d[-1]
        end_gap = haversine_m(start_pt[1], start_pt[0], end_pt[1], end_pt[0])
        if end_gap <= 150.0 and route.trail_type not in ("loop", "out_and_back"):
            route.trail_type = "loop"

    return route


def route_from_trail_segment(
    segment: TrailSegment,
    make_out_and_back: bool = False,
) -> ComposedRoute:
    """Create an editable ComposedRoute directly from a TrailSegment."""
    coords = [list(pt) for pt in segment.coords_3d]
    if make_out_and_back and len(coords) >= 2:
        start_pt, end_pt = coords[0], coords[-1]
        if haversine_m(start_pt[1], start_pt[0], end_pt[1], end_pt[0]) > 100.0:
            coords = coords + [list(pt) for pt in reversed(coords[:-1])]

    waypoints: List[RouteWaypoint] = []
    if coords:
        p0 = coords[0]
        waypoints.append(
            RouteWaypoint(
                name=f"{segment.name} Start",
                lat=p0[1],
                lon=p0[0],
                ele_m=p0[2] if len(p0) > 2 else 0.0,
                kind="start",
                snapped_trail_id=segment.id,
            )
        )
        if len(coords) > 2:
            mid = coords[len(coords) // 2]
            waypoints.append(
                RouteWaypoint(
                    name=f"{segment.name} Midpoint/Summit",
                    lat=mid[1],
                    lon=mid[0],
                    ele_m=mid[2] if len(mid) > 2 else 0.0,
                    kind="summit",
                    snapped_trail_id=segment.id,
                )
            )
        plast = coords[-1]
        waypoints.append(
            RouteWaypoint(
                name=f"{segment.name} End",
                lat=plast[1],
                lon=plast[0],
                ele_m=plast[2] if len(plast) > 2 else 0.0,
                kind="end",
                snapped_trail_id=segment.id,
            )
        )

    summary = summarize_elevation(coords)
    warnings = []
    if segment.condition_status != "open":
        warnings.append(
            f"{segment.name}: {segment.condition_status.upper()}"
            + (f" — {segment.condition_note}" if segment.condition_note else "")
        )

    return ComposedRoute(
        pack_id=segment.pack_id,
        name=segment.name + (" (Out & Back)" if make_out_and_back else ""),
        difficulty=segment.difficulty,
        trail_type="out_and_back" if make_out_and_back else segment.trail_type,
        snap_to_trails=True,
        waypoints=waypoints,
        trail_ids=[segment.id],
        coords_3d=coords,
        distance_m=summary["distance_m"],
        elevation_gain_m=summary["elevation_gain_m"],
        elevation_loss_m=summary["elevation_loss_m"],
        min_ele_m=summary["min_ele_m"],
        max_ele_m=summary["max_ele_m"],
        max_grade_pct=summary["max_grade_pct"],
        dogs_allowed=segment.dogs_allowed,
        water_available=segment.water_available,
        condition_warnings=warnings,
        tags=list(segment.tags),
        notes=segment.description,
    )


def snap_and_route_waypoints(
    segments: List[TrailSegment],
    waypoints: List[Tuple[float, float]],
    name: str = "Traced Trail Route",
    destination_id: str = "white_mountains_nh",
    dem: Optional[DEMGrid] = None,
) -> ComposedRoute:
    """Snap a list of (lat, lon) clicks to the trail network and build a ComposedRoute."""
    router = TrailRouter(segments, dem=dem)
    route_wps: List[RouteWaypoint] = []
    for idx, (lat, lon) in enumerate(waypoints):
        _, _, snapped_pt, tid = router.snap_to_nearest_node(lat, lon)
        kind = "start" if idx == 0 else ("end" if idx == len(waypoints) - 1 else "waypoint")
        route_wps.append(
            RouteWaypoint(
                name=f"Waypoint {idx + 1}",
                lat=snapped_pt[1],
                lon=snapped_pt[0],
                ele_m=snapped_pt[2] if len(snapped_pt) > 2 else 0.0,
                kind=kind,
                snapped_trail_id=tid,
            )
        )
    route = ComposedRoute(
        pack_id=destination_id or (segments[0].pack_id if segments else "white_mountains_nh"),
        name=name,
        waypoints=route_wps,
        snap_to_trails=True,
    )
    return recompute_composed_route(route, segments, dem=dem)

