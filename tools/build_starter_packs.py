#!/usr/bin/env python3
"""Deterministic builder for AegisHiker's 8 bundled regional destination packs.

Generates:
  - data/destinations_catalog.json
  - data/packs/white_mountains_nh.json
  - data/packs/yosemite_valley.json
  - data/packs/acadia_np.json
  - data/packs/shenandoah_central.json
  - data/packs/rocky_mountain_bear_lake.json
  - data/packs/zion_canyon.json
  - data/packs/mount_rainier_paradise.json
  - data/packs/mount_monadnock.json

Usage: build_starter_packs.py [pack_id ...]   (no ids = rebuild all)
"""
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.hiking.elevation import summarize_elevation
from src.hiking.geo import compute_bbox
from src.hiking.models import ContourLine, DEMGrid, DestinationPack, POI, TrailSegment


def _densify_3d_waypoints(
    anchors: List[Tuple[float, float, float]],
    points_per_leg: int = 8,
) -> List[List[float]]:
    """Interpolate smooth 3D vertices between anchor (lon, lat, ele_m) points,
    preserving exact endpoint coordinates for graph junction connectivity."""
    if len(anchors) < 2:
        return [[round(a[0], 6), round(a[1], 6), round(a[2], 1)] for a in anchors]

    coords: List[List[float]] = []
    for leg_idx in range(len(anchors) - 1):
        lon1, lat1, ele1 = anchors[leg_idx]
        lon2, lat2, ele2 = anchors[leg_idx + 1]
        # s runs 0..points_per_leg-1, so each leg emits its own start anchor (the previous leg's
        # end) exactly once; the final anchor is appended after the loop.
        for s in range(points_per_leg):
            t = s / float(points_per_leg)
            # Subtle natural trail meander perpendicular to leg (0 at endpoints)
            meander = math.sin(t * math.pi) * 0.00035 * (1 if leg_idx % 2 == 0 else -1)
            dlon = lon2 - lon1
            dlat = lat2 - lat1
            lon = lon1 + t * dlon - meander * dlat
            lat = lat1 + t * dlat + meander * dlon
            ele = ele1 + t * (ele2 - ele1) + math.sin(t * math.pi * 2) * 4.0 * math.sin(t * math.pi)
            coords.append([round(lon, 6), round(lat, 6), round(ele, 1)])
    last = anchors[-1]
    coords.append([round(last[0], 6), round(last[1], 6), round(last[2], 1)])
    return coords


def _make_trail(
    pack_id: str,
    trail_id: str,
    name: str,
    code: str,
    difficulty: str,
    surface: str,
    trail_type: str,
    anchors: List[Tuple[float, float, float]],
    dogs_allowed: bool,
    water_available: bool,
    condition_status: str,
    condition_note: str,
    tags: List[str],
    description: str,
) -> TrailSegment:
    coords = _densify_3d_waypoints(anchors, points_per_leg=10)
    summary = summarize_elevation(coords)
    min_lat, max_lat, min_lon, max_lon = compute_bbox(coords)
    return TrailSegment(
        id=trail_id,
        pack_id=pack_id,
        name=name,
        code=code,
        difficulty=difficulty,  # type: ignore[arg-type]
        surface=surface,  # type: ignore[arg-type]
        trail_type=trail_type,  # type: ignore[arg-type]
        distance_m=summary["distance_m"],
        elevation_gain_m=summary["elevation_gain_m"],
        elevation_loss_m=summary["elevation_loss_m"],
        min_ele_m=summary["min_ele_m"],
        max_ele_m=summary["max_ele_m"],
        max_grade_pct=summary["max_grade_pct"],
        dogs_allowed=dogs_allowed,
        water_available=water_available,
        condition_status=condition_status,  # type: ignore[arg-type]
        condition_note=condition_note,
        condition_updated="2026-10-15",
        min_lat=min_lat,
        max_lat=max_lat,
        min_lon=min_lon,
        max_lon=max_lon,
        coords_3d=coords,
        tags=tags,
        description=description,
    )


def _generate_dem_and_contours(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    base_ele_m: float,
    max_ele_m: float,
    peaks: List[Tuple[float, float, float]],
) -> Tuple[DEMGrid, List[ContourLine]]:
    rows, cols = 16, 16
    grid: List[List[float]] = []
    for r in range(rows):
        lat = min_lat + (r / (rows - 1)) * (max_lat - min_lat)
        row_vals: List[float] = []
        for c in range(cols):
            lon = min_lon + (c / (cols - 1)) * (max_lon - min_lon)
            ele = base_ele_m
            for plon, plat, pele in peaks:
                dist_deg = math.hypot((lon - plon) * 0.75, lat - plat)
                influence = max(0.0, 1.0 - (dist_deg / 0.055))
                ele = max(ele, base_ele_m + (pele - base_ele_m) * (influence ** 1.4))
            row_vals.append(round(ele, 1))
        grid.append(row_vals)

    dem = DEMGrid(
        min_lat=min_lat,
        max_lat=max_lat,
        min_lon=min_lon,
        max_lon=max_lon,
        rows=rows,
        cols=cols,
        elevations_m=grid,
    )

    contours: List[ContourLine] = []
    step = max(100.0, round((max_ele_m - base_ele_m) / 5.0 / 50.0) * 50.0)
    for k in range(1, 5):
        target_ele = round(base_ele_m + k * step, 0)
        if target_ele >= max_ele_m:
            break
        for idx, (plon, plat, pele) in enumerate(peaks[:3]):
            if pele <= target_ele:
                continue
            ratio = (pele - target_ele) / max(100.0, pele - base_ele_m)
            rx = 0.008 + ratio * 0.022
            ry = 0.006 + ratio * 0.018
            ring: List[List[float]] = []
            for a in range(25):
                ang = (a / 24.0) * 2.0 * math.pi
                clon = round(plon + math.cos(ang) * rx, 5)
                clat = round(plat + math.sin(ang) * ry, 5)
                ring.append([clon, clat])
            contours.append(ContourLine(ele_m=target_ele, coords=ring))

    return dem, contours


