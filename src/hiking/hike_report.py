"""Everything worth telling about one recorded hike, Qt-free: the trails walked (with how far on
each), where it started and ended, when and for how long, who came along, the weather, and the
numbers. Used by the hike website export and the desktop hike views.

Companions and weather are kept in the hike's `conditions_logged` dictionary (see DETAIL_KEYS).
"""
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .elevation import compute_gain_loss
from .geo import haversine_m
from .hike_days import stages_of
from .hike_info import hike_date_label, hike_people, hike_start, hike_title, real_people
from .models import ComposedRoute, DestinationPack, HikePhotoPin, HikeSession, TrailSegment

# Free-form hike details stored in HikeSession.conditions_logged
DETAIL_KEYS = ("companions", "weather", "temp_low_c", "temp_high_c", "precip_mm", "wind_kmh", "weather_source",
               "weather_hourly")  # weather_hourly: JSON rows from core.weather.hourly_rows, for the replays

ON_TRAIL_M = 30.0  # a route vertex this close to a trail is walking on it
MIN_LEG_M = 60.0  # shorter runs (junction jitter, overlapping trails) merge into their neighbours
PLACE_MAX_M = 400.0  # start/end named after a place this close
PLACE_KINDS = ("trailhead", "parking", "peak", "hut", "shelter", "viewpoint", "waterfall", "campsite", "ranger_station")


# ── hike details (companions, weather) ─────────────────────────────────────────


def hike_details(session: Optional[HikeSession]) -> Dict[str, str]:
    raw = dict(session.conditions_logged) if session else {}
    return {k: str(raw.get(k, "") or "") for k in DETAIL_KEYS}


def set_hike_details(session: HikeSession, details: Dict[str, str]) -> None:
    for key in DETAIL_KEYS:
        val = str(details.get(key, "") or "").strip()
        if val:
            session.conditions_logged[key] = val
        else:
            session.conditions_logged.pop(key, None)


def split_names(text: str) -> List[str]:
    """'Anna, Ben and Carla' / 'Anna; Ben' → ['Anna', 'Ben', 'Carla']."""
    parts = re.split(r"\s*(?:,|;|\n|\band\b|&)\s*", text or "")
    return [p.strip() for p in parts if p.strip()]


def hike_companions(session: Optional[HikeSession], pins: Iterable[HikePhotoPin]) -> List[str]:
    """People on the hike: those entered by hand first, then those recognised in the photos."""
    out: List[str] = []
    for name in split_names(hike_details(session).get("companions", "")) + hike_people(pins):
        if name.casefold() not in {n.casefold() for n in out}:
            out.append(name)
    return real_people(out)


# ── trails walked ──────────────────────────────────────────────────────────────


@dataclass
class TrailLeg:
    """A stretch of the hike on one trail (or on unnamed paths/roads when `trail` is None)."""

    name: str
    trail: Optional[TrailSegment]
    from_m: float
    to_m: float
    gain_m: float = 0.0
    loss_m: float = 0.0
    coords: List[List[float]] = field(default_factory=list)

    @property
    def distance_m(self) -> float:
        return self.to_m - self.from_m


def _cumulative(coords: Sequence[Sequence[float]]) -> List[float]:
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine_m(a[1], a[0], b[1], b[0]))
    return cum


class _TrailIndex:
    """Nearest-trail lookup for route vertices (a coarse grid over the trails' vertices)."""

    CELL = 0.002  # degrees (~200 m)

    def __init__(self, trails: Sequence[TrailSegment]) -> None:
        self.trails = list(trails)
        self.grid: Dict[Tuple[int, int], List[int]] = {}
        for i, t in enumerate(self.trails):
            for p in t.coords_3d:
                cell = (int(p[0] / self.CELL), int(p[1] / self.CELL))
                bucket = self.grid.setdefault(cell, [])
                if not bucket or bucket[-1] != i:
                    bucket.append(i)

    def near(self, lon: float, lat: float, tol_m: float) -> Dict[int, float]:
        """trail index -> distance (m) for trails within tol_m of the point."""
        cx, cy = int(lon / self.CELL), int(lat / self.CELL)
        cand = {i for dx in (-1, 0, 1) for dy in (-1, 0, 1) for i in self.grid.get((cx + dx, cy + dy), ())}
        out = {}
        for i in cand:
            d = dist_to_line_m(lat, lon, self.trails[i].coords_3d)
            if d <= tol_m:
                out[i] = d
        return out


def dist_to_line_m(lat: float, lon: float, pts: Sequence[Sequence[float]]) -> float:
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
    if len(pts) == 1:
        best = haversine_m(lat, lon, pts[0][1], pts[0][0])
    return best


def _segment_order(route: ComposedRoute) -> List[str]:
    """Trail names in walking order as the segment builder recorded them ('Segments: A → B')."""
    if route.notes.startswith("Segments: "):
        return [n.strip() for n in route.notes[len("Segments: "):].split("→") if n.strip()]
    return []


