"""Plan the photo stops of a hike replay (Qt-free).

Photos taken close together (along the trail or in time) become one stop that cross-fades
through them, instead of stopping the replay for every single photo.
"""
from dataclasses import dataclass, field
from typing import List

from .models import HikePhotoPin

GROUP_GAP_M = 100.0  # photos this close along the trail share a stop…
GROUP_GAP_S = 180.0  # …or taken within this many seconds of each other
GROUP_MAX_SPAN_M = 400.0  # a stop never covers more of the trail than this
MAX_PER_STOP = 12  # photos shown per stop; the rest are counted ("+N more here")


@dataclass
class StopPlan:
    shown: List[HikePhotoPin]
    hidden: int = 0
    all_pins: List[HikePhotoPin] = field(default_factory=list)

    @property
    def along_m(self) -> float:
        return min(p.distance_along_m for p in self.all_pins)


def _pick(pins: List[HikePhotoPin], cap: int) -> List[HikePhotoPin]:
    """Starred photos first, the rest spread evenly; shown in the order they were taken."""
    if len(pins) <= cap:
        return list(pins)
    starred = [p for p in pins if p.is_starred][:cap]
    rest = [p for p in pins if not p.is_starred]
    need = cap - len(starred)
    step = len(rest) / need if need > 0 else 0
    chosen = starred + [rest[int(i * step)] for i in range(need)]
    order = {id(p): i for i, p in enumerate(pins)}
    return sorted(chosen, key=lambda p: order[id(p)])


def plan_photo_stops(pins: List[HikePhotoPin], cap: int = MAX_PER_STOP) -> List[StopPlan]:
    """Group pins (in trail order) into stops."""
    ordered = sorted(pins, key=lambda p: (p.distance_along_m, p.date_taken or 0))
    groups: List[List[HikePhotoPin]] = []
    for pin in ordered:
        if groups:
            g = groups[-1]
            prev = g[-1]
            near_m = pin.distance_along_m - prev.distance_along_m <= GROUP_GAP_M
            near_s = bool(pin.date_taken and prev.date_taken) and abs(pin.date_taken - prev.date_taken) <= GROUP_GAP_S
            within_span = pin.distance_along_m - g[0].distance_along_m <= GROUP_MAX_SPAN_M
            if (near_m or near_s) and within_span:
                g.append(pin)
                continue
        groups.append([pin])
    plans = []
    for g in groups:
        in_time = sorted(g, key=lambda p: (p.date_taken or 0, p.distance_along_m))
        shown = _pick(in_time, cap)
        plans.append(StopPlan(shown=shown, hidden=len(g) - len(shown), all_pins=g))
    return plans