def build_white_mountains_nh() -> DestinationPack:
    pid = "white_mountains_nh"
    # Shared junctions in Franconia Notch & Pinkham Notch
    j_lafayette_th = (-71.6812, 44.1416, 535.0)
    j_little_haystack = (-71.6445, 44.1404, 1457.0)
    j_mt_lincoln = (-71.6418, 44.1488, 1551.0)
    j_mt_lafayette = (-71.6409, 44.1607, 1600.0)
    j_greenleaf_hut = (-71.6538, 44.1602, 1280.0)
    j_lonesome_lake = (-71.6935, 44.1390, 840.0)
    j_pinkham_notch = (-71.2528, 44.2573, 619.0)
    j_hermit_lake = (-71.2855, 44.2602, 1175.0)
    j_mt_washington = (-71.3032, 44.2705, 1917.0)
    j_lakes_clouds = (-71.3188, 44.2588, 1540.0)

    trails = [
        _make_trail(
            pid,
            "wmnf_falling_waters",
            "Falling Waters Trail to Little Haystack",
            "WMNF-FW",
            "strenuous",
            "rocky",
            "segment",
            [
                j_lafayette_th,
                (-71.6710, 44.1412, 740.0),
                (-71.6605, 44.1409, 985.0),
                (-71.6512, 44.1406, 1260.0),
                j_little_haystack,
            ],
            True,
            True,
            "open",
            "Waterfalls flowing well; wet granite slabs near Cloudland Falls.",
            ["waterfall", "franconia", "climb", "white_mountains"],
            "Ascends alongside Stairs, Swiftwater, and 80-ft Cloudland Falls to emerge above treeline on Little Haystack Mountain.",
        ),
        _make_trail(
            pid,
            "wmnf_franconia_ridge",
            "Franconia Ridge Trail (Little Haystack – Mt. Lincoln – Mt. Lafayette)",
            "AT-FRT",
            "strenuous",
            "alpine",
            "segment",
            [
                j_little_haystack,
                (-71.6430, 44.1445, 1485.0),
                j_mt_lincoln,
                (-71.6413, 44.1545, 1510.0),
                j_mt_lafayette,
            ],
            True,
            False,
            "caution",
            "Exposed alpine knife-edge ridge above 1,450 m; high winds and rapid fog possible.",
            ["alpine", "ridge", "peaks", "appalachian_trail", "views"],
            "Iconic 360-degree alpine traverse along the Appalachian Trail linking Little Haystack (4,780 ft), Mt. Lincoln (5,089 ft), and Mt. Lafayette (5,249 ft).",
        ),
        _make_trail(
            pid,
            "wmnf_old_bridle_path",
            "Greenleaf Trail & Old Bridle Path Descent",
            "WMNF-OBP",
            "strenuous",
            "rocky",
            "segment",
            [
                j_mt_lafayette,
                j_greenleaf_hut,
                (-71.6632, 44.1538, 1080.0),
                (-71.6725, 44.1476, 795.0),
                j_lafayette_th,
            ],
            True,
            True,
            "open",
            "Potable water and shelter at AMC Greenleaf Hut; rocky ledges on Agony Ridge.",
            ["hut", "views", "franconia", "loop_connector"],
            "Descends from Mt. Lafayette past AMC Greenleaf Hut and down Old Bridle Path with sweeping views back across Franconia Ridge.",
        ),
        _make_trail(
            pid,
            "wmnf_franconia_ridge_loop",
            "Franconia Ridge Complete Loop (Falling Waters, Ridge & Old Bridle Path)",
            "WMNF-FRL",
            "strenuous",
            "alpine",
            "loop",
            [
                j_lafayette_th,
                (-71.6620, 44.1410, 960.0),
                j_little_haystack,
                j_mt_lincoln,
                j_mt_lafayette,
                j_greenleaf_hut,
                (-71.6680, 44.1500, 910.0),
                j_lafayette_th,
            ],
            True,
            True,
            "open",
            "Premier White Mountains loop; carry wind shell for alpine zone.",
            ["loop", "alpine", "waterfall", "peaks", "hut", "classic"],
            "New Hampshire's signature 8.9-mile alpine loop climbing Falling Waters Trail, traversing Franconia Ridge over three 4,000-footers, and descending via Greenleaf Hut and Old Bridle Path.",
        ),
        _make_trail(
            pid,
            "wmnf_lonesome_lake_loop",
            "Lonesome Lake & Cannon Balls View Loop",
            "WMNF-LLL",
            "moderate",
            "packed_dirt",
            "loop",
            [
                j_lafayette_th,
                (-71.6875, 44.1402, 710.0),
                j_lonesome_lake,
                (-71.6960, 44.1425, 860.0),
                (-71.6880, 44.1430, 690.0),
                j_lafayette_th,
            ],
            True,
            True,
            "open",
            "Bog bridges around Lonesome Lake in great shape; family & dog friendly.",
            ["lake", "hut", "moderate", "views", "family"],
            "Steady woodland climb from Lafayette Place to glacial Lonesome Lake and AMC Lonesome Lake Hut with dramatic reflections of Franconia Ridge.",
        ),
        _make_trail(
            pid,
            "wmnf_tuckerman_ravine",
            "Mt. Washington via Tuckerman Ravine Trail",
            "WMNF-TRT",
            "expert",
            "scramble",
            "out_and_back",
            [
                j_pinkham_notch,
                (-71.2690, 44.2588, 890.0),
                j_hermit_lake,
                (-71.2940, 44.2630, 1460.0),
                j_mt_washington,
            ],
            False,
            True,
            "snow",
            "Lingering snow/ice patches in Tuckerman headwall; steep boulder talus on summit cone.",
            ["presidentials", "mt_washington", "summit", "alpine", "scramble"],
            "Classic steep ascent from Pinkham Notch Visitor Center through glacial Tuckerman Ravine bowl and up the boulder-strewn summit cone to the 6,288-ft rooftop of New England.",
        ),
        _make_trail(
            pid,
            "wmnf_lion_head_boott_spur",
            "Mt. Washington Loop via Lion Head & Boott Spur",
            "WMNF-LHBS",
            "expert",
            "alpine",
            "loop",
            [
                j_pinkham_notch,
                j_hermit_lake,
                (-71.2890, 44.2665, 1530.0),
                j_mt_washington,
                j_lakes_clouds,
                (-71.2950, 44.2520, 1676.0),
                (-71.2720, 44.2545, 1120.0),
                j_pinkham_notch,
            ],
            False,
            True,
            "caution",
            "Long exposure above treeline across Lion Head, Mt. Washington, Lakes of the Clouds, and Boott Spur.",
            ["loop", "presidentials", "mt_washington", "alpine", "hut"],
            "Grand Presidential Range loop ascending Lion Head ridge to Mt. Washington summit, visiting AMC Lakes of the Clouds Hut on Crawford Path, and descending scenic Boott Spur.",
        ),
    ]

    pois = [
        POI(id="poi_wm_laf_th", pack_id=pid, name="Lafayette Place Trailhead", kind="trailhead", lat=44.1416, lon=-71.6812, ele_m=535.0, note="I-93 Franconia Notch parking & hiker shuttle stop"),
        POI(id="poi_wm_cloudland", pack_id=pid, name="Cloudland Falls (80 ft)", kind="waterfall", lat=44.1409, lon=-71.6605, ele_m=985.0, note="Largest waterfall on Falling Waters Trail"),
        POI(id="poi_wm_haystack", pack_id=pid, name="Little Haystack Mountain", kind="peak", lat=44.1404, lon=-71.6445, ele_m=1457.0, note="Southern gateway peak of Franconia Ridge (4,780 ft)"),
        POI(id="poi_wm_lincoln", pack_id=pid, name="Mount Lincoln Summit", kind="peak", lat=44.1488, lon=-71.6418, ele_m=1551.0, note="Sharp alpine pyramid peak (5,089 ft)"),
        POI(id="poi_wm_lafayette", pack_id=pid, name="Mount Lafayette Summit", kind="peak", lat=44.1607, lon=-71.6409, ele_m=1600.0, note="Highest peak in Franconia Range (5,249 ft)"),
        POI(id="poi_wm_greenleaf", pack_id=pid, name="AMC Greenleaf Hut", kind="hut", lat=44.1602, lon=-71.6538, ele_m=1280.0, note="Potable water, restrooms, and weather postings"),
        POI(id="poi_wm_pinkham", pack_id=pid, name="AMC Pinkham Notch Visitor Center", kind="trailhead", lat=44.2573, lon=-71.2528, ele_m=619.0, note="Base camp for Mt. Washington & Tuckerman Ravine"),
        POI(id="poi_wm_washington", pack_id=pid, name="Mount Washington Summit (6,288 ft)", kind="peak", lat=44.2705, lon=-71.3032, ele_m=1917.0, note="Highest peak in the Northeast; Mount Washington Observatory"),
        POI(id="poi_wm_lakes_clouds", pack_id=pid, name="AMC Lakes of the Clouds Hut", kind="hut", lat=44.2588, lon=-71.3188, ele_m=1540.0, note="Highest AMC hut on the shoulder of Mt. Monroe & Mt. Washington"),
    ]

    # South to the Flume Slide Trail (Franconia Notch), north to Randolph for the Howker Ridge
    # Trail and the Northern Presidentials, east to the Maine border (Wildcats, Carters, Baldfaces,
    # Evans Notch)
    # (-70.95: just past the state line so Evans Notch trails that cross it, e.g. Bickford Brook, stay whole)
    min_lat, max_lat, min_lon, max_lon = 44.095, 44.385, -71.710, -70.950
    dem, contours = _generate_dem_and_contours(
        min_lat,
        max_lat,
        min_lon,
        max_lon,
        520.0,
        1917.0,
        [(-71.3032, 44.2705, 1917.0), (-71.6409, 44.1607, 1600.0), (-71.6418, 44.1488, 1551.0)],
    )
    return DestinationPack(
        id=pid,
        name="New Hampshire White Mountains",
        state_or_region="New Hampshire (White Mountain National Forest)",
        agency="USFS White Mountain National Forest, USGS 3DEP & OpenStreetMap Contributors",
        summary="Alpine ridgelines, glacially carved ravines, waterfalls, and AMC high huts from Franconia Ridge across the Presidential Range to the Wildcats, Carters and Baldfaces at the Maine border.",
        pack_version="1.2",  # 1.1: Flume Slide / Howker Ridge; 1.2: east to the Maine border
        min_lat=min_lat,
        max_lat=max_lat,
        min_lon=min_lon,
        max_lon=max_lon,
        base_ele_m=520.0,
        max_ele_m=1917.0,
        emergency_contact="NH Fish & Game Dispatch: (603) 271-3361 / 911",
        trails=trails,
        pois=pois,
        contours=contours,
        dem=dem,
    )


