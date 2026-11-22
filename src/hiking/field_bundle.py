"""Portable Field Hike Bundle for the iPhone Offline PWA Companion (src/hiking/field_bundle.py).

Packages a composed hike (`ComposedRoute`) together with its region context (`DestinationPack`)
into a compact, self-contained JSON-serializable structure (`FieldHikeBundle`) containing:
1. Hike summary, metadata, elevation bounds, pacing, and emergency dispatch info.
2. Full 3D route geometry with cumulative distances (`[[lon, lat, ele_m, cum_dist_m], ...]`) and
   elevation/grade profile.
3. Ordered trails taken (`legs`: name, code, difficulty, surface, condition, from_m, to_m,
   distance_m, gain_m, loss_m, coords).
4. Nearby landmarks, viewpoints, peaks, waterfalls, huts, shelters, water sources, and trailheads
   (`landmarks`), each projected onto the route (`along_m`, `offset_m`) with altitude (`ele_m`)
   and notes (`info`).
5. Clipped local basemap (`basemap`: contour lines, DEM sub-grid for offline elevation lookup &
   hillshading, streams/water, and connecting side trails) around the hike's bounding box.
6. Solar sunset / civil dusk (`time_to_darkness`) helpers using the NOAA solar equations from
   `src.hiking.daylight`.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from .daylight import sun_position
from .elevation import sample_dem
from .geo import compute_bbox, haversine_m
from .hike_report import trail_legs
from .models import ComposedRoute, ContourLine, DEMGrid, DestinationPack, TrailSegment
from .pace import estimate_pace_minutes

LANDMARK_CORRIDOR_M = 550.0  # POIs/features within this distance of the route are included as route landmarks
CONTEXT_PEAK_MARGIN_DEG = 0.015  # ~1.5 km margin around the hike bbox for context peaks/huts & basemap
BASEMAP_MARGIN_DEG = 0.014  # ~1.4 km basemap pad around the hike bbox

LANDMARK_KINDS = {
    "peak",
    "viewpoint",
    "waterfall",
    "hut",
    "shelter",
    "campsite",
    "camp",
    "spring",
    "drinking_water",
    "water",
    "saddle",
    "trailhead",
    "parking",
    "ranger_station",
    "ranger",
    "guidepost",
    "historic",
}


class FieldTrailLeg(BaseModel):
    """One named trail segment walked along the composed hike."""

    index: int
    trail_id: str = ""
    name: str
    code: str = ""
    difficulty: str = "moderate"
    surface: str = "rocky"
    condition: str = "open"
    condition_note: str = ""
    from_m: float
    to_m: float
    distance_m: float
    gain_m: float
    loss_m: float
    min_ele_m: float = 0.0
    max_ele_m: float = 0.0
    coords: List[List[float]] = Field(default_factory=list)  # [[lon, lat, ele_m], ...]


class FieldLandmark(BaseModel):
    """A landmark, viewpoint, summit, hut, water source, or trailhead along or near the hike."""

    id: str
    name: str
    kind: str  # peak, viewpoint, waterfall, hut, shelter, water, trailhead, parking, saddle, campsite, guidepost
    lat: float
    lon: float
    ele_m: float = 0.0
    along_m: float = 0.0  # closest distance along the hike (m from start)
    offset_m: float = 0.0  # perpendicular distance from the route line (m)
    on_route: bool = True  # True if within LANDMARK_CORRIDOR_M of the route
    info: str = ""


class FieldBasemap(BaseModel):
    """Compact local basemap slice clipped around the hike's bounding box."""

    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    contour_interval_m: float = 60.96
    contours: List[ContourLine] = Field(default_factory=list)
    dem: Optional[DEMGrid] = None
    nearby_trails: List[Dict[str, Any]] = Field(default_factory=list)
    water: List[Dict[str, Any]] = Field(default_factory=list)


