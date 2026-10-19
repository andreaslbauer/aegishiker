"""Split photos matched along a route into separate hikes, one per day they were taken.

A trail (or some of its segments) may have been hiked on several occasions. Photo timestamps
(UTC, from AegisPhotoVoyager) are grouped by local calendar day; each day becomes its own hike
with real start/end times and the stretch of the route its photos cover.
"""
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Dict, List, Optional, Tuple

from .geo import haversine_m

from .models import ComposedRoute, HikePhotoPin, HikeSession

MIN_SPAN_MIN = 20.0  # shorter photo spans say little about duration: use the route estimate
PARTIAL_COVERAGE = 0.7  # photos covering less of the route than this suggest a partial hike
# (lenient: few people photograph the last stretch back to the car)


@dataclass
class HikeDay:
    day: Optional[date]  # None = photos without a timestamp
    pins: List[HikePhotoPin] = field(default_factory=list)
    first: Optional[datetime] = None
    last: Optional[datetime] = None

    @property
    def along_range_m(self) -> tuple:
        vals = [p.distance_along_m for p in self.pins]
        return (min(vals), max(vals)) if vals else (0.0, 0.0)

    def coverage(self, route_length_m: float) -> float:
        lo, hi = self.along_range_m
        return 0.0 if route_length_m <= 0 else max(0.0, min(1.0, (hi - lo) / route_length_m))


def _local(ts: int, tz: Optional[tzinfo]) -> datetime:
    return datetime.fromtimestamp(ts, tz) if tz else datetime.fromtimestamp(ts).astimezone()


def group_pins_by_day(pins: List[HikePhotoPin], tz: Optional[tzinfo] = None) -> List[HikeDay]:
    """Dated days in chronological order, then (if any) one group of undated photos.
    `tz` defaults to this computer's local time zone."""
    days: dict = {}
    undated = HikeDay(day=None)
    for pin in pins:
        if not pin.date_taken:
            undated.pins.append(pin)
            continue
        when = _local(int(pin.date_taken), tz)
        group = days.setdefault(when.date(), HikeDay(day=when.date()))
        group.pins.append(pin)
        group.first = when if group.first is None or when < group.first else group.first
        group.last = when if group.last is None or when > group.last else group.last
    out = [days[d] for d in sorted(days)]
    for g in out:
        g.pins.sort(key=lambda p: p.date_taken or 0)
    if undated.pins:
        out.append(undated)
    return out


def build_day_session(
    route: ComposedRoute,
    group: HikeDay,
    title: str,
    notes: str = "",
    fallback_day: Optional[date] = None,
    units_fmt=None,
) -> HikeSession:
    """A completed HikeSession for one day of photos (or for `fallback_day` if undated)."""
    estimate_min = float(route.estimated_duration_min or 0.0)
    if group.first and group.last:
        span_min = (group.last - group.first).total_seconds() / 60.0
        start = group.first.replace(tzinfo=None)
        duration_min = span_min if span_min >= MIN_SPAN_MIN else estimate_min
    else:
        day = group.day or fallback_day or date.today()
        start = datetime(day.year, day.month, day.day, 8, 0)
        duration_min = estimate_min
    end = start + timedelta(minutes=duration_min or 0.0)

    note_lines = [notes] if notes else []
    coverage = group.coverage(route.distance_m)
    if len(group.pins) >= 2 and coverage < PARTIAL_COVERAGE:  # one photo says nothing about extent
        lo, hi = group.along_range_m
        fmt = units_fmt or (lambda m: f"{m / 1000.0:.1f} km")
        note_lines.append(
            f"Photos from this day cover {fmt(lo)}–{fmt(hi)} of the {fmt(route.distance_m)} route "
            f"({coverage:.0%}): possibly only part of it was hiked."
        )

    return HikeSession(
        route_id=route.id,
        destination_id=route.destination_id,
        title=title,
        started_at=start.isoformat(timespec="seconds"),
        ended_at=end.isoformat(timespec="seconds"),
        status="traced_hiked",
        device_source="traced_desktop",
        actual_distance_m=route.distance_m,
        actual_elevation_gain_m=route.elevation_gain_m,
        actual_duration_min=round(duration_min, 1),
        notes="\n".join(note_lines),
    )


# ── multi-day hikes: one hike over several days, with nights at huts or tent sites ─────────

OVERNIGHT_KINDS = ("hut", "shelter", "campsite", "camp")  # map features / POIs where people sleep
OVERNIGHT_SEARCH_M = 2500.0  # places this close to the route around a night are proposed
BACKTRACK_M = 300.0  # a day's photos may start this far before the previous day's last photo


