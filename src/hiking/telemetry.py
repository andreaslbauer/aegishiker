"""GPS telemetry filtering, moving/stopped detection, split statistics, and off-trail analysis."""
from datetime import datetime
from typing import List, Optional

from .elevation import compute_gain_loss, compute_vam
from .geo import cross_track_distance_m, haversine_m
from .models import HikeStats, SplitStat, TelemetryPoint
from .units import M_PER_KM, M_PER_MILE


def filter_telemetry_points(
    points: List[TelemetryPoint],
    max_horizontal_accuracy_m: float = 20.0,
) -> List[TelemetryPoint]:
    """Filter out inaccurate GPS fixes (matches Swift LocationTrackerService accuracy gate < 20 m)."""
    valid: List[TelemetryPoint] = []
    for pt in points:
        if 0.0 <= pt.horizontal_accuracy_m < max_horizontal_accuracy_m:
            valid.append(pt)
    return valid


def _parse_ts(ts: str) -> Optional[float]:
    try:
        clean = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(clean).timestamp()
    except Exception:
        return None


def analyze_telemetry(
    points: List[TelemetryPoint],
    planned_coords_3d: Optional[List[List[float]]] = None,
    units: str = "us",
    hysteresis_m: float = 3.0,
    body_weight_kg: float = 75.0,
    pack_weight_kg: float = 6.0,
) -> HikeStats:
    """Compute comprehensive HikeStats and per-mile/km splits from recorded telemetry."""
    filtered = filter_telemetry_points(points)
    if not filtered:
        filtered = list(points)

    planned_dist = 0.0
    planned_gain = 0.0
    if planned_coords_3d and len(planned_coords_3d) >= 2:
        for i in range(1, len(planned_coords_3d)):
            p1, p2 = planned_coords_3d[i - 1], planned_coords_3d[i]
            planned_dist += haversine_m(p1[1], p1[0], p2[1], p2[0])
        planned_eles = [p[2] if len(p) > 2 else 0.0 for p in planned_coords_3d]
        planned_gain, _ = compute_gain_loss(planned_eles, hysteresis_m=hysteresis_m)

    if len(filtered) < 2:
        ele0 = (
            (filtered[0].baro_ele_m if filtered[0].baro_ele_m is not None else filtered[0].gps_ele_m)
            if filtered
            else 0.0
        )
        return HikeStats(
            planned_distance_m=round(planned_dist, 1),
            planned_gain_m=round(planned_gain, 1),
            min_ele_m=round(ele0, 1),
            max_ele_m=round(ele0, 1),
        )

    total_dist = 0.0
    moving_time_s = 0.0
    stopped_time_s = 0.0
    max_speed = 0.0
    max_off_trail = 0.0

    # Prefer barometric altimeter readings when present for smoother vertical gain/loss
    elevations = [
        pt.baro_ele_m if pt.baro_ele_m is not None else pt.gps_ele_m for pt in filtered
    ]
    gain, loss = compute_gain_loss(elevations, hysteresis_m=hysteresis_m)

    split_length_m = M_PER_MILE if units == "us" else M_PER_KM
    splits: List[SplitStat] = []
    split_dist = 0.0
    split_time = 0.0
    split_gain = 0.0
    split_loss = 0.0
    split_idx = 1

    for i in range(1, len(filtered)):
        p1, p2 = filtered[i - 1], filtered[i]
        d = haversine_m(p1.lat, p1.lon, p2.lat, p2.lon)
        t1, t2 = _parse_ts(p1.timestamp), _parse_ts(p2.timestamp)
        dt = max(0.0, (t2 - t1)) if (t1 is not None and t2 is not None and t2 >= t1) else 0.0
        if dt == 0.0 and p2.speed_mps > 0.1:
            dt = d / p2.speed_mps

        seg_speed = (d / dt) if dt > 0.0 else p2.speed_mps
        if d > 0.0:
            p2.grade_pct = round(((elevations[i] - elevations[i - 1]) / d) * 100.0, 1)
        if seg_speed > 0.1:
            p2.speed_mps = round(seg_speed, 2)

        total_dist += d
        if seg_speed >= 0.25:
            moving_time_s += dt
            if seg_speed < 8.5:  # ignore GPS jump spikes
                max_speed = max(max_speed, seg_speed)
        else:
            stopped_time_s += dt

        if planned_coords_3d:
            off_m = cross_track_distance_m(p2.lat, p2.lon, planned_coords_3d)
            max_off_trail = max(max_off_trail, off_m)

        # Accumulate split
        dz = elevations[i] - elevations[i - 1]
        split_dist += d
        split_time += dt
        if dz > 0:
            split_gain += dz
        else:
            split_loss += abs(dz)

        if split_dist >= split_length_m:
            avg_spd = split_dist / split_time if split_time > 0 else 0.0
            avg_grd = ((split_gain - split_loss) / split_dist) * 100.0 if split_dist > 0 else 0.0
            splits.append(
                SplitStat(
                    split_index=split_idx,
                    distance_m=round(split_dist, 1),
                    elapsed_s=round(split_time, 1),
                    elevation_gain_m=round(split_gain, 1),
                    elevation_loss_m=round(split_loss, 1),
                    avg_speed_mps=round(avg_spd, 2),
                    avg_grade_pct=round(avg_grd, 1),
                )
            )
            split_idx += 1
            split_dist = 0.0
            split_time = 0.0
            split_gain = 0.0
            split_loss = 0.0

    # Partial final split if >= 150 m
    if split_dist >= 150.0:
        avg_spd = split_dist / split_time if split_time > 0 else 0.0
        avg_grd = ((split_gain - split_loss) / split_dist) * 100.0 if split_dist > 0 else 0.0
        splits.append(
            SplitStat(
                split_index=split_idx,
                distance_m=round(split_dist, 1),
                elapsed_s=round(split_time, 1),
                elevation_gain_m=round(split_gain, 1),
                elevation_loss_m=round(split_loss, 1),
                avg_speed_mps=round(avg_spd, 2),
                avg_grade_pct=round(avg_grd, 1),
            )
        )

    elapsed_s = moving_time_s + stopped_time_s
    if elapsed_s <= 0.0 and total_dist > 0.0:
        # Fallback when timestamps are absent (e.g., static route trace)
        moving_time_s = total_dist / 1.3
        elapsed_s = moving_time_s

    avg_speed = (total_dist / elapsed_s) if elapsed_s > 0 else 0.0
    moving_speed = (total_dist / moving_time_s) if moving_time_s > 0 else avg_speed
    vam = compute_vam(gain, moving_time_s)

    total_mass = max(40.0, body_weight_kg + pack_weight_kg)
    calories = round(
        (total_mass * 5.2 * (moving_time_s / 3600.0)) + (total_mass * gain * 0.012), 1
    )

    return HikeStats(
        planned_distance_m=round(planned_dist or total_dist, 1),
        actual_distance_m=round(total_dist, 1),
        planned_gain_m=round(planned_gain or gain, 1),
        actual_gain_m=gain,
        actual_loss_m=loss,
        min_ele_m=round(min(elevations), 1),
        max_ele_m=round(max(elevations), 1),
        elapsed_time_s=round(elapsed_s, 1),
        moving_time_s=round(moving_time_s, 1),
        stopped_time_s=round(stopped_time_s, 1),
        avg_speed_mps=round(avg_speed, 2),
        moving_speed_mps=round(moving_speed, 2),
        max_speed_mps=round(max_speed, 2),
        vam_m_per_hr=vam,
        max_off_trail_m=round(max_off_trail, 1),
        calories_kcal=calories,
        splits=splits,
    )