class FieldHikeBundle(BaseModel):
    """Complete portable data structure for exploring and navigating a composed hike on a phone PWA."""

    id: str
    name: str
    pack_id: str = "white_mountains_nh"
    region_name: str = ""
    state_or_region: str = ""
    agency: str = ""
    emergency_contact: str = "911"
    notes: str = ""
    created_at: str = ""

    # Summary statistics
    distance_m: float = 0.0
    elevation_gain_m: float = 0.0
    elevation_loss_m: float = 0.0
    min_ele_m: float = 0.0
    max_ele_m: float = 0.0
    max_grade_pct: float = 0.0
    avg_grade_pct: float = 0.0
    estimated_duration_min: float = 0.0
    pacing_model: str = "naismith"
    pack_weight_kg: float = 6.0

    # Bounding box [min_lon, min_lat, max_lon, max_lat]
    bbox: List[float] = Field(default_factory=list)

    # [[lon, lat, ele_m, cum_dist_m], ...]
    coords_3d: List[List[float]] = Field(default_factory=list)
    elevation_profile: List[Dict[str, float]] = Field(default_factory=list)

    # Trails taken in walking order
    legs: List[FieldTrailLeg] = Field(default_factory=list)

    # Landmarks, viewpoints, summits, huts, water sources, trailheads
    landmarks: List[FieldLandmark] = Field(default_factory=list)

    # Local clipped topographic basemap
    basemap: Optional[FieldBasemap] = None


def _project_point_onto_route(
    lat: float,
    lon: float,
    coords_4d: Sequence[Sequence[float]],
) -> Tuple[float, float, float]:
    """Project (lat, lon) onto `coords_4d` ([[lon, lat, ele_m, cum_m], ...]).
    Returns `(along_m, offset_m, route_ele_m)`."""
    if not coords_4d:
        return 0.0, 0.0, 0.0
    if len(coords_4d) == 1:
        p0 = coords_4d[0]
        return 0.0, haversine_m(lat, lon, p0[1], p0[0]), float(p0[2])

    kx = 111_320.0 * math.cos(math.radians(lat))
    ky = 110_540.0
    best_d = math.inf
    best_along = 0.0
    best_ele = float(coords_4d[0][2])

    for a, b in zip(coords_4d, coords_4d[1:]):
        ax, ay = (a[0] - lon) * kx, (a[1] - lat) * ky
        bx, by = (b[0] - lon) * kx, (b[1] - lat) * ky
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 <= 1e-12 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
        d = math.hypot(ax + t * dx, ay + t * dy)
        if d < best_d:
            best_d = d
            best_along = float(a[3]) + t * (float(b[3]) - float(a[3]))
            best_ele = float(a[2]) + t * (float(b[2]) - float(a[2]))
    return round(best_along, 1), round(best_d, 1), round(best_ele, 1)


def _clip_polyline(
    coords: Sequence[Sequence[float]],
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    min_pts: int = 2,
) -> List[List[List[float]]]:
    chains: List[List[List[float]]] = []
    cur: List[List[float]] = []
    for pt in coords:
        if min_lon <= pt[0] <= max_lon and min_lat <= pt[1] <= max_lat:
            cur.append([round(float(pt[0]), 5), round(float(pt[1]), 5)])
        else:
            if len(cur) >= min_pts:
                chains.append(cur)
            cur = []
    if len(cur) >= min_pts:
        chains.append(cur)
    return chains


def _slice_dem_for_bbox(
    dem: Optional[DEMGrid],
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    max_side: int = 120,
) -> Optional[DEMGrid]:
    if dem is None or not dem.elevations_m:
        return None
    dlat = max(1e-5, (dem.max_lat - dem.min_lat) / max(1, dem.rows - 1))
    dlon = max(1e-5, (dem.max_lon - dem.min_lon) / max(1, dem.cols - 1))
    raw_rows = max(8, int(round((max_lat - min_lat) / dlat)) + 1)
    raw_cols = max(8, int(round((max_lon - min_lon) / dlon)) + 1)
    scale = max(1.0, max(raw_rows, raw_cols) / float(max_side))
    rows = max(8, int(round(raw_rows / scale)))
    cols = max(8, int(round(raw_cols / scale)))
    grid: List[List[float]] = []
    for r in range(rows):
        lat = min_lat + (r / max(1, rows - 1)) * (max_lat - min_lat)
        row_vals: List[float] = []
        for c in range(cols):
            lon = min_lon + (c / max(1, cols - 1)) * (max_lon - min_lon)
            ele = sample_dem(dem, lat, lon)
            row_vals.append(round(float(ele), 1))
        grid.append(row_vals)
    return DEMGrid(
        min_lat=round(min_lat, 5),
        max_lat=round(max_lat, 5),
        min_lon=round(min_lon, 5),
        max_lon=round(max_lon, 5),
        rows=rows,
        cols=cols,
        elevations_m=grid,
    )