def trail_legs(route: Optional[ComposedRoute], trails: Sequence[TrailSegment]) -> List[TrailLeg]:
    """Split the route into the trails it follows, in walking order, with the distance, climb and
    descent on each. Where trails share a stretch, the one the hike was built from wins."""
    if route is None or len(route.coords_3d) < 2:
        return []
    coords = route.coords_3d
    cum = _cumulative(coords)
    preferred = [t for t in trails if t.id in set(route.trail_ids)] or list(trails)
    index = _TrailIndex(preferred)
    by_name: Dict[str, List[int]] = {}
    for i, t in enumerate(index.trails):
        by_name.setdefault(t.name, []).append(i)
    order = _segment_order(route)
    pos = 0  # position in `order`

    labels: List[Optional[int]] = []
    current: Optional[int] = None
    for lon, lat, *_ in coords:
        near = index.near(lon, lat, ON_TRAIL_M)
        choice: Optional[int] = None
        if current is not None and current in near:
            choice = current  # keep following the same trail while possible
        else:
            # the next trails in the recorded segment order, then simply the nearest
            for k in range(pos, min(len(order), pos + 4)):
                hit = [i for i in by_name.get(order[k], []) if i in near]
                if hit:
                    choice, pos = hit[0], k
                    break
            if choice is None and near:
                choice = min(near, key=near.get)
        labels.append(choice)
        current = choice

    # Runs of the same label → legs; tiny runs are absorbed by the previous leg
    runs: List[List[int]] = []  # [label or -1, start_idx, end_idx]
    for i, lab in enumerate(labels):
        key = -1 if lab is None else lab
        if runs and runs[-1][0] == key:
            runs[-1][2] = i
        else:
            runs.append([key, max(0, i - 1) if runs else 0, i])
    merged: List[List[int]] = []
    for run in runs:
        length = cum[run[2]] - cum[run[1]]
        if merged and (length < MIN_LEG_M or merged[-1][0] == run[0]):
            merged[-1][2] = run[2]
        else:
            merged.append(list(run))
    if len(merged) > 1 and cum[merged[0][2]] - cum[merged[0][1]] < MIN_LEG_M:
        merged[1][1] = merged[0][1]
        merged.pop(0)

    legs: List[TrailLeg] = []
    for key, a, b in merged:
        trail = index.trails[key] if key >= 0 else None
        pts = [list(p) for p in coords[a:b + 1]]
        gain, loss = compute_gain_loss([p[2] if len(p) > 2 else 0.0 for p in pts])
        name = trail.name if trail else "Connector (unnamed path or road)"
        if legs and legs[-1].name == name:
            prev = legs[-1]
            prev.to_m, prev.gain_m, prev.loss_m = cum[b], prev.gain_m + gain, prev.loss_m + loss
            prev.coords.extend(pts[1:])
            continue
        legs.append(TrailLeg(name, trail, cum[a], cum[b], gain, loss, pts))
    return legs


def leg_at(legs: Sequence[TrailLeg], along_m: float) -> Optional[TrailLeg]:
    for leg in legs:
        if leg.from_m - 1.0 <= along_m <= leg.to_m + 1.0:
            return leg
    return legs[-1] if legs else None


# ── places ─────────────────────────────────────────────────────────────────────


def named_places(pack: Optional[DestinationPack]) -> List[Tuple[str, str, float, float]]:
    """(name, kind, lon, lat) of the region's named points of interest and map features."""
    if pack is None:
        return []
    out = [(p.name, str(p.kind), p.lon, p.lat) for p in pack.pois if p.name]
    out += [(f.name, f.kind, f.coords[0][0], f.coords[0][1])
            for f in pack.features if f.geom == "point" and f.name and f.kind in PLACE_KINDS and f.coords]
    return out


def place_near(places: Sequence[Tuple[str, str, float, float]], lon: float, lat: float,
               max_m: float = PLACE_MAX_M) -> Optional[str]:
    best, best_d = None, max_m
    for name, _kind, plon, plat in places:
        d = haversine_m(lat, lon, plat, plon)
        if d < best_d:
            best, best_d = name, d
    return best


# ── summary ────────────────────────────────────────────────────────────────────


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def _shape(route: ComposedRoute) -> str:
    if len(route.coords_3d) < 3:
        return "segment"
    a, b = route.coords_3d[0], route.coords_3d[-1]
    if haversine_m(a[1], a[0], b[1], b[0]) > 150.0:
        return "one_way"
    return "out_and_back" if route.trail_type == "out_and_back" else "loop"


def _photo_span(pins: Sequence[HikePhotoPin]) -> Tuple[Optional[datetime], Optional[datetime]]:
    stamps = sorted(int(p.date_taken) for p in pins if p.date_taken)
    if not stamps:
        return None, None
    return datetime.fromtimestamp(stamps[0]), datetime.fromtimestamp(stamps[-1])