def build_yosemite_valley() -> DestinationPack:
    pid = "yosemite_valley"
    j_happy_isles = (-119.5582, 37.7329, 1230.0)
    j_vernal_top = (-119.5435, 37.7275, 1540.0)
    j_nevada_top = (-119.5322, 37.7252, 1805.0)
    j_glacier_pt = (-119.5729, 37.7304, 2199.0)
    j_valley_floor = (-119.5980, 37.7270, 1215.0)
    j_half_dome = (-119.5332, 37.7459, 2695.0)

    trails = [
        _make_trail(
            pid,
            "yos_mist_trail",
            "Mist Trail to Vernal & Nevada Falls",
            "YOS-MT",
            "strenuous",
            "rocky",
            "segment",
            [j_happy_isles, (-119.5505, 37.7295, 1365.0), j_vernal_top, j_nevada_top],
            False,
            True,
            "open",
            "Granite steps wet from waterfall spray; potable water seasonal at Vernal footbridge.",
            ["waterfall", "granite", "classic", "canyon"],
            "Yosemite's signature waterfall trail climbing over 600 granite steps alongside Vernal Fall (317 ft) and Nevada Fall (594 ft).",
        ),
        _make_trail(
            pid,
            "yos_jmt_return",
            "John Muir Trail (Clark Point Return to Happy Isles)",
            "YOS-JMT",
            "moderate",
            "packed_dirt",
            "segment",
            [j_nevada_top, (-119.5460, 37.7235, 1680.0), (-119.5540, 37.7270, 1420.0), j_happy_isles],
            False,
            False,
            "open",
            "Clear and dry; ideal knee-friendly descent forming a loop with the Mist Trail.",
            ["views", "loop_connector", "jmt"],
            "Scenic switchback corridor via Clark Point with panoramic views of Liberty Cap and the back of Half Dome.",
        ),
        _make_trail(
            pid,
            "yos_mist_jmt_loop",
            "Vernal & Nevada Falls Loop via Mist Trail & JMT",
            "YOS-MJL",
            "strenuous",
            "rocky",
            "loop",
            [j_happy_isles, j_vernal_top, j_nevada_top, (-119.5460, 37.7235, 1680.0), j_happy_isles],
            False,
            True,
            "open",
            "Ascend Mist Trail, descend JMT for optimal footing.",
            ["loop", "waterfall", "classic", "views"],
            "Complete loop ascending the dramatic granite staircase of the Mist Trail and returning via the panoramic John Muir Trail.",
        ),
        _make_trail(
            pid,
            "yos_half_dome",
            "Half Dome Summit Extension (from Nevada Fall)",
            "YOS-HD",
            "expert",
            "scramble",
            "out_and_back",
            [j_nevada_top, (-119.5260, 37.7340, 2150.0), (-119.5315, 37.7420, 2490.0), j_half_dome],
            False,
            False,
            "caution",
            "Day-use permit required for Sub Dome & cables; never ascend if thunderclouds form.",
            ["summit", "granite", "cables", "expert"],
            "Climbs through Little Yosemite Valley and up Sub Dome to the famous Half Dome cable route at 8,842 ft.",
        ),
        _make_trail(
            pid,
            "yos_panorama_four_mile",
            "Panorama Trail & Four Mile Trail Loop",
            "YOS-PFM",
            "strenuous",
            "packed_dirt",
            "loop",
            [j_happy_isles, j_nevada_top, (-119.5550, 37.7160, 2020.0), j_glacier_pt, j_valley_floor, j_happy_isles],
            False,
            True,
            "open",
            "Continuous Yosemite Falls, Half Dome, and Illilouette Fall views.",
            ["loop", "views", "waterfall", "glacier_point"],
            "Grand Yosemite Valley rim loop connecting Happy Isles, Nevada Fall, Illilouette Ridge, Glacier Point, and the Four Mile Trail.",
        ),
    ]

    pois = [
        POI(id="poi_yos_hi", pack_id=pid, name="Happy Isles Trailhead", kind="trailhead", lat=37.7329, lon=-119.5582, ele_m=1230.0, note="Shuttle Stop #16; water & restrooms"),
        POI(id="poi_yos_vernal", pack_id=pid, name="Vernal Fall Brink", kind="waterfall", lat=37.7275, lon=-119.5435, ele_m=1540.0, note="317-ft waterfall overlook & Emerald Pool"),
        POI(id="poi_yos_nevada", pack_id=pid, name="Nevada Fall Overlook", kind="waterfall", lat=37.7252, lon=-119.5322, ele_m=1805.0, note="594-ft waterfall & JMT junction"),
        POI(id="poi_yos_hd", pack_id=pid, name="Half Dome Summit (8,842 ft)", kind="peak", lat=37.7459, lon=-119.5332, ele_m=2695.0, note="Iconic granite dome summit"),
        POI(id="poi_yos_gp", pack_id=pid, name="Glacier Point Overlook", kind="viewpoint", lat=37.7304, lon=-119.5729, ele_m=2199.0, note="3,200-ft cliff viewpoint above Yosemite Valley"),
    ]

    min_lat, max_lat, min_lon, max_lon = 37.710, 37.755, -119.605, -119.515
    dem, contours = _generate_dem_and_contours(
        min_lat, max_lat, min_lon, max_lon, 1210.0, 2695.0,
        [(-119.5332, 37.7459, 2695.0), (-119.5729, 37.7304, 2199.0)],
    )
    return DestinationPack(
        id=pid,
        name="Yosemite Valley & High Country",
        state_or_region="California (Yosemite National Park)",
        agency="NPS Yosemite GIS, USGS 3DEP & OpenStreetMap Contributors",
        summary="Granite monoliths, thundering waterfalls, and high-country rim loops from Happy Isles to Glacier Point and Half Dome.",
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
        base_ele_m=1210.0, max_ele_m=2695.0,
        emergency_contact="Yosemite Emergency Dispatch: (209) 372-0911 / 911",
        trails=trails, pois=pois, contours=contours, dem=dem,
    )


