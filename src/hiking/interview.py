"""Deterministic topic state machine for the AI Hike Brainstorm (Feature F2)."""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from .filters import TrailFilterCriteria, filter_trails
from .models import BriefField, DestinationPack, HikeBrief, TrailSegment


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    prompt_hint: str
    required: bool = True


TOPICS: List[Topic] = [
    Topic(
        key="pack_id",
        title="Destination Region",
        prompt_hint="Ask which regional destination pack they want to hike in (e.g. New Hampshire White Mountains, Yosemite, Acadia, Shenandoah, Rocky Mountain, Zion, or Mount Rainier).",
    ),
    Topic(
        key="party_and_fitness",
        title="Hiker Party & Fitness",
        prompt_hint="Ask who is hiking (solo, group, family) and their comfortable fitness level (leisurely, moderate, athletic, mountaineer).",
    ),
    Topic(
        key="max_distance_km",
        title="Distance & Time Window",
        prompt_hint="Ask what maximum distance (miles or km) or duration window they have in mind.",
    ),
    Topic(
        key="max_elevation_gain_m",
        title="Elevation Gain Ceiling",
        prompt_hint="Ask how much vertical climb (ft or m) they want to tackle today.",
    ),
    Topic(
        key="route_shape_and_scenery",
        title="Route Shape & Scenery",
        prompt_hint="Ask if they prefer a loop vs. out-and-back, and what scenery they want (alpine ridge, summit peak, waterfall, canyon, forest).",
    ),
    Topic(
        key="conditions_and_dogs",
        title="Conditions & Dogs",
        prompt_hint="Ask if they are bringing a dog and whether they want to avoid snow/ice or muddy trail conditions.",
        required=False,
    ),
]


class BriefPatchItem(BaseModel):
    value: Optional[Any] = None
    source: str = "stated"
    evidence: str = ""


class HikeBriefPatch(BaseModel):
    pack_id: Optional[BriefPatchItem] = None
    party_and_fitness: Optional[BriefPatchItem] = None
    max_distance_km: Optional[BriefPatchItem] = None
    max_elevation_gain_m: Optional[BriefPatchItem] = None
    route_shape_and_scenery: Optional[BriefPatchItem] = None
    conditions_and_dogs: Optional[BriefPatchItem] = None
    dogs_with_party: Optional[bool] = None
    avoid_snow_and_mud: Optional[bool] = None


def next_missing_topic(brief: HikeBrief) -> Optional[Topic]:
    for topic in TOPICS:
        field: BriefField = getattr(brief, topic.key)
        if field.value is None or str(field.value).strip() == "":
            if topic.required or topic.key == "conditions_and_dogs":
                return topic
    return None


def is_brief_ready(brief: HikeBrief) -> bool:
    for topic in TOPICS:
        if not topic.required:
            continue
        field: BriefField = getattr(brief, topic.key)
        if field.value is None or str(field.value).strip() == "":
            return False
    return True


def apply_patch(brief: HikeBrief, patch: HikeBriefPatch) -> HikeBrief:
    updated = brief.model_copy(deep=True)
    for topic in TOPICS:
        item: Optional[BriefPatchItem] = getattr(patch, topic.key, None)
        if item and item.value is not None and str(item.value).strip() != "":
            src = "stated" if item.source not in ("stated", "inferred") else item.source
            setattr(
                updated,
                topic.key,
                BriefField(value=item.value, source=src, evidence=item.evidence or ""),
            )
    if patch.dogs_with_party is not None:
        updated.dogs_with_party = patch.dogs_with_party
    if patch.avoid_snow_and_mud is not None:
        updated.avoid_snow_and_mud = patch.avoid_snow_and_mud
    return updated


