#!/usr/bin/env python3
"""Deterministic builder for AegisHiker's 16 bundled regional destination packs.

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
  - data/packs/fitchburg_state_forest.json
  - data/packs/acton_boxborough_harvard.json
  - data/packs/pioneer_valley.json
  - data/packs/great_smoky_mountains.json
  - data/packs/grand_canyon.json
  - data/packs/yellowstone.json
  - data/packs/grand_teton.json
  - data/packs/joshua_tree.json

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
        POI(id="poi_wm_moosilauke", pack_id=pid, name="Mount Moosilauke Summit", kind="peak", lat=44.0244, lon=-71.8314, ele_m=1464.0, note="Broad alpine summit (4,802 ft) at the southwest corner of the Whites; Appalachian Trail via Glencliff and Beaver Brook"),
        POI(id="poi_wm_ravine_lodge", pack_id=pid, name="Moosilauke Ravine Lodge", kind="hut", lat=44.0005, lon=-71.8155, ele_m=760.0, note="Dartmouth Outing Club lodge; Gorge Brook and Snapper trailheads"),
        POI(id="poi_wm_cabot", pack_id=pid, name="Mount Cabot Summit", kind="peak", lat=44.5056, lon=-71.4167, ele_m=1271.0, note="Northernmost 4,000-footer (4,170 ft) in the Pliny Range; Cabot Cabin just below the summit"),
        POI(id="poi_wm_lakes_clouds", pack_id=pid, name="AMC Lakes of the Clouds Hut", kind="hut", lat=44.2588, lon=-71.3188, ele_m=1540.0, note="Highest AMC hut on the shoulder of Mt. Monroe & Mt. Washington"),
    ]

    # South to the Flume Slide Trail (Franconia Notch), north to Randolph for the Howker Ridge
    # Trail and the Northern Presidentials, east to the Maine border (Wildcats, Carters, Baldfaces,
    # Evans Notch), north to the Pliny / Kilkenny Range for Mount Cabot, The Horn and Unknown Pond,
    # southwest to Mount Moosilauke (Glencliff, Gorge Brook, Beaver Brook and Benton trails)
    # (-70.95: just past the state line so Evans Notch trails that cross it, e.g. Bickford Brook, stay whole)
    min_lat, max_lat, min_lon, max_lon = 43.985, 44.545, -71.905, -70.950
    dem, contours = _generate_dem_and_contours(
        min_lat,
        max_lat,
        min_lon,
        max_lon,
        520.0,
        1917.0,
        [(-71.3032, 44.2705, 1917.0), (-71.6409, 44.1607, 1600.0), (-71.6418, 44.1488, 1551.0), (-71.4167, 44.5056, 1271.0), (-71.8314, 44.0244, 1464.0)],
    )
    return DestinationPack(
        id=pid,
        name="New Hampshire White Mountains",
        state_or_region="New Hampshire (White Mountain National Forest)",
        agency="USFS White Mountain National Forest, USGS 3DEP & OpenStreetMap Contributors",
        summary="Alpine ridgelines, glacially carved ravines, waterfalls, and AMC high huts from Franconia Ridge across the Presidential Range to the Wildcats, Carters and Baldfaces at the Maine border, north to Mount Cabot and southwest to Mount Moosilauke.",
        pack_version="1.6",  # 1.1: Flume Slide / Howker Ridge; 1.2: east to the Maine border; 1.3: + connector trails, Lost Pond; 1.4: every named trail in the region (Presidentials); 1.5: north to Mount Cabot; 1.6: southwest to Mount Moosilauke
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


def build_fitchburg_state_forest() -> DestinationPack:
    """Fitchburg State Forest (MA) with the adjoining Pearl Hill State Park, Willard Brook State Forest
    and Table Rock Conservation Area, plus Leominster State Forest (Crow Hills) and the town of Princeton
    (Wachusett Mountain) to the south, linked by the Midstate Trail. Trails come from OpenStreetMap
    (data/osm_trails.json)."""
    pid = "fitchburg_state_forest"
    pois = [
        POI(id="poi_fsf_center", pack_id=pid, name="Fitchburg State Forest", kind="junction", lat=42.62592, lon=-71.77174, ele_m=171.0, note="Centre of the state forest (Fitchburg / Lunenburg / Townsend)"),
        POI(id="poi_fsf_pearl", pack_id=pid, name="Pearl Hill (1,017 ft)", kind="peak", lat=42.62383, lon=-71.78722, ele_m=310.0, note="High point of the area, in Fitchburg State Forest"),
        POI(id="poi_fsf_rattlesnake", pack_id=pid, name="Rattlesnake Hill", kind="peak", lat=42.65295, lon=-71.77955, ele_m=255.0),
        POI(id="poi_fsf_fort", pack_id=pid, name="Fort Hill", kind="peak", lat=42.66009, lon=-71.78007, ele_m=241.0),
        POI(id="poi_fsf_table_rock", pack_id=pid, name="Table Rock Conservation Area", kind="viewpoint", lat=42.61672, lon=-71.76357, ele_m=200.0),
        POI(id="poi_fsf_pearl_pond", pack_id=pid, name="Pearl Hill Pond", kind="water", lat=42.65687, lon=-71.75823, ele_m=165.0, note="Pearl Hill State Park swimming pond"),
        POI(id="poi_fsf_pearl_camp", pack_id=pid, name="Pearl Hill Campground", kind="camp", lat=42.65338, lon=-71.76672, ele_m=170.0, note="Pearl Hill State Park campground (reservations)"),
        POI(id="poi_fsf_damon_camp", pack_id=pid, name="Damon Pond Campground", kind="camp", lat=42.66036, lon=-71.78972, ele_m=150.0, note="Willard Brook State Forest campground"),
        POI(id="poi_fsf_willard_hq", pack_id=pid, name="Willard Brook Headquarters", kind="ranger", lat=42.67344, lon=-71.7733, ele_m=130.0, note="DCR headquarters, Route 119, Townsend"),
        POI(id="poi_fsf_trap_falls", pack_id=pid, name="Trap Falls Parking", kind="trailhead", lat=42.67221, lon=-71.77579, ele_m=135.0, note="Trap Falls on Willard Brook, a short walk from the lot"),
        # Leominster State Forest
        POI(id="poi_lsf_crow_hills", pack_id=pid, name="Crow Hills", kind="peak", lat=42.5263, lon=-71.8607, ele_m=360.0, note="Twin rocky summits above Crow Hill Pond, Leominster State Forest"),
        POI(id="poi_lsf_crow_ledges", pack_id=pid, name="Crow Hill Ledges", kind="viewpoint", lat=42.5193, lon=-71.8593, ele_m=330.0, note="Cliffs with views east; popular rock-climbing area. Steep scramble on the Crow Hill Trail"),
        POI(id="poi_lsf_crow_parking", pack_id=pid, name="Crow Hill Pond Parking", kind="trailhead", lat=42.5186, lon=-71.8565, ele_m=290.0, note="Route 31 lot for Crow Hill Ledges and Crow Hill Pond"),
        POI(id="poi_lsf_crow_pond", pack_id=pid, name="Crow Hill Pond", kind="water", lat=42.5170, lon=-71.8553, ele_m=285.0, note="Swimming beach (Lower Crow Hill Pond)"),
        POI(id="poi_lsf_office", pack_id=pid, name="Leominster State Forest Headquarters", kind="ranger", lat=42.5331, lon=-71.8554, ele_m=300.0, note="DCR office, Route 31, Princeton / Westminster"),
        POI(id="poi_lsf_rocky_pond", pack_id=pid, name="Rocky Pond", kind="water", lat=42.5150, lon=-71.8470, ele_m=290.0),
        # Princeton & Wachusett Mountain
        POI(id="poi_pri_wachusett", pack_id=pid, name="Mount Wachusett (2,006 ft)", kind="peak", lat=42.48906, lon=-71.88684, ele_m=609.0, note="High point of the area and of Massachusetts east of the Connecticut River; Midstate Trail crosses the summit"),
        POI(id="poi_pri_visitor_center", pack_id=pid, name="Wachusett Mountain Visitor Center", kind="ranger", lat=42.49146, lon=-71.87957, ele_m=380.0, note="DCR Wachusett Mountain State Reservation, Mountain Rd, Princeton"),
        POI(id="poi_pri_harlow", pack_id=pid, name="Harlow Outlook", kind="viewpoint", lat=42.49204, lon=-71.88337, ele_m=540.0),
        POI(id="poi_pri_echo_lake", pack_id=pid, name="Echo Lake", kind="water", lat=42.47771, lon=-71.8886, ele_m=455.0),
        POI(id="poi_pri_little_wachusett", pack_id=pid, name="Little Wachusett", kind="peak", lat=42.46342, lon=-71.8898, ele_m=476.0),
        POI(id="poi_pri_redemption_rock", pack_id=pid, name="Redemption Rock Trailhead", kind="trailhead", lat=42.5036, lon=-71.8852, ele_m=370.0, note="Midstate Trail crossing of Route 140 at Redemption Rock"),
        POI(id="poi_pri_wachusett_meadow", pack_id=pid, name="Wachusett Meadows Wildlife Sanctuary", kind="trailhead", lat=42.45989, lon=-71.90899, ele_m=340.0, note="Mass Audubon sanctuary on Goodnow Rd; Midstate Trail passes through (fee for non-members)"),
        POI(id="poi_pri_center", pack_id=pid, name="Princeton Center", kind="junction", lat=42.44841, lon=-71.87681, ele_m=346.0),
    ]
    return DestinationPack(
        id=pid,
        name="Fitchburg, Leominster & Princeton",
        pack_version="1.1",  # 1.1: south to Princeton / Wachusett, Midstate Trail sections, Crow Hill Ledges climbs
        state_or_region="Massachusetts (Fitchburg, Townsend, Westminster, Leominster & Princeton — with Pearl Hill, Willard Brook, Crow Hills & Wachusett Mountain)",
        agency="MA DCR, Mass Audubon, USGS 3DEP & OpenStreetMap Contributors",
        summary="Rolling hemlock and pine woods, brooks and ponds north of Fitchburg: Pearl Hill, Table Rock, "
                "Trap Falls and a dense web of shared hiking and mountain-bike trails, linked by the Midstate Trail "
                "to Leominster State Forest, the Crow Hill Ledges and the town of Princeton, with Wachusett Mountain "
                "and Wachusett Meadow.",
        min_lat=42.390, max_lat=42.678, min_lon=-71.970, max_lon=-71.730,
        base_ele_m=120.0, max_ele_m=620.0,
        emergency_contact="911",
        trails=[], pois=pois,
    )


def build_acton_boxborough_harvard() -> DestinationPack:
    """Town conservation lands of Acton, Boxborough and Harvard (MA): Nashoba Brook, Great Hill, Steele
    Farm, Beaver Brook, Prospect & Oak Hills, Holy Hill and the Oxbow refuge on the Nashua River.
    Trails come from OpenStreetMap (data/osm_trails.json)."""
    pid = "acton_boxborough_harvard"
    pois = [
        # Acton
        POI(id="poi_abh_nashoba_brook", pack_id=pid, name="Nashoba Brook Conservation Land", kind="trailhead", lat=42.5081, lon=-71.4104, ele_m=60.0, note="Acton's largest conservation area: brook, old pencil-mill ruins and Wheeler Farm foundation. Parking on Davis Rd"),
        POI(id="poi_abh_wheeler", pack_id=pid, name="Wheeler Farm Foundation", kind="viewpoint", lat=42.51326, lon=-71.4053, ele_m=60.0),
        POI(id="poi_abh_great_hill", pack_id=pid, name="Great Hill", kind="peak", lat=42.46843, lon=-71.45423, ele_m=109.0, note="Great Hill Conservation Land, behind the Acton public safety facility"),
        POI(id="poi_abh_spring_hill", pack_id=pid, name="Spring Hill Conservation Land", kind="trailhead", lat=42.50155, lon=-71.40214, ele_m=80.0, note="Joins Camp Acton and Nashoba Brook conservation lands"),
        POI(id="poi_abh_nagog_hill", pack_id=pid, name="Nagog Hill", kind="peak", lat=42.50704, lon=-71.44312, ele_m=111.0),
        POI(id="poi_abh_grassy_pond", pack_id=pid, name="Grassy Pond", kind="water", lat=42.50192, lon=-71.45297, ele_m=70.0, note="Grassy Pond Conservation Land"),
        POI(id="poi_abh_wills_hole", pack_id=pid, name="Wills Hole", kind="water", lat=42.51868, lon=-71.42185, ele_m=60.0, note="Wills Hole Conservation Land / Acton Town Forest"),
        POI(id="poi_abh_arboretum", pack_id=pid, name="Acton Arboretum", kind="trailhead", lat=42.48001, lon=-71.43315, ele_m=70.0, note="Gardens, boardwalk and short woodland trails off Taylor Rd"),
        POI(id="poi_abh_annursnac", pack_id=pid, name="Annursnac Hill", kind="peak", lat=42.47787, lon=-71.39534, ele_m=110.0),
        # Boxborough
        POI(id="poi_abh_steele_farm", pack_id=pid, name="Steele Farm", kind="trailhead", lat=42.48727, lon=-71.5214, ele_m=100.0, note="Boxborough town farm: open fields, stone walls and woodland loops off Middle Rd"),
        POI(id="poi_abh_flerra", pack_id=pid, name="Flerra Meadows", kind="trailhead", lat=42.47269, lon=-71.51382, ele_m=90.0),
        POI(id="poi_abh_flagg_hill", pack_id=pid, name="Flagg Hill", kind="peak", lat=42.46522, lon=-71.4992, ele_m=139.0),
        POI(id="poi_abh_patch_hill", pack_id=pid, name="Patch Hill Conservation Area", kind="trailhead", lat=42.50023, lon=-71.50985, ele_m=110.0),
        POI(id="poi_abh_beaver_brook", pack_id=pid, name="Beaver Brook Valley Preserve", kind="trailhead", lat=42.50638, lon=-71.52765, ele_m=90.0, note="Beaver Brook woods and meadows on the Boxborough / Harvard line"),
        # Harvard
        POI(id="poi_abh_prospect_hill", pack_id=pid, name="Prospect Hill", kind="peak", lat=42.50353, lon=-71.60912, ele_m=171.0, note="Orchard hilltop with long views west to Wachusett and Monadnock"),
        POI(id="poi_abh_fruitlands", pack_id=pid, name="Fruitlands Museum", kind="trailhead", lat=42.50563, lon=-71.614, ele_m=120.0, note="Museum grounds with woodland and meadow trails down toward the Nashua valley (fee)"),
        POI(id="poi_abh_oak_hill", pack_id=pid, name="Oak Hill", kind="peak", lat=42.51609, lon=-71.54911, ele_m=187.0, note="High point of the area, Harvard"),
        POI(id="poi_abh_holy_hill", pack_id=pid, name="Holy Hill", kind="viewpoint", lat=42.5366, lon=-71.5605, ele_m=130.0, note="Shaker Village conservation land; site of the Shakers' outdoor worship ground"),
        POI(id="poi_abh_bare_hill_pond", pack_id=pid, name="Bare Hill Pond", kind="water", lat=42.48844, lon=-71.59791, ele_m=80.0, note="Harvard Town Beach on the north shore"),
        POI(id="poi_abh_bowers", pack_id=pid, name="Bowers Springs Conservation Area", kind="trailhead", lat=42.4645, lon=-71.60676, ele_m=110.0),
        POI(id="poi_abh_delaney", pack_id=pid, name="Delaney Wildlife Management Area", kind="water", lat=42.45292, lon=-71.54822, ele_m=70.0, note="Delaney Pond and marshes (Harvard / Stow / Bolton); popular for birding. Hunting in season"),
        POI(id="poi_abh_oxbow", pack_id=pid, name="Oxbow National Wildlife Refuge", kind="ranger", lat=42.53356, lon=-71.61999, ele_m=65.0, note="USFWS refuge along the Nashua River floodplain"),
        POI(id="poi_abh_bill_ashe", pack_id=pid, name="Bill Ashe Trailhead", kind="trailhead", lat=42.54023, lon=-71.63342, ele_m=70.0, note="Oxbow NWR, off Still River Depot Rd"),
    ]
    return DestinationPack(
        id=pid,
        name="Acton, Boxborough & Harvard",
        state_or_region="Massachusetts (Acton, Boxborough & Harvard — with Nashoba Brook, Steele Farm, Holy Hill & the Oxbow refuge)",
        agency="Town Conservation Commissions, Harvard Conservation Trust, USFWS, USGS 3DEP & OpenStreetMap Contributors",
        summary="Gentle hills, orchards, stone walls and brooks west of Concord: a patchwork of town conservation "
                "lands from Acton's Nashoba Brook and Great Hill through Boxborough's Steele Farm and Beaver Brook "
                "to Harvard's Prospect and Oak Hills, the Shaker lands on Holy Hill and the Nashua River at Oxbow.",
        min_lat=42.434, max_lat=42.556, min_lon=-71.643, max_lon=-71.381,
        base_ele_m=40.0, max_ele_m=200.0,
        emergency_contact="911",
        trails=[], pois=pois,
    )


def build_pioneer_valley() -> DestinationPack:
    """Pioneer Valley (MA): the Connecticut River valley from Mt. Tom and the Holyoke Range north past
    Northampton and Amherst to Mt. Sugarloaf and Mt. Toby, crossed by the New England Trail
    (Metacomet-Monadnock) and the Robert Frost Trail. Trails come from OpenStreetMap (data/osm_trails.json)."""
    pid = "pioneer_valley"
    pois = [
        # Mt. Tom Range
        POI(id="poi_pv_mt_tom", pack_id=pid, name="Mount Tom", kind="peak", lat=42.2465, lon=-72.6337, ele_m=366.0, note="Highest point of the Mt. Tom Range (1,202 ft); basalt cliffs above the Connecticut River"),
        POI(id="poi_pv_goat_peak", pack_id=pid, name="Goat Peak Lookout", kind="viewpoint", lat=42.2620, lon=-72.6290, ele_m=250.0, note="Observation tower on the Metacomet-Monadnock Trail; hawk-watching in autumn"),
        POI(id="poi_pv_bray_lake", pack_id=pid, name="Lake Bray", kind="trailhead", lat=42.2630, lon=-72.6200, ele_m=140.0, note="Mt. Tom State Reservation, Route 5 entrance: parking, fishing pond and trail access"),
        # Holyoke Range
        POI(id="poi_pv_mt_holyoke", pack_id=pid, name="Mount Holyoke", kind="peak", lat=42.3005, lon=-72.5880, ele_m=285.0, note="Skinner State Park; the historic Summit House looks over the Oxbow (935 ft)"),
        POI(id="poi_pv_summit_house", pack_id=pid, name="Summit House", kind="viewpoint", lat=42.3002, lon=-72.5878, ele_m=285.0, note="1851 summit hotel, now a museum (seasonal); summit road open in season"),
        POI(id="poi_pv_norwottuck", pack_id=pid, name="Mount Norwottuck", kind="peak", lat=42.3025, lon=-72.5258, ele_m=337.0, note="Highest peak of the Holyoke Range (1,106 ft)"),
        POI(id="poi_pv_horse_caves", pack_id=pid, name="Horse Caves", kind="viewpoint", lat=42.3010, lon=-72.5235, ele_m=290.0, note="Overhanging ledges below Norwottuck, said to have hidden Shays' Rebellion horses"),
        POI(id="poi_pv_bare_mtn", pack_id=pid, name="Bare Mountain", kind="peak", lat=42.3040, lon=-72.5380, ele_m=310.0, note="Short, steep climb from the Notch with wide views north over Amherst"),
        POI(id="poi_pv_notch", pack_id=pid, name="Notch Visitor Center", kind="ranger", lat=42.3035, lon=-72.5290, ele_m=180.0, note="Holyoke Range State Park, Route 116: parking, restrooms and trail maps"),
        # Northampton & Easthampton
        POI(id="poi_pv_arcadia", pack_id=pid, name="Arcadia Wildlife Sanctuary", kind="trailhead", lat=42.2960, lon=-72.6560, ele_m=40.0, note="Mass Audubon sanctuary on the Oxbow marshes (fee for non-members)"),
        POI(id="poi_pv_look_park", pack_id=pid, name="Look Park", kind="trailhead", lat=42.3360, lon=-72.6600, ele_m=60.0, note="Northampton city park on the Mill River"),
        # Sugarloaf & Toby
        POI(id="poi_pv_sugarloaf", pack_id=pid, name="Mount Sugarloaf", kind="peak", lat=42.4707, lon=-72.5947, ele_m=199.0, note="Mount Sugarloaf State Reservation: sandstone cliffs and an observation tower over the river (652 ft)"),
        POI(id="poi_pv_mt_toby", pack_id=pid, name="Mount Toby", kind="peak", lat=42.4920, lon=-72.5305, ele_m=389.0, note="High point of the area (1,269 ft); fire tower on the summit, Robert Frost Trail"),
        POI(id="poi_pv_roaring_falls", pack_id=pid, name="Roaring Falls", kind="waterfall", lat=42.4985, lon=-72.5160, ele_m=170.0, note="Cascades on Roaring Brook, Mt. Toby State Forest"),
    ]
    return DestinationPack(
        id=pid,
        name="Pioneer Valley",
        state_or_region="Massachusetts (Connecticut River valley — Mt. Tom, Holyoke Range, Northampton, Amherst, Mt. Sugarloaf & Mt. Toby)",
        agency="MA DCR, Mass Audubon, Kestrel Land Trust, USGS 3DEP & OpenStreetMap Contributors",
        summary="Traprock ridges above the Connecticut River: the cliffs of Mt. Tom, the Holyoke Range traverse from "
                "Mt. Holyoke's Summit House to Norwottuck and the Horse Caves, and north past Northampton and "
                "Amherst to Mt. Sugarloaf and the Mt. Toby fire tower, linked by the New England Trail and the "
                "Robert Frost Trail.",
        min_lat=42.205, max_lat=42.520, min_lon=-72.720, max_lon=-72.440,
        base_ele_m=30.0, max_ele_m=390.0,
        emergency_contact="911",
        trails=[], pois=pois,
    )


def _national_park(pid: str, name: str, region: str, agency: str, summary: str, bbox: Tuple[float, float, float, float],
                   base_ele_m: float, max_ele_m: float, emergency: str, pois: List[POI]) -> DestinationPack:
    """A whole-park pack: bbox (min_lat, min_lon, max_lat, max_lon) around the full park boundary; every
    named trail is added from OpenStreetMap by tools/fetch_map_details.py (see data/osm_trails.json)."""
    min_lat, min_lon, max_lat, max_lon = bbox
    return DestinationPack(
        id=pid, name=name, state_or_region=region, agency=agency, summary=summary,
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
        base_ele_m=base_ele_m, max_ele_m=max_ele_m, emergency_contact=emergency,
        trails=[], pois=pois,
    )


def build_great_smoky_mountains() -> DestinationPack:
    pid = "great_smoky_mountains"
    poi = lambda i, n, k, lat, lon, e, note="": POI(id=f"poi_gsm_{i}", pack_id=pid, name=n, kind=k, lat=lat, lon=lon, ele_m=e, note=note)  # noqa: E731
    return _national_park(
        pid, "Great Smoky Mountains National Park",
        "Tennessee & North Carolina (Great Smoky Mountains National Park)",
        "National Park Service, USGS 3DEP & OpenStreetMap Contributors",
        "The whole of America's most-visited national park: misty ridges along the Appalachian Trail from "
        "Fontana Dam to Davenport Gap, Clingmans Dome and Mount Le Conte, old-growth coves, waterfalls, "
        "Cades Cove and Cataloochee.",
        (35.420, -84.030, 35.800, -82.980), 260.0, 2025.0,
        "Great Smoky Mountains NP dispatch: (865) 436-1230 / 911",
        [
            poi("clingmans", "Clingmans Dome", "peak", 35.5628, -83.4985, 2025.0, "Highest point in the park and in Tennessee (6,643 ft); observation tower"),
            poi("leconte", "Mount Le Conte", "peak", 35.6544, -83.4369, 2010.0, "Third-highest peak in the Smokies; LeConte Lodge just below the summit"),
            poi("leconte_lodge", "LeConte Lodge", "hut", 35.6545, -83.4334, 1950.0, "Hike-in lodge (reservations); reached only on foot"),
            poi("newfound_gap", "Newfound Gap", "trailhead", 35.6111, -83.4250, 1538.0, "Appalachian Trail crossing of US 441 on the TN/NC line"),
            poi("charlies_bunion", "Charlies Bunion", "viewpoint", 35.6370, -83.3770, 1680.0, "Rocky outcrop on the Appalachian Trail with sheer drop-offs"),
            poi("alum_cave", "Alum Cave Trailhead", "trailhead", 35.6290, -83.4513, 1170.0, "US 441: Arch Rock, Inspiration Point and Alum Cave Bluffs on the way to Mount Le Conte"),
            poi("sugarlands", "Sugarlands Visitor Center", "ranger", 35.6856, -83.5363, 445.0, "Main Tennessee visitor center near Gatlinburg"),
            poi("oconaluftee", "Oconaluftee Visitor Center", "ranger", 35.5131, -83.3066, 620.0, "North Carolina visitor center near Cherokee"),
            poi("cades_cove", "Cades Cove Visitor Center", "ranger", 35.5875, -83.8448, 535.0, "Historic valley on the 11-mile loop road"),
            poi("abrams_falls", "Abrams Falls", "waterfall", 35.5910, -83.8530, 400.0),
            poi("laurel_falls", "Laurel Falls", "waterfall", 35.6782, -83.5949, 760.0, "Paved, very busy trail from Fighting Creek Gap"),
            poi("rainbow_falls", "Rainbow Falls", "waterfall", 35.6640, -83.4740, 1300.0),
            poi("ramsey_cascades", "Ramsey Cascades", "waterfall", 35.7020, -83.3530, 1330.0, "Tallest waterfall in the park, in old-growth forest"),
            poi("mount_cammerer", "Mount Cammerer Lookout", "viewpoint", 35.7633, -83.1617, 1503.0, "Stone fire lookout at the park's northeast corner"),
            poi("gregory_bald", "Gregory Bald", "viewpoint", 35.5186, -83.8584, 1517.0, "Grassy bald famous for its June flame azaleas"),
            poi("fontana_dam", "Fontana Dam", "trailhead", 35.4510, -83.8050, 520.0, "Southern entry of the Appalachian Trail into the park"),
            poi("cataloochee", "Cataloochee Valley", "trailhead", 35.6290, -83.0770, 800.0, "Remote historic valley; elk often in the fields at dawn and dusk"),
        ],
    )


def build_grand_canyon() -> DestinationPack:
    pid = "grand_canyon"
    poi = lambda i, n, k, lat, lon, e, note="": POI(id=f"poi_gc_{i}", pack_id=pid, name=n, kind=k, lat=lat, lon=lon, ele_m=e, note=note)  # noqa: E731
    return _national_park(
        pid, "Grand Canyon National Park",
        "Arizona (Grand Canyon National Park — South Rim, North Rim, Inner Canyon & Marble Canyon)",
        "National Park Service, USGS 3DEP & OpenStreetMap Contributors",
        "The whole canyon from Lees Ferry and Marble Canyon to the Grand Wash Cliffs: South and North Rim, "
        "the Bright Angel, Kaibab and Grandview corridors, Phantom Ranch and the remote inner-canyon routes. "
        "Extreme heat below the rim — a rim-to-river day hike is not recommended.",
        (35.850, -113.970, 36.880, -111.570), 360.0, 2800.0,
        "Grand Canyon NP emergency: 911 (from park phones) / (928) 638-7805",
        [
            poi("gc_village", "Grand Canyon Village", "ranger", 36.0544, -112.1401, 2100.0, "South Rim: lodges, shuttles and the Rim Trail"),
            poi("visitor_center", "Grand Canyon Visitor Center", "ranger", 36.0590, -112.1090, 2130.0, "Main South Rim visitor center near Mather Point"),
            poi("mather_point", "Mather Point", "viewpoint", 36.0617, -112.1078, 2170.0),
            poi("bright_angel_th", "Bright Angel Trailhead", "trailhead", 36.0577, -112.1439, 2090.0, "Rest houses with seasonal water at 1.5 and 3 miles; Havasupai Gardens"),
            poi("south_kaibab_th", "South Kaibab Trailhead", "trailhead", 36.0531, -112.0839, 2200.0, "Steep ridge trail with no water; shuttle access only"),
            poi("ooh_aah", "Ooh Aah Point", "viewpoint", 36.0470, -112.0850, 1980.0),
            poi("havasupai_gardens", "Havasupai Gardens", "camp", 36.0790, -112.1260, 1160.0, "Campground and ranger station on the Bright Angel Trail (formerly Indian Garden)"),
            poi("phantom_ranch", "Phantom Ranch", "hut", 36.1050, -112.0950, 750.0, "Lodge at the canyon bottom (lottery reservations)"),
            poi("bright_angel_cg", "Bright Angel Campground", "camp", 36.1010, -112.0940, 750.0, "Backcountry permit required"),
            poi("plateau_point", "Plateau Point", "viewpoint", 36.0920, -112.1170, 1170.0, "Overlook of the Colorado River from the Tonto Platform"),
            poi("desert_view", "Desert View Watchtower", "viewpoint", 36.0440, -111.8260, 2270.0, "Eastern end of the South Rim"),
            poi("grandview", "Grandview Point", "trailhead", 35.9990, -111.9870, 2256.0, "Grandview Trail to Horseshoe Mesa"),
            poi("hermits_rest", "Hermits Rest", "trailhead", 36.0590, -112.2110, 2030.0, "West end of Hermit Road; Hermit Trail"),
            poi("north_rim_lodge", "Grand Canyon Lodge (North Rim)", "ranger", 36.1980, -112.0520, 2500.0, "North Rim (open mid-May to mid-October)"),
            poi("north_kaibab_th", "North Kaibab Trailhead", "trailhead", 36.2170, -112.0570, 2510.0, "Only maintained trail from the North Rim to the river"),
            poi("roaring_springs", "Roaring Springs", "water", 36.1960, -112.0350, 1570.0),
            poi("cottonwood_cg", "Cottonwood Campground", "camp", 36.1670, -112.0410, 1240.0),
            poi("ribbon_falls", "Ribbon Falls", "waterfall", 36.1480, -112.0450, 1100.0),
            poi("point_imperial", "Point Imperial", "viewpoint", 36.2780, -111.9770, 2683.0, "Highest viewpoint on the North Rim"),
            poi("cape_royal", "Cape Royal", "viewpoint", 36.1180, -111.9480, 2400.0),
            poi("lees_ferry", "Lees Ferry", "trailhead", 36.8650, -111.5870, 950.0, "Start of Colorado River trips; Cathedral Wash and Spencer trails"),
            poi("toroweap", "Toroweap Overlook", "viewpoint", 36.2150, -113.0580, 1400.0, "Remote North Rim overlook 3,000 ft straight above the river; high-clearance road"),
        ],
    )


def build_yellowstone() -> DestinationPack:
    pid = "yellowstone"
    poi = lambda i, n, k, lat, lon, e, note="": POI(id=f"poi_ys_{i}", pack_id=pid, name=n, kind=k, lat=lat, lon=lon, ele_m=e, note=note)  # noqa: E731
    return _national_park(
        pid, "Yellowstone National Park",
        "Wyoming, Montana & Idaho (Yellowstone National Park)",
        "National Park Service, USGS 3DEP & OpenStreetMap Contributors",
        "The whole park: geyser basins and boardwalks, the Grand Canyon of the Yellowstone, Yellowstone Lake, "
        "the Lamar Valley and the Absaroka, Gallatin and Washburn ranges. Stay on boardwalks in thermal areas; "
        "carry bear spray.",
        (44.120, -111.170, 45.120, -109.810), 1600.0, 3462.0,
        "Yellowstone NP emergency: 911 / (307) 344-7381",
        [
            poi("old_faithful", "Old Faithful", "viewpoint", 44.4605, -110.8281, 2245.0, "Geyser erupting about every 60–110 minutes; Upper Geyser Basin boardwalks"),
            poi("old_faithful_vc", "Old Faithful Visitor Education Center", "ranger", 44.4597, -110.8300, 2240.0),
            poi("grand_prismatic", "Grand Prismatic Spring", "viewpoint", 44.5250, -110.8382, 2210.0, "Midway Geyser Basin; overlook via the Fairy Falls trailhead"),
            poi("fairy_falls", "Fairy Falls", "waterfall", 44.5280, -110.8690, 2220.0),
            poi("artist_point", "Artist Point", "viewpoint", 44.7203, -110.4794, 2390.0, "Classic view of the Lower Falls in the Grand Canyon of the Yellowstone"),
            poi("lower_falls", "Lower Falls of the Yellowstone", "waterfall", 44.7178, -110.4964, 2290.0, "308 ft waterfall"),
            poi("canyon_vc", "Canyon Visitor Education Center", "ranger", 44.7350, -110.4890, 2400.0),
            poi("mount_washburn", "Mount Washburn", "peak", 44.7975, -110.4342, 3122.0, "Fire lookout; Dunraven Pass and Chittenden Road trails"),
            poi("mammoth", "Mammoth Hot Springs", "viewpoint", 44.9690, -110.7040, 1900.0, "Travertine terraces and park headquarters"),
            poi("albright_vc", "Albright Visitor Center", "ranger", 44.9765, -110.6990, 1890.0),
            poi("norris", "Norris Geyser Basin", "viewpoint", 44.7270, -110.7030, 2290.0, "Hottest geyser basin; Steamboat Geyser"),
            poi("lamar_valley", "Lamar Valley", "viewpoint", 44.8980, -110.2250, 2000.0, "Wolf and bison watching at dawn and dusk"),
            poi("west_thumb", "West Thumb Geyser Basin", "viewpoint", 44.4170, -110.5730, 2365.0, "Hot springs on the shore of Yellowstone Lake"),
            poi("lake_village", "Lake Village", "ranger", 44.5490, -110.4010, 2360.0),
            poi("eagle_peak", "Eagle Peak", "peak", 44.2364, -109.9150, 3462.0, "Highest point in the park, in the remote Absaroka Range"),
            poi("electric_peak", "Electric Peak", "peak", 45.0058, -110.8358, 3343.0, "Highest peak in the Gallatin Range inside the park"),
            poi("bunsen_peak", "Bunsen Peak", "peak", 44.9289, -110.7180, 2610.0),
            poi("lone_star", "Lone Star Geyser", "viewpoint", 44.4180, -110.8050, 2310.0, "Cone geyser at the end of a flat old road / bike path"),
        ],
    )


def build_grand_teton() -> DestinationPack:
    pid = "grand_teton"
    poi = lambda i, n, k, lat, lon, e, note="": POI(id=f"poi_gt_{i}", pack_id=pid, name=n, kind=k, lat=lat, lon=lon, ele_m=e, note=note)  # noqa: E731
    return _national_park(
        pid, "Grand Teton National Park",
        "Wyoming (Grand Teton National Park & John D. Rockefeller, Jr. Memorial Parkway)",
        "National Park Service, USGS 3DEP & OpenStreetMap Contributors",
        "The whole park and the Rockefeller Parkway to Yellowstone: the Teton Range above Jenny, Jackson and "
        "the other glacial lakes, Cascade and Paintbrush canyons, the Teton Crest, and the sagebrush flats of "
        "Jackson Hole. Carry bear spray.",
        (43.580, -110.960, 44.160, -110.390), 1900.0, 4199.0,
        "Grand Teton NP emergency: 911 / (307) 739-3301",
        [
            poi("grand_teton", "Grand Teton", "peak", 43.7412, -110.8024, 4199.0, "Technical climb only; no hiking route to the summit"),
            poi("jenny_lake_vc", "Jenny Lake Visitor Center", "ranger", 43.7520, -110.7220, 2070.0, "Shuttle boat across the lake to Hidden Falls and Inspiration Point"),
            poi("hidden_falls", "Hidden Falls", "waterfall", 43.7610, -110.7400, 2150.0),
            poi("inspiration_point", "Inspiration Point", "viewpoint", 43.7630, -110.7430, 2200.0),
            poi("lake_solitude", "Lake Solitude", "water", 43.7930, -110.8490, 2750.0, "Head of Cascade Canyon's north fork"),
            poi("amphitheater_lake", "Amphitheater Lake", "water", 43.7290, -110.7780, 2930.0),
            poi("lupine_meadows", "Lupine Meadows Trailhead", "trailhead", 43.7340, -110.7420, 2090.0, "Garnet Canyon, Surprise and Amphitheater lakes"),
            poi("craig_thomas", "Craig Thomas Discovery & Visitor Center", "ranger", 43.6560, -110.7160, 1990.0, "Moose; main park visitor center"),
            poi("taggart_lake_th", "Taggart Lake Trailhead", "trailhead", 43.6930, -110.7330, 2030.0),
            poi("death_canyon_th", "Death Canyon Trailhead", "trailhead", 43.6560, -110.7820, 2110.0, "Phelps Lake overlook and Death Canyon"),
            poi("phelps_lake", "Phelps Lake", "water", 43.6450, -110.7980, 2015.0, "Laurance S. Rockefeller Preserve"),
            poi("lsr_preserve", "Laurance S. Rockefeller Preserve Center", "ranger", 43.6260, -110.7720, 2020.0),
            poi("string_lake", "String Lake Trailhead", "trailhead", 43.7860, -110.7290, 2090.0, "Paintbrush Canyon and Holly Lake"),
            poi("colter_bay", "Colter Bay Visitor Center", "ranger", 43.9030, -110.6420, 2070.0),
            poi("signal_mtn", "Signal Mountain Summit", "viewpoint", 43.8430, -110.5650, 2350.0),
            poi("teton_village", "Teton Village (Aerial Tram)", "trailhead", 43.5880, -110.8280, 1930.0, "Tram to Rendezvous Mountain for the Teton Crest Trail"),
            poi("flagg_ranch", "Headwaters Lodge at Flagg Ranch", "ranger", 44.1050, -110.6680, 2060.0, "Rockefeller Parkway, near Yellowstone's south entrance"),
        ],
    )


def build_joshua_tree() -> DestinationPack:
    pid = "joshua_tree"
    poi = lambda i, n, k, lat, lon, e, note="": POI(id=f"poi_jt_{i}", pack_id=pid, name=n, kind=k, lat=lat, lon=lon, ele_m=e, note=note)  # noqa: E731
    return _national_park(
        pid, "Joshua Tree National Park",
        "California (Joshua Tree National Park — Mojave & Colorado deserts)",
        "National Park Service, USGS 3DEP & OpenStreetMap Contributors",
        "The whole park where the Mojave meets the Colorado Desert: Joshua tree forests, granite boulder "
        "piles, fan-palm oases, the Little San Bernardino Mountains and the wide Pinto Basin. No water in "
        "the park's backcountry — carry at least a gallon per person per day.",
        (33.660, -116.480, 34.140, -115.240), 300.0, 1773.0,
        "Joshua Tree NP emergency: 911 / (909) 383-5651",
        [
            poi("ryan_mtn", "Ryan Mountain", "peak", 33.9890, -116.1370, 1653.0, "Steep 1.5-mile climb with views over the park's valleys"),
            poi("quail_mtn", "Quail Mountain", "peak", 34.0230, -116.2560, 1773.0, "Highest point in the park; cross-country route"),
            poi("keys_view", "Keys View", "viewpoint", 33.9270, -116.1870, 1580.0, "View over the Coachella Valley and San Andreas Fault"),
            poi("hidden_valley", "Hidden Valley", "trailhead", 34.0120, -116.1680, 1260.0, "Loop through a boulder-ringed valley"),
            poi("barker_dam", "Barker Dam", "water", 34.0240, -116.1430, 1290.0),
            poi("skull_rock", "Skull Rock", "viewpoint", 33.9980, -116.0610, 1310.0),
            poi("arch_rock", "Arch Rock", "viewpoint", 33.9840, -116.0170, 1180.0, "White Tank campground"),
            poi("cholla_garden", "Cholla Cactus Garden", "viewpoint", 33.9250, -115.9290, 690.0, "Stay on the path — the cholla spines detach at a touch"),
            poi("joshua_tree_vc", "Joshua Tree Visitor Center", "ranger", 34.1350, -116.3130, 830.0, "West entrance, town of Joshua Tree"),
            poi("oasis_vc", "Oasis Visitor Center", "ranger", 34.1220, -116.0400, 600.0, "Oasis of Mara, Twentynine Palms"),
            poi("cottonwood_vc", "Cottonwood Visitor Center", "ranger", 33.7470, -115.8230, 920.0, "South entrance; water available"),
            poi("lost_palms", "Lost Palms Oasis", "water", 33.7300, -115.7880, 800.0, "Largest fan-palm oasis in the park"),
            poi("forty_nine_palms", "Fortynine Palms Oasis", "water", 34.1020, -116.1040, 860.0),
            poi("lost_horse_mine", "Lost Horse Mine", "viewpoint", 33.9520, -116.1640, 1550.0),
            poi("black_rock", "Black Rock Canyon Visitor Center", "ranger", 34.0730, -116.3900, 1220.0, "Northwest corner near Yucca Valley"),
            poi("indian_cove", "Indian Cove Campground", "camp", 34.0940, -116.1560, 970.0),
        ],
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
        build_fitchburg_state_forest(),
        build_acton_boxborough_harvard(),
        build_pioneer_valley(),
        build_great_smoky_mountains(),
        build_grand_canyon(),
        build_yellowstone(),
        build_grand_teton(),
        build_joshua_tree(),
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