def build_acadia_np() -> DestinationPack:
    pid = "acadia_np"
    j_sand_beach = (-68.1825, 44.3292, 18.0)
    j_beehive_top = (-68.1852, 44.3340, 158.0)
    j_bowl_pond = (-68.1890, 44.3358, 122.0)
    j_jordan_pond = (-68.2530, 44.3225, 85.0)
    j_cadillac_summit = (-68.2252, 44.3526, 466.0)
    j_north_ridge_th = (-68.2175, 44.3725, 115.0)

    trails = [
        _make_trail(
            pid, "acad_beehive_loop", "The Beehive & The Bowl Loop", "ACAD-BH", "strenuous", "scramble", "loop",
            [j_sand_beach, j_beehive_top, j_bowl_pond, (-68.1855, 44.3315, 75.0), j_sand_beach],
            False, False, "open", "Iron rungs dry; not suitable for dogs or acrophobia.",
            ["loop", "iron_rungs", "ocean_views", "lake"],
            "Climbs exposed granite cliff ledges using historic iron rungs to panoramic views over Sand Beach and the Gulf of Maine, descending past The Bowl.",
        ),
        _make_trail(
            pid, "acad_jordan_pond", "Jordan Pond Shore Path Loop", "ACAD-JP", "easy", "gravel", "loop",
            [j_jordan_pond, (-68.2565, 44.3320, 88.0), (-68.2500, 44.3385, 92.0), (-68.2475, 44.3290, 86.0), j_jordan_pond],
            True, True, "open", "Cedar boardwalk on west shore in excellent condition.",
            ["loop", "lake", "easy", "family", "dogs"],
            "Level shoreline loop ringing crystal-clear Jordan Pond with classic views of The Bubbles.",
        ),
        _make_trail(
            pid, "acad_cadillac_north", "Cadillac Mountain North Ridge Trail", "ACAD-CNR", "moderate", "rocky", "out_and_back",
            [j_north_ridge_th, (-68.2210, 44.3620, 290.0), j_cadillac_summit],
            True, False, "open", "Open pink granite slabs with constant Frenchman Bay views.",
            ["summit", "ocean_views", "granite", "dogs"],
            "Ascends open pink granite ridges to the 1,530-ft summit of Cadillac Mountain, the highest point along the North Atlantic seaboard.",
        ),
        _make_trail(
            pid, "acad_cadillac_south", "Cadillac South Ridge to Jordan Pond Connector", "ACAD-CSR", "strenuous", "rocky", "segment",
            [j_cadillac_summit, (-68.2340, 44.3380, 310.0), j_jordan_pond],
            True, False, "open", "Long open ridge walk connecting Cadillac summit and Jordan Pond.",
            ["ridge", "summit", "views"],
            "Traverses the long granite spine of Cadillac's South Ridge down toward Jordan Pond House.",
        ),
    ]

    pois = [
        POI(id="poi_ac_sb", pack_id=pid, name="Sand Beach & Beehive Trailhead", kind="trailhead", lat=44.3292, lon=-68.1825, ele_m=18.0, note="Park Loop Road oceanfront trailhead"),
        POI(id="poi_ac_bh", pack_id=pid, name="The Beehive Summit (520 ft)", kind="peak", lat=44.3340, lon=-68.1852, ele_m=158.0, note="Panoramic overlook of Sand Beach & Great Head"),
        POI(id="poi_ac_jp", pack_id=pid, name="Jordan Pond House Trailhead", kind="trailhead", lat=44.3225, lon=-68.2530, ele_m=85.0, note="Water, restrooms, and Island Explorer bus stop"),
        POI(id="poi_ac_cad", pack_id=pid, name="Cadillac Mountain Summit (1,530 ft)", kind="peak", lat=44.3526, lon=-68.2252, ele_m=466.0, note="Highest peak on Mount Desert Island"),
    ]

    min_lat, max_lat, min_lon, max_lon = 44.315, 44.380, -68.265, -68.170
    dem, contours = _generate_dem_and_contours(
        min_lat, max_lat, min_lon, max_lon, 10.0, 466.0,
        [(-68.2252, 44.3526, 466.0), (-68.1852, 44.3340, 158.0)],
    )
    return DestinationPack(
        id=pid,
        name="Acadia National Park",
        state_or_region="Maine (Mount Desert Island)",
        agency="NPS Acadia GIS, USGS 3DEP & OpenStreetMap Contributors",
        summary="Pink granite peaks rising straight from the Atlantic Ocean, historic iron-rung cliff trails, and glacial lakes.",
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
        base_ele_m=10.0, max_ele_m=466.0,
        emergency_contact="Acadia NPS Dispatch: (207) 288-3338 / 911",
        trails=trails, pois=pois, contours=contours, dem=dem,
    )


