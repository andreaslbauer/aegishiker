"""Display helpers for recorded hikes: title, date, trails walked and the people in its photos."""
import re
from collections import Counter
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from .models import ComposedRoute, HikePhotoPin, HikeSession, TrailSegment

_PLACEHOLDER_PERSON = re.compile(r"^(person|unknown( person)?|face|cluster)[\s_#-]*\d*$", re.IGNORECASE)


def is_placeholder_person(name: str) -> bool:
    """AegisPhotoVoyager names unidentified face clusters 'Person 1234' / 'Unknown Person'."""
    return bool(_PLACEHOLDER_PERSON.match(name.strip()))


def real_people(names) -> List[str]:
    return [n for n in names if n and not is_placeholder_person(n)]


_GENERIC_TITLES = {"", "Recorded Hike", "Photo Hike Session", "Traced Trail Hike"}


def hike_title(session: HikeSession, route: Optional[ComposedRoute] = None) -> str:
    """The hike's own (possibly user-given) title; older hikes fall back to the route name."""
    title = (session.title or "").strip()
    if title in _GENERIC_TITLES and route is not None:
        return route.name or "Recorded Hike"
    return title or "Recorded Hike"


def hiked_route(session: HikeSession, saved: Optional[ComposedRoute] = None) -> Optional[ComposedRoute]:
    """The route a hike followed: its saved route, else rebuilt from the track stored with the hike
    (the route may have been deleted since; never substitute some other route)."""
    if saved is not None:
        return saved
    for data in (session.route_snapshot, session.interactive_map_data):
        if data and len(data.get("coords_3d") or []) >= 2:
            return ComposedRoute(
                id=session.route_id or f"rte_{session.id}",
                pack_id=session.pack_id or data.get("pack_id") or "",
                name=data.get("name") or data.get("title") or session.title or "Recorded Hike",
                **{k: data[k] for k in ("coords_3d", "distance_m", "elevation_gain_m", "elevation_loss_m",
                                        "min_ele_m", "max_ele_m") if data.get(k) is not None},
            )
    return None


def hike_start(session: HikeSession) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(session.started_at)
    except (TypeError, ValueError):
        return None


def hike_date_label(session: HikeSession) -> str:
    """'Thu Aug 14, 2025 · 7:13 AM – 10:33 AM' (times only when known)."""
    start = hike_start(session)
    if start is None:
        return (session.started_at or "")[:10]
    try:
        last = datetime.fromisoformat(session.ended_at) if session.ended_at else None
    except ValueError:
        last = None
    if last and last.date() > start.date():  # a multi-day hike
        days = (last.date() - start.date()).days + 1
        first = f"{start:%a %b %d}" + (f", {start:%Y}" if start.year != last.year else "")
        return f"{first} – {last:%a %b %d, %Y} · {days} days"
    label = f"{start:%a %b %d, %Y}"
    if start.hour or start.minute:
        label += " · " + f"{start:%I:%M %p}".lstrip("0")
        try:
            end = datetime.fromisoformat(session.ended_at) if session.ended_at else None
        except ValueError:
            end = None
        if end and end.date() == start.date() and end > start:
            label += " – " + f"{end:%I:%M %p}".lstrip("0")
    return label


def hike_trail_names(route: Optional[ComposedRoute], trails_by_id: Dict[str, TrailSegment]) -> List[str]:
    """Trails walked, in order: the segment list of a segment-built hike, else its trail ids."""
    if route is None:
        return []
    if route.notes.startswith("Segments: "):
        names = [n.strip() for n in route.notes[len("Segments: "):].split("→") if n.strip()]
        out: List[str] = []
        for n in names:
            if not out or out[-1] != n:
                out.append(n)
        return out
    names = [trails_by_id[t].name for t in route.trail_ids if t in trails_by_id]
    return names or ([route.name] if route.name else [])


def hike_people(pins: Iterable[HikePhotoPin]) -> List[str]:
    """People recognised in the hike's photos, most frequently photographed first."""
    counts: Counter = Counter()
    first_seen: Dict[str, int] = {}
    for i, pin in enumerate(pins):
        for name in real_people(pin.people):
            counts[name] += 1
            first_seen.setdefault(name, i)
    return sorted(counts, key=lambda n: (-counts[n], first_seen[n]))


def people_label(names: List[str], limit: int = 4) -> str:
    if not names:
        return ""
    shown = ", ".join(names[:limit])
    return shown + (f" +{len(names) - limit}" if len(names) > limit else "")
