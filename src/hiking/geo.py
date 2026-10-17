"""Geodesy, bounding-box math, polyline simplification, and cross-track projection.

Pure Python stdlib math — no external GIS dependencies.
Coordinates in 3D polylines use [lon, lat, ele_m].
"""
import math
from typing import List, Tuple

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 coordinates."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing in degrees [0, 360) from point 1 to point 2."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def polyline_length_m(coords_3d: List[List[float]]) -> float:
    """Total horizontal distance in meters along a [[lon, lat, ...], ...] polyline."""
    if len(coords_3d) < 2:
        return 0.0
    total = 0.0
    for i in range(1, len(coords_3d)):
        lon1, lat1 = coords_3d[i - 1][0], coords_3d[i - 1][1]
        lon2, lat2 = coords_3d[i][0], coords_3d[i][1]
        total += haversine_m(lat1, lon1, lat2, lon2)
    return total


def compute_bbox(coords_3d: List[List[float]]) -> Tuple[float, float, float, float]:
    """Return (min_lat, max_lat, min_lon, max_lon) for a [[lon, lat, ...], ...] polyline."""
    if not coords_3d:
        return (0.0, 0.0, 0.0, 0.0)
    lons = [pt[0] for pt in coords_3d]
    lats = [pt[1] for pt in coords_3d]
    return (min(lats), max(lats), min(lons), max(lons))


def bboxes_intersect(
    a_min_lat: float,
    a_max_lat: float,
    a_min_lon: float,
    a_max_lon: float,
    b_min_lat: float,
    b_max_lat: float,
    b_min_lon: float,
    b_max_lon: float,
) -> bool:
    return not (
        a_max_lat < b_min_lat
        or a_min_lat > b_max_lat
        or a_max_lon < b_min_lon
        or a_min_lon > b_max_lon
    )


def expand_bbox_meters(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    buffer_m: float,
) -> Tuple[float, float, float, float]:
    """Pad a bounding box by `buffer_m` meters in all directions."""
    mid_lat = 0.5 * (min_lat + max_lat)
    dlat = buffer_m / 111_320.0
    cos_lat = max(0.1, math.cos(math.radians(mid_lat)))
    dlon = buffer_m / (111_320.0 * cos_lat)
    return (min_lat - dlat, max_lat + dlat, min_lon - dlon, max_lon + dlon)


def _local_xy_m(lat: float, lon: float, ref_lat: float, ref_lon: float) -> Tuple[float, float]:
    """Equirectangular local tangent plane coordinates (x_east_m, y_north_m)."""
    cos_lat = math.cos(math.radians(ref_lat))
    x = math.radians(lon - ref_lon) * EARTH_RADIUS_M * cos_lat
    y = math.radians(lat - ref_lat) * EARTH_RADIUS_M
    return x, y


def point_to_segment_projection(
    lat: float,
    lon: float,
    lat1: float,
    lon1: float,
    ele1: float,
    lat2: float,
    lon2: float,
    ele2: float,
) -> Tuple[float, float, float, float, float]:
    """Project (lat, lon) onto segment (lat1, lon1, ele1) -> (lat2, lon2, ele2).

    Returns (cross_track_m, t_fraction_0_to_1, proj_lat, proj_lon, proj_ele_m).
    """
    px, py = _local_xy_m(lat, lon, lat1, lon1)
    bx, by = _local_xy_m(lat2, lon2, lat1, lon1)
    seg_len_sq = bx * bx + by * by
    if seg_len_sq <= 1e-9:
        dist = math.hypot(px, py)
        return (dist, 0.0, lat1, lon1, ele1)
    t = max(0.0, min(1.0, (px * bx + py * by) / seg_len_sq))
    proj_x = t * bx
    proj_y = t * by
    dist = math.hypot(px - proj_x, py - proj_y)
    proj_lat = lat1 + t * (lat2 - lat1)
    proj_lon = lon1 + t * (lon2 - lon1)
    proj_ele = ele1 + t * (ele2 - ele1)
    return (dist, t, proj_lat, proj_lon, proj_ele)


