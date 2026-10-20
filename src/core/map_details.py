"""Downloads real basemap detail for a destination pack's bounding box.

  - Terrain: AWS Open Data Terrain Tiles (Terrarium PNG encoding; USGS 3DEP in the US) →
    a DEMGrid for hillshading/elevation lookup and computed contour lines.
  - OpenStreetMap (Overpass API): every footpath/track, roads, streams, lakes, cliffs, scree,
    wetlands, peaks, saddles, springs, drinking water, shelters, huts, campsites, viewpoints,
    waterfalls, parking, trailheads, toilets, ranger stations and signposts.

Used by tools/fetch_map_details.py (bundled packs) and the opt-in region downloader.
Every network call is logged to ~/.aegishiker/logs/network.jsonl.
"""
import io
import logging
import heapq
import math
import re
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import requests
from PIL import Image

from src.hiking.elevation import summarize_elevation
from src.hiking.geo import compute_bbox, haversine_m
from src.hiking.models import (
    ContourLine, DEMGrid, DestinationPack, MapFeature, TrailCondition, TrailDifficulty, TrailSegment,
)

from . import llm_log

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
USER_AGENT = "AegisHiker/1.0 (offline hiking planner; map pack builder)"
ATTRIBUTION = "© OpenStreetMap contributors (ODbL) · Terrain: USGS 3DEP / AWS Terrain Tiles"

MAX_MOSAIC_PX = 2400  # longest side of the contouring grid (~25-35 m cells for typical packs)
MAX_STORED_DEM = 560  # longest side of the DEM kept in the pack for hillshading/lookup

Bbox = Tuple[float, float, float, float]  # (min_lat, min_lon, max_lat, max_lon)
Progress = Optional[Callable[[str], None]]

_M_PER_DEG_LAT = 110540.0
_M_PER_DEG_LON_EQ = 111320.0


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


# ── Terrain ─────────────────────────────────────────────────────────────────────


def _lonlat_to_global_px(lon: float, lat: float, z: int) -> Tuple[float, float]:
    n = 256.0 * (2**z)
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def _pick_zoom(bbox: Bbox) -> int:
    min_lat, min_lon, max_lat, max_lon = bbox
    for z in range(14, 7, -1):
        x0, y0 = _lonlat_to_global_px(min_lon, max_lat, z)
        x1, y1 = _lonlat_to_global_px(max_lon, min_lat, z)
        if max(x1 - x0, y1 - y0) <= MAX_MOSAIC_PX:
            return z
    return 8