def build_shenandoah_central() -> DestinationPack:
    pid = "shenandoah_central"
    j_old_rag_th = (-78.2870, 38.5705, 365.0)
    j_old_rag_summit = (-78.3158, 38.5516, 998.0)
    j_saddle = (-78.3220, 38.5540, 880.0)
    j_hawksbill_th = (-78.3950, 38.5620, 1020.0)
    j_hawksbill_top = (-78.3955, 38.5555, 1234.0)

    trails = [
        _make_trail(
            pid, "shen_old_rag_circuit", "Old Rag Mountain Rock Scramble Circuit", "SHEN-OR", "strenuous", "scramble", "loop",
            [j_old_rag_th, (-78.3020, 38.5590, 740.0), j_old_rag_summit, j_saddle, (-78.3080, 38.5680, 520.0), j_old_rag_th],
            False, True, "open", "1-mile granite boulder scramble near summit; day-use ticket required.",
            ["loop", "scramble", "summit", "views", "classic"],
            "Virginia's most famous hike: ascends the thrilling 360-degree granite boulder scramble to Old Rag summit (3,284 ft) and loops back via Saddle and Weakley Hollow Fire Road.",
        ),
        _make_trail(
            pid, "shen_hawksbill_loop", "Hawksbill Summit Loop via Salamander & AT", "SHEN-HB", "moderate", "packed_dirt", "loop",
            [j_hawksbill_th, (-78.3990, 38.5585, 1130.0), j_hawksbill_top, (-78.3920, 38.5580, 1140.0), j_hawksbill_th],
            True, False, "open", "Highest peak in Shenandoah NP; Byrd Nest shelter at summit.",
            ["loop", "summit", "appalachian_trail", "dogs", "views"],
            "Climbs through high-elevation balsam fir forest along the Appalachian Trail and Salamander Trail to the 4,050-ft rocky summit of Hawksbill.",
        ),
    ]

    pois = [
        POI(id="poi_sh_orth", pack_id=pid, name="Old Rag Boundary Trailhead", kind="trailhead", lat=38.5705, lon=-78.2870, ele_m=365.0, note="Main parking lot & ranger check station"),
        POI(id="poi_sh_ors", pack_id=pid, name="Old Rag Summit (3,284 ft)", kind="peak", lat=38.5516, lon=-78.3158, ele_m=998.0, note="360-degree Blue Ridge panorama"),
        POI(id="poi_sh_hb", pack_id=pid, name="Hawksbill Mountain (4,050 ft)", kind="peak", lat=38.5555, lon=-78.3955, ele_m=1234.0, note="Highest summit in Shenandoah National Park"),
    ]

    min_lat, max_lat, min_lon, max_lon = 38.540, 38.580, -78.410, -78.275
    dem, contours = _generate_dem_and_contours(
        min_lat, max_lat, min_lon, max_lon, 350.0, 1234.0,
        [(-78.3955, 38.5555, 1234.0), (-78.3158, 38.5516, 998.0)],
    )
    return DestinationPack(
        id=pid,
        name="Shenandoah National Park Central",
        state_or_region="Virginia (Blue Ridge Mountains)",
        agency="NPS Shenandoah GIS, USGS 3DEP & OpenStreetMap Contributors",
        summary="Blue Ridge granite scrambles, Appalachian Trail ridge walks, and cascading hollow waterfalls.",
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
        base_ele_m=350.0, max_ele_m=1234.0,
        emergency_contact="Shenandoah NPS Emergency: (800) 732-0911 / 911",
        trails=trails, pois=pois, contours=contours, dem=dem,
    )