def compute_solar_darkness(
    lat: float,
    lon: float,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute today's local sunset (0° elevation) and civil dusk / darkness (-6° elevation)
    using the NOAA solar position equations from `src.hiking.daylight`.

    Returns UTC timestamps and remaining minutes to sunset and civil dusk from `now_ts`.
    """
    ts = float(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    _, current_el = sun_position(ts, lat, lon)

    # Scan forward in 5-minute steps up to 24 hours to locate solar noon, sunset (0°), and civil dusk (-6°),
    # then refine each crossing with binary search to ~2-second accuracy.
    step_s = 300.0
    samples: List[Tuple[float, float]] = []
    for k in range(0, int(86400 / step_s) + 2):
        t_k = ts + k * step_s
        _, el_k = sun_position(t_k, lat, lon)
        samples.append((t_k, el_k))

    def refine_crossing(t_a: float, t_b: float, target_deg: float) -> float:
        lo, hi = t_a, t_b
        for _ in range(12):
            mid = 0.5 * (lo + hi)
            _, el_m = sun_position(mid, lat, lon)
            if el_m > target_deg:
                lo = mid
            else:
                hi = mid
        return round(0.5 * (lo + hi), 1)

    sunset_ts: Optional[float] = None
    civil_dusk_ts: Optional[float] = None
    for (t0, e0), (t1, e1) in zip(samples, samples[1:]):
        if sunset_ts is None and e0 >= 0.0 > e1:
            sunset_ts = refine_crossing(t0, t1, 0.0)
        if civil_dusk_ts is None and e0 >= -6.0 > e1:
            civil_dusk_ts = refine_crossing(t0, t1, -6.0)
        if sunset_ts is not None and civil_dusk_ts is not None:
            break

    is_dark = current_el < -6.0
    is_twilight = -6.0 <= current_el < 0.0
    min_to_sunset = round((sunset_ts - ts) / 60.0, 1) if sunset_ts is not None and current_el >= 0.0 else 0.0
    min_to_darkness = (
        round((civil_dusk_ts - ts) / 60.0, 1)
        if civil_dusk_ts is not None and not is_dark
        else 0.0
    )
    return {
        "now_ts": round(ts, 1),
        "sun_elevation_deg": round(current_el, 2),
        "is_twilight": is_twilight,
        "is_dark": is_dark,
        "sunset_ts": sunset_ts,
        "civil_dusk_ts": civil_dusk_ts,
        "minutes_to_sunset": max(0.0, min_to_sunset),
        "minutes_to_darkness": max(0.0, min_to_darkness),
    }


def build_field_hike_bundle(
    route: ComposedRoute,
    pack: Optional[DestinationPack] = None,
    trails: Optional[Sequence[TrailSegment]] = None,
) -> FieldHikeBundle:
    """Build a self-contained `FieldHikeBundle` for `route` using `pack`'s trails, POIs,
    OSM features, contours, and DEM."""
    pack_trails = list(trails if trails is not None else (pack.trails if pack else []))
    raw_coords = list(route.coords_3d)
    if not raw_coords and pack_trails:
        raw_coords = list(pack_trails[0].coords_3d)

    # 1. Build 4D coordinates [[lon, lat, ele_m, cum_dist_m], ...]
    coords_4d: List[List[float]] = []
    cum_m = 0.0
    for idx, pt in enumerate(raw_coords):
        lon = round(float(pt[0]), 6)
        lat = round(float(pt[1]), 6)
        ele = round(float(pt[2]) if len(pt) > 2 else (sample_dem(pack.dem, lat, lon) if pack else 0.0), 1)
        if idx > 0:
            prev = coords_4d[-1]
            cum_m += haversine_m(prev[1], prev[0], lat, lon)
        coords_4d.append([lon, lat, ele, round(cum_m, 1)])

    if coords_4d:
        min_lat, max_lat, min_lon, max_lon = compute_bbox(coords_4d)
    elif pack:
        min_lat, max_lat, min_lon, max_lon = pack.min_lat, pack.max_lat, pack.min_lon, pack.max_lon
    else:
        min_lat, max_lat, min_lon, max_lon = 44.13, 44.17, -71.69, -71.63

    total_dist_m = round(cum_m if cum_m > 0 else float(route.distance_m), 1)
    est_min = float(route.estimated_duration_min or 0.0)
    pace_model_str = str(getattr(route, "pace_model", getattr(route, "pacing_model", "naismith")) or "naismith")
    pack_wt = float(getattr(route, "pack_weight_kg", 6.0) or 6.0)
    if est_min <= 0 and total_dist_m > 0:
        est_min = estimate_pace_minutes(
            total_dist_m,
            route.elevation_gain_m,
            route.elevation_loss_m,
            model=pace_model_str,
            pack_weight_kg=pack_wt,
        )

    # 2. Extract ordered trails taken (`legs`)
    raw_legs = trail_legs(route, pack_trails) if len(coords_4d) >= 2 else []
    bundle_legs: List[FieldTrailLeg] = []
    for idx, lg in enumerate(raw_legs):
        t = lg.trail
        eles = [float(p[2]) for p in lg.coords if len(p) > 2] or [0.0]
        bundle_legs.append(
            FieldTrailLeg(
                index=idx,
                trail_id=t.id if t else "",
                name=lg.name,
                code=t.code if t else "",
                difficulty=t.difficulty.value if t and hasattr(t.difficulty, "value") else (str(t.difficulty) if t else "moderate"),
                surface=t.surface.value if t and hasattr(t.surface, "value") else (str(t.surface) if t else "rocky"),
                condition=t.condition_status.value if t and hasattr(t.condition_status, "value") else (str(t.condition_status) if t else "open"),
                condition_note=t.condition_note if t else "",
                from_m=round(lg.from_m, 1),
                to_m=round(lg.to_m, 1),
                distance_m=round(lg.distance_m, 1),
                gain_m=round(lg.gain_m, 1),
                loss_m=round(lg.loss_m, 1),
                min_ele_m=round(min(eles), 1),
                max_ele_m=round(max(eles), 1),
                coords=[[round(float(p[0]), 6), round(float(p[1]), 6), round(float(p[2]) if len(p) > 2 else 0.0, 1)] for p in lg.coords],
            )
        )

    # 3. Extract landmarks, viewpoints, peaks, waterfalls, huts, water sources & trailheads
    b_min_lat = min_lat - BASEMAP_MARGIN_DEG
    b_max_lat = max_lat + BASEMAP_MARGIN_DEG
    b_min_lon = min_lon - BASEMAP_MARGIN_DEG
    b_max_lon = max_lon + BASEMAP_MARGIN_DEG

    landmarks: List[FieldLandmark] = []
    seen_landmarks: set[Tuple[str, int, int]] = set()

    def _add_landmark(
        lid: str,
        name: str,
        kind: str,
        lat: float,
        lon: float,
        ele_m: Optional[float],
        info: str,
        force_in_bbox: bool = False,
    ) -> None:
        if not (b_min_lat <= lat <= b_max_lat and b_min_lon <= lon <= b_max_lon):
            return
        along_m, offset_m, route_ele = _project_point_onto_route(lat, lon, coords_4d)
        on_route = offset_m <= LANDMARK_CORRIDOR_M
        if not on_route and not force_in_bbox and kind not in ("peak", "hut", "viewpoint", "waterfall", "trailhead"):
            return
        if not on_route and offset_m > 2200.0:
            return
        dedup_key = ((name or kind).strip().lower(), int(round(lat * 2000)), int(round(lon * 2000)))
        if dedup_key in seen_landmarks:
            return
        seen_landmarks.add(dedup_key)
        resolved_ele = float(ele_m) if ele_m is not None and ele_m > 0 else (
            sample_dem(pack.dem, lat, lon) if pack and pack.dem else route_ele
        )
        landmarks.append(
            FieldLandmark(
                id=lid,
                name=name or kind.replace("_", " ").title(),
                kind=kind,
                lat=round(lat, 6),
                lon=round(lon, 6),
                ele_m=round(resolved_ele, 1),
                along_m=along_m,
                offset_m=offset_m,
                on_route=on_route,
                info=info,
            )
        )

    if pack:
        for poi in pack.pois:
            _add_landmark(
                poi.id,
                poi.name,
                str(poi.kind.value if hasattr(poi.kind, "value") else poi.kind),
                poi.lat,
                poi.lon,
                poi.ele_m,
                poi.note,
                force_in_bbox=True,
            )
        for idx, feat in enumerate(pack.features):
            if feat.geom != "point" or not feat.coords or feat.kind not in LANDMARK_KINDS:
                continue
            # Unnamed parking/guideposts outside the immediate trailhead are skipped to avoid clutter
            if not feat.name and feat.kind not in ("viewpoint", "spring", "drinking_water", "shelter", "hut", "waterfall"):
                continue
            flon, flat = float(feat.coords[0][0]), float(feat.coords[0][1])
            _add_landmark(
                f"feat_{idx}",
                feat.name or feat.kind.replace("_", " ").title(),
                feat.kind,
                flat,
                flon,
                feat.ele_m,
                feat.info,
                force_in_bbox=False,
            )

    # Sort landmarks by progression along the route (`along_m`), with on-route landmarks first
    landmarks.sort(key=lambda lm: (0 if lm.on_route else 1, lm.along_m, lm.offset_m))

    # 4. Build clipped local basemap around the hike bbox
    basemap: Optional[FieldBasemap] = None
    if pack:
        clipped_contours: List[ContourLine] = []
        for c in pack.contours:
            for ch in _clip_polyline(c.coords, b_min_lon, b_min_lat, b_max_lon, b_max_lat, min_pts=4):
                clipped_contours.append(ContourLine(ele_m=c.ele_m, coords=ch))

        route_trail_ids = set(route.trail_ids) | {lg.trail_id for lg in bundle_legs if lg.trail_id}
        nearby_trails: List[Dict[str, Any]] = []
        for t in pack_trails:
            if t.id in route_trail_ids:
                continue
            if t.max_lat < b_min_lat or t.min_lat > b_max_lat or t.max_lon < b_min_lon or t.min_lon > b_max_lon:
                continue
            for ch in _clip_polyline(t.coords_3d, b_min_lon, b_min_lat, b_max_lon, b_max_lat, min_pts=2):
                nearby_trails.append(
                    {
                        "id": t.id,
                        "name": t.name,
                        "condition": t.condition_status.value if hasattr(t.condition_status, "value") else str(t.condition_status),
                        "difficulty": t.difficulty.value if hasattr(t.difficulty, "value") else str(t.difficulty),
                        "coords": ch,
                    }
                )

        water_features: List[Dict[str, Any]] = []
        for f in pack.features:
            if f.kind not in ("river", "stream", "water"):
                continue
            for ch in _clip_polyline(f.coords, b_min_lon, b_min_lat, b_max_lon, b_max_lat, min_pts=2):
                water_features.append(
                    {
                        "kind": f.kind,
                        "geom": f.geom,
                        "name": f.name,
                        "coords": ch,
                    }
                )

        basemap = FieldBasemap(
            min_lat=round(b_min_lat, 5),
            max_lat=round(b_max_lat, 5),
            min_lon=round(b_min_lon, 5),
            max_lon=round(b_max_lon, 5),
            contour_interval_m=pack.contour_interval_m or 60.96,
            contours=clipped_contours,
            dem=_slice_dem_for_bbox(pack.dem, b_min_lat, b_max_lat, b_min_lon, b_max_lon),
            nearby_trails=nearby_trails[:120],
            water=water_features[:150],
        )

    profile = [
        {"distance_m": round(ep.distance_m, 1), "ele_m": round(ep.ele_m, 1), "grade_pct": round(ep.grade_pct, 1)}
        for ep in route.elevation_profile
    ]
    if not profile and coords_4d:
        profile = [
            {"distance_m": pt[3], "ele_m": pt[2], "grade_pct": 0.0}
            for pt in coords_4d
        ]

    avg_grade = (
        sum(abs(ep["grade_pct"]) for ep in profile) / max(1, len(profile))
        if profile
        else 0.0
    )

    return FieldHikeBundle(
        id=route.id,
        name=route.name,
        pack_id=route.pack_id or (pack.id if pack else "white_mountains_nh"),
        region_name=pack.name if pack else route.pack_id,
        state_or_region=pack.state_or_region if pack else "",
        agency=pack.agency if pack else "",
        emergency_contact=(pack.emergency_contact if pack else "") or route.safety_card.ranger_dispatch or "911",
        notes=route.notes,
        created_at=route.modified or route.created,
        distance_m=total_dist_m,
        elevation_gain_m=round(route.elevation_gain_m, 1),
        elevation_loss_m=round(route.elevation_loss_m, 1),
        min_ele_m=round(route.min_ele_m, 1),
        max_ele_m=round(route.max_ele_m, 1),
        max_grade_pct=round(route.max_grade_pct, 1),
        avg_grade_pct=round(float(getattr(route, "avg_grade_pct", avg_grade)), 1),
        estimated_duration_min=round(est_min, 1),
        pacing_model=pace_model_str,
        pack_weight_kg=pack_wt,
        bbox=[round(min_lon, 5), round(min_lat, 5), round(max_lon, 5), round(max_lat, 5)],
        coords_3d=coords_4d,
        elevation_profile=profile,
        legs=bundle_legs,
        landmarks=landmarks,
        basemap=basemap,
    )
