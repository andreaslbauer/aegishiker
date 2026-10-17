"""Local dynamic trail filtering by physical constraints and current trailhead conditions."""
from dataclasses import dataclass
from typing import List, Optional

from .models import TrailSegment


@dataclass
class TrailFilterCriteria:
    query: str = ""
    max_distance_m: Optional[float] = None
    min_distance_m: Optional[float] = None
    max_elevation_gain_m: Optional[float] = None
    max_altitude_m: Optional[float] = None
    difficulties: Optional[List[str]] = None
    allowed_difficulties: Optional[set] = None
    excluded_conditions: Optional[set] = None
    trail_types: Optional[List[str]] = None
    dogs_only: bool = False
    water_only: bool = False
    exclude_closed: bool = True
    exclude_snow_and_mud: bool = False


def filter_trails(
    trails: List[TrailSegment],
    criteria: TrailFilterCriteria,
) -> List[TrailSegment]:
    """Filter regional trails offline according to physical constraints and condition flags."""
    q = criteria.query.strip().lower()
    results: List[TrailSegment] = []

    excluded_cond_strs = {
        (c.value if hasattr(c, "value") else str(c)).lower()
        for c in (criteria.excluded_conditions or set())
    }
    allowed_diff_strs = (
        {(d.value if hasattr(d, "value") else str(d)).lower() for d in criteria.allowed_difficulties}
        if criteria.allowed_difficulties is not None
        else None
    )

    for t in trails:
        cond_str = (t.condition_status.value if hasattr(t.condition_status, "value") else str(t.condition_status)).lower()
        diff_str = (t.difficulty.value if hasattr(t.difficulty, "value") else str(t.difficulty)).lower()

        if criteria.exclude_closed and cond_str == "closed":
            continue
        if cond_str in excluded_cond_strs:
            continue
        if criteria.exclude_snow_and_mud and cond_str in ("snow", "muddy", "high_water", "icy"):
            continue
        if allowed_diff_strs is not None and diff_str not in allowed_diff_strs:
            continue
        if criteria.dogs_only and not t.dogs_allowed:
            continue
        if criteria.water_only and not t.water_available:
            continue
        if criteria.max_distance_m is not None and t.distance_m > criteria.max_distance_m:
            continue
        if criteria.min_distance_m is not None and t.distance_m < criteria.min_distance_m:
            continue
        if criteria.max_elevation_gain_m is not None and t.elevation_gain_m > criteria.max_elevation_gain_m:
            continue
        if criteria.max_altitude_m is not None and t.max_ele_m > criteria.max_altitude_m:
            continue
        if criteria.difficulties and diff_str not in [str(d).lower() for d in criteria.difficulties]:
            continue
        if criteria.trail_types and t.trail_type not in criteria.trail_types:
            continue

        if q:
            haystack = " ".join(
                [t.name, t.code, t.description, t.condition_note, " ".join(t.tags)]
            ).lower()
            if q not in haystack:
                continue

        results.append(t)

    return results


filter_trail_segments = filter_trails