def build_rocky_mountain_bear_lake() -> DestinationPack:
    pid = "rocky_mountain_bear_lake"
    j_bear_lake = (-105.6454, 40.3120, 2888.0)
    j_dream_lake = (-105.6592, 40.3092, 3020.0)
    j_emerald_lake = (-105.6682, 40.3098, 3082.0)
    j_sky_pond = (-105.6675, 40.2782, 3322.0)
    j_flattop = (-105.6915, 40.3090, 3756.0)

    trails = [
        _make_trail(
            pid, "rmnp_emerald_lake", "Bear, Nymph, Dream & Emerald Lakes Trail", "RMNP-EL", "moderate", "packed_dirt", "out_and_back",
            [j_bear_lake, (-105.6510, 40.3105, 2956.0), j_dream_lake, j_emerald_lake, j_dream_lake, j_bear_lake],
            False, True, "open", "Classic subalpine four-lake walk beneath Hallett Peak.",
            ["lake", "views", "classic", "subalpine"],
            "Strings together four subalpine glacial lakes culminating at Emerald Lake (10,110 ft) directly beneath the sheer cirque walls of Hallett Peak and Flattop Mountain.",
        ),
        _make_trail(
            pid, "rmnp_sky_pond", "Glacier Gorge toThe Loch & Sky Pond", "RMNP-SP", "strenuous", "scramble", "out_and_back",
            [j_bear_lake, (-105.6480, 40.2960, 3105.0), (-105.6575, 40.2855, 3230.0), j_sky_pond],
            False, True, "snow", "Short wet rock scramble beside Timberline Falls; snow patches linger in cirque.",
            ["lake", "waterfall", "alpine", "scramble"],
            "Climbs past Alberta Falls and The Loch before scrambling alongside Timberline Falls into the dramatic alpine cathedral of Sky Pond (10,900 ft) and the Sharkstooth.",
        ),
        _make_trail(
            pid, "rmnp_flattop_mountain", "Flattop Mountain & Continental Divide Trail", "RMNP-FM", "strenuous", "alpine", "out_and_back",
            [j_bear_lake, (-105.6620, 40.3135, 3280.0), (-105.6780, 40.3115, 3540.0), j_flattop],
            False, False, "caution", "High-altitude tundra above 11,500 ft; afternoon lightning risk.",
            ["continental_divide", "alpine", "summit", "views"],
            "Ascends continuously from Bear Lake above treeline onto the Continental Divide at Flattop Mountain (12,324 ft).",
        ),
    ]

    pois = [
        POI(id="poi_rm_bl", pack_id=pid, name="Bear Lake Trailhead (9,475 ft)", kind="trailhead", lat=40.3120, lon=-105.6454, ele_m=2888.0, note="Ranger station, restrooms & park shuttle stop"),
        POI(id="poi_rm_el", pack_id=pid, name="Emerald Lake (10,110 ft)", kind="water", lat=40.3098, lon=-105.6682, ele_m=3082.0, note="Alpine cirque lake beneath Hallett Peak"),
        POI(id="poi_rm_sp", pack_id=pid, name="Sky Pond (10,900 ft)", kind="water", lat=40.2782, lon=-105.6675, ele_m=3322.0, note="High alpine tarn beneath Taylor Glacier & Sharkstooth"),
        POI(id="poi_rm_ft", pack_id=pid, name="Flattop Mountain Summit (12,324 ft)", kind="peak", lat=40.3090, lon=-105.6915, ele_m=3756.0, note="Continental Divide overlook"),
    ]

    min_lat, max_lat, min_lon, max_lon = 40.270, 40.325, -105.700, -105.635
    dem, contours = _generate_dem_and_contours(
        min_lat, max_lat, min_lon, max_lon, 2850.0, 3756.0,
        [(-105.6915, 40.3090, 3756.0), (-105.6675, 40.2782, 3322.0)],
    )
    return DestinationPack(
        id=pid,
        name="Rocky Mountain National Park – Bear Lake Corridor",
        state_or_region="Colorado (Front Range / Continental Divide)",
        agency="NPS Rocky Mountain GIS, USGS 3DEP & OpenStreetMap Contributors",
        summary="High-altitude glacial gorges, alpine cirque lakes, and Continental Divide tundra climbs between 9,475 ft and 12,324 ft.",
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
        base_ele_m=2850.0, max_ele_m=3756.0,
        emergency_contact="RMNP Dispatch: (970) 586-1204 / 911",
        trails=trails, pois=pois, contours=contours, dem=dem,
    )


