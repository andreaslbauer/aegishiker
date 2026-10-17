"""Elevation profile extraction, DEM bilinear interpolation, hysteresis-filtered gain/loss,
slope grade (%), and Vertical Ascent Meters per hour (VAM).
"""
from typing import Dict, List, Optional, Tuple

from .geo import haversine_m
from .models import DEMGrid, ElevationPoint


def sample_dem(dem: Optional[DEMGrid], lat: float, lon: float, fallback: float = 0.0) -> float:
    """Bilinearly interpolate altitude (meters) from a regional DEMGrid."""
    if dem is None or dem.rows < 2 or dem.cols < 2 or not dem.elevations_m:
        return fallback
    if dem.max_lat <= dem.min_lat or dem.max_lon <= dem.min_lon:
        return fallback

    u = (lon - dem.min_lon) / (dem.max_lon - dem.min_lon)
    v = (lat - dem.min_lat) / (dem.max_lat - dem.min_lat)
    u = max(0.0, min(1.0, u))
    v = max(0.0, min(1.0, v))

    col_f = u * (dem.cols - 1)
    row_f = v * (dem.rows - 1)
    c0 = int(col_f)
    r0 = int(row_f)
    c1 = min(c0 + 1, dem.cols - 1)
    r1 = min(r0 + 1, dem.rows - 1)
    dc = col_f - c0
    dr = row_f - r0

    z00 = dem.elevations_m[r0][c0]
    z10 = dem.elevations_m[r0][c1]
    z01 = dem.elevations_m[r1][c0]
    z11 = dem.elevations_m[r1][c1]

    z0 = z00 * (1.0 - dc) + z10 * dc
    z1 = z01 * (1.0 - dc) + z11 * dc
    return round(z0 * (1.0 - dr) + z1 * dr, 1)


def compute_gain_loss(
    elevations_m: List[float],
    hysteresis_m: float = 3.0,
) -> Tuple[float, float]:
    """Compute cumulative elevation gain and loss (m) with vertical hysteresis filtering.

    Hysteresis suppresses small GPS altitude jitter (< `hysteresis_m`) while accurately
    capturing every real climb and descent. If a route has fewer points or total range
    smaller than hysteresis, falls back to direct point-to-point sums.
    """
    if len(elevations_m) < 2:
        return (0.0, 0.0)

    if hysteresis_m <= 0.0:
        gain = sum(
            max(0.0, elevations_m[i] - elevations_m[i - 1]) for i in range(1, len(elevations_m))
        )
        loss = sum(
            max(0.0, elevations_m[i - 1] - elevations_m[i]) for i in range(1, len(elevations_m))
        )
        return (round(gain, 1), round(loss, 1))

    gain = 0.0
    loss = 0.0
    anchor = elevations_m[0]
    for z in elevations_m[1:]:
        diff = z - anchor
        if diff >= hysteresis_m:
            gain += diff
            anchor = z
        elif diff <= -hysteresis_m:
            loss += -diff
            anchor = z

    return (round(gain, 1), round(loss, 1))


def build_elevation_profile(
    coords_3d: List[List[float]],
    dem: Optional[DEMGrid] = None,
) -> List[ElevationPoint]:
    """Build a list of ElevationPoint(distance_m, ele_m, grade_pct, lat, lon) along a 3D path."""
    if not coords_3d:
        return []

    points: List[ElevationPoint] = []
    cum_dist = 0.0

    for i, pt in enumerate(coords_3d):
        lon, lat = pt[0], pt[1]
        ele = pt[2] if len(pt) > 2 else sample_dem(dem, lat, lon, 0.0)
        grade_pct = 0.0
        if i > 0:
            prev = points[-1]
            seg_d = haversine_m(prev.lat, prev.lon, lat, lon)
            cum_dist += seg_d
            if seg_d >= 1.0:
                grade_pct = round(((ele - prev.ele_m) / seg_d) * 100.0, 1)
                # Clamp extreme step artifacts
                grade_pct = max(-75.0, min(75.0, grade_pct))
        points.append(
            ElevationPoint(
                distance_m=round(cum_dist, 1),
                ele_m=round(ele, 1),
                grade_pct=grade_pct,
                lat=lat,
                lon=lon,
            )
        )
    return points


def summarize_elevation(
    coords_3d: List[List[float]],
    hysteresis_m: float = 3.0,
    dem: Optional[DEMGrid] = None,
) -> Dict[str, float]:
    """Return distance_m, elevation_gain_m, elevation_loss_m, min_ele_m, max_ele_m, max_grade_pct."""
    profile = build_elevation_profile(coords_3d, dem=dem)
    if not profile:
        return {
            "distance_m": 0.0,
            "elevation_gain_m": 0.0,
            "elevation_loss_m": 0.0,
            "min_ele_m": 0.0,
            "max_ele_m": 0.0,
            "max_grade_pct": 0.0,
        }
    eles = [p.ele_m for p in profile]
    gain, loss = compute_gain_loss(eles, hysteresis_m=hysteresis_m)
    max_grade = max((abs(p.grade_pct) for p in profile), default=0.0)
    return {
        "distance_m": round(profile[-1].distance_m, 1),
        "elevation_gain_m": gain,
        "elevation_loss_m": loss,
        "min_ele_m": round(min(eles), 1),
        "max_ele_m": round(max(eles), 1),
        "max_grade_pct": round(max_grade, 1),
    }


def compute_vam(elevation_gain_m: float, moving_time_s: float) -> float:
    """Vertical Ascent Meters per hour (VAM)."""
    if moving_time_s <= 60.0 or elevation_gain_m <= 0.0:
        return 0.0
    return round(elevation_gain_m / (moving_time_s / 3600.0), 1)