def build_hike_summary(
    session: Optional[HikeSession],
    route: ComposedRoute,
    pack: Optional[DestinationPack],
    pins: Sequence[HikePhotoPin],
) -> Dict:
    """A JSON-ready description of the hike: title, when, where, who, weather, numbers and trails."""
    trails = list(pack.trails) if pack else []
    legs = trail_legs(route, trails)
    places = named_places(pack)
    start_pt, end_pt = route.coords_3d[0], route.coords_3d[-1]
    start_place = place_near(places, start_pt[0], start_pt[1])
    end_place = place_near(places, end_pt[0], end_pt[1])
    high = max(route.coords_3d, key=lambda c: c[2] if len(c) > 2 else 0.0)
    high_place = place_near(places, high[0], high[1], 250.0)

    start = hike_start(session) if session else None
    end = _parse(session.ended_at) if session else None
    first_photo, last_photo = _photo_span(pins)
    elapsed_min = (end - start).total_seconds() / 60.0 if start and end and end > start else 0.0
    if session and not elapsed_min:
        elapsed_min = session.actual_duration_min
    stages = stages_of(session) if session else []
    if len(stages) > 1:  # several days: time walking, not the nights in between
        elapsed_min = session.actual_duration_min
    days = []
    for st in stages:
        pts = [c for c, d in zip(route.coords_3d, _cumulative(route.coords_3d)) if st.from_m - 1.0 <= d <= st.to_m + 1.0]
        gain, loss = compute_gain_loss([c[2] if len(c) > 2 else 0.0 for c in pts]) if len(pts) > 1 else (0.0, 0.0)
        days.append({
            "day": st.day.isoformat(), "from_m": round(st.from_m, 1), "to_m": round(st.to_m, 1),
            "distance_m": round(st.to_m - st.from_m, 1), "gain_m": gain, "loss_m": loss,
            "start_iso": st.start.isoformat(timespec="minutes") if st.start else "",
            "end_iso": st.end.isoformat(timespec="minutes") if st.end else "",
            "hiking_min": round(st.hiking_min, 1), "overnight": st.overnight,
            "from_place": place_near(places, pts[0][0], pts[0][1], 300.0) or "" if pts else "",
            "to_place": st.overnight or (place_near(places, pts[-1][0], pts[-1][1], 300.0) or "" if pts else ""),
        })

    details = hike_details(session)
    weather = {
        "summary": details["weather"],
        "temp_low_c": _num(details["temp_low_c"]),
        "temp_high_c": _num(details["temp_high_c"]),
        "precip_mm": _num(details["precip_mm"]),
        "wind_kmh": _num(details["wind_kmh"]),
        "source": details["weather_source"],
    }
    grades = [abs(p.grade_pct) for p in route.elevation_profile]
    return {
        "title": hike_title(session, route) if session else route.name,
        "route_name": route.name,
        "date_label": hike_date_label(session) if session else "",
        "date_iso": (session.started_at or "")[:10] if session else "",
        "start_iso": start.isoformat(timespec="minutes") if start and (start.hour or start.minute) else "",
        "end_iso": end.isoformat(timespec="minutes") if end and (end.hour or end.minute) else "",
        "first_photo_iso": first_photo.isoformat(timespec="minutes") if first_photo else "",
        "last_photo_iso": last_photo.isoformat(timespec="minutes") if last_photo else "",
        "elapsed_min": round(elapsed_min, 1),
        "days": days if len(days) > 1 else [],
        "estimated_min": round(float(route.estimated_duration_min or 0.0), 1),
        "area": pack.name if pack else route.pack_id,
        "region": pack.region if pack else "",
        "area_summary": pack.summary if pack else "",
        "agency": pack.agency if pack else "",
        "attribution": pack.map_attribution if pack else "",
        "shape": _shape(route),
        "start_place": start_place or "",
        "end_place": end_place or "",
        "high_place": high_place or "",
        "distance_m": round(route.distance_m, 1),
        "gain_m": round(route.elevation_gain_m, 1),
        "loss_m": round(route.elevation_loss_m, 1),
        "min_ele_m": round(route.min_ele_m, 1),
        "max_ele_m": round(route.max_ele_m, 1),
        "max_grade_pct": round(max(grades, default=0.0), 1),
        "avg_grade_pct": round(sum(grades) / len(grades), 1) if grades else 0.0,
        "people": hike_companions(session, pins),
        "weather": weather,
        "notes": (session.notes if session else route.notes if not route.notes.startswith("Segments: ") else "").strip(),
        "photo_count": len(pins),
        "trails": [
            {
                "name": leg.name,
                "named": leg.trail is not None,
                "from_m": round(leg.from_m, 1),
                "to_m": round(leg.to_m, 1),
                "distance_m": round(leg.distance_m, 1),
                "gain_m": round(leg.gain_m, 1),
                "loss_m": round(leg.loss_m, 1),
                "difficulty": leg.trail.difficulty.value if leg.trail else "",
                "surface": leg.trail.surface if leg.trail else "",
                "description": leg.trail.description if leg.trail else "",
                "from_place": place_near(places, leg.coords[0][0], leg.coords[0][1], 250.0) or "",
                "to_place": place_near(places, leg.coords[-1][0], leg.coords[-1][1], 250.0) or "",
            }
            for leg in legs
        ],
    }


def _num(text: str) -> Optional[float]:
    try:
        return float(text) if str(text).strip() else None
    except ValueError:
        return None
