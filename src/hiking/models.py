"""Pydantic data models for AegisHiker.

All coordinates in 3D polylines follow the GeoJSON convention: [lon, lat, ele_m],
with convenience `.coordinates` properties returning [(lat, lon, ele_m), ...] for GUI/lat-lon callers.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple
import uuid

from pydantic import BaseModel, Field, model_validator


def new_id(prefix: str = "hk") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class TrailDifficulty(str, Enum):
    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"
    STRENUOUS = "strenuous"
    EXPERT = "expert"


class TrailCondition(str, Enum):
    OPEN = "open"
    GOOD = "good"
    CAUTION = "caution"
    MUDDY = "muddy"
    ICY = "icy"
    SNOW = "snow"
    HIGH_WATER = "high_water"
    OVERGROWN = "overgrown"
    CLOSED = "closed"


DifficultyLevel = TrailDifficulty
SurfaceType = Literal["paved", "packed_dirt", "gravel", "rocky", "scramble", "alpine"]
TrailShape = Literal["loop", "out_and_back", "segment", "connector"]
ConditionStatus = TrailCondition
POIKind = Literal[
    "trailhead",
    "peak",
    "waterfall",
    "water",
    "viewpoint",
    "camp",
    "hut",
    "junction",
    "ranger",
]


class POI(BaseModel):
    id: str = Field(default_factory=lambda: new_id("poi"))
    pack_id: str = ""
    name: str
    kind: POIKind = "viewpoint"
    lat: float
    lon: float
    ele_m: float = 0.0
    note: str = ""

    @property
    def destination_id(self) -> str:
        return self.pack_id


class TrailSegment(BaseModel):
    id: str = Field(default_factory=lambda: new_id("trl"))
    pack_id: str = ""
    name: str
    code: str = ""
    difficulty: TrailDifficulty = TrailDifficulty.MODERATE
    surface: SurfaceType = "packed_dirt"
    trail_type: TrailShape = "segment"
    distance_m: float = 0.0
    elevation_gain_m: float = 0.0
    elevation_loss_m: float = 0.0
    min_ele_m: float = 0.0
    max_ele_m: float = 0.0
    max_grade_pct: float = 0.0
    dogs_allowed: bool = True
    water_available: bool = False
    condition_status: TrailCondition = TrailCondition.OPEN
    condition_note: str = ""
    condition_updated: str = ""
    min_lat: float = 0.0
    max_lat: float = 0.0
    min_lon: float = 0.0
    max_lon: float = 0.0
    coords_3d: List[List[float]] = Field(default_factory=list)  # [[lon, lat, ele_m], ...]
    tags: List[str] = Field(default_factory=list)
    description: str = ""

    @property
    def condition(self) -> TrailCondition:
        return self.condition_status

    @property
    def length_m(self) -> float:
        return self.distance_m

    @property
    def destination_id(self) -> str:
        return self.pack_id

    @property
    def coordinates(self) -> List[Tuple[float, float, float]]:
        return [(pt[1], pt[0], pt[2] if len(pt) > 2 else 0.0) for pt in self.coords_3d]


class ContourLine(BaseModel):
    ele_m: float
    coords: List[List[float]] = Field(default_factory=list)  # [[lon, lat], ...]


class DEMGrid(BaseModel):
    """Compact regional Digital Elevation Model grid for bilinear interpolation."""

    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    rows: int
    cols: int
    elevations_m: List[List[float]] = Field(default_factory=list)


class MapFeature(BaseModel):
    """Basemap detail downloaded from OpenStreetMap (paths, water, peaks, shelters, ...).

    `geom` is 'point', 'line' or 'area'; `coords` is [[lon, lat], ...] (one vertex for points,
    closed outer ring for areas). `info` holds short hiker-relevant notes (SAC scale, fee, capacity).
    """

    kind: str
    geom: Literal["point", "line", "area"] = "point"
    name: str = ""
    coords: List[List[float]] = Field(default_factory=list)
    ele_m: Optional[float] = None
    info: str = ""


class DestinationPack(BaseModel):
    id: str
    name: str
    state_or_region: str = ""
    agency: str = "USGS / NPS / USFS & OpenStreetMap Contributors"
    license: str = "Public Domain (USGS 3DEP / Federal Lands) & ODbL (OpenStreetMap)"
    summary: str = ""
    min_lat: float = 0.0
    max_lat: float = 0.0
    min_lon: float = 0.0
    max_lon: float = 0.0
    base_ele_m: float = 0.0
    max_ele_m: float = 0.0
    emergency_contact: str = "911"
    pack_version: str = "1.0"
    installed_at: str = Field(default_factory=now_iso)
    trails: List[TrailSegment] = Field(default_factory=list)
    pois: List[POI] = Field(default_factory=list)
    contours: List[ContourLine] = Field(default_factory=list)
    contour_interval_m: float = 0.0
    dem: Optional[DEMGrid] = None
    features: List[MapFeature] = Field(default_factory=list)
    map_attribution: str = ""

    @property
    def region(self) -> str:
        return self.state_or_region

    @property
    def base_elevation_m(self) -> float:
        return self.base_ele_m

    @property
    def destination(self) -> "DestinationPack":
        return self


Destination = DestinationPack


class RouteWaypoint(BaseModel):
    id: str = Field(default_factory=lambda: new_id("wp"))
    name: str
    lat: float
    lon: float
    ele_m: float = 0.0
    kind: str = "waypoint"  # 'start' | 'waypoint' | 'junction' | 'summit' | 'water' | 'end'
    snapped_trail_id: Optional[str] = None
    note: str = ""


class ElevationPoint(BaseModel):
    distance_m: float
    ele_m: float
    grade_pct: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    label: str = ""


ElevationSample = ElevationPoint


class PaceEstimate(BaseModel):
    naismith_seconds: float = 0.0
    langmuir_seconds: float = 0.0
    tobler_seconds: float = 0.0
    recommended_seconds: float = 0.0
    water_liters: float = 0.0
    calories_kcal: float = 0.0
    turnaround_time_str: str = ""
    estimated_finish_str: str = ""


class GearItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("gear"))
    route_id: str = ""
    category: str
    name: str
    weight_g: float = 100.0
    essential: bool = True
    checked: bool = False
    reason: str = ""


class SafetyCard(BaseModel):
    hiker_names: str = "Trail Hiker"
    emergency_contact_name: str = ""
    emergency_contact_phone: str = ""
    vehicle_description: str = ""
    trailhead_parking: str = ""
    start_time: str = "08:00"
    expected_return_time: str = "15:00"
    panic_overdue_time: str = "18:00"
    bailout_route: str = "Retrace primary trail to trailhead"
    ranger_dispatch: str = "911"
    notes: str = ""


class ComposedRoute(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rte"))
    pack_id: str = "white_mountains_nh"
    name: str = "New Composed Hike"
    status: Literal["planned", "ready_for_phone", "completed", "favorite"] = "planned"
    rating: int = 0
    tags: List[str] = Field(default_factory=list)
    difficulty: TrailDifficulty = TrailDifficulty.MODERATE
    trail_type: TrailShape = "loop"
    snap_to_trails: bool = True
    waypoints: List[RouteWaypoint] = Field(default_factory=list)
    trail_ids: List[str] = Field(default_factory=list)
    coords_3d: List[List[float]] = Field(default_factory=list)  # [[lon, lat, ele_m], ...]
    distance_m: float = 0.0
    elevation_gain_m: float = 0.0
    elevation_loss_m: float = 0.0
    min_ele_m: float = 0.0
    max_ele_m: float = 0.0
    max_grade_pct: float = 0.0
    estimated_duration_min: float = 0.0
    dogs_allowed: bool = True
    water_available: bool = False
    condition_warnings: List[str] = Field(default_factory=list)
    gear_checklist: List[GearItem] = Field(default_factory=list)
    safety_card: SafetyCard = Field(default_factory=SafetyCard)
    notes: str = ""
    parent_id: Optional[str] = None
    created: str = Field(default_factory=now_iso)
    modified: str = Field(default_factory=now_iso)

    @model_validator(mode="before")
    @classmethod
    def _normalize_route_inputs(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "destination_id" in data and "pack_id" not in data:
                data["pack_id"] = data["destination_id"]
            if "coordinates" in data and "coords_3d" not in data:
                data["coords_3d"] = [
                    [float(c[1]), float(c[0]), float(c[2]) if len(c) > 2 else 0.0]
                    for c in data["coordinates"]
                ]
            dist = float(data.get("distance_m") or 0.0)
            gain = float(data.get("elevation_gain_m") or 0.0)
            if not data.get("estimated_duration_min") and (dist > 0 or gain > 0):
                data["estimated_duration_min"] = round((dist / 1000.0 / 4.5) * 60.0 + (gain / 600.0) * 60.0, 1)
        return data

    @property
    def destination_id(self) -> str:
        return self.pack_id

    @property
    def coordinates(self) -> List[Tuple[float, float, float]]:
        return [(pt[1], pt[0], pt[2] if len(pt) > 2 else 0.0) for pt in self.coords_3d]

    @property
    def elevation_profile(self) -> List[ElevationPoint]:
        from .elevation import build_elevation_profile

        return build_elevation_profile(self.coords_3d)


class TelemetryPoint(BaseModel):
    seq: int = 0
    session_id: str = ""
    timestamp: str = Field(default_factory=now_iso)
    lat: float
    lon: float
    gps_ele_m: float = 0.0
    baro_ele_m: Optional[float] = None
    horizontal_accuracy_m: float = 5.0
    speed_mps: float = 0.0
    grade_pct: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def _normalize_telemetry_inputs(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "ele_m" in data and "gps_ele_m" not in data:
                data["gps_ele_m"] = float(data["ele_m"] or 0.0)
            if "accuracy_m" in data and "horizontal_accuracy_m" not in data:
                data["horizontal_accuracy_m"] = float(data["accuracy_m"] or 5.0)
        return data

    @property
    def ele_m(self) -> float:
        return self.baro_ele_m if self.baro_ele_m is not None else self.gps_ele_m

    @property
    def accuracy_m(self) -> float:
        return self.horizontal_accuracy_m


class SplitStat(BaseModel):
    split_index: int
    distance_m: float
    elapsed_s: float = 0.0
    elevation_gain_m: float = 0.0
    elevation_loss_m: float = 0.0
    avg_speed_mps: float = 1.25
    avg_grade_pct: float = 0.0

    @property
    def elevation_change_m(self) -> float:
        return self.elevation_gain_m - self.elevation_loss_m

    @property
    def pace_min_per_km(self) -> float:
        spd = max(0.2, self.avg_speed_mps)
        return (1000.0 / spd) / 60.0


class HikeStats(BaseModel):
    planned_distance_m: float = 0.0
    actual_distance_m: float = 0.0
    planned_gain_m: float = 0.0
    actual_gain_m: float = 0.0
    actual_loss_m: float = 0.0
    min_ele_m: float = 0.0
    max_ele_m: float = 0.0
    elapsed_time_s: float = 0.0
    moving_time_s: float = 0.0
    stopped_time_s: float = 0.0
    avg_speed_mps: float = 0.0
    moving_speed_mps: float = 0.0
    max_speed_mps: float = 0.0
    vam_m_per_hr: float = 0.0
    max_off_trail_m: float = 0.0
    calories_kcal: float = 0.0
    photo_count: int = 0
    splits: List[SplitStat] = Field(default_factory=list)


class HikePhotoPin(BaseModel):
    """A geotagged photo matched from AegisPhotoVoyager (or phone sync) onto a hike trail."""

    id: str = Field(default_factory=lambda: new_id("pin"))
    session_id: str = ""
    route_id: str = ""
    voyager_photo_id: Optional[int] = None
    photo_path: str = ""
    thumb_path: Optional[str] = None
    filename: str = ""
    date_taken: Optional[int] = None  # UTC Unix timestamp
    date_iso: str = ""
    lat: float
    lon: float
    ele_m: float = 0.0
    distance_along_m: float = 0.0
    cross_track_m: float = 0.0
    camera_model: str = ""
    is_starred: bool = False
    ai_summary: str = ""
    ai_tags: List[str] = Field(default_factory=list)
    ai_mood: str = ""
    people: List[str] = Field(default_factory=list)
    caption: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_pin_inputs(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "file_path" in data and "photo_path" not in data:
                data["photo_path"] = data["file_path"]
            if "thumbnail_path" in data and "thumb_path" not in data:
                data["thumb_path"] = data["thumbnail_path"]
            if "distance_along_route_m" in data and "distance_along_m" not in data:
                data["distance_along_m"] = data["distance_along_route_m"]
            if "taken_at" in data and "date_iso" not in data:
                data["date_iso"] = str(data["taken_at"])
            if "tags" in data and "ai_tags" not in data:
                data["ai_tags"] = list(data["tags"])
            if "mood" in data and "ai_mood" not in data:
                data["ai_mood"] = str(data["mood"])
            if "people_names" in data and "people" not in data:
                data["people"] = list(data["people_names"])
            if not data.get("caption") and data.get("filename"):
                data["caption"] = str(data["filename"])
            if not data.get("filename") and data.get("caption"):
                data["filename"] = str(data["caption"])
        return data

    @property
    def distance_along_route_m(self) -> float:
        return self.distance_along_m

    @property
    def thumbnail_path(self) -> Optional[str]:
        return self.thumb_path

    @property
    def file_path(self) -> str:
        return self.photo_path

    @property
    def taken_at(self) -> str:
        return self.date_iso

    @property
    def people_names(self) -> List[str]:
        return self.people

    @property
    def tags(self) -> List[str]:
        return self.ai_tags

    @property
    def mood(self) -> str:
        return self.ai_mood


TrailPhotoPin = HikePhotoPin


class InteractiveHikeMap(BaseModel):
    """Persisted interactive hike replay map state (for 'I Hiked This Trail' & 'Re-Experience Hike')."""

    session_id: str
    pack_id: str = ""
    title: str
    hiked_date: str = ""
    region_name: str = ""
    coords_3d: List[List[float]] = Field(default_factory=list)  # [[lon, lat, ele_m], ...]
    elevation_profile: List[ElevationPoint] = Field(default_factory=list)
    photo_pins: List[HikePhotoPin] = Field(default_factory=list)
    pois: List[POI] = Field(default_factory=list)
    distance_m: float = 0.0
    elevation_gain_m: float = 0.0
    elevation_loss_m: float = 0.0
    min_ele_m: float = 0.0
    max_ele_m: float = 0.0
    notes: str = ""
    html_path: str = ""
    created_at: str = Field(default_factory=now_iso)


class HikeSession(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ses"))
    pack_id: Optional[str] = None
    route_id: Optional[str] = None
    route_snapshot: Optional[Dict[str, Any]] = None
    title: str = "Recorded Hike"
    started_at: str = Field(default_factory=now_iso)
    ended_at: Optional[str] = None
    status: Literal["tracking", "completed", "traced_hiked", "imported", "planned"] = "completed"
    device_source: Literal["traced_desktop", "pwa_phone", "ios_native", "gpx_import", "phone"] = "pwa_phone"
    stats: HikeStats = Field(default_factory=HikeStats)
    conditions_logged: Dict[str, str] = Field(default_factory=dict)
    interactive_map_path: str = ""
    interactive_map_data: Optional[Dict[str, Any]] = None
    notes: str = ""
    created: str = Field(default_factory=now_iso)
    modified: str = Field(default_factory=now_iso)

    @model_validator(mode="before")
    @classmethod
    def _normalize_session_inputs(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "destination_id" in data and "pack_id" not in data:
                data["pack_id"] = data["destination_id"]
            stats_obj = data.get("stats")
            if not isinstance(stats_obj, (dict, HikeStats)):
                stats_obj = {}
            elif isinstance(stats_obj, HikeStats):
                stats_obj = stats_obj.model_dump()
            else:
                stats_obj = dict(stats_obj)
            if "actual_distance_m" in data:
                stats_obj["actual_distance_m"] = float(data["actual_distance_m"] or 0.0)
            if "actual_elevation_gain_m" in data:
                stats_obj["actual_gain_m"] = float(data["actual_elevation_gain_m"] or 0.0)
            if "actual_duration_min" in data:
                stats_obj["moving_time_s"] = float(data["actual_duration_min"] or 0.0) * 60.0
                stats_obj["elapsed_time_s"] = float(data["actual_duration_min"] or 0.0) * 60.0
            data["stats"] = stats_obj
        return data

    @property
    def destination_id(self) -> str:
        return self.pack_id or ""

    @property
    def actual_distance_m(self) -> float:
        return self.stats.actual_distance_m or self.stats.planned_distance_m

    @property
    def actual_elevation_gain_m(self) -> float:
        return self.stats.actual_gain_m or self.stats.planned_gain_m

    @property
    def actual_duration_min(self) -> float:
        sec = self.stats.moving_time_s or self.stats.elapsed_time_s
        return sec / 60.0


class BriefField(BaseModel):
    value: Any = None
    source: Literal["unset", "stated", "inferred"] = "unset"
    evidence: str = ""


class HikeBrief(BaseModel):
    """Structured state for the guided AI Hike Brainstorm interview."""

    id: str = Field(default_factory=lambda: new_id("brf"))
    pack_id: BriefField = Field(default_factory=BriefField)
    party_and_fitness: BriefField = Field(default_factory=BriefField)
    max_distance_km: BriefField = Field(default_factory=BriefField)
    max_elevation_gain_m: BriefField = Field(default_factory=BriefField)
    route_shape_and_scenery: BriefField = Field(default_factory=BriefField)
    conditions_and_dogs: BriefField = Field(default_factory=BriefField)
    pack_and_start_time: BriefField = Field(default_factory=BriefField)
    dogs_with_party: bool = False
    avoid_snow_and_mud: bool = True
    notes: str = ""


class ProposalOperation(BaseModel):
    op_type: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ProposalEnvelope(BaseModel):
    summary: str
    rationale: str = ""
    operations: List[ProposalOperation] = Field(default_factory=list)

