"""Plan the photo stops of a hike replay (Qt-free).

Photos taken close together (along the trail or in time) become one stop that cross-fades
through them, instead of stopping the replay for every single photo. Bursts of near-identical
shots of one spot can be collapsed to their best photo first (collapse_similar).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .geo import haversine_m
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


# ── Similar photos: one per spot ──────────────────────────────────────────────

SIMILAR_RADIUS_M = 40.0  # photos of one spot are taken this close together…
SIMILAR_GAP_S = 900.0  # …within this many seconds of each other…
SIMILAR_MIN_COSINE = 0.95  # …and look alike: CLIP embeddings this close (Voyager)…
SIMILAR_MAX_PHASH = 10  # …or perceptual hashes this few bits apart


@dataclass
class VisualSignature:
    """What AegisPhotoVoyager knows about how a photo looks."""
    embedding: Optional[Sequence[float]] = None  # unit-length CLIP vector
    phash: Optional[int] = None  # 64-bit perceptual hash
    quality: Optional[int] = None  # overall quality score (higher is better)


def _alike(a: VisualSignature, b: VisualSignature) -> bool:
    if a.embedding is not None and b.embedding is not None and len(a.embedding) == len(b.embedding):
        return sum(x * y for x, y in zip(a.embedding, b.embedding)) >= SIMILAR_MIN_COSINE
    if a.phash is not None and b.phash is not None:
        return bin(a.phash ^ b.phash).count("1") <= SIMILAR_MAX_PHASH
    return False  # nothing to compare: keep both


def collapse_similar(pins: List[HikePhotoPin], signatures: Dict[int, VisualSignature]) -> List[HikePhotoPin]:
    """Keep one photo of each cluster of similar photos taken at the same spot: the starred one,
    else the best by Voyager's quality score, else the richest. Photos Voyager has no visual data
    for are always kept. Order of the input is kept."""
    def sig(p: HikePhotoPin) -> Optional[VisualSignature]:
        return signatures.get(p.voyager_photo_id) if p.voyager_photo_id is not None else None

    ordered = sorted(pins, key=lambda p: (p.date_taken or 0, p.distance_along_m))
    clusters: List[List[HikePhotoPin]] = []
    for pin in ordered:
        s = sig(pin)
        home = None
        if s is not None:
            # compare with each cluster's first photo so a slow pan doesn't chain into one cluster
            for c in reversed(clusters):
                seed, seed_sig = c[0], sig(c[0])
                if pin.date_taken and seed.date_taken and pin.date_taken - seed.date_taken > SIMILAR_GAP_S:
                    break
                if seed_sig is not None and haversine_m(pin.lat, pin.lon, seed.lat, seed.lon) <= SIMILAR_RADIUS_M \
                        and _alike(s, seed_sig):
                    home = c
                    break
        if home is None:
            clusters.append([pin])
        else:
            home.append(pin)

    def score(p: HikePhotoPin) -> tuple:
        q = sig(p).quality if sig(p) is not None else None
        return (bool(p.is_starred), q if q is not None else -1, bool(p.ai_summary) + bool(p.caption))

    keep = {id(max(c, key=score)) for c in clusters}
    return [p for p in pins if id(p) in keep]
