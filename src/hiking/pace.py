"""Backcountry pace, duration, turnaround time, hydration, and calorie calculations.

Implements:
1. Naismith's Rule (1892): 1 hour per 5 km horizontal + 1 hour per 600 m ascent.
2. Langmuir's Corrections (1984):
   - Gentle descent (-5° to -12°, ~ -8.7% to -21.2% grade): subtract 10 min per 300 m descent.
   - Steep descent (steeper than -12°, < -21.2% grade): add 10 min per 300 m descent.
3. Tobler's Hiking Function (1993):
   - V(km/h) = 6 * exp(-3.5 * |dh/dx + 0.05|) scaled to hiker's flat_speed_kmh.
4. Pack-weight & high-altitude (>2,500 m) modifiers.
"""
import math
from typing import List

from .geo import haversine_m
from .models import PaceEstimate


def naismith_seconds(
    distance_m: float,
    elevation_gain_m: float,
    flat_speed_kmh: float = 4.8,
    climb_rate_m_per_hr: float = 600.0,
) -> float:
    flat_speed_mps = max(0.4, (flat_speed_kmh * 1000.0) / 3600.0)
    climb_rate_mps = max(0.05, climb_rate_m_per_hr / 3600.0)
    return (distance_m / flat_speed_mps) + (max(0.0, elevation_gain_m) / climb_rate_mps)


def langmuir_seconds(
    coords_3d: List[List[float]],
    flat_speed_kmh: float = 4.8,
    climb_rate_m_per_hr: float = 600.0,
) -> float:
    """Segment-by-segment Naismith + Langmuir steep/gentle descent adjustment."""
    if len(coords_3d) < 2:
        return 0.0

    flat_speed_mps = max(0.4, (flat_speed_kmh * 1000.0) / 3600.0)
    climb_rate_mps = max(0.05, climb_rate_m_per_hr / 3600.0)
    total_sec = 0.0

    for i in range(1, len(coords_3d)):
        p1, p2 = coords_3d[i - 1], coords_3d[i]
        dx = haversine_m(p1[1], p1[0], p2[1], p2[0])
        if dx <= 0.1:
            continue
        e1 = p1[2] if len(p1) > 2 else 0.0
        e2 = p2[2] if len(p2) > 2 else 0.0
        dz = e2 - e1
        slope = dz / dx

        seg_sec = dx / flat_speed_mps
        if dz > 0:
            seg_sec += dz / climb_rate_mps
        elif dz < 0:
            drop = abs(dz)
            if -0.2125 <= slope <= -0.0875:
                # Gentle descent speeds hiker up slightly (-10 min per 300 m)
                seg_sec = max(dx / (flat_speed_mps * 1.35), seg_sec - (drop / 300.0) * 600.0)
            elif slope < -0.2125:
                # Steep descent slows hiker down (+10 min per 300 m)
                seg_sec += (drop / 300.0) * 600.0
        total_sec += seg_sec

    return total_sec


def tobler_seconds(
    coords_3d: List[List[float]],
    flat_speed_kmh: float = 4.8,
) -> float:
    """Tobler's hiking function integrated over 3D segments."""
    if len(coords_3d) < 2:
        return 0.0

    # Tobler's nominal flat speed at slope=0 is 6 * exp(-3.5 * 0.05) = 5.0367 km/h
    scale = max(0.3, flat_speed_kmh / 5.0367)
    total_sec = 0.0

    for i in range(1, len(coords_3d)):
        p1, p2 = coords_3d[i - 1], coords_3d[i]
        dx = haversine_m(p1[1], p1[0], p2[1], p2[0])
        if dx <= 0.1:
            continue
        e1 = p1[2] if len(p1) > 2 else 0.0
        e2 = p2[2] if len(p2) > 2 else 0.0
        slope = max(-0.8, min(0.8, (e2 - e1) / dx))
        speed_kmh = max(0.4, 6.0 * math.exp(-3.5 * abs(slope + 0.05)) * scale)
        speed_mps = (speed_kmh * 1000.0) / 3600.0
        total_sec += dx / speed_mps

    return total_sec


