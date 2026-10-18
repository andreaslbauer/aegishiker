"""Qt-free 3D geometry for the hike replay animation.

Local metric frame: x east, y north (metres from the region's centre), z up (metres, optionally
exaggerated). A regular terrain mesh is sampled from the pack DEM; a chase camera follows the
hiker along the route; points are perspective-projected to screen pixels.
"""
import bisect
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .elevation import sample_dem
from .models import DEMGrid

M_PER_DEG_LAT = 110_540.0


class LocalFrame:
    def __init__(self, lat0: float, lon0: float, z_exaggeration: float = 1.0) -> None:
        self.lat0, self.lon0 = lat0, lon0
        self.kx = 111_320.0 * math.cos(math.radians(lat0))
        self.zx = z_exaggeration

    def to_xy(self, lat: float, lon: float) -> Tuple[float, float]:
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * M_PER_DEG_LAT

    def to_latlon(self, x: float, y: float) -> Tuple[float, float]:
        return self.lat0 + y / M_PER_DEG_LAT, self.lon0 + x / self.kx

    def z(self, ele_m: float) -> float:
        return ele_m * self.zx


def _hypso(t: float) -> Tuple[float, float, float]:
    """Daylight landscape tint by relative elevation: valley green → forest → rock → pale summit."""
    stops = [(0.0, (62, 104, 58)), (0.45, (78, 112, 64)), (0.72, (118, 116, 92)), (1.0, (196, 192, 182))]
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return tuple(c0[i] + (c1[i] - c0[i]) * f for i in range(3))  # type: ignore[return-value]
    return stops[-1][1]  # type: ignore[return-value]


@dataclass
class TerrainMesh:
    """Regular grid of terrain heights with a flat-shaded colour per cell."""

    frame: LocalFrame
    x0: float
    y0: float
    cell_m: float
    cols: int  # vertices per row
    rows: int
    z: List[List[float]]  # [row][col] exaggerated height (m)
    color: List[List[Tuple[int, int, int]]]  # [row][col] cell colour (cells = rows-1 x cols-1)

    def vertex(self, r: int, c: int) -> Tuple[float, float, float]:
        return self.x0 + c * self.cell_m, self.y0 + r * self.cell_m, self.z[r][c]

    def cell_of(self, x: float, y: float) -> Tuple[int, int]:
        return int((y - self.y0) // self.cell_m), int((x - self.x0) // self.cell_m)

    def height_at(self, x: float, y: float) -> float:
        fx = max(0.0, min(self.cols - 1.001, (x - self.x0) / self.cell_m))
        fy = max(0.0, min(self.rows - 1.001, (y - self.y0) / self.cell_m))
        c, r = int(fx), int(fy)
        tx, ty = fx - c, fy - r
        z = self.z
        top = z[r][c] + (z[r][c + 1] - z[r][c]) * tx
        bot = z[r + 1][c] + (z[r + 1][c + 1] - z[r + 1][c]) * tx
        return top + (bot - top) * ty


def build_mesh(
    dem: Optional[DEMGrid],
    frame: LocalFrame,
    bounds_xy: Tuple[float, float, float, float],
    cell_m: float = 120.0,
    fallback_ele: float = 0.0,
) -> TerrainMesh:
    """Sample the DEM on a `cell_m` grid covering bounds (x_min, y_min, x_max, y_max)."""
    x_min, y_min, x_max, y_max = bounds_xy
    cols = max(2, int(math.ceil((x_max - x_min) / cell_m)) + 1)
    rows = max(2, int(math.ceil((y_max - y_min) / cell_m)) + 1)
    z: List[List[float]] = []
    for r in range(rows):
        row = []
        for c in range(cols):
            lat, lon = frame.to_latlon(x_min + c * cell_m, y_min + r * cell_m)
            row.append(frame.z(sample_dem(dem, lat, lon, fallback_ele) if dem else fallback_ele))
        z.append(row)
    lo = min(min(r) for r in z)
    hi = max(max(r) for r in z)
    span = max(1.0, hi - lo)
    # Sun from the north-west, 40° up
    lx, ly, lz = -0.54, 0.54, 0.64
    color: List[List[Tuple[int, int, int]]] = []
    for r in range(rows - 1):
        crow = []
        for c in range(cols - 1):
            z00, z10, z01, z11 = z[r][c], z[r][c + 1], z[r + 1][c], z[r + 1][c + 1]
            dzdx = ((z10 - z00) + (z11 - z01)) / (2 * cell_m)
            dzdy = ((z01 - z00) + (z11 - z10)) / (2 * cell_m)
            n = math.sqrt(dzdx * dzdx + dzdy * dzdy + 1.0)
            shade = max(0.0, (-dzdx * lx - dzdy * ly + lz) / n)
            base = _hypso(((z00 + z10 + z01 + z11) / 4.0 - lo) / span)
            f = 0.45 + 0.85 * shade
            crow.append(tuple(min(255, int(ch * f)) for ch in base))  # type: ignore[misc]
        color.append(crow)
    return TerrainMesh(frame, x_min, y_min, cell_m, cols, rows, z, color)


@dataclass
class RoutePath:
    """Route in the local frame with cumulative distance for position lookups."""

    pts: List[Tuple[float, float, float]]  # x, y, z (exaggerated)
    ele: List[float]  # true elevation (m)
    cum: List[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cum = [0.0]
        for a, b in zip(self.pts, self.pts[1:]):
            self.cum.append(self.cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))

    @property
    def length(self) -> float:
        return self.cum[-1]

    def at(self, d: float) -> Tuple[float, float, float, float]:
        """(x, y, z, true elevation) at distance d along the route."""
        d = max(0.0, min(self.length, d))
        i = max(1, min(len(self.cum) - 1, bisect.bisect_left(self.cum, d)))
        seg = self.cum[i] - self.cum[i - 1]
        t = 0.0 if seg <= 0 else (d - self.cum[i - 1]) / seg
        a, b = self.pts[i - 1], self.pts[i]
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t,
                self.ele[i - 1] + (self.ele[i] - self.ele[i - 1]) * t)

    def heading(self, d: float, behind: float = 150.0, ahead: float = 300.0) -> float:
        """Direction of travel (radians, 0 = east) smoothed over a window around d."""
        a = self.at(d - behind)
        b = self.at(d + ahead)
        if abs(b[0] - a[0]) + abs(b[1] - a[1]) < 1e-6:
            return 0.0
        return math.atan2(b[1] - a[1], b[0] - a[0])