def build_zion_canyon() -> DestinationPack:
    pid = "zion_canyon"
    j_grotto = (-112.9508, 37.2591, 1318.0)
    j_scout_lookout = (-112.9516, 37.2712, 1765.0)
    j_visitor_ctr = (-112.9870, 37.2002, 1212.0)
    j_watchman = (-112.9765, 37.1975, 1325.0)

    trails = [
        _make_trail(
            pid, "zion_west_rim_scout", "West Rim Trail via Walter's Wiggles to Scout Lookout", "ZION-WR", "strenuous", "paved", "out_and_back",
            [j_grotto, (-112.9535, 37.2655, 1520.0), j_scout_lookout, (-112.9535, 37.2655, 1520.0), j_grotto],
            False, False, "open", "21 tight switchbacks of Walter's Wiggles; Angels Landing chain section requires permit.",
            ["canyon", "views", "classic", "switchbacks"],
            "Climbs Refrigerated Canyon and the historic 21 switchbacks of Walter's Wiggles to Scout Lookout (5,790 ft) overlooking Zion Canyon.",
        ),
        _make_trail(
            pid, "zion_watchman_loop", "The Watchman & Pa'rus Trail Loop", "ZION-WT", "moderate", "packed_dirt", "loop",
            [j_visitor_ctr, j_watchman, (-112.9810, 37.2040, 1245.0), j_visitor_ctr],
            False, True, "open", "Great sunset hike from Zion Canyon Visitor Center; Pa'rus segment is dog-friendly.",
            ["loop", "canyon", "views", "sunset"],
            "Ascends sandstone benches above the Virgin River to a mesa viewpoint facing the Towers of the Virgin and the Watchman spire.",
        ),
    ]

    pois = [
        POI(id="poi_zn_gr", pack_id=pid, name="The Grotto Trailhead (Shuttle #6)", kind="trailhead", lat=37.2591, lon=-112.9508, ele_m=1318.0, note="Water bottle filling station & restrooms"),
        POI(id="poi_zn_sl", pack_id=pid, name="Scout Lookout (5,790 ft)", kind="viewpoint", lat=37.2712, lon=-112.9516, ele_m=1765.0, note="Saddle overlook at base of Angels Landing spine"),
        POI(id="poi_zn_vc", pack_id=pid, name="Zion Canyon Visitor Center", kind="trailhead", lat=37.2002, lon=-112.9870, ele_m=1212.0, note="Wilderness desk, water & shuttle terminal"),
    ]

    min_lat, max_lat, min_lon, max_lon = 37.190, 37.280, -112.995, -112.940
    dem, contours = _generate_dem_and_contours(
        min_lat, max_lat, min_lon, max_lon, 1200.0, 1765.0,
        [(-112.9516, 37.2712, 1765.0), (-112.9765, 37.1975, 1325.0)],
    )
    return DestinationPack(
        id=pid,
        name="Zion Canyon",
        state_or_region="Utah (Zion National Park)",
        agency="NPS Zion GIS, USGS 3DEP & OpenStreetMap Contributors",
        summary="Soaring Navajo sandstone cliffs, Virgin River narrows, and carved canyon switchbacks.",
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
        base_ele_m=1200.0, max_ele_m=1765.0,
        emergency_contact="Zion NPS Dispatch: (435) 772-3322 / 911",
        trails=trails, pois=pois, contours=contours, dem=dem,
    )


def build_mount_rainier_paradise() -> DestinationPack:
    pid = "mount_rainier_paradise"
    j_paradise = (-121.7350, 46.7860, 1652.0)
    j_panorama_pt = (-121.7325, 46.8045, 2072.0)
    j_myrtle_falls = (-121.7270, 46.7912, 1720.0)

    trails = [
        _make_trail(
            pid, "mora_skyline_loop", "Skyline Trail Loop via Panorama Point & Myrtle Falls", "MORA-SL", "strenuous", "alpine", "loop",
            [j_paradise, (-121.7415, 46.7960, 1890.0), j_panorama_pt, (-121.7235, 46.7985, 1940.0), j_myrtle_falls, j_paradise],
            False, True, "snow", "Early/late season snowfields near Panorama Point; trek poles recommended.",
            ["loop", "alpine", "glacier", "waterfall", "wildflowers", "classic"],
            "Mount Rainier's premier subalpine and alpine loop climbing beside the Nisqually Glacier to Panorama Point (6,800 ft) and descending past Myrtle Falls.",
        ),
        _make_trail(
            pid, "mora_nisqually_vista", "Nisqually Vista Loop", "MORA-NV", "easy", "paved", "loop",
            [j_paradise, (-121.7430, 46.7885, 1690.0), (-121.7400, 46.7910, 1680.0), j_paradise],
            False, False, "open", "Family-friendly paved loop with direct views of Nisqually Glacier.",
            ["loop", "easy", "glacier", "family"],
            "Accessible subalpine meadow loop offering close-up views of the snout of the Nisqually Glacier and Mount Rainier's upper icefalls.",
        ),
    ]

    pois = [
        POI(id="poi_mr_par", pack_id=pid, name="Henry M. Jackson Visitor Center (Paradise)", kind="trailhead", lat=46.7860, lon=-121.7350, ele_m=1652.0, note="Climbing ranger station, water & exhibits"),
        POI(id="poi_mr_pan", pack_id=pid, name="Panorama Point (6,800 ft)", kind="peak", lat=46.8045, lon=-121.7325, ele_m=2072.0, note="Views of Mt. Adams, Mt. St. Helens & Mt. Hood"),
        POI(id="poi_mr_mf", pack_id=pid, name="Myrtle Falls (72 ft)", kind="waterfall", lat=46.7912, lon=-121.7270, ele_m=1720.0, note="Waterfall framed by Mount Rainier"),
    ]

    min_lat, max_lat, min_lon, max_lon = 46.780, 46.815, -121.750, -121.715
    dem, contours = _generate_dem_and_contours(
        min_lat, max_lat, min_lon, max_lon, 1640.0, 2100.0,
        [(-121.7325, 46.8045, 2072.0)],
    )
    return DestinationPack(
        id=pid,
        name="Mount Rainier – Paradise Corridor",
        state_or_region="Washington (Mount Rainier National Park)",
        agency="NPS Mount Rainier GIS, USGS 3DEP & OpenStreetMap Contributors",
        summary="Subalpine wildflower meadows, cascading waterfalls, and high-altitude viewpoints overlooking active glaciers.",
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
        base_ele_m=1640.0, max_ele_m=2100.0,
        emergency_contact="Mount Rainier NPS Dispatch: (360) 569-6600 / 911",
        trails=trails, pois=pois, contours=contours, dem=dem,
    )