def summarize_telemetry(points: List[TelemetryPoint]) -> dict:
    stats = analyze_telemetry(points, units="metric")
    return {
        "distance_m": stats.actual_distance_m,
        "elevation_gain_m": stats.actual_gain_m,
        "elevation_loss_m": stats.actual_loss_m,
        "duration_min": round(stats.moving_time_s / 60.0, 1),
        "avg_speed_mps": stats.moving_speed_mps,
    }


def compute_splits(points: List[TelemetryPoint], split_distance_m: float = 1000.0) -> List[SplitStat]:
    units = "metric" if split_distance_m <= 1100.0 else "us"
    stats = analyze_telemetry(points, units=units)
    if stats.splits:
        return stats.splits
    if len(points) >= 2:
        return [
            SplitStat(
                split_index=1,
                distance_m=max(100.0, stats.actual_distance_m),
                elapsed_s=max(60.0, stats.moving_time_s),
                elevation_gain_m=stats.actual_gain_m,
                elevation_loss_m=stats.actual_loss_m,
                avg_speed_mps=max(0.5, stats.moving_speed_mps or 1.25),
            )
        ]
    return []


def detect_off_trail(
    current_lat_lon: tuple[float, float],
    route_coords: List,
    threshold_m: float = 45.0,
) -> dict:
    lat, lon = current_lat_lon
    if not route_coords:
        return {"is_off_trail": False, "deviation_m": 0.0}
    # Normalize route_coords whether [(lat, lon, ele)] or [[lon, lat, ele]]
    first = route_coords[0]
    if abs(first[0]) > 65.0 and abs(first[1]) < 65.0:
        coords_3d = [[c[0], c[1], c[2] if len(c) > 2 else 0.0] for c in route_coords]
    else:
        coords_3d = [[c[1], c[0], c[2] if len(c) > 2 else 0.0] for c in route_coords]
    dev_m = cross_track_distance_m(lat, lon, coords_3d)
    return {"is_off_trail": dev_m > threshold_m, "deviation_m": round(dev_m, 1)}

