"""Matches geotagged photos (from AegisPhotoVoyager or local imports) onto a traced hike trail.

Given a 3D trail polyline [[lon, lat, ele_m], ...] and candidate photo dicts containing
(lat, lon, date_taken, ...):
1. Computes perpendicular cross-track distance (meters) from each photo to the trail.
2. Filters photos within `corridor_m` (default 250 m).
3. Projects each matched photo onto its nearest point along the trail to determine its
   cumulative distance along the hike (`distance_along_m`) and interpolated elevation (`ele_m`).
4. Groups matched photos into calendar-date clusters so the user can pick a specific hike
   date or include all matching photos.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .geo import project_point_onto_polyline
from .models import HikePhotoPin, new_id


def _parse_date_taken_int(ts: Any) -> Optional[int]:
    if ts is None or ts == "":
        return None
    if isinstance(ts, (int, float)):
        return int(ts)
    s = str(ts).strip()
    if s.isdigit():
        return int(s)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def timestamp_to_date_str(ts: Optional[Any]) -> str:
    val = _parse_date_taken_int(ts)
    if not val:
        if isinstance(ts, str) and len(ts) >= 10:
            return ts[:10]
        return "Unknown Date"
    try:
        return datetime.fromtimestamp(val, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return "Unknown Date"


def timestamp_to_iso(ts: Optional[Any]) -> str:
    val = _parse_date_taken_int(ts)
    if not val:
        return str(ts or "")
    try:
        return datetime.fromtimestamp(val, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts or "")



def match_photos_to_trail(
    coords_3d: List[List[float]],
    candidate_photos: List[Dict[str, Any]],
    corridor_m: float = 250.0,
    date_filter: Optional[str] = None,
    session_id: str = "",
) -> List[HikePhotoPin]:
    """Return HikePhotoPin list ordered by progression along the trail (`distance_along_m`)."""
    if not coords_3d or not candidate_photos:
        return []

    matched: List[HikePhotoPin] = []
    for raw in candidate_photos:
        lat = raw.get("lat")
        lon = raw.get("lon")
        if lat is None or lon is None:
            continue
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            continue

        date_taken = raw.get("date_taken")
        if date_filter and date_filter not in ("", "all", "All Dates"):
            if timestamp_to_date_str(date_taken) != date_filter:
                continue

        cross_m, along_m, _, _, proj_ele = project_point_onto_polyline(lat_f, lon_f, coords_3d)
        if cross_m <= corridor_m:
            tags = raw.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            people = raw.get("people") or raw.get("named_people") or []
            if isinstance(people, str):
                people = [people]

            summary = str(raw.get("ai_summary") or raw.get("summary") or "").strip()
            pin = HikePhotoPin(
                id=raw.get("id") or new_id("pin"),
                session_id=session_id,
                voyager_photo_id=raw.get("voyager_photo_id"),
                photo_path=str(raw.get("photo_path") or raw.get("full_path") or ""),
                thumb_path=raw.get("thumb_path"),
                filename=str(raw.get("filename") or raw.get("name") or "photo.jpg"),
                date_taken=_parse_date_taken_int(date_taken),
                date_iso=timestamp_to_iso(date_taken),
                lat=round(lat_f, 6),
                lon=round(lon_f, 6),
                ele_m=round(proj_ele, 1),
                distance_along_m=round(along_m, 1),
                cross_track_m=round(cross_m, 1),
                camera_model=str(raw.get("camera_model") or ""),
                is_starred=bool(raw.get("is_starred")),
                ai_summary=summary,
                ai_tags=[str(t) for t in tags[:12]],
                ai_mood=str(raw.get("mood") or ""),
                people=[str(p) for p in people],
                caption=str(raw.get("caption") or summary),
            )
            matched.append(pin)

    # Sort primarily by distance along the trail, breaking ties by timestamp
    matched = dedupe_pins(matched)
    matched.sort(key=lambda p: (p.distance_along_m, p.date_taken or 0))
    return matched


def photo_keys(pin: HikePhotoPin) -> List[tuple]:
    """Identities under which two pins are the same photo: the Voyager photo id, or the same
    capture moment and spot (a duplicate import / edited copy), or file name + capture time."""
    keys: List[tuple] = []
    if pin.voyager_photo_id is not None:
        keys.append(("vid", int(pin.voyager_photo_id)))
    if pin.date_taken:
        keys.append(("shot", int(pin.date_taken), round(pin.lat, 5), round(pin.lon, 5)))
        if pin.filename:
            keys.append(("file", pin.filename.lower(), int(pin.date_taken)))
    return keys


_FORMAT_RANK = {".jpg": 3, ".jpeg": 3, ".png": 3, ".webp": 3, ".tif": 3, ".tiff": 3, ".heic": 2, ".heif": 2}


def _format_rank(pin: HikePhotoPin) -> int:
    """Prefer copies that open at full size everywhere: JPEG/PNG over HEIC over RAW/video."""
    path = (pin.photo_path or "").lower()
    ext = path[path.rfind("."):] if "." in path else ""
    return _FORMAT_RANK.get(ext, 1 if path else 0)


def dedupe_pins(pins: List[HikePhotoPin]) -> List[HikePhotoPin]:
    """Drop duplicate photos (see photo_keys). Of each set of copies the most readable file wins
    (e.g. IMG_1234.HEIC.jpg over IMG_1234.HEIC), then the richest; the winner keeps the star, AI
    notes, people and thumbnail of its copies. Groups keep the order in which they first appear."""
    def score(p: HikePhotoPin) -> tuple:
        return (_format_rank(p), 4 * bool(p.is_starred) + 2 * bool(p.thumb_path) + bool(p.ai_summary) + bool(p.people))

    groups: List[List[HikePhotoPin]] = []
    group_of: Dict[tuple, int] = {}
    for pin in pins:
        keys = photo_keys(pin)
        g = next((group_of[k] for k in keys if k in group_of), None)
        if g is None:
            g = len(groups)
            groups.append([])
        groups[g].append(pin)
        for k in keys:
            group_of.setdefault(k, g)

    out: List[HikePhotoPin] = []
    for copies in groups:
        best = max(copies, key=score)
        if len(copies) > 1:
            update: Dict[str, Any] = {}
            if not best.is_starred and any(c.is_starred for c in copies):
                update["is_starred"] = True
            for field in ("thumb_path", "ai_summary", "ai_mood", "caption"):
                if not getattr(best, field):
                    donor = next((getattr(c, field) for c in copies if getattr(c, field)), None)
                    if donor:
                        update[field] = donor
            for field in ("people", "ai_tags"):
                if not getattr(best, field):
                    donor = next((getattr(c, field) for c in copies if getattr(c, field)), None)
                    if donor:
                        update[field] = list(donor)
            if update:
                best = best.model_copy(update=update)
        out.append(best)
    return out