def route_path(coords_3d: Sequence[Sequence[float]], frame: LocalFrame, step_m: float = 20.0) -> RoutePath:
    """Resample [lon, lat, ele] route coordinates to roughly even `step_m` spacing."""
    pts: List[Tuple[float, float, float]] = []
    ele: List[float] = []
    prev = None
    for lon, lat, e in ((c[0], c[1], c[2] if len(c) > 2 else 0.0) for c in coords_3d):
        x, y = frame.to_xy(lat, lon)
        if prev is not None:
            px, py, pe = prev
            dist = math.hypot(x - px, y - py)
            n = int(dist // step_m)
            for k in range(1, n + 1):
                t = k / (n + 1)
                ee = pe + (e - pe) * t
                pts.append((px + (x - px) * t, py + (y - py) * t, frame.z(ee)))
                ele.append(ee)
        pts.append((x, y, frame.z(e)))
        ele.append(e)
        prev = (x, y, e)
    if len(pts) == 1:
        pts.append(pts[0])
        ele.append(ele[0])
    return RoutePath(pts, ele)


@dataclass
class Camera:
    x: float
    y: float
    z: float
    yaw: float  # radians, direction the camera faces (0 = east)
    pitch: float  # radians below horizontal
    focal: float  # pixels
    cx: float  # screen centre
    cy: float

    def basis(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]:
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        f = (math.cos(self.yaw) * cp, math.sin(self.yaw) * cp, -sp)  # forward
        r = (math.sin(self.yaw), -math.cos(self.yaw), 0.0)  # right
        u = (r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0])  # up
        return f, r, u

    def projector(self):
        """Fast closure: (x, y, z) -> (screen_x, screen_y, depth) or None when behind the camera."""
        (fx, fy, fz), (rx, ry, rz), (ux, uy, uz) = self.basis()
        cx_, cy_, cz_ = self.x, self.y, self.z
        focal, scx, scy = self.focal, self.cx, self.cy

        def project(x: float, y: float, z: float) -> Optional[Tuple[float, float, float]]:
            dx, dy, dz = x - cx_, y - cy_, z - cz_
            depth = dx * fx + dy * fy + dz * fz
            if depth < 5.0:
                return None
            return (scx + focal * (dx * rx + dy * ry + dz * rz) / depth,
                    scy - focal * (dx * ux + dy * uy + dz * uz) / depth,
                    depth)

        return project


def chase_camera(
    target: Tuple[float, float, float], yaw: float, width: int, height: int,
    back_m: float = 1300.0, up_m: float = 600.0, look_ahead_m: float = 500.0,
    ground_at=None, clearance_m: float = 150.0,
) -> Camera:
    """Camera behind and above `target`, looking a little ahead of it along `yaw`."""
    tx, ty, tz = target
    cxw = tx - math.cos(yaw) * back_m
    cyw = ty - math.sin(yaw) * back_m
    czw = tz + up_m
    if ground_at is not None:
        # Never inside a mountain: a ridge behind the hiker lifts the camera (and steepens the view)
        czw = max(czw, ground_at(cxw, cyw) + clearance_m)
    horiz = back_m + look_ahead_m
    pitch = math.atan2(czw - tz, horiz)
    return Camera(cxw, cyw, czw, yaw, pitch, focal=width * 0.85, cx=width / 2.0, cy=height * 0.42)


def look_camera(
    hiker: Tuple[float, float, float], target: Tuple[float, float, float], fallback_yaw: float,
    width: int, height: int, back_m: float = 750.0, up_m: float = 380.0,
    ground_at=None, clearance_m: float = 150.0,
) -> Camera:
    """Closer camera behind the hiker that looks at `target` (where a photo was taken)."""
    hx, hy, hz = hiker
    dx, dy = target[0] - hx, target[1] - hy
    yaw = math.atan2(dy, dx) if math.hypot(dx, dy) > 40.0 else fallback_yaw
    cxw = hx - math.cos(yaw) * back_m
    cyw = hy - math.sin(yaw) * back_m
    czw = hz + up_m
    if ground_at is not None:
        czw = max(czw, ground_at(cxw, cyw) + clearance_m)
    horiz = max(1.0, math.hypot(target[0] - cxw, target[1] - cyw))
    pitch = math.atan2(czw - target[2], horiz)
    return Camera(cxw, cyw, czw, yaw, pitch, focal=width * 0.85, cx=width / 2.0, cy=height * 0.42)


def blend_cameras(a: Camera, b: Camera, t: float) -> Camera:
    """Camera t of the way from a to b (position, heading and tilt)."""
    t = max(0.0, min(1.0, t))
    return Camera(
        a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t,
        lerp_angle(a.yaw, b.yaw, t), a.pitch + (b.pitch - a.pitch) * t,
        a.focal + (b.focal - a.focal) * t, a.cx, a.cy,
    )


def smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def lerp_angle(a: float, b: float, t: float) -> float:
    d = (b - a + math.pi) % (2 * math.pi) - math.pi
    return a + d * t