def rank_candidates_for_brief(
    brief: HikeBrief,
    pack: DestinationPack,
    limit: int = 4,
) -> List[Dict[str, Any]]:
    """Deterministic code-based trail matching & ranking against hard constraints in `brief`."""
    max_dist_m: Optional[float] = None
    if brief.max_distance_km.value is not None:
        try:
            max_dist_m = float(brief.max_distance_km.value) * 1000.0
        except (TypeError, ValueError):
            pass

    max_gain_m: Optional[float] = None
    if brief.max_elevation_gain_m.value is not None:
        try:
            max_gain_m = float(brief.max_elevation_gain_m.value)
        except (TypeError, ValueError):
            pass

    criteria = TrailFilterCriteria(
        max_distance_m=max_dist_m,
        max_elevation_gain_m=max_gain_m,
        dogs_only=brief.dogs_with_party,
        exclude_closed=True,
        exclude_snow_and_mud=brief.avoid_snow_and_mud,
    )
    matches = filter_trails(pack.trails, criteria)
    if not matches:
        # Relax distance/elevation slightly if strict filter returned 0, while keeping hard safety/dog rules
        relaxed = TrailFilterCriteria(
            dogs_only=brief.dogs_with_party,
            exclude_closed=True,
            exclude_snow_and_mud=brief.avoid_snow_and_mud,
        )
        matches = filter_trails(pack.trails, relaxed)

    scenery_pref = str(brief.route_shape_and_scenery.value or "").lower()

    def score_trail(t: TrailSegment) -> float:
        s = 100.0
        if max_dist_m:
            s -= abs(t.distance_m - 0.8 * max_dist_m) / 250.0
        if max_gain_m:
            s -= abs(t.elevation_gain_m - 0.75 * max_gain_m) / 50.0
        if "loop" in scenery_pref and t.trail_type == "loop":
            s += 25.0
        for tag in t.tags:
            if tag.lower() in scenery_pref:
                s += 18.0
        if t.condition_status == "open":
            s += 10.0
        return s

    ranked = sorted(matches, key=score_trail, reverse=True)[:limit]
    out: List[Dict[str, Any]] = []
    for t in ranked:
        reasons = [f"{t.difficulty.title()} {t.trail_type.replace('_', ' ')}"]
        if t.dogs_allowed and brief.dogs_with_party:
            reasons.append("Dog-friendly")
        if t.water_available:
            reasons.append("Water source on trail")
        if t.condition_status == "open":
            reasons.append("Condition: Open")
        else:
            reasons.append(f"Condition: {t.condition_status}")
        out.append({"trail": t, "reasons": reasons})
    return out


@dataclass(frozen=True)
class InterviewQuestionOption:
    id: str
    prompt: str
    options: List[str]
    default: str


INTERVIEW_QUESTIONS: List[InterviewQuestionOption] = [
    InterviewQuestionOption(
        id="party_and_fitness",
        prompt="1. Hiker Party & Fitness Level:",
        options=["moderate", "leisurely", "athletic", "mountaineer", "family"],
        default="moderate",
    ),
    InterviewQuestionOption(
        id="max_distance_km",
        prompt="2. Target Maximum Distance (km):",
        options=["6.0", "10.0", "15.0", "22.0"],
        default="15.0",
    ),
    InterviewQuestionOption(
        id="max_elevation_gain_m",
        prompt="3. Maximum Elevation Gain (m):",
        options=["300", "600", "1000", "1500"],
        default="1000",
    ),
    InterviewQuestionOption(
        id="route_shape_and_scenery",
        prompt="4. Preferred Route Shape & Scenery:",
        options=["loop, ridge, summit", "out_and_back, waterfall", "loop, lake, forest", "alpine, peak, canyon"],
        default="loop, ridge, summit",
    ),
    InterviewQuestionOption(
        id="conditions_and_dogs",
        prompt="5. Dogs & Trail Surface Rules:",
        options=["Open & dry trails preferred", "With dog", "All open trails"],
        default="Open & dry trails preferred",
    ),
    InterviewQuestionOption(
        id="pack_and_start_time",
        prompt="6. Target Trailhead Start Time:",
        options=["07:00", "08:00", "09:30", "11:00"],
        default="08:00",
    ),
]

