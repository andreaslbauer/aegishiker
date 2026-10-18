"""Split photos matched along a route into separate hikes, one per day they were taken.

A trail (or some of its segments) may have been hiked on several occasions. Photo timestamps
(UTC, from AegisPhotoVoyager) are grouped by local calendar day; each day becomes its own hike
with real start/end times and the stretch of the route its photos cover.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import List, Optional

from .models import ComposedRoute, HikePhotoPin, HikeSession

MIN_SPAN_MIN = 20.0  # shorter photo spans say little about duration: use the route estimate
PARTIAL_COVERAGE = 0.8  # photos covering less of the route than this suggest a partial hike


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
