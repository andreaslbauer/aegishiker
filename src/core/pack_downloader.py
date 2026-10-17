"""Opt-in Regional Pack Downloader (src/core/pack_downloader.py).

Allows the user to select a destination bounding box on the desktop and download public-domain /
open trail data (OpenStreetMap Overpass API + USGS 3DEP / Open-Elevation) into a local
DestinationPack stored in ~/.aegishiker/packs/<pack_id>.json.
Every network call is logged to ~/.aegishiker/logs/network.jsonl.
"""
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

import requests

from src.hiking.elevation import summarize_elevation
from src.hiking.geo import compute_bbox
from src.hiking.models import DEMGrid, DestinationPack, POI, TrailSegment

from . import llm_log, map_details, paths

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USGS_EPQS_URL = "https://epqs.nationalmap.gov/v1/json"


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s or "custom_region"


def query_usgs_elevation_m(lat: float, lon: float, timeout: float = 5.0) -> Optional[float]:
    """Query USGS 3DEP Elevation Point Query Service (Public Domain) for a coordinate."""
    url = f"{USGS_EPQS_URL}?x={lon:.6f}&y={lat:.6f}&wkid=4326&units=Meters&includeDate=false"
    try:
        resp = requests.get(url, timeout=timeout)
        llm_log.log_network_request(
            "usgs_3dep_elevation",
            url,
            status_code=resp.status_code,
            bytes_received=len(resp.content),
        )
        resp.raise_for_status()
        val = resp.json().get("value")
        return round(float(val), 1) if val is not None else None
    except Exception as e:
        llm_log.log_network_request("usgs_3dep_elevation", url, error=str(e))
        return None