def build_mount_monadnock() -> DestinationPack:
    """Monadnock State Park. No sketched trails: every named trail on the mountain is added from
    OpenStreetMap by tools/fetch_map_details.py (see data/osm_trails.json)."""
    pid = "mount_monadnock"
    pois = [
        POI(id="poi_mon_summit", pack_id=pid, name="Mount Monadnock Summit (3,165 ft)", kind="peak", lat=42.8611, lon=-72.1083, ele_m=965.0, note="Open bare-rock summit; 360° views to six New England states on clear days"),
        POI(id="poi_mon_hq", pack_id=pid, name="Monadnock State Park Headquarters Trailhead", kind="trailhead", lat=42.8456, lon=-72.0889, ele_m=390.0, note="Poole Rd, Jaffrey: visitor center, parking fee, White Dot & White Cross trails. No dogs in the park"),
        POI(id="poi_mon_toll", pack_id=pid, name="Old Toll Road Parking", kind="trailhead", lat=42.8353, lon=-72.1141, ele_m=430.0, note="Route 124: Old Toll Road to the Halfway House site, White Arrow Trail"),
        POI(id="poi_mon_bald", pack_id=pid, name="Bald Rock", kind="peak", lat=42.8539, lon=-72.1069, ele_m=790.0, note="Open ledges on the south shoulder"),
        POI(id="poi_mon_thoreau", pack_id=pid, name="Thoreau's Seat", kind="viewpoint", lat=42.8504, lon=-72.1075, ele_m=690.0, note="Ledge where Thoreau camped in 1858 and 1860"),
        POI(id="poi_mon_falcon", pack_id=pid, name="Falcon Spring", kind="water", lat=42.8540, lon=-72.0934, ele_m=700.0, note="Spring on the Cascade Link / White Cross area; treat before drinking"),
        POI(id="poi_mon_gilson", pack_id=pid, name="Gilson Pond Campground", kind="camp", lat=42.8635, lon=-72.0630, ele_m=360.0, note="State park campground (reservations); Birchtoft & Pond Loop trails"),
    ]
    return DestinationPack(
        id=pid,
        name="Mount Monadnock",
        state_or_region="New Hampshire (Monadnock State Park — Jaffrey & Dublin)",
        agency="NH Division of Parks & Recreation, USGS 3DEP & OpenStreetMap Contributors",
        summary="One of the most-climbed mountains in the world: a 3,165 ft bald summit with a dense web of short, steep, rocky trails from Jaffrey, Dublin and the Old Toll Road.",
        min_lat=42.810, max_lat=42.905, min_lon=-72.170, max_lon=-72.045,
        base_ele_m=300.0, max_ele_m=965.0,
        emergency_contact="Monadnock State Park: (603) 532-8862 / 911",
        trails=[], pois=pois,
    )


def build_all(only: List[str] | None = None) -> None:
    packs = [
        build_white_mountains_nh(),
        build_yosemite_valley(),
        build_acadia_np(),
        build_shenandoah_central(),
        build_rocky_mountain_bear_lake(),
        build_zion_canyon(),
        build_mount_rainier_paradise(),
        build_mount_monadnock(),
    ]
    if only:
        packs = [p for p in packs if p.id in only]
    packs_dir = REPO_ROOT / "data" / "packs"
    packs_dir.mkdir(parents=True, exist_ok=True)

    catalog_entries: List[Dict[str, Any]] = []
    for pack in packs:
        target = packs_dir / f"{pack.id}.json"
        target.write_text(json.dumps(pack.model_dump(), indent=2) + "\n", encoding="utf-8")
        catalog_entries.append(
            {
                "id": pack.id,
                "name": pack.name,
                "state_or_region": pack.state_or_region,
                "agency": pack.agency,
                "license": pack.license,
                "summary": pack.summary,
                "bbox": [pack.min_lon, pack.min_lat, pack.max_lon, pack.max_lat],
                "base_ele_m": pack.base_ele_m,
                "max_ele_m": pack.max_ele_m,
                "trail_count": len(pack.trails),
                "poi_count": len(pack.pois),
                "bundled_file": f"packs/{pack.id}.json",
            }
        )

    catalog_path = REPO_ROOT / "data" / "destinations_catalog.json"
    if only and catalog_path.exists():
        # Partial rebuild: keep the other packs' catalog entries, in their original order
        rebuilt = {e["id"]: e for e in catalog_entries}
        existing = json.loads(catalog_path.read_text(encoding="utf-8")).get("destinations", [])
        catalog_entries = [rebuilt.pop(e["id"], e) for e in existing] + list(rebuilt.values())

    catalog = {
        "as_of": "2026-10-17",
        "source": "USGS National Digital Trails / 3DEP Public Domain & OpenStreetMap ODbL",
        "destinations": catalog_entries,
    }
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(packs)} regional destination packs in {packs_dir}")
    print("Next: .venv/bin/python tools/fetch_map_details.py  (real terrain, contours, OSM detail, trail alignment)")


if __name__ == "__main__":
    # Optional pack ids rebuild only those packs (others, possibly enriched, are left untouched)
    build_all(sys.argv[1:] or None)