def fetch_terrain_grid(
    bbox: Bbox, session: Optional[requests.Session] = None, progress: Progress = None
) -> Tuple[List[List[float]], int, int]:
    """Return a lat/lon-regular elevation grid (row 0 = min_lat) covering bbox at ~tile resolution."""
    session = session or _session()
    min_lat, min_lon, max_lat, max_lon = bbox
    z = _pick_zoom(bbox)
    gx0, gy0 = _lonlat_to_global_px(min_lon, max_lat, z)
    gx1, gy1 = _lonlat_to_global_px(max_lon, min_lat, z)
    tx0, ty0, tx1, ty1 = int(gx0 // 256), int(gy0 // 256), int(gx1 // 256), int(gy1 // 256)

    tiles: Dict[Tuple[int, int], List[float]] = {}
    total = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            url = TERRARIUM_URL.format(z=z, x=tx, y=ty)
            if progress:
                progress(f"terrain tile {len(tiles) + 1}/{total} (z{z})")
            try:
                resp = session.get(url, timeout=30)
                llm_log.log_network_request(
                    "terrain_tiles", url, status_code=resp.status_code, bytes_received=len(resp.content)
                )
                resp.raise_for_status()
            except Exception as e:
                llm_log.log_network_request("terrain_tiles", url, error=str(e))
                raise RuntimeError(f"Could not download terrain tile {url}: {e}") from e
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            tiles[(tx, ty)] = [r * 256.0 + g + b / 256.0 - 32768.0 for (r, g, b) in img.getdata()]

    def sample(px: float, py: float) -> float:
        # Bilinear in global mercator pixel space
        px -= 0.5
        py -= 0.5
        x0, y0 = math.floor(px), math.floor(py)
        fx, fy = px - x0, py - y0
        vals = []
        for dy in (0, 1):
            for dx in (0, 1):
                ix, iy = x0 + dx, y0 + dy
                t = tiles.get((ix // 256, iy // 256))
                if t is None:
                    t = tiles[(min(max(ix // 256, tx0), tx1), min(max(iy // 256, ty0), ty1))]
                    ix = min(max(ix, tx0 * 256), tx1 * 256 + 255)
                    iy = min(max(iy, ty0 * 256), ty1 * 256 + 255)
                vals.append(t[(iy % 256) * 256 + (ix % 256)])
        top = vals[0] + (vals[1] - vals[0]) * fx
        bot = vals[2] + (vals[3] - vals[2]) * fx
        return top + (bot - top) * fy

    # Square-ish cells in metres, matched to the mosaic resolution
    mid_lat = 0.5 * (min_lat + max_lat)
    width_m = (max_lon - min_lon) * _M_PER_DEG_LON_EQ * math.cos(math.radians(mid_lat))
    height_m = (max_lat - min_lat) * _M_PER_DEG_LAT
    cols = max(8, int(round(gx1 - gx0)))
    cell_m = width_m / (cols - 1)
    rows = max(8, int(round(height_m / cell_m)) + 1)

    if progress:
        progress(f"resampling terrain to {cols}x{rows} grid")
    px_cols = [_lonlat_to_global_px(min_lon + c / (cols - 1) * (max_lon - min_lon), mid_lat, z)[0] for c in range(cols)]
    grid: List[List[float]] = []
    for r in range(rows):
        lat = min_lat + r / (rows - 1) * (max_lat - min_lat)
        py = _lonlat_to_global_px(min_lon, lat, z)[1]
        grid.append([sample(px, py) for px in px_cols])
    return grid, rows, cols


def _box_blur(grid: List[List[float]]) -> List[List[float]]:
    """Separable 3x3 box blur: removes 1-pixel tile noise so contours come out smooth."""
    rows, cols = len(grid), len(grid[0])
    tmp = []
    for row in grid:
        out = [0.0] * cols
        for c in range(cols):
            a, b = max(0, c - 1), min(cols - 1, c + 1)
            out[c] = (row[a] + row[c] + row[b]) / 3.0
        tmp.append(out)
    res = []
    for r in range(rows):
        a, b = tmp[max(0, r - 1)], tmp[min(rows - 1, r + 1)]
        m = tmp[r]
        res.append([(a[c] + m[c] + b[c]) / 3.0 for c in range(cols)])
    return res


def downsample_dem(grid: List[List[float]], bbox: Bbox, max_side: int = MAX_STORED_DEM) -> DEMGrid:
    min_lat, min_lon, max_lat, max_lon = bbox
    rows, cols = len(grid), len(grid[0])
    step = max(1.0, max(rows, cols) / float(max_side))
    out_rows = max(2, int(round((rows - 1) / step)) + 1)
    out_cols = max(2, int(round((cols - 1) / step)) + 1)
    out: List[List[float]] = []
    for r in range(out_rows):
        sr = min(rows - 1, int(round(r * (rows - 1) / (out_rows - 1))))
        src = grid[sr]
        out.append([float(round(src[min(cols - 1, int(round(c * (cols - 1) / (out_cols - 1))))])) for c in range(out_cols)])
    return DEMGrid(
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
        rows=out_rows, cols=out_cols, elevations_m=out,
    )


def choose_contour_interval(min_ele: float, max_ele: float, units: str = "ft") -> float:
    """Contour interval in metres, chosen as a round number in the map's display units."""
    relief = max_ele - min_ele
    if units == "ft":
        ft = 200.0 if relief > 900 else (80.0 if relief > 450 else 40.0)
        return ft * 0.3048
    return 50.0 if relief > 900 else (20.0 if relief > 450 else 10.0)


def _simplify(points: List[Tuple[float, float]], tol: float) -> List[Tuple[float, float]]:
    """Iterative Douglas-Peucker on planar coordinates."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = tol * tol
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best, best_i = -1.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            if seg2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
                d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d2 > best:
                best, best_i = d2, i
        if best > tol2 and best_i > 0:
            keep[best_i] = True
            stack.append((a, best_i))
            stack.append((best_i, b))
    return [p for p, k in zip(points, keep) if k]


def compute_contours(
    grid: List[List[float]], bbox: Bbox, interval: float, simplify_cells: float = 0.45, min_points: int = 4
) -> List[ContourLine]:
    """Marching squares over the grid → chained, simplified contour polylines ([[lon, lat], ...])."""
    min_lat, min_lon, max_lat, max_lon = bbox
    rows, cols = len(grid), len(grid[0])
    segs: Dict[float, List[Tuple[int, int]]] = defaultdict(list)
    pos: Dict[float, Dict[int, Tuple[float, float]]] = defaultdict(dict)

    # Edge ids: horizontal edge (r,c)-(r,c+1) → 2*(r*cols+c); vertical edge (r,c)-(r+1,c) → +1
    for r in range(rows - 1):
        g0, g1 = grid[r], grid[r + 1]
        for c in range(cols - 1):
            z00, z01, z10, z11 = g0[c], g0[c + 1], g1[c], g1[c + 1]
            lo = min(z00, z01, z10, z11)
            hi = max(z00, z01, z10, z11)
            k = math.floor(lo / interval) + 1
            while k * interval <= hi:
                level = round(k * interval, 2)
                case = (z00 >= level) | ((z01 >= level) << 1) | ((z11 >= level) << 2) | ((z10 >= level) << 3)
                if case not in (0, 15):
                    e_b = 2 * (r * cols + c)  # bottom (row r)
                    e_t = 2 * ((r + 1) * cols + c)  # top (row r+1)
                    e_l = 2 * (r * cols + c) + 1  # left (col c)
                    e_r = 2 * (r * cols + c + 1) + 1  # right (col c+1)
                    p = pos[level]
                    if e_b not in p and (z00 >= level) != (z01 >= level):
                        p[e_b] = (c + (level - z00) / (z01 - z00), r)
                    if e_t not in p and (z10 >= level) != (z11 >= level):
                        p[e_t] = (c + (level - z10) / (z11 - z10), r + 1)
                    if e_l not in p and (z00 >= level) != (z10 >= level):
                        p[e_l] = (c, r + (level - z00) / (z10 - z00))
                    if e_r not in p and (z01 >= level) != (z11 >= level):
                        p[e_r] = (c + 1, r + (level - z01) / (z11 - z01))
                    s = segs[level]
                    if case in (1, 14):
                        s.append((e_l, e_b))
                    elif case in (2, 13):
                        s.append((e_b, e_r))
                    elif case in (3, 12):
                        s.append((e_l, e_r))
                    elif case in (4, 11):
                        s.append((e_r, e_t))
                    elif case in (6, 9):
                        s.append((e_b, e_t))
                    elif case in (7, 8):
                        s.append((e_l, e_t))
                    else:  # saddles 5 / 10: resolve with the cell-centre value
                        centre_high = (z00 + z01 + z10 + z11) / 4.0 >= level
                        if (case == 5) == centre_high:
                            s.append((e_l, e_t))
                            s.append((e_b, e_r))
                        else:
                            s.append((e_l, e_b))
                            s.append((e_r, e_t))
                k += 1

    dlon = (max_lon - min_lon) / (cols - 1)
    dlat = (max_lat - min_lat) / (rows - 1)
    tol = simplify_cells  # in grid cells (cells are ~square in metres)

    out: List[ContourLine] = []
    for level in sorted(segs):
        adj: Dict[int, List[int]] = defaultdict(list)
        for a, b in segs[level]:
            adj[a].append(b)
            adj[b].append(a)
        p = pos[level]
        visited: set = set()
        starts = [e for e, n in adj.items() if len(n) == 1] + list(adj.keys())
        for start in starts:
            if start in visited:
                continue
            chain = [start]
            visited.add(start)
            cur = start
            while True:
                nxt = next((n for n in adj[cur] if n not in visited), None)
                if nxt is None:
                    break
                chain.append(nxt)
                visited.add(nxt)
                cur = nxt
            if start in adj[cur] and len(chain) > 2:
                chain.append(start)  # closed ring
            pts = _simplify([p[e] for e in chain], tol)
            if len(pts) < min_points:
                continue
            coords = [[round(min_lon + x * dlon, 5), round(min_lat + y * dlat, 5)] for x, y in pts]
            out.append(ContourLine(ele_m=float(level), coords=coords))
    return out


def sample_dem(dem: DEMGrid, lat: float, lon: float) -> Optional[float]:
    if not (dem.min_lat <= lat <= dem.max_lat and dem.min_lon <= lon <= dem.max_lon):
        return None
    fy = (lat - dem.min_lat) / (dem.max_lat - dem.min_lat) * (dem.rows - 1)
    fx = (lon - dem.min_lon) / (dem.max_lon - dem.min_lon) * (dem.cols - 1)
    r0, c0 = min(int(fy), dem.rows - 2), min(int(fx), dem.cols - 2)
    ty, tx = fy - r0, fx - c0
    g = dem.elevations_m
    top = g[r0][c0] + (g[r0][c0 + 1] - g[r0][c0]) * tx
    bot = g[r0 + 1][c0] + (g[r0 + 1][c0 + 1] - g[r0 + 1][c0]) * tx
    return top + (bot - top) * ty


# ── OpenStreetMap features ──────────────────────────────────────────────────────

_LINE_AREA_QUERY = """
  way["highway"~"^(path|footway|bridleway|steps|track)$"]["footway"!~"^(sidewalk|crossing|access_aisle)$"]({b});
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential)$"]({b});
  way["waterway"~"^(river|stream|canal)$"]({b});
  way["natural"~"^(water|cliff|bare_rock|scree|wetland|glacier)$"]({b});
  way["aerialway"~"^(cable_car|gondola|chair_lift)$"]({b});
  relation["natural"="water"]({b});
  way["natural"~"^(wood|scrub|heath|grassland)$"]({b});
  way["landuse"~"^(forest|meadow|grass|farmland|orchard|conservation)$"]({b});
  relation["natural"="wood"]({b});
  relation["landuse"="forest"]({b});
  way["leisure"="nature_reserve"]({b});
  relation["leisure"="nature_reserve"]({b});
  way["boundary"="protected_area"]({b});
  relation["boundary"="protected_area"]({b});
"""

_POINT_QUERY = """
  node["natural"~"^(peak|saddle|spring|cave_entrance|volcano)$"]({b});
  node["waterway"="waterfall"]({b});
  node["amenity"~"^(shelter|drinking_water|toilets|parking|ranger_station)$"]({b});
  node["tourism"~"^(alpine_hut|wilderness_hut|camp_site|viewpoint)$"]({b});
  node["information"="guidepost"]({b});
  node["highway"="trailhead"]({b});
  node["emergency"="phone"]({b});
  way["amenity"~"^(shelter|parking|ranger_station|toilets)$"]({b});
  way["tourism"~"^(alpine_hut|wilderness_hut|camp_site|viewpoint)$"]({b});
  way["highway"="trailhead"]({b});
  node["barrier"="gate"]({b});
  node["natural"~"^(rock|stone)$"]({b});
  way["natural"="rock"]({b});
  node["leisure"="picnic_table"]({b});
  nwr["tourism"="picnic_site"]({b});
  node["historic"]({b});
"""

_SAC_LABELS = {
    "hiking": "SAC T1 hiking",
    "mountain_hiking": "SAC T2 mountain hiking",
    "demanding_mountain_hiking": "SAC T3 demanding (scrambling possible)",
    "alpine_hiking": "SAC T4 alpine (hands needed)",
    "demanding_alpine_hiking": "SAC T5 demanding alpine",
    "difficult_alpine_hiking": "SAC T6 difficult alpine",
}


def fetch_osm_elements(
    bbox: Bbox, session: Optional[requests.Session] = None, timeout: float = 180.0, retries: int = 2
) -> List[Dict[str, Any]]:
    session = session or _session()
    min_lat, min_lon, max_lat, max_lon = bbox
    b = f"{min_lat:.5f},{min_lon:.5f},{max_lat:.5f},{max_lon:.5f}"
    query = (
        f"[out:json][timeout:{int(timeout) - 10}];"
        f"({_LINE_AREA_QUERY.format(b=b)});out tags geom({b}) qt;"
        f"({_POINT_QUERY.format(b=b)});out tags center qt;"
        # Long-distance routes (e.g. a pack trail spec's `osm_route`): members with clipped geometry
        f"relation[\"route\"~\"^(hiking|foot)$\"]({b});out body geom({b}) qt;"
    )
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(15 * attempt)  # Overpass sheds load with 429/502/504; back off and retry
        try:
            resp = session.post(OVERPASS_URL, data={"data": query}, timeout=timeout)
            llm_log.log_network_request(
                "osm_overpass_map_details", OVERPASS_URL, status_code=resp.status_code, bytes_received=len(resp.content)
            )
            if resp.status_code in (429, 502, 503, 504):
                last_err = RuntimeError(f"Overpass busy (HTTP {resp.status_code})")
                continue
            resp.raise_for_status()
            return list(resp.json().get("elements") or [])
        except Exception as e:
            llm_log.log_network_request("osm_overpass_map_details", OVERPASS_URL, error=str(e))
            last_err = e
    raise RuntimeError(f"Could not download OpenStreetMap map details: {last_err}") from last_err


def _parse_ele(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    txt = str(raw).strip().lower().replace(",", ".")
    try:
        if txt.endswith("ft") or txt.endswith("'"):
            return round(float(txt.rstrip("ft'").strip()) * 0.3048, 1)
        return round(float(txt.split()[0].rstrip("m")), 1)
    except (ValueError, IndexError):
        return None


def _classify(tags: Dict[str, str]) -> Optional[str]:
    hw = tags.get("highway", "")
    if hw in ("path", "footway", "bridleway", "steps"):
        return "path"
    if hw == "track":
        return "track"
    if hw in ("motorway", "trunk", "primary", "secondary"):
        return "road_major"
    if hw in ("tertiary", "unclassified", "residential"):
        return "road_minor"
    if hw == "trailhead":
        return "trailhead"
    ww = tags.get("waterway", "")
    if ww in ("river", "canal"):
        return "river"
    if ww == "stream":
        return "stream"
    if ww == "waterfall":
        return "waterfall"
    nat = tags.get("natural", "")
    if nat in ("water", "cliff", "bare_rock", "scree", "wetland", "glacier", "peak", "saddle", "spring"):
        return nat
    if nat in ("rock", "stone"):
        return "rock"
    # Land cover and protected land (New England woods are mapped this way rather than with
    # alpine features): forest vs open ground, and reserves / conservation land
    if tags.get("leisure") == "nature_reserve" or tags.get("boundary") == "protected_area" \
            or tags.get("landuse") == "conservation":
        return "reserve"
    if nat == "wood" or tags.get("landuse") == "forest":
        return "forest"
    if nat in ("scrub", "heath", "grassland") or tags.get("landuse") in ("meadow", "grass", "farmland", "orchard"):
        return "open"
    if tags.get("barrier") == "gate":
        return "gate"
    if tags.get("leisure") == "picnic_table" or tags.get("tourism") == "picnic_site":
        return "picnic"
    if tags.get("historic"):
        return "historic"
    if nat == "volcano":
        return "peak"
    if nat == "cave_entrance":
        return "cave"
    if tags.get("aerialway"):
        return "aerialway"
    tour = tags.get("tourism", "")
    if tour in ("alpine_hut", "wilderness_hut"):
        return "hut"
    if tour == "camp_site":
        return "campsite"
    if tour == "viewpoint":
        return "viewpoint"
    am = tags.get("amenity", "")
    if am == "parking" and (tags.get("access") in ("private", "customers", "permit", "no")
                            or tags.get("parking") in ("street_side", "lane", "underground", "multi-storey", "rooftop")):
        return None  # town lots and on-street parking, not trailhead parking
    if am in ("shelter", "drinking_water", "toilets", "parking", "ranger_station"):
        return am
    if tags.get("information") == "guidepost":
        return "guidepost"
    if tags.get("emergency") == "phone":
        return "emergency_phone"
    return None


def _info(kind: str, tags: Dict[str, str]) -> str:
    bits: List[str] = []
    if kind == "path":
        if tags.get("highway") == "steps":
            bits.append("steps")
        if tags.get("sac_scale") in _SAC_LABELS:
            bits.append(_SAC_LABELS[tags["sac_scale"]])
        if tags.get("trail_visibility") in ("bad", "horrible", "no"):
            bits.append(f"trail visibility {tags['trail_visibility']}")
        if tags.get("ref"):
            bits.append(f"ref {tags['ref']}")
    if kind in ("path", "track"):
        if tags.get("surface"):
            bits.append(tags["surface"].replace("_", " "))
        if tags.get("access") in ("no", "private"):
            bits.append("no public access")
    if kind in ("spring", "drinking_water"):
        dw = tags.get("drinking_water")
        bits.append({"yes": "potable", "no": "not potable — treat"}.get(dw or "", "potability unknown — treat"))
        if tags.get("seasonal") == "yes":
            bits.append("seasonal")
    if kind in ("hut", "shelter", "campsite"):
        if tags.get("shelter_type"):
            bits.append(tags["shelter_type"].replace("_", " "))
        if tags.get("capacity"):
            bits.append(f"capacity {tags['capacity']}")
        if tags.get("fee") == "yes":
            bits.append("fee")
        if tags.get("operator"):
            bits.append(tags["operator"])
        if tags.get("opening_hours"):
            bits.append(tags["opening_hours"])
        if tags.get("reservation") in ("required", "recommended"):
            bits.append(f"reservation {tags['reservation']}")
    if kind == "parking" and tags.get("fee") == "yes":
        bits.append("fee")
    if kind == "guidepost" and tags.get("description"):
        bits.append(tags["description"])
    if kind == "waterfall" and tags.get("height"):
        bits.append(f"height {tags['height']} m")
    if kind == "aerialway":
        bits.append(tags.get("aerialway", "").replace("_", " "))
    if kind == "historic":
        bits.append(tags["historic"].replace("_", " "))
        if tags.get("description"):
            bits.append(tags["description"])
    if kind == "reserve" and tags.get("operator"):
        bits.append(tags["operator"])
    if kind == "reserve" and tags.get("access") in ("no", "private"):
        bits.append("no public access")
    if kind == "gate" and tags.get("access") in ("no", "private"):
        bits.append("closed to the public")
    return "; ".join(b for b in bits if b)[:160]


def _stitch_rings(member_lines: List[List[Tuple[float, float]]]) -> List[List[Tuple[float, float]]]:
    """Join multipolygon outer member ways end-to-end into closed rings."""
    pending = [list(m) for m in member_lines if len(m) >= 2]
    rings: List[List[Tuple[float, float]]] = []
    while pending:
        ring = pending.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, m in enumerate(pending):
                if m[0] == ring[-1]:
                    ring.extend(m[1:])
                elif m[-1] == ring[-1]:
                    ring.extend(reversed(m[:-1]))
                elif m[-1] == ring[0]:
                    ring = m[:-1] + ring
                elif m[0] == ring[0]:
                    ring = list(reversed(m[1:])) + ring
                else:
                    continue
                pending.pop(i)
                changed = True
                break
        rings.append(ring)
    return rings


def build_features(elements: Sequence[Dict[str, Any]], mid_lat: float, simplify_m: float = 4.0) -> List[MapFeature]:
    kx = math.cos(math.radians(mid_lat))
    tol = simplify_m / _M_PER_DEG_LAT

    def finish(pts: List[Tuple[float, float]]) -> List[List[float]]:
        planar = [(lon * kx, lat) for lon, lat in pts]
        simp = _simplify(planar, tol)
        return [[round(x / kx, 5), round(y, 5)] for x, y in simp]

    features: List[MapFeature] = []
    seen: set = set()
    for el in elements:
        key = (el.get("type"), el.get("id"))
        if key in seen:
            continue
        seen.add(key)
        tags = el.get("tags") or {}
        kind = _classify(tags)
        if not kind:
            continue
        name = str(tags.get("name") or tags.get("ref") or "").strip()[:80]
        ele = _parse_ele(tags.get("ele"))
        info = _info(kind, tags)
        etype = el.get("type")

        if etype == "node" or "center" in el:
            c = el if etype == "node" else el["center"]
            features.append(MapFeature(
                kind=kind, geom="point", name=name, info=info, ele_m=ele,
                coords=[[round(float(c["lon"]), 5), round(float(c["lat"]), 5)]],
            ))
            continue

        if etype == "way":
            geom = [g for g in (el.get("geometry") or []) if g]
            pts = [(float(g["lon"]), float(g["lat"])) for g in geom]
            if len(pts) < 2:
                continue
            area_kinds = ("water", "bare_rock", "scree", "wetland", "glacier", "forest", "open", "reserve")
            is_area = kind in area_kinds and pts[0] == pts[-1]
            if kind in area_kinds and not is_area:
                # Area way cut by the bbox: keep water as a shoreline and reserves as a boundary line
                if kind not in ("water", "reserve"):
                    continue
            coords = finish(pts)
            if len(coords) >= 2:
                features.append(MapFeature(kind=kind, geom="area" if is_area else "line", name=name, info=info, coords=coords))
        elif etype == "relation":
            outers = [
                [(float(g["lon"]), float(g["lat"])) for g in (m.get("geometry") or []) if g]
                for m in (el.get("members") or [])
                if m.get("type") == "way" and m.get("role") in ("outer", "")
            ]
            for ring in _stitch_rings(outers):
                coords = finish(ring)
                if len(coords) >= 3:
                    features.append(MapFeature(
                        kind=kind, geom="area" if ring[0] == ring[-1] else "line", name=name, info=info, coords=coords,
                    ))
    return features


# ── Aligning sketched trails to the real OSM path network ──────────────────────

_GRAPH_COST = {"path": 1.0, "track": 1.0, "road_minor": 1.6}
SNAP_MAX_M = 1200.0  # sketched starter-pack anchors can be ~1 km off the real trailhead
OFF_NAME_PENALTY_M = 1000.0  # prefer a node on a same-named way unless another is this much closer
MAX_SPUR_M = 250.0  # out-and-back detours shorter than this (snap artefacts) are trimmed
_NAME_STOPWORDS = {
    "trail", "trails", "loop", "to", "via", "the", "and", "mt", "mount", "path", "descent", "complete",
    "summit", "return", "extension", "from", "circuit", "rock", "scramble", "connector", "north", "south",
    "east", "west", "ridge", "lake", "falls", "fall", "point", "view", "mountain", "peak", "head", "spur",
    "cutoff", "cut", "off", "link", "upper", "lower", "shore", "trailhead", "corridor", "overlook",
}


def _name_tokens(name: str) -> set:
    return {t for t in re.findall(r"[a-z0-9']+", name.lower()) if len(t) >= 3 and t not in _NAME_STOPWORDS}


class _PathGraph:
    """Walkable network from raw (unsimplified) Overpass ways; junctions = shared vertices."""

    def __init__(self, elements: Sequence[Dict[str, Any]]):
        self.pts: List[Tuple[float, float]] = []
        self.adj: Dict[int, List[Tuple[int, float, int]]] = defaultdict(list)  # (node, metres, way)
        self.ways: List[Tuple[str, set]] = []  # (kind, name tokens)
        self.node_ways: Dict[int, set] = defaultdict(set)
        self._ids: Dict[Tuple[float, float], int] = {}
        self._cells: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for el in elements:
            if el.get("type") != "way":
                continue
            tags = el.get("tags") or {}
            kind = _classify(tags)
            if kind not in _GRAPH_COST or tags.get("access") in ("no", "private"):
                continue
            wid = len(self.ways)
            self.ways.append((kind, _name_tokens(tags.get("name", "")) if kind != "road_minor" else set()))
            geom = [g for g in (el.get("geometry") or []) if g]
            ids = [self._node(float(g["lon"]), float(g["lat"])) for g in geom]
            for n in ids:
                self.node_ways[n].add(wid)
            for a, b in zip(ids, ids[1:]):
                (lon1, lat1), (lon2, lat2) = self.pts[a], self.pts[b]
                w = haversine_m(lat1, lon1, lat2, lon2)
                self.adj[a].append((b, w, wid))
                self.adj[b].append((a, w, wid))

    def _node(self, lon: float, lat: float) -> int:
        key = (round(lon, 7), round(lat, 7))
        nid = self._ids.get(key)
        if nid is None:
            nid = len(self.pts)
            self._ids[key] = nid
            self.pts.append(key)
            self._cells[(int(lon * 100), int(lat * 100))].append(nid)
        return nid

    def matching_ways(self, trail_name: str) -> set:
        tokens = _name_tokens(trail_name)
        return {i for i, (_k, wt) in enumerate(self.ways) if wt & tokens}

    def nearest(self, lon: float, lat: float, preferred: set, max_m: float = SNAP_MAX_M) -> Optional[int]:
        cx, cy = int(lon * 100), int(lat * 100)  # ~0.01° cells (~800-1100 m)
        best, best_score = None, math.inf
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                for nid in self._cells.get((cx + dx, cy + dy), ()):
                    plon, plat = self.pts[nid]
                    d = haversine_m(lat, lon, plat, plon)
                    if d > max_m:
                        continue
                    score = d if self.node_ways[nid] & preferred else d + OFF_NAME_PENALTY_M
                    if score < best_score:
                        best, best_score = nid, score
        return best

    def shortest(self, a: int, b: int, preferred: set, max_cost: float) -> Optional[List[int]]:
        """Dijkstra where same-named trails cost 1x, other paths 2.5x, minor roads 4x."""
        if a == b:
            return [a]
        dist = {a: 0.0}
        prev: Dict[int, int] = {}
        heap = [(0.0, a)]
        while heap:
            d, n = heapq.heappop(heap)
            if n == b:
                out = [b]
                while out[-1] != a:
                    out.append(prev[out[-1]])
                return out[::-1]
            if d > dist.get(n, math.inf) or d > max_cost:
                continue
            for m, w, wid in self.adj.get(n, ()):
                kind = self.ways[wid][0]
                mult = 1.0 if wid in preferred else (4.0 if kind == "road_minor" else 2.5)
                nd = d + w * mult
                if nd < dist.get(m, math.inf):
                    dist[m] = nd
                    prev[m] = n
                    heapq.heappush(heap, (nd, m))
        return None


def _anchor_indices(n: int) -> List[int]:
    """Anchor vertices of a tools/build_starter_packs.py trail: every anchor leg is densified into
    10 vertices starting at its anchor, plus the final anchor."""
    idx = list(range(0, n, 10))
    return idx if idx[-1] == n - 1 else idx + [n - 1]


def _near_key(pt: Sequence[float], fixes: Dict[Tuple[float, float], Tuple[float, float]]) -> Tuple[float, float]:
    """Key of the corrected POI this anchor sits on (within ~5 m), else the anchor itself."""
    for key in fixes:
        if abs(key[0] - pt[0]) < 5e-5 and abs(key[1] - pt[1]) < 5e-5:
            return key
    return (pt[0], pt[1])


def _trim_spurs(route: List[int], pts: List[Tuple[float, float]], max_spur_m: float = MAX_SPUR_M) -> List[int]:
    """Remove short there-and-back detours (A→…→X→…→A) left where an anchor snapped beside a junction."""
    changed = True
    while changed:
        changed = False
        for i in range(1, len(route) - 1):
            if route[i - 1] != route[i + 1]:
                continue
            k = 1
            while i - k - 1 >= 0 and i + k + 1 < len(route) and route[i - k - 1] == route[i + k + 1]:
                k += 1
            spur = sum(
                haversine_m(pts[a][1], pts[a][0], pts[b][1], pts[b][0])
                for a, b in zip(route[i - k:i], route[i - k + 1:i + 1])
            )
            if spur <= max_spur_m:
                route = route[:i - k + 1] + route[i + k + 1:]
                changed = True
                break
    return route


def _grid_sampler(grid: List[List[float]], bbox: Bbox) -> Callable[[float, float], float]:
    min_lat, min_lon, max_lat, max_lon = bbox
    rows, cols = len(grid), len(grid[0])

    def sample(lat: float, lon: float) -> float:
        fy = max(0.0, min(rows - 1.001, (lat - min_lat) / (max_lat - min_lat) * (rows - 1)))
        fx = max(0.0, min(cols - 1.001, (lon - min_lon) / (max_lon - min_lon) * (cols - 1)))
        r0, c0 = int(fy), int(fx)
        ty, tx = fy - r0, fx - c0
        top = grid[r0][c0] + (grid[r0][c0 + 1] - grid[r0][c0]) * tx
        bot = grid[r0 + 1][c0] + (grid[r0 + 1][c0 + 1] - grid[r0 + 1][c0]) * tx
        return top + (bot - top) * ty

    return sample


def _densify(lonlats: List[Tuple[float, float]], max_step_m: float = 30.0) -> List[Tuple[float, float]]:
    out = [lonlats[0]]
    for (lon1, lat1), (lon2, lat2) in zip(lonlats, lonlats[1:]):
        d = haversine_m(lat1, lon1, lat2, lon2)
        n = int(d // max_step_m)
        for i in range(1, n + 1):
            t = i / (n + 1)
            out.append((lon1 + (lon2 - lon1) * t, lat1 + (lat2 - lat1) * t))
        if (lon2, lat2) != out[-1]:
            out.append((lon2, lat2))
    return out


POI_MATCH_MAX_M = 600.0
_POI_KIND_MATCH = {
    "peak": {"peak", "saddle", "viewpoint"},
    "viewpoint": {"viewpoint", "peak"},
    "trailhead": {"trailhead", "parking", "ranger_station"},
    "waterfall": {"waterfall"},
    "water": {"spring", "drinking_water", "waterfall"},
    "hut": {"hut", "shelter"},
    "shelter": {"hut", "shelter"},
    "campsite": {"campsite"},
    "camp": {"campsite"},
    "ranger": {"ranger_station", "trailhead", "parking"},
}


def correct_pois_from_osm(
    pack: DestinationPack, elements: Sequence[Dict[str, Any]], ele_at: Callable[[float, float], float]
) -> Dict[Tuple[float, float], Tuple[float, float]]:
    """Move sketched pack POIs onto the same-named OSM feature (summit, lookout, trailhead, ...).

    A POI matches an OSM point when every significant word of the OSM name appears in the POI name;
    the OSM feature type must fit the POI kind, and the most specific (most shared words), then
    nearest, match within 600 m wins.
    Returns {old (lon, lat): new (lon, lat)} so trail anchors at those POIs can follow.
    """
    named: List[Tuple[set, str, float, float]] = []
    for el in elements:
        tags = el.get("tags") or {}
        kind = _classify(tags)
        if not tags.get("name") or kind is None:
            continue
        c = el if el.get("type") == "node" else el.get("center")
        if not c:
            continue
        tokens = _name_tokens(tags["name"])
        if tokens:
            named.append((tokens, kind, float(c["lon"]), float(c["lat"])))

    moved: Dict[Tuple[float, float], Tuple[float, float]] = {}
    for poi in pack.pois:
        ptoks = _name_tokens(poi.name)
        kinds = _POI_KIND_MATCH.get(str(poi.kind), {str(poi.kind)})
        best, best_key = None, None
        for tokens, kind, lon, lat in named:
            if kind not in kinds or not tokens <= ptoks:
                continue
            d = haversine_m(poi.lat, poi.lon, lat, lon)
            if d > POI_MATCH_MAX_M:
                continue
            key = (-len(tokens), d)
            if best_key is None or key < best_key:
                best, best_key = (lon, lat), key
        if best and best_key and best_key[1] > 15.0:
            moved[(poi.lon, poi.lat)] = best
            poi.lon, poi.lat = round(best[0], 6), round(best[1], 6)
            poi.ele_m = round(ele_at(poi.lat, poi.lon), 1)
    return moved


def align_trails_to_osm(
    pack: DestinationPack,
    elements: Sequence[Dict[str, Any]],
    ele_at: Callable[[float, float], float],
    anchor_fixes: Optional[Dict[Tuple[float, float], Tuple[float, float]]] = None,
) -> Tuple[int, List[str]]:
    """Re-route each sketched trail along real OSM paths between its anchor points and
    re-elevate it from the terrain grid. Trails whose anchors are far from any mapped path
    (or whose network route is implausibly long) are left unchanged.

    Anchors are recovered from the starter-pack builder's densification (see _anchor_indices). Shared junction anchors snap to the same OSM node, so trails stay
    connected for routing. Returns (aligned_count, names_left_unchanged).
    """
    graph = _PathGraph(elements)
    junction_snaps: Dict[Tuple[float, float], int] = {}  # shared trail endpoints → one OSM node
    aligned, skipped = 0, []
    for trail in pack.trails:
        if "osm" in trail.tags or "osm_aligned" in trail.tags or len(trail.coords_3d) < 2:
            continue
        pts = trail.coords_3d
        anchors = [pts[i] for i in _anchor_indices(len(pts))]
        preferred = graph.matching_ways(trail.name)
        fixes = anchor_fixes or {}
        snap_at = [fixes.get(_near_key(a, fixes), (a[0], a[1])) for a in anchors]
        snapped = [graph.nearest(lon, lat, preferred) for lon, lat in snap_at]
        for idx in (0, len(anchors) - 1):
            key = (anchors[idx][0], anchors[idx][1])
            if key in junction_snaps:
                snapped[idx] = junction_snaps[key]
            elif snapped[idx] is not None:
                junction_snaps[key] = snapped[idx]  # type: ignore[assignment]
        route: List[int] = []
        ok = all(n is not None for n in snapped)
        if ok:
            for a, b, na, nb in zip(anchors, anchors[1:], snapped, snapped[1:]):
                straight = haversine_m(a[1], a[0], b[1], b[0])
                leg = graph.shortest(na, nb, preferred, max_cost=6.0 * straight + 3000.0)  # type: ignore[arg-type]
                if leg is None:
                    ok = False
                    break
                route.extend(leg[1:] if route else leg)
            route = _trim_spurs(route, graph.pts)
        if ok and len(route) >= 2:
            new_len = sum(
                haversine_m(graph.pts[x][1], graph.pts[x][0], graph.pts[y][1], graph.pts[y][0])
                for x, y in zip(route, route[1:])
            )
            # Real trails wind more than the sketch, but not wildly: reject implausible matches
            ok = 0.6 <= new_len / max(1.0, trail.distance_m) <= 2.5
        if not ok or len(route) < 2:
            skipped.append(trail.name)
            continue
        lonlats = _densify([graph.pts[n] for n in route])
        coords = [[round(lon, 6), round(lat, 6), round(ele_at(lat, lon), 1)] for lon, lat in lonlats]
        _apply_profile(trail, coords)
        trail.tags = list(trail.tags) + ["osm_aligned"]
        aligned += 1
    return aligned, skipped


def _apply_profile(trail: TrailSegment, coords: List[List[float]]) -> None:
    """Set trail geometry with a lightly smoothed (±1 vertex, ~±30 m) elevation profile and refresh
    its stats. Terrain cells beside cliffs make raw DEM samples jump and inflate cumulative gain."""
    eles = [c[2] for c in coords]
    for i, c in enumerate(coords):
        window = eles[max(0, i - 1):i + 2]
        c[2] = round(sum(window) / len(window), 1)
    summary = summarize_elevation(coords)
    trail.coords_3d = coords
    trail.distance_m = summary["distance_m"]
    trail.elevation_gain_m = summary["elevation_gain_m"]
    trail.elevation_loss_m = summary["elevation_loss_m"]
    trail.min_ele_m = summary["min_ele_m"]
    trail.max_ele_m = summary["max_ele_m"]
    trail.max_grade_pct = summary["max_grade_pct"]
    trail.min_lat, trail.max_lat, trail.min_lon, trail.max_lon = compute_bbox(coords)


def elevate_osm_trails(trails: List[TrailSegment], ele_at: Callable[[float, float], float]) -> None:
    """Give downloaded OSM trails real vertex elevations and recompute their stats."""
    for trail in trails:
        lonlats = _densify([(c[0], c[1]) for c in trail.coords_3d])
        coords = [[round(lon, 6), round(lat, 6), round(ele_at(lat, lon), 1)] for lon, lat in lonlats]
        _apply_profile(trail, coords)


# ── Named OSM trails added to a pack ───────────────────────────────────────────

CURATED_TAG = "osm_curated"
_FT = 3.28084


def _way_names(tags: Dict[str, str]) -> List[str]:
    """OSM names like "Dublin Trail / Monadnock-Sunapee Greenway" carry several route names."""
    return [n.strip() for n in str(tags.get("name", "")).split("/") if n.strip()]


def _named_way_graph(
    elements: Sequence[Dict[str, Any]], osm_names: Sequence[str], way_ids: Sequence[int] = (), routes: Sequence[str] = (),
) -> Tuple[Dict[Tuple[float, float], Dict[Tuple[float, float], float]], List[Dict[str, str]]]:
    """Graph of the paths/tracks named `osm_names`, plus the ways listed in `way_ids` and every member
    way of the route relations named in `routes` (road walks included). Also returns the ways' tags."""
    graph: Dict[Tuple[float, float], Dict[Tuple[float, float], float]] = defaultdict(dict)
    wanted, ids, route_names = set(osm_names), set(way_ids), set(routes)
    ways = {el.get("id"): el for el in elements if el.get("type") == "way"}
    lines: List[List[Optional[Dict[str, Any]]]] = []
    tag_sets: List[Dict[str, str]] = []
    for el in elements:
        tags = el.get("tags") or {}
        if el.get("type") == "relation" and route_names and tags.get("name") in route_names:
            for m in el.get("members") or []:
                if m.get("type") == "way" and any(m.get("geometry") or []):
                    lines.append(m["geometry"])
                    tag_sets.append((ways.get(m.get("ref")) or {}).get("tags") or {})
            continue
        if el.get("type") != "way":
            continue
        if el.get("id") in ids or (_classify(tags) in ("path", "track") and wanted & set(_way_names(tags))):
            lines.append(el.get("geometry") or [])
            tag_sets.append(tags)
    for geom in lines:
        for a, b in zip(geom, geom[1:]):
            if not a or not b:  # vertices clipped off by the bbox come back as null
                continue
            pa = (round(float(a["lon"]), 7), round(float(a["lat"]), 7))
            pb = (round(float(b["lon"]), 7), round(float(b["lat"]), 7))
            d = haversine_m(pa[1], pa[0], pb[1], pb[0])
            graph[pa][pb] = d
            graph[pb][pa] = d
    return graph, tag_sets


def _farthest(graph, start) -> Tuple[Tuple[float, float], Dict, Dict]:
    dist = {start: 0.0}
    prev: Dict = {}
    heap = [(0.0, start)]
    while heap:
        d, n = heapq.heappop(heap)
        if d > dist[n]:
            continue
        for m, w in graph[n].items():
            if d + w < dist.get(m, math.inf):
                dist[m] = d + w
                prev[m] = n
                heapq.heappush(heap, (d + w, m))
    far = max(dist, key=dist.get)
    return far, dist, prev


def _trail_line(graph) -> List[Tuple[float, float]]:
    """Longest end-to-end stretch (graph diameter) of the largest connected piece of a named trail."""
    seen: set = set()
    best: List[Tuple[float, float]] = []
    best_len = -1.0
    for node in list(graph):
        if node in seen:
            continue
        a, dist, _ = _farthest(graph, node)
        seen.update(dist)
        b, dist_b, prev = _farthest(graph, a)
        if dist_b[b] > best_len:
            path = [b]
            while path[-1] != a:
                path.append(prev[path[-1]])
            best, best_len = path, dist_b[b]
    return best


def _path_between(graph, start: Sequence[float], end: Sequence[float]) -> List[Tuple[float, float]]:
    """Shortest stretch of the graph between the vertices nearest two [lat, lon] points (one section
    of a long trail); empty if they are not connected."""
    if not graph:
        return []

    def nearest(p: Sequence[float]) -> Tuple[float, float]:
        return min(graph, key=lambda n: haversine_m(p[0], p[1], n[1], n[0]))

    a, b = nearest(start), nearest(end)
    _, dist, prev = _farthest(graph, a)
    if b not in dist:
        return []
    path = [b]
    while path[-1] != a:
        path.append(prev[path[-1]])
    return path[::-1]


def _near_features(features: List[MapFeature], kinds: set, max_m: float) -> Callable[[float, float], List[MapFeature]]:
    cells: Dict[Tuple[int, int], List[Tuple[MapFeature, float, float]]] = defaultdict(list)
    for f in features:
        if f.kind not in kinds:
            continue
        for lon, lat in f.coords:
            cells[(int(lon * 500), int(lat * 500))].append((f, lon, lat))

    def query(lon: float, lat: float) -> List[MapFeature]:
        cx, cy = int(lon * 500), int(lat * 500)
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for f, flon, flat in cells.get((cx + dx, cy + dy), ()):
                    if haversine_m(lat, lon, flat, flon) <= max_m:
                        out.append(f)
        return out

    return query


def _classify_difficulty(gain_m: float, dist_m: float, max_grade: float, sac: set) -> str:
    if sac & {"alpine_hiking", "demanding_alpine_hiking", "difficult_alpine_hiking"}:
        return "expert"
    gain_ft, miles = gain_m * _FT, dist_m / 1609.344
    effort = gain_ft / 1000.0 + miles / 2.0  # ~"Shenandoah" style: 1000 ft ≈ 2 mi of flat walking
    avg_grade = gain_m / max(1.0, dist_m) * 100.0  # short, very steep climbs are hard despite low "effort"
    if "demanding_mountain_hiking" in sac or effort > 5.0:
        return "strenuous"
    if effort > 3.0 or (gain_ft > 1500 and max_grade > 35) or (avg_grade > 25 and gain_ft > 500):
        return "hard"
    if gain_ft > 700 or effort > 1.8 or (avg_grade > 18 and gain_ft > 250):
        return "moderate"
    return "easy"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def expand_trail_specs(pack_id: str, elements: Sequence[Dict[str, Any]], specs: Any) -> List[Dict[str, Any]]:
    """Trail specs for a pack. Three forms:

    * a list: hand-picked trails, used as is;
    * {"all_named": true, ...}: one spec per distinct named path/track in the downloaded area
      (see _expand_all_named for the options);
    * {"trails": [...], "auto": {...}}: the hand-picked list plus every other named trail found by
      the "auto" rules (typically limited with "within" to a newly added strip of the region).
    """
    if not isinstance(specs, dict):
        return list(specs or [])
    if "trails" in specs:
        base = list(specs["trails"])
        if not specs.get("auto"):
            return base
        taken = {n for s in base for n in [s["osm_name"], *s.get("osm_names", [])]}
        used_codes = {s.get("code", "") for s in base}
        return base + _expand_all_named(pack_id, elements, specs["auto"], skip=taken, used_codes=used_codes)
    return _expand_all_named(pack_id, elements, specs)


def _expand_all_named(
    pack_id: str, elements: Sequence[Dict[str, Any]], cfg: Dict[str, Any],
    skip: Optional[set] = None, used_codes: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """One spec per distinct named path/track, minus `exclude_regex` and `skip`. Options:
    `within` [min_lat, min_lon, max_lat, max_lon] (a way must have a vertex inside), `aliases`
    (variant OSM name → canonical name), `rename` (canonical → display name), `code_prefix`,
    `tags`, `defaults` / per-name `overrides` (difficulty, surface, note, tags, no_dogs,
    alpine_above_m, min_length_m)."""
    exclude = re.compile(cfg["exclude_regex"]) if cfg.get("exclude_regex") else None
    aliases: Dict[str, str] = cfg.get("aliases", {})
    within = cfg.get("within")
    skip = skip or set()
    names: Dict[str, set] = defaultdict(set)
    for el in elements:
        tags = el.get("tags") or {}
        if el.get("type") != "way" or _classify(tags) not in ("path", "track"):
            continue
        if within and not any(
            within[0] <= g["lat"] <= within[2] and within[1] <= g["lon"] <= within[3]
            for g in (el.get("geometry") or []) if g
        ):
            continue
        for nm in _way_names(tags):
            canonical = aliases.get(nm, nm)
            if canonical in skip or nm in skip or (exclude and exclude.search(canonical)):
                continue
            names[canonical].add(nm)
    prefix = cfg.get("code_prefix", pack_id[:3].upper())
    out = []
    used = set(used_codes or ())
    used_ids: set = set()
    for canonical in sorted(names):
        code = prefix + "-" + "".join(w[0] for w in re.findall(r"[A-Za-z]+", canonical)).upper()[:4]
        base, n = code, 2
        while code in used:
            code, n = f"{base}{n}", n + 1
        used.add(code)
        # Names differing only in case or punctuation ("Old Route" / "old route") share a slug
        trail_id = base_id = f"{pack_id}_{_slug(canonical)}"
        n = 2
        while trail_id in used_ids:
            trail_id, n = f"{base_id}_{n}", n + 1
        used_ids.add(trail_id)
        spec: Dict[str, Any] = {
            "id": trail_id,
            "osm_name": canonical,
            "osm_names": sorted(names[canonical]),
            "name": cfg.get("rename", {}).get(canonical, canonical),
            "code": code,
            "tags": list(cfg.get("tags", [])),
        }
        spec.update(cfg.get("defaults", {}))
        spec.update(cfg.get("overrides", {}).get(canonical, {}))
        out.append(spec)
    return out


def build_named_osm_trails(
    pack: DestinationPack,
    elements: Sequence[Dict[str, Any]],
    ele_at: Callable[[float, float], float],
    specs: Any,
    min_length_m: float = 80.0,
) -> Tuple[int, List[str]]:
    """Add each spec'd named OSM trail to the pack (replacing earlier curated copies).

    Geometry is the longest connected end-to-end stretch of ways with that name, oriented to
    climb (lower end first). Difficulty, surface, water access, endpoints and landmarks are
    derived from OSM tags, the terrain and the pack's map features.

    Optional spec keys: `osm_route` (build from the members of the route relation with that name
    instead of name-matched ways), `osm_ways` (extra way ids, e.g. unnamed connectors) and
    `between` ([[lat, lon], [lat, lon]]: only the stretch between these points, one section of a
    long trail).
    """
    pack.trails = [t for t in pack.trails if CURATED_TAG not in t.tags]
    specs = expand_trail_specs(pack.id, elements, specs)
    feats = pack.features
    water_near = _near_features(feats, {"stream", "river", "spring", "drinking_water", "water"}, 60.0)
    sights_near = _near_features(feats, {"peak", "hut", "shelter", "waterfall", "viewpoint", "campsite"}, 120.0)
    ends_near = _near_features(feats, {"trailhead", "parking", "peak", "hut", "shelter"}, 350.0)

    built, missing = 0, []
    for spec in specs:
        osm_name = spec["osm_name"]
        route = spec.get("osm_route")
        graph, way_tags = _named_way_graph(
            elements, [] if route else spec.get("osm_names", [osm_name]), spec.get("osm_ways", ()), [route] if route else (),
        )
        line = _path_between(graph, *spec["between"]) if spec.get("between") else _trail_line(graph)
        if len(line) < 2 or sum(
            haversine_m(a[1], a[0], b[1], b[0]) for a, b in zip(line, line[1:])
        ) < spec.get("min_length_m", min_length_m):
            missing.append(osm_name)
            continue
        if ele_at(line[0][1], line[0][0]) > ele_at(line[-1][1], line[-1][0]):
            line.reverse()
        lonlats = _densify(line)
        coords = [[round(lon, 6), round(lat, 6), round(ele_at(lat, lon), 1)] for lon, lat in lonlats]
        trail = TrailSegment(id=spec["id"], pack_id=pack.id, name=spec.get("name", osm_name), code=spec.get("code", ""))
        _apply_profile(trail, coords)

        sac = {t["sac_scale"] for t in way_tags if t.get("sac_scale")}
        trail.difficulty = TrailDifficulty(
            _classify_difficulty(trail.elevation_gain_m, trail.distance_m, trail.max_grade_pct, sac)
        )
        surfaces = {t["surface"] for t in way_tags if t.get("surface")}
        if sac & {"demanding_mountain_hiking", "alpine_hiking", "demanding_alpine_hiking"}:
            trail.surface = "scramble"
        elif trail.max_ele_m >= spec.get("alpine_above_m", 1450):  # White Mountains treeline ~4,800 ft
            trail.surface = "alpine"
        elif surfaces & {"gravel", "fine_gravel", "compacted"}:
            trail.surface = "gravel"
        elif surfaces & {"dirt", "ground", "earth"}:
            trail.surface = "packed_dirt"
        else:
            trail.surface = "rocky"
        trail.dogs_allowed = not any(t.get("dog") == "no" for t in way_tags) and not spec.get("no_dogs", False)
        trail.water_available = any(water_near(c[0], c[1]) for c in coords[::3])
        trail.condition_status = TrailCondition.OPEN
        trail.condition_note = "Geometry from OpenStreetMap; check current WMNF/AMC trail reports."
        trail.trail_type = "segment"

        def end_name(c: List[float]) -> str:
            cands = [f for f in ends_near(c[0], c[1]) if f.name]
            if not cands:
                return f"{c[2] * _FT:,.0f} ft"
            f = min(cands, key=lambda f: haversine_m(c[1], c[0], f.coords[0][1], f.coords[0][0]))
            return f.name
        landmarks: List[str] = []
        for c in coords[::2]:
            for f in sights_near(c[0], c[1]):
                label = f.name or ""
                if label and label not in landmarks:
                    landmarks.append(label)
        start, end = end_name(coords[0]), end_name(coords[-1])
        desc = (
            f"{trail.name} from {start} to {end}: {trail.distance_m / 1609.344:.1f} mi one way, "
            f"+{trail.elevation_gain_m * _FT:,.0f} ft / -{trail.elevation_loss_m * _FT:,.0f} ft "
            f"({trail.min_ele_m * _FT:,.0f}–{trail.max_ele_m * _FT:,.0f} ft)."
        )
        others = [x for x in landmarks if x not in (start, end)][:6]
        if others:
            desc += f" Passes {', '.join(others)}."
        if sac:
            desc += " " + "; ".join(sorted({_SAC_LABELS[x] for x in sac if x in _SAC_LABELS})) + "."
        if trail.surface == "alpine":
            desc += " Exposed above treeline: turn back in poor weather."
        # Hand-checked facts the tags miss (e.g. a scramble OSM has no sac_scale for)
        if spec.get("difficulty"):
            trail.difficulty = TrailDifficulty(spec["difficulty"])
        if spec.get("surface"):
            trail.surface = spec["surface"]
        if spec.get("note"):
            desc += " " + spec["note"]
        trail.description = desc
        trail.tags = list(dict.fromkeys(list(spec.get("tags", [])) + ["osm", CURATED_TAG] + (["water"] if trail.water_available else [])))
        pack.trails.append(trail)
        built += 1
    return built, missing


# ── Orchestration ───────────────────────────────────────────────────────────────


def enrich_pack(
    pack: DestinationPack,
    progress: Progress = None,
    fetch_osm: bool = True,
    contour_units: str = "ft",
    align_trails: bool = True,
    trail_specs: Optional[Sequence[Dict[str, Any]]] = None,
) -> DestinationPack:
    """Download terrain + OSM detail for pack's bbox and attach DEM, contours and features in place.

    With `align_trails`, sketched trails are re-routed onto real OSM paths; downloaded OSM trails
    get real vertex elevations either way.
    """
    bbox: Bbox = (pack.min_lat, pack.min_lon, pack.max_lat, pack.max_lon)
    session = _session()

    grid, rows, cols = fetch_terrain_grid(bbox, session=session, progress=progress)
    smooth = _box_blur(grid)
    lo = min(min(r) for r in smooth)
    hi = max(max(r) for r in smooth)
    interval = choose_contour_interval(lo, hi, contour_units)
    if progress:
        progress(f"tracing {interval:.1f} m contours ({lo:.0f}–{hi:.0f} m)")
    pack.contours = compute_contours(smooth, bbox, interval)
    pack.contour_interval_m = interval
    pack.dem = downsample_dem(grid, bbox)

    if fetch_osm:
        if progress:
            progress("querying OpenStreetMap (Overpass)")
        elements = fetch_osm_elements(bbox, session=session)
        feats = build_features(elements, 0.5 * (pack.min_lat + pack.max_lat))
        for f in feats:
            if f.geom == "point" and f.ele_m is None and f.kind in ("peak", "saddle", "hut", "shelter", "spring", "viewpoint"):
                e = sample_dem(pack.dem, f.coords[0][1], f.coords[0][0])
                f.ele_m = round(e, 0) if e is not None else None
        pack.features = feats
        ele_at = _grid_sampler(grid, bbox)  # unblurred: blur flattens canyons and cliff-side trails
        pack.trails = [t for t in pack.trails if CURATED_TAG not in t.tags]  # rebuilt below
        osm_trails = [t for t in pack.trails if "osm" in t.tags]
        if osm_trails:
            elevate_osm_trails(osm_trails, ele_at)
        if align_trails:
            moved = correct_pois_from_osm(pack, elements, ele_at)
            if progress and moved:
                progress(f"moved {len(moved)} POI(s) onto their OpenStreetMap location")
            aligned, skipped = align_trails_to_osm(pack, elements, ele_at, anchor_fixes=moved)
            if progress:
                progress(f"aligned {aligned} trail(s) to OSM paths" + (f"; unchanged: {', '.join(skipped)}" if skipped else ""))
        if trail_specs:
            added, missing = build_named_osm_trails(pack, elements, ele_at, trail_specs)
            if progress:
                progress(f"added {added} named OSM trail(s)" + (f"; not found: {', '.join(missing)}" if missing else ""))

    pack.map_attribution = ATTRIBUTION
    return pack