def estimate_hike_pace(
    coords_3d: List[List[float]],
    distance_m: float,
    elevation_gain_m: float,
    max_ele_m: float = 1000.0,
    flat_speed_kmh: float = 4.8,
    climb_rate_m_per_hr: float = 600.0,
    body_weight_kg: float = 75.0,
    pack_weight_kg: float = 6.0,
    start_time_hhmm: str = "08:00",
    hot_weather: bool = False,
) -> PaceEstimate:
    """Compute Naismith, Langmuir, and Tobler durations, water need, calories, and turnaround time."""
    nais = naismith_seconds(distance_m, elevation_gain_m, flat_speed_kmh, climb_rate_m_per_hr)
    lang = (
        langmuir_seconds(coords_3d, flat_speed_kmh, climb_rate_m_per_hr)
        if len(coords_3d) >= 2
        else nais
    )
    tob = tobler_seconds(coords_3d, flat_speed_kmh) if len(coords_3d) >= 2 else nais

    # Pack weight penalty (~1.5% slower per kg over 5 kg)
    pack_factor = 1.0 + max(0.0, (pack_weight_kg - 5.0) * 0.015)
    # High altitude penalty (~4% slower per 500 m above 2,200 m)
    alt_factor = 1.0 + max(0.0, ((max_ele_m - 2200.0) / 500.0) * 0.04)
    modifier = pack_factor * alt_factor

    nais *= modifier
    lang *= modifier
    tob *= modifier
    recommended = round(max(lang, 0.5 * (lang + tob)), 1)

    hours = recommended / 3600.0
    # Hydration: 0.55 L per hour base (0.8 L/hr in hot weather), min 0.5 L
    liters_per_hr = 0.80 if hot_weather else 0.55
    water_l = round(max(0.5, hours * liters_per_hr) if distance_m > 0 else 0.0, 2)

    # Calories (Pandolf / ACSM approximation): ~5.5 kcal/kg/hr + climbing work
    total_mass = max(40.0, body_weight_kg + pack_weight_kg)
    calories = round((total_mass * 5.2 * hours) + (total_mass * max(0.0, elevation_gain_m) * 0.012))

    start_min = _parse_hhmm_minutes(start_time_hhmm)
    finish_min = start_min + int(round(recommended / 60.0))
    turnaround_min = start_min + int(round((recommended * 0.55) / 60.0))

    return PaceEstimate(
        naismith_seconds=round(nais, 1),
        langmuir_seconds=round(lang, 1),
        tobler_seconds=round(tob, 1),
        recommended_seconds=recommended,
        water_liters=water_l,
        calories_kcal=float(calories),
        turnaround_time_str=_format_hhmm_minutes(turnaround_min),
        estimated_finish_str=_format_hhmm_minutes(finish_min),
    )


def _parse_hhmm_minutes(hhmm: str) -> int:
    try:
        parts = hhmm.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 8 * 60


def _format_hhmm_minutes(total_minutes: int) -> str:
    mins = total_minutes % (24 * 60)
    return f"{mins // 60:02d}:{mins % 60:02d}"


def estimate_pace_minutes(
    distance_m: float,
    elevation_gain_m: float,
    elevation_loss_m: float = 0.0,
    model: str = "naismith",
    base_speed_kmh: float = 4.5,
    pack_weight_kg: float = 6.0,
) -> float:
    """Compute estimated hike duration in minutes for Naismith, Langmuir, or Tobler."""
    pack_factor = 1.0 + max(0.0, (pack_weight_kg - 5.0) * 0.015)
    nais_s = naismith_seconds(distance_m, elevation_gain_m, flat_speed_kmh=base_speed_kmh)
    m = model.strip().lower()
    if m == "langmuir":
        steep_adj_s = (max(0.0, elevation_loss_m) / 300.0) * 300.0
        sec = (nais_s + steep_adj_s) * pack_factor
    elif m == "tobler":
        avg_slope = (elevation_gain_m - elevation_loss_m) / max(100.0, distance_m)
        scale = max(0.3, base_speed_kmh / 5.0367)
        spd_kmh = max(0.6, 6.0 * math.exp(-3.5 * abs(avg_slope + 0.05)) * scale)
        sec = ((distance_m / 1000.0) / spd_kmh) * 3600.0 + (elevation_gain_m / 600.0) * 1800.0
        sec *= pack_factor
    else:
        sec = nais_s * pack_factor
    return round(sec / 60.0, 1)