def project_point_onto_polyline(
    lat: float,
    lon: float,
    coords_3d: List[List[float]],
) -> Tuple[float, float, float, float, float]:
    """Find the closest point along a 3D polyline [[lon, lat, ele_m], ...].

    Returns:
        (cross_track_m, distance_along_m, proj_lat, proj_lon, proj_ele_m)
    """
    if not coords_3d:
        return (float("inf"), 0.0, lat, lon, 0.0)
    if len(coords_3d) == 1:
        lon0, lat0 = coords_3d[0][0], coords_3d[0][1]
        ele0 = coords_3d[0][2] if len(coords_3d[0]) > 2 else 0.0
        return (haversine_m(lat, lon, lat0, lon0), 0.0, lat0, lon0, ele0)

    best_cross = float("inf")
    best_along = 0.0
    best_lat = coords_3d[0][1]
    best_lon = coords_3d[0][0]
    best_ele = coords_3d[0][2] if len(coords_3d[0]) > 2 else 0.0

    cum_dist = 0.0
    for i in range(1, len(coords_3d)):
        p1, p2 = coords_3d[i - 1], coords_3d[i]
        lon1, lat1 = p1[0], p1[1]
        ele1 = p1[2] if len(p1) > 2 else 0.0
        lon2, lat2 = p2[0], p2[1]
        ele2 = p2[2] if len(p2) > 2 else 0.0
        seg_len = haversine_m(lat1, lon1, lat2, lon2)
        cross_m, t, plat, plon, pele = point_to_segment_projection(
            lat, lon, lat1, lon1, ele1, lat2, lon2, ele2
        )
        if cross_m < best_cross:
            best_cross = cross_m
            best_along = cum_dist + t * seg_len
            best_lat = plat
            best_lon = plon
            best_ele = pele
        cum_dist += seg_len

    return (best_cross, best_along, best_lat, best_lon, best_ele)


def cross_track_distance_m(lat: float, lon: float, coords_3d: List[List[float]]) -> float:
    """Minimum perpendicular distance in meters from (lat, lon) to a 3D polyline."""
    cross_m, _, _, _, _ = project_point_onto_polyline(lat, lon, coords_3d)
    return cross_m


def interpolate_at_distance(
    coords_3d: List[List[float]],
    target_distance_m: float,
) -> Tuple[float, float, float]:
    """Return (lat, lon, ele_m) at `target_distance_m` along a 3D polyline."""
    if not coords_3d:
        return (0.0, 0.0, 0.0)
    if len(coords_3d) == 1 or target_distance_m <= 0.0:
        p = coords_3d[0]
        return (p[1], p[0], p[2] if len(p) > 2 else 0.0)

    cum = 0.0
    for i in range(1, len(coords_3d)):
        p1, p2 = coords_3d[i - 1], coords_3d[i]
        lon1, lat1 = p1[0], p1[1]
        ele1 = p1[2] if len(p1) > 2 else 0.0
        lon2, lat2 = p2[0], p2[1]
        ele2 = p2[2] if len(p2) > 2 else 0.0
        seg_len = haversine_m(lat1, lon1, lat2, lon2)
        if cum + seg_len >= target_distance_m and seg_len > 1e-6:
            t = (target_distance_m - cum) / seg_len
            return (
                lat1 + t * (lat2 - lat1),
                lon1 + t * (lon2 - lon1),
                ele1 + t * (ele2 - ele1),
            )
        cum += seg_len

    last = coords_3d[-1]
    return (last[1], last[0], last[2] if len(last) > 2 else 0.0)


def simplify_polyline_dp(
    coords_3d: List[List[float]],
    tolerance_m: float = 5.0,
) -> List[List[float]]:
    """Douglas-Peucker simplification of a [[lon, lat, ele_m], ...] polyline."""
    if len(coords_3d) <= 2 or tolerance_m <= 0:
        return [list(pt) for pt in coords_3d]

    p_start = coords_3d[0]
    p_end = coords_3d[-1]
    max_dist = -1.0
    index = 0
    for i in range(1, len(coords_3d) - 1):
        pt = coords_3d[i]
        dist, _, _, _, _ = point_to_segment_projection(
            pt[1],
            pt[0],
            p_start[1],
            p_start[0],
            p_start[2] if len(p_start) > 2 else 0.0,
            p_end[1],
            p_end[0],
            p_end[2] if len(p_end) > 2 else 0.0,
        )
        if dist > max_dist:
            max_dist = dist
            index = i

    if max_dist > tolerance_m:
        left = simplify_polyline_dp(coords_3d[: index + 1], tolerance_m)
        right = simplify_polyline_dp(coords_3d[index:], tolerance_m)
        return left[:-1] + right
    return [list(p_start), list(p_end)]