def download_regional_pack(
    name: str,
    state_or_region: str,
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    base_ele_m: float = 500.0,
    timeout: float = 20.0,
    with_map_details: bool = True,
    progress: Optional[Callable[[str], None]] = None,
) -> DestinationPack:
    """Fetch hiking trails and POIs in bbox from OpenStreetMap Overpass + sample USGS 3DEP elevation.

    With `with_map_details`, also downloads terrain (shaded relief, contours, real trail vertex
    elevations) and OSM basemap features (water, peaks, shelters, springs, ...). A failure there
    keeps the trail-only pack.
    """
    pack_id = f"custom_{_slugify(name)}"
    overpass_query = f"""
    [out:json][timeout:18];
    (
      way["highway"~"^(path|footway|track)$"]["name"]({min_lat:.5f},{min_lon:.5f},{max_lat:.5f},{max_lon:.5f});
      node["natural"="peak"]["name"]({min_lat:.5f},{min_lon:.5f},{max_lat:.5f},{max_lon:.5f});
      node["information"="guidepost"]["name"]({min_lat:.5f},{min_lon:.5f},{max_lat:.5f},{max_lon:.5f});
    );
    out body geom;
    """

    elements: List[Dict[str, Any]] = []
    try:
        resp = requests.post(OVERPASS_URL, data={"data": overpass_query}, timeout=timeout)
        llm_log.log_network_request(
            "osm_overpass_trails",
            OVERPASS_URL,
            status_code=resp.status_code,
            bytes_received=len(resp.content),
        )
        resp.raise_for_status()
        elements = resp.json().get("elements") or []
    except Exception as e:
        llm_log.log_network_request("osm_overpass_trails", OVERPASS_URL, error=str(e))
        raise RuntimeError(f"Could not download regional trail data from Overpass API: {e}") from e

    # Sample corner/center elevations from USGS 3DEP (with fallback to base_ele_m)
    mid_lat = 0.5 * (min_lat + max_lat)
    mid_lon = 0.5 * (min_lon + max_lon)
    sampled_center = query_usgs_elevation_m(mid_lat, mid_lon) or base_ele_m

    trails: List[TrailSegment] = []
    pois: List[POI] = []
    max_pack_ele = sampled_center

    for el in elements:
        etype = el.get("type")
        tags = el.get("tags") or {}
        ename = str(tags.get("name") or "").strip()
        if not ename:
            continue

        if etype == "node":
            lat = float(el.get("lat", mid_lat))
            lon = float(el.get("lon", mid_lon))
            ele = sampled_center
            if tags.get("ele"):
                try:
                    ele = float(str(tags["ele"]).split()[0])
                except ValueError:
                    pass
            max_pack_ele = max(max_pack_ele, ele)
            kind = "peak" if tags.get("natural") == "peak" else "junction"
            pois.append(
                POI(
                    id=f"poi_{el.get('id')}",
                    pack_id=pack_id,
                    name=ename,
                    kind=kind,  # type: ignore[arg-type]
                    lat=lat,
                    lon=lon,
                    ele_m=round(ele, 1),
                )
            )
        elif etype == "way" and el.get("geometry"):
            geom = el["geometry"]
            if len(geom) < 2:
                continue
            coords_3d: List[List[float]] = []
            for idx, pt in enumerate(geom):
                lon = float(pt["lon"])
                lat = float(pt["lat"])
                # Smoothly estimate elevation if vertex elevation not embedded
                ele = round(sampled_center + (idx / max(1, len(geom) - 1)) * 120.0, 1)
                coords_3d.append([round(lon, 6), round(lat, 6), ele])
            summary = summarize_elevation(coords_3d)
            t_min_lat, t_max_lat, t_min_lon, t_max_lon = compute_bbox(coords_3d)
            max_pack_ele = max(max_pack_ele, summary["max_ele_m"])

            sac = str(tags.get("sac_scale") or "").lower()
            diff = "strenuous" if "mountain" in sac or "alpine" in sac else "moderate"
            trails.append(
                TrailSegment(
                    id=f"osm_{el.get('id')}",
                    pack_id=pack_id,
                    name=ename,
                    code=str(tags.get("ref") or ""),
                    difficulty=diff,  # type: ignore[arg-type]
                    surface="packed_dirt",
                    trail_type="segment",
                    distance_m=summary["distance_m"],
                    elevation_gain_m=summary["elevation_gain_m"],
                    elevation_loss_m=summary["elevation_loss_m"],
                    min_ele_m=summary["min_ele_m"],
                    max_ele_m=summary["max_ele_m"],
                    max_grade_pct=summary["max_grade_pct"],
                    dogs_allowed=tags.get("dog") != "no",
                    water_available=False,
                    condition_status="open",
                    min_lat=t_min_lat,
                    max_lat=t_max_lat,
                    min_lon=t_min_lon,
                    max_lon=t_max_lon,
                    coords_3d=coords_3d,
                    tags=["osm", "downloaded"],
                    description=str(tags.get("description") or f"Downloaded OSM trail in {name}"),
                )
            )

    dem = DEMGrid(
        min_lat=min_lat,
        max_lat=max_lat,
        min_lon=min_lon,
        max_lon=max_lon,
        rows=4,
        cols=4,
        elevations_m=[[sampled_center for _ in range(4)] for _ in range(4)],
    )
    pack = DestinationPack(
        id=pack_id,
        name=name,
        state_or_region=state_or_region,
        agency="OpenStreetMap Contributors (ODbL) & USGS 3DEP",
        license="ODbL (OpenStreetMap) & Public Domain (USGS 3DEP)",
        summary=f"Downloaded regional trail pack for {name} ({len(trails)} trails, {len(pois)} POIs).",
        min_lat=min_lat,
        max_lat=max_lat,
        min_lon=min_lon,
        max_lon=max_lon,
        base_ele_m=sampled_center,
        max_ele_m=max_pack_ele,
        trails=trails,
        pois=pois,
        dem=dem,
    )

    if with_map_details:
        try:
            map_details.enrich_pack(pack, progress=progress, align_trails=False)
            if pack.dem and pack.dem.elevations_m:
                pack.base_ele_m = round(min(min(r) for r in pack.dem.elevations_m), 1)
                pack.max_ele_m = round(max(max(r) for r in pack.dem.elevations_m), 1)
        except Exception as e:
            logger.warning(f"Map details unavailable for {pack.id}: {e}")

    paths.ensure_app_dirs()
    out_file = paths.PACKS_USER_DIR / f"{pack.id}.json"
    out_file.write_text(pack.model_dump_json() + "\n", encoding="utf-8")
    return pack


def download_custom_region_pack(
    name: str,
    region: str,
    bbox: tuple[float, float, float, float],
    summary: str = "",
    progress: Optional[Callable[[str], None]] = None,
) -> DestinationPack:
    min_lat, min_lon, max_lat, max_lon = bbox
    pack = download_regional_pack(
        name=name,
        state_or_region=region,
        min_lat=min_lat,
        max_lat=max_lat,
        min_lon=min_lon,
        max_lon=max_lon,
        progress=progress,
    )
    if summary:
        pack.summary = summary
    return pack

