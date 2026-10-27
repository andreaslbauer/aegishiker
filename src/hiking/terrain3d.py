"""Qt-free 3D geometry for the hike replay animation.

Local metric frame: x east, y north (metres from the region's centre), z up (metres, optionally
exaggerated). A regular terrain mesh is sampled from the pack DEM; a chase camera follows the
hiker along the route; points are perspective-projected to screen pixels.
"""
import bisect
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

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
    mesh = TerrainMesh(frame, x_min, y_min, cell_m, cols, rows, z, [])
    shade_mesh(mesh, lambda r, c: _hypso(((z[r][c] + z[r][c + 1] + z[r + 1][c] + z[r + 1][c + 1]) / 4.0 - lo) / span))
    return mesh


class MeshShader:
    """Lights the mesh for a sun direction: each cell's ground colour and slope are worked out once,
    so re-shading for another time of day is one pass of dot products."""

    def __init__(self, mesh: TerrainMesh, ground: Callable[[int, int], Tuple[float, float, float]]) -> None:
        z, cell_m = mesh.z, mesh.cell_m
        self.cols, self.rows, self.cell_m = mesh.cols - 1, mesh.rows - 1, cell_m
        self.normals: List[Tuple[float, float, float]] = []
        self.ground: List[Tuple[float, float, float]] = []
        self.heights: List[float] = []  # true height of each cell's centre (m), for cast shadows
        zx = mesh.frame.zx or 1.0
        for r in range(mesh.rows - 1):
            for c in range(mesh.cols - 1):
                z00, z10, z01, z11 = z[r][c], z[r][c + 1], z[r + 1][c], z[r + 1][c + 1]
                self.heights.append((z00 + z10 + z01 + z11) / (4.0 * zx))
                dzdx = ((z10 - z00) + (z11 - z01)) / (2 * cell_m)
                dzdy = ((z01 - z00) + (z11 - z10)) / (2 * cell_m)
                n = math.sqrt(dzdx * dzdx + dzdy * dzdy + 1.0)
                self.normals.append((-dzdx / n, -dzdy / n, 1.0 / n))
                self.ground.append(tuple(ground(r, c)))  # type: ignore[arg-type]

    def shadows(self, sun: Tuple[float, float, float]) -> Optional[bytearray]:
        """Cells in the shadow of terrain between them and the sun (1) or lit (0), row by row; None when
        the sun is overhead or set. One sweep, not a ray per cell: cells are taken nearest the sun first,
        and each carries on the shadow line of the point 1.5 cells sunward of it (already done),
        sinking by the sun's slope over that step."""
        lx, ly, lz = sun
        flat = math.hypot(lx, ly)
        if lz <= 0.0 or flat < 1e-3 or lz / flat > 6.0:
            return None
        ux, uy = lx / flat, ly / flat
        step = 1.5  # cells; the sunward point's four neighbours are all nearer the sun than this cell
        drop = step * self.cell_m * lz / flat
        cols, rows, hts = self.cols, self.rows, self.heights
        line = [-1e9] * (cols * rows)  # shadow line height over each cell
        order = sorted(range(cols * rows), key=lambda i: -((i % cols) * ux + (i // cols) * uy))
        out = bytearray(cols * rows)
        for i in order:
            c, r = i % cols, i // cols
            fx, fy = c + ux * step, r + uy * step
            h = hts[i]
            if 0.0 <= fx <= cols - 1 and 0.0 <= fy <= rows - 1:
                c0, r0 = min(int(fx), cols - 2), min(int(fy), rows - 2)
                tx, ty = fx - c0, fy - r0
                j = r0 * cols + c0
                top = line[j] + (line[j + 1] - line[j]) * tx
                bot = line[j + cols] + (line[j + cols + 1] - line[j + cols]) * tx
                over = top + (bot - top) * ty - drop
                if over > h + 2.0:
                    out[i] = 1
                    line[i] = over
                    continue
            line[i] = h
        return out

    def colors(self, sun: Tuple[float, float, float] = (-0.54, 0.54, 0.64), ambient: float = 0.45,
               diffuse: float = 0.85, snow: float = 0.0,
               shadow: Optional[bytearray] = None) -> List[List[Tuple[int, int, int]]]:
        """Cell colours lit by `sun`; `snow` (0..1) whitens the ground, most on gentle slopes; cells
        marked in `shadow` get no direct sun."""
        lx, ly, lz = sun
        cover = min(0.85, snow * 1.3)
        flat = []
        for i, ((nx, ny, nz), (gr, gg, gb)) in enumerate(zip(self.normals, self.ground)):
            if cover > 0.0:
                k = cover * nz ** 6  # steep rock sheds the snow
                gr, gg, gb = gr + (232 - gr) * k, gg + (236 - gg) * k, gb + (242 - gb) * k
            f = ambient if shadow is not None and shadow[i] else ambient + diffuse * max(0.0, nx * lx + ny * ly + nz * lz)
            flat.append((min(255, int(gr * f)), min(255, int(gg * f)), min(255, int(gb * f))))
        return [flat[i:i + self.cols] for i in range(0, len(flat), self.cols)]


def shade_mesh(mesh: TerrainMesh, ground: Callable[[int, int], Tuple[float, float, float]]) -> MeshShader:
    """(Re)colour the mesh: each cell's `ground(row, col)` colour, lit by a sun from the north-west, 40° up.
    Returns the shader, to light the mesh for other times of day."""
    shader = MeshShader(mesh, ground)
    mesh.color = shader.colors()
    return shader


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


def compass_to_yaw(bearing_deg: float) -> float:
    """Compass bearing (0 = north, clockwise) as a world heading (radians, 0 = east, anticlockwise)."""
    return math.radians(90.0 - bearing_deg)


def photo_camera(
    spot: Tuple[float, float, float], yaw: float, width: int, height: int,
    back_m: float = 450.0, up_m: float = 220.0, ahead_m: float = 900.0,
    ground_at=None, clearance_m: float = 120.0,
) -> Camera:
    """Camera just behind and above where a photo was taken, looking the way the photo was."""
    sx, sy, sz = spot
    cxw = sx - math.cos(yaw) * back_m
    cyw = sy - math.sin(yaw) * back_m
    czw = sz + up_m
    tz = sz
    if ground_at is not None:
        czw = max(czw, ground_at(cxw, cyw) + clearance_m)
        tz = ground_at(sx + math.cos(yaw) * ahead_m, sy + math.sin(yaw) * ahead_m)
    # Down onto the land ahead, but never so far up or down that the view is all sky or all ground
    pitch = max(-0.05, min(0.45, math.atan2(czw - tz, back_m + ahead_m)))
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


# ── map layers draped on the terrain (other trails, paths, streams, rivers, lakes) ────────────

LAYER_STYLES = ("water_edge", "river", "stream", "trail", "path")  # drawing order, bottom to top
DRAPE_STEP_M = 60.0  # lines are cut into pieces this long so they follow the terrain (cells are 120 m)
DRAPE_LIFT_M = 4.0  # drawn this far above the ground so the terrain doesn't swallow them


@dataclass
class DrapedLayers:
    """Map lines draped on the terrain mesh, as segments grouped by the mesh cell they lie in (the
    3D view draws each cell's lines right after the cell, so nearer hills still hide them)."""

    kinds: List[str] = field(default_factory=list)  # per segment
    segs: List[Tuple[float, float, float, float, float, float]] = field(default_factory=list)  # x0,y0,z0,x1,y1,z1
    cells: Dict[Tuple[int, int], List[int]] = field(default_factory=dict)  # (row, col) -> segment indices
    # Water surfaces: per cell, the parts of lakes / ponds inside it as [(x, y, z), ...] rings
    water: Dict[Tuple[int, int], List[List[Tuple[float, float, float]]]] = field(default_factory=dict)


def _inside(bounds: Tuple[float, float, float, float], x: float, y: float) -> bool:
    return bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]


def drape_lines(mesh: TerrainMesh, lines: Sequence[Tuple[str, Sequence[Sequence[float]]]],
                step_m: float = DRAPE_STEP_M, lift_m: float = DRAPE_LIFT_M) -> DrapedLayers:
    """Drape (kind, [[lon, lat], ...]) lines on the mesh. Parts outside the mesh are dropped."""
    frame = mesh.frame
    bounds = (mesh.x0, mesh.y0, mesh.x0 + (mesh.cols - 1) * mesh.cell_m, mesh.y0 + (mesh.rows - 1) * mesh.cell_m)
    out = DrapedLayers()
    for kind, coords in lines:
        pts = [frame.to_xy(c[1], c[0]) for c in coords]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            if not (_inside(bounds, ax, ay) or _inside(bounds, bx, by)):
                continue
            n = max(1, int(math.hypot(bx - ax, by - ay) // step_m) + 1)
            prev = None
            for k in range(n + 1):
                x, y = ax + (bx - ax) * k / n, ay + (by - ay) * k / n
                cur = (x, y, mesh.height_at(x, y) + lift_m * frame.zx)
                if prev is not None and _inside(bounds, x, y) and _inside(bounds, prev[0], prev[1]):
                    idx = len(out.segs)
                    out.segs.append((*prev, *cur))
                    out.kinds.append(kind)
                    out.cells.setdefault(mesh.cell_of((prev[0] + x) / 2, (prev[1] + y) / 2), []).append(idx)
                prev = cur
    return out


def _clip_ring(ring: List[Tuple[float, float]], x0: float, y0: float, x1: float, y1: float) -> List[Tuple[float, float]]:
    """Sutherland–Hodgman clip of a polygon to an axis-aligned rectangle."""
    def clip(pts, inside, cross):
        out = []
        for i, cur in enumerate(pts):
            prev = pts[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
        return out

    def at_x(x):
        return lambda a, b: (x, a[1] + (b[1] - a[1]) * (x - a[0]) / ((b[0] - a[0]) or 1e-12))

    def at_y(y):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (y - a[1]) / ((b[1] - a[1]) or 1e-12), y)

    pts = ring
    for inside, cross in ((lambda p: p[0] >= x0, at_x(x0)), (lambda p: p[0] <= x1, at_x(x1)),
                          (lambda p: p[1] >= y0, at_y(y0)), (lambda p: p[1] <= y1, at_y(y1))):
        if not pts:
            break
        pts = clip(pts, inside, cross)
    return pts


def drape_water(mesh: TerrainMesh, polygons: Sequence[Sequence[Sequence[float]]], into: DrapedLayers,
                lift_m: float = 2.0) -> None:
    """Lakes and ponds ([[lon, lat], ...] outlines) cut into the mesh cells they cover, each piece
    draped on its cell, so the 3D view can draw water with the terrain it lies on."""
    frame = mesh.frame
    for poly in polygons:
        ring = [frame.to_xy(c[1], c[0]) for c in poly]
        if len(ring) >= 2 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) < 3:
            continue
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        r0, c0 = mesh.cell_of(min(xs), min(ys))
        r1, c1 = mesh.cell_of(max(xs), max(ys))
        for r in range(max(0, r0), min(mesh.rows - 2, r1) + 1):
            for c in range(max(0, c0), min(mesh.cols - 2, c1) + 1):
                cx0, cy0 = mesh.x0 + c * mesh.cell_m, mesh.y0 + r * mesh.cell_m
                piece = _clip_ring(ring, cx0, cy0, cx0 + mesh.cell_m, cy0 + mesh.cell_m)
                if len(piece) >= 3:
                    into.water.setdefault((r, c), []).append(
                        [(x, y, mesh.height_at(x, y) + lift_m * frame.zx) for x, y in piece])


def replay_map_layers(pack, mesh: TerrainMesh, skip_trail_ids: Sequence[str] = ()) -> DrapedLayers:
    """The region's other trails, paths and tracks, streams, rivers and lake shores on the terrain;
    lakes and ponds also tint the terrain. `pack` is a DestinationPack (or None)."""
    if pack is None:
        return DrapedLayers()
    frame = mesh.frame
    x1 = mesh.x0 + (mesh.cols - 1) * mesh.cell_m
    y1 = mesh.y0 + (mesh.rows - 1) * mesh.cell_m
    lat_lo, lon_lo = frame.to_latlon(mesh.x0, mesh.y0)
    lat_hi, lon_hi = frame.to_latlon(x1, y1)

    def near(coords) -> bool:
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        return not (max(lons) < lon_lo or min(lons) > lon_hi or max(lats) < lat_lo or min(lats) > lat_hi)

    skip = set(skip_trail_ids)
    trail_names = {t.name for t in pack.trails}
    lines: List[Tuple[str, Sequence[Sequence[float]]]] = []
    water: List[Sequence[Sequence[float]]] = []
    for f in pack.features:
        if len(f.coords) < 2 or not near(f.coords):
            continue
        if f.kind == "water":
            if f.geom == "area":
                water.append(f.coords)  # filled (its outline is the fill's edge)
            else:
                lines.append(("water_edge", f.coords))  # a shoreline cut by the region's edge
        elif f.kind in ("river", "stream"):
            lines.append((f.kind, f.coords))
        elif f.kind in ("path", "track") and f.geom == "line":
            # Paths that are pieces of a listed trail are drawn as that trail
            if f.name and any(n.strip() in trail_names or any(t.startswith(n.strip() + " (") for t in trail_names)
                              for n in f.name.split("/")):
                continue
            lines.append(("path", f.coords))
    for t in pack.trails:
        if t.id not in skip and len(t.coords_3d) >= 2 and near(t.coords_3d):
            lines.append(("trail", [c[:2] for c in t.coords_3d]))
    order = {k: i for i, k in enumerate(LAYER_STYLES)}
    lines.sort(key=lambda kl: order.get(kl[0], 0))
    layers = drape_lines(mesh, lines)
    drape_water(mesh, water, layers)
    return layers