@dataclass
class Stage:
    """One day of a multi-day hike: the stretch of the route walked that day and where the night
    after it was spent (empty for the last day)."""

    day: date
    from_m: float
    to_m: float
    start: Optional[datetime] = None  # first / last photo of the day (local time)
    end: Optional[datetime] = None
    overnight: str = ""

    def to_dict(self) -> dict:
        return {
            "day": self.day.isoformat(), "from_m": round(self.from_m, 1), "to_m": round(self.to_m, 1),
            "start": self.start.isoformat(timespec="minutes") if self.start else "",
            "end": self.end.isoformat(timespec="minutes") if self.end else "",
            "overnight": self.overnight,
        }

    @staticmethod
    def from_dict(d: dict) -> "Stage":
        def dt(v):
            try:
                return datetime.fromisoformat(v) if v else None
            except ValueError:
                return None
        return Stage(date.fromisoformat(d["day"]), float(d.get("from_m", 0.0)), float(d.get("to_m", 0.0)),
                     dt(d.get("start")), dt(d.get("end")), str(d.get("overnight", "")))

    @property
    def hiking_min(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0 if self.start and self.end and self.end > self.start else 0.0


def looks_multi_day(groups: List[HikeDay]) -> bool:
    """Consecutive calendar days whose photos move on along the route (each day starting about
    where the previous one stopped): one trip with nights out, not the same trail hiked twice."""
    dated = [g for g in groups if g.day is not None and g.pins]
    if len(dated) < 2:
        return False
    for a, b in zip(dated, dated[1:]):
        if (b.day - a.day).days != 1:
            return False
        if b.along_range_m[0] < a.along_range_m[1] - BACKTRACK_M:
            return False
    return True


def overnight_candidates(
    route: ComposedRoute, along_m: float, places: List[Tuple[str, str, float, float]],
    max_m: float = OVERNIGHT_SEARCH_M,
) -> List[Tuple[str, float]]:
    """Huts, shelters and campsites near the route around `along_m`, nearest first: (name, metres)."""
    coords = route.coords_3d
    if not coords:
        return []
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine_m(a[1], a[0], b[1], b[0]))
    i = min(range(len(coords)), key=lambda k: abs(cum[k] - along_m))
    lon, lat = coords[i][0], coords[i][1]
    out: Dict[str, float] = {}
    for name, kind, plon, plat in places:
        if kind not in OVERNIGHT_KINDS or not name:
            continue
        d = haversine_m(lat, lon, plat, plon)
        if d <= max_m and d < out.get(name, math.inf):
            out[name] = d
    return sorted(out.items(), key=lambda kv: kv[1])


def plan_stages(route: ComposedRoute, groups: List[HikeDay],
                places: Optional[List[Tuple[str, str, float, float]]] = None) -> List[Stage]:
    """Split the route into one stage per photo day. A night falls between a day's last photo and
    the next day's first: at the nearest hut or campsite there, if the map has one, otherwise halfway.
    The first stage starts at the route's start and the last one ends at its end."""
    dated = sorted((g for g in groups if g.day is not None and g.pins), key=lambda g: g.day)
    if not dated:
        return []
    places = places or []
    coords = route.coords_3d
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine_m(a[1], a[0], b[1], b[0]))
    total = cum[-1] if coords else route.distance_m
    stages: List[Stage] = []
    start_m = 0.0
    for n, g in enumerate(dated):
        first = g.first.replace(tzinfo=None) if g.first else None
        last = g.last.replace(tzinfo=None) if g.last else None
        if n + 1 < len(dated):
            lo, hi = g.along_range_m[1], dated[n + 1].along_range_m[0]
            mid = (lo + max(lo, hi)) / 2.0
            night = overnight_candidates(route, mid, places)
            name, split = "", mid
            if night:
                name = night[0][0]
                # The night's place, projected onto the route between the two days' photos
                plat, plon = next((p[3], p[2]) for p in places if p[0] == name)
                ks = [k for k in range(len(coords)) if lo - 1.0 <= cum[k] <= max(lo, hi) + 1.0] or list(range(len(coords)))
                k = min(ks, key=lambda k: haversine_m(plat, plon, coords[k][1], coords[k][0]))
                split = cum[k]
            stages.append(Stage(g.day, start_m, split, first, last, name))
            start_m = split
        else:
            stages.append(Stage(g.day, start_m, total, first, last, ""))
    return stages


def stages_of(session: HikeSession) -> List[Stage]:
    """The day stages saved with a multi-day hike (empty for a one-day hike)."""
    raw = (session.conditions_logged or {}).get("stages", "")
    try:
        return [Stage.from_dict(d) for d in json.loads(raw)] if raw else []
    except (ValueError, KeyError, TypeError):
        return []


def build_multi_day_session(
    route: ComposedRoute, groups: List[HikeDay], stages: List[Stage], title: str, notes: str = "",
) -> HikeSession:
    """One completed hike spanning several days. Time on the trail is the sum of the days (first to
    last photo each day, or the pace estimate's share of a day with too few photos), not the nights."""
    estimate_min = float(route.estimated_duration_min or 0.0)
    total_m = max(1.0, stages[-1].to_m if stages else route.distance_m)
    hiking = 0.0
    for st in stages:
        share = estimate_min * max(0.0, st.to_m - st.from_m) / total_m
        hiking += st.hiking_min if st.hiking_min >= MIN_SPAN_MIN else share
    first = stages[0].start or datetime(stages[0].day.year, stages[0].day.month, stages[0].day.day, 8, 0)
    last = stages[-1].end or (datetime(stages[-1].day.year, stages[-1].day.month, stages[-1].day.day, 8, 0)
                              + timedelta(minutes=estimate_min * (stages[-1].to_m - stages[-1].from_m) / total_m))
    session = HikeSession(
        route_id=route.id,
        destination_id=route.destination_id,
        title=title,
        started_at=first.isoformat(timespec="seconds"),
        ended_at=last.isoformat(timespec="seconds"),
        status="traced_hiked",
        device_source="traced_desktop",
        actual_distance_m=route.distance_m,
        actual_elevation_gain_m=route.elevation_gain_m,
        actual_duration_min=round(hiking, 1),
        notes=notes,
    )
    session.stats.elapsed_time_s = max(0.0, (last - first).total_seconds())
    session.conditions_logged["stages"] = json.dumps([st.to_dict() for st in stages])
    return session
