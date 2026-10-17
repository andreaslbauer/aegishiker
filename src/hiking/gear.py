"""Rule-based Ten Essentials & condition-specific backcountry gear checklist generator."""
import json
from pathlib import Path
from typing import List, Optional

from .models import ComposedRoute, GearItem


def generate_gear_checklist(
    route_or_id: ComposedRoute | str,
    templates_path_or_key: Optional[Path | str] = None,
    with_dogs: bool = False,
) -> List[GearItem]:
    """Generate Ten Essentials + conditional gear items tailored to `route` or template key."""
    from src.core import paths

    templates_path = paths.GEAR_TEMPLATES_PATH
    template_key = "day_hike"
    if isinstance(templates_path_or_key, Path):
        templates_path = templates_path_or_key
    elif isinstance(templates_path_or_key, str):
        template_key = templates_path_or_key

    if isinstance(route_or_id, str):
        route = ComposedRoute(
            id=route_or_id,
            max_ele_m=2400.0 if "winter" in template_key or "alpine" in template_key else 1200.0,
            distance_m=14000.0 if "overnight" in template_key else 8000.0,
            elevation_gain_m=750.0 if "winter" in template_key or "overnight" in template_key else 400.0,
        )
    else:
        route = route_or_id

    raw = json.loads(templates_path.read_text(encoding="utf-8"))
    items: List[GearItem] = []

    existing_checked = {g.id: g.checked for g in route.gear_checklist}

    for entry in raw.get("ten_essentials", []):
        gid = entry["id"]
        items.append(
            GearItem(
                id=gid,
                route_id=route.id,
                category=entry["category"],
                name=entry["name"],
                weight_g=float(entry.get("weight_g", 100)),
                essential=True,
                checked=existing_checked.get(gid, True),
                reason=entry.get("reason", ""),
            )
        )

    warnings_text = " ".join(route.condition_warnings).lower()
    has_snow = "snow" in warnings_text or route.max_ele_m >= 2200.0 or "winter" in template_key
    has_mud = "mud" in warnings_text or "water" in warnings_text
    is_steep = route.max_grade_pct >= 12.0 or route.elevation_gain_m >= 500.0
    is_long = route.distance_m >= 10_000.0 or route.elevation_gain_m >= 600.0 or "overnight" in template_key

    for cond in raw.get("conditional_items", []):
        trig = cond.get("trigger", "")
        include = False
        if trig == "snow_or_high_altitude" and has_snow:
            include = True
        elif trig == "steep_or_strenuous" and is_steep:
            include = True
        elif trig == "muddy_or_high_water" and has_mud:
            include = True
        elif trig == "dogs" and with_dogs and route.dogs_allowed:
            include = True
        elif trig == "long_duration" and is_long:
            include = True

        if include:
            gid = cond["id"]
            items.append(
                GearItem(
                    id=gid,
                    route_id=route.id,
                    category=cond["category"],
                    name=cond["name"],
                    weight_g=float(cond.get("weight_g", 150)),
                    essential=False,
                    checked=existing_checked.get(gid, True),
                    reason=cond.get("reason", ""),
                )
            )

    return items


def total_pack_weight_g(items: List[GearItem], checked_only: bool = True) -> float:
    return sum(float(i.weight_g) for i in items if (i.checked or not checked_only))


def estimate_hydration_ml(
    duration_min: float,
    elevation_gain_m: float = 0.0,
    temp_c: float = 18.0,
) -> int:
    hours = max(0.5, duration_min / 60.0)
    rate_ml_hr = 550.0 + max(0.0, temp_c - 18.0) * 25.0 + (elevation_gain_m / 500.0) * 80.0
    return int(round(max(500.0, hours * rate_ml_hr)))


def estimate_calories_kcal(
    distance_m: float,
    elevation_gain_m: float,
    hiker_weight_kg: float = 75.0,
    pack_weight_kg: float = 6.0,
) -> int:
    total_mass = max(40.0, hiker_weight_kg + pack_weight_kg)
    dist_km = max(0.0, distance_m / 1000.0)
    kcal = (dist_km * total_mass * 0.78) + (max(0.0, elevation_gain_m) * total_mass * 0.014)
    return int(round(kcal))

