"""GPX 1.1 XML parser and exporter for routes, tracks, and waypoints."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET

from .elevation import summarize_elevation
from .models import ComposedRoute, HikeSession, RouteWaypoint, TelemetryPoint
from .telemetry import analyze_telemetry

GPX_NS = "http://www.topografix.com/GPX/1/1"


def _strip_tag(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_gpx(xml_text: str) -> Dict[str, Any]:
    """Parse a GPX 1.1 string into {name, description, waypoints, coords_3d, telemetry_points}."""
    root = ET.fromstring(xml_text.strip())
    name = "Imported GPX Hike"
    desc = ""

    waypoints: List[RouteWaypoint] = []
    coords_3d: List[List[float]] = []
    telemetry: List[TelemetryPoint] = []
    seq = 0

    for child in root:
        tag = _strip_tag(child.tag)
        if tag == "metadata":
            for m in child:
                mtag = _strip_tag(m.tag)
                if mtag == "name" and m.text:
                    name = m.text.strip()
                elif mtag == "desc" and m.text:
                    desc = m.text.strip()
        elif tag == "wpt":
            lat = float(child.attrib.get("lat", 0.0))
            lon = float(child.attrib.get("lon", 0.0))
            ele = 0.0
            wname = "Waypoint"
            wnote = ""
            for w in child:
                wtag = _strip_tag(w.tag)
                if wtag == "ele" and w.text:
                    ele = float(w.text.strip())
                elif wtag == "name" and w.text:
                    wname = w.text.strip()
                elif wtag == "desc" and w.text:
                    wnote = w.text.strip()
            waypoints.append(RouteWaypoint(name=wname, lat=lat, lon=lon, ele_m=ele, note=wnote))
        elif tag == "rte":
            for r in child:
                rtag = _strip_tag(r.tag)
                if rtag == "name" and r.text:
                    name = r.text.strip()
                elif rtag == "rtept":
                    lat = float(r.attrib.get("lat", 0.0))
                    lon = float(r.attrib.get("lon", 0.0))
                    ele = 0.0
                    for sub in r:
                        if _strip_tag(sub.tag) == "ele" and sub.text:
                            ele = float(sub.text.strip())
                    coords_3d.append([lon, lat, ele])
        elif tag == "trk":
            for t in child:
                ttag = _strip_tag(t.tag)
                if ttag == "name" and t.text:
                    name = t.text.strip()
                elif ttag == "trkseg":
                    for pt in t:
                        if _strip_tag(pt.tag) != "trkpt":
                            continue
                        lat = float(pt.attrib.get("lat", 0.0))
                        lon = float(pt.attrib.get("lon", 0.0))
                        ele = 0.0
                        ts = datetime.now(timezone.utc).isoformat()
                        for sub in pt:
                            stag = _strip_tag(sub.tag)
                            if stag == "ele" and sub.text:
                                ele = float(sub.text.strip())
                            elif stag == "time" and sub.text:
                                ts = sub.text.strip()
                        coords_3d.append([lon, lat, ele])
                        telemetry.append(
                            TelemetryPoint(
                                seq=seq,
                                timestamp=ts,
                                lat=lat,
                                lon=lon,
                                gps_ele_m=ele,
                            )
                        )
                        seq += 1

    return {
        "name": name,
        "description": desc,
        "waypoints": waypoints,
        "coords_3d": coords_3d,
        "telemetry_points": telemetry,
    }


def gpx_to_route(xml_text: str, pack_id: str = "white_mountains_nh") -> ComposedRoute:
    parsed = parse_gpx(xml_text)
    coords = parsed["coords_3d"]
    summary = summarize_elevation(coords)
    wps = parsed["waypoints"]
    if not wps and coords:
        p0 = coords[0]
        plast = coords[-1]
        wps = [
            RouteWaypoint(name="Start", lat=p0[1], lon=p0[0], ele_m=p0[2] if len(p0) > 2 else 0.0, kind="start"),
            RouteWaypoint(name="End", lat=plast[1], lon=plast[0], ele_m=plast[2] if len(plast) > 2 else 0.0, kind="end"),
        ]
    return ComposedRoute(
        pack_id=pack_id,
        name=parsed["name"],
        waypoints=wps,
        coords_3d=coords,
        distance_m=summary["distance_m"],
        elevation_gain_m=summary["elevation_gain_m"],
        elevation_loss_m=summary["elevation_loss_m"],
        min_ele_m=summary["min_ele_m"],
        max_ele_m=summary["max_ele_m"],
        max_grade_pct=summary["max_grade_pct"],
        notes=parsed["description"],
    )


def gpx_to_session(
    xml_text: str,
    pack_id: Optional[str] = None,
    units: str = "us",
) -> tuple[HikeSession, List[TelemetryPoint]]:
    parsed = parse_gpx(xml_text)
    pts = parsed["telemetry_points"]
    coords = parsed["coords_3d"]
    stats = analyze_telemetry(pts, planned_coords_3d=coords, units=units)
    start_ts = pts[0].timestamp if pts else datetime.now(timezone.utc).isoformat()
    end_ts = pts[-1].timestamp if pts else start_ts
    session = HikeSession(
        pack_id=pack_id,
        title=parsed["name"],
        started_at=start_ts,
        ended_at=end_ts,
        status="imported",
        device_source="gpx_import",
        stats=stats,
        route_snapshot={"name": parsed["name"], "coords_3d": coords},
        notes=parsed["description"],
    )
    return (session, pts)


def route_to_gpx(route: ComposedRoute) -> str:
    """Serialize a ComposedRoute into a standard GPX 1.1 XML string."""
    ET.register_namespace("", GPX_NS)
    gpx = ET.Element(
        f"{{{GPX_NS}}}gpx",
        attrib={"version": "1.1", "creator": "AegisHiker - https://yourprivacysw.com"},
    )
    meta = ET.SubElement(gpx, f"{{{GPX_NS}}}metadata")
    mname = ET.SubElement(meta, f"{{{GPX_NS}}}name")
    mname.text = route.name
    if route.notes:
        mdesc = ET.SubElement(meta, f"{{{GPX_NS}}}desc")
        mdesc.text = route.notes

    for wp in route.waypoints:
        wpt = ET.SubElement(
            gpx,
            f"{{{GPX_NS}}}wpt",
            attrib={"lat": f"{wp.lat:.6f}", "lon": f"{wp.lon:.6f}"},
        )
        ele = ET.SubElement(wpt, f"{{{GPX_NS}}}ele")
        ele.text = f"{wp.ele_m:.1f}"
        wname = ET.SubElement(wpt, f"{{{GPX_NS}}}name")
        wname.text = wp.name
        if wp.note:
            wdesc = ET.SubElement(wpt, f"{{{GPX_NS}}}desc")
            wdesc.text = wp.note

    trk = ET.SubElement(gpx, f"{{{GPX_NS}}}trk")
    tname = ET.SubElement(trk, f"{{{GPX_NS}}}name")
    tname.text = route.name
    trkseg = ET.SubElement(trk, f"{{{GPX_NS}}}trkseg")
    for pt in route.coords_3d:
        lon, lat = pt[0], pt[1]
        ele_val = pt[2] if len(pt) > 2 else 0.0
        trkpt = ET.SubElement(
            trkseg,
            f"{{{GPX_NS}}}trkpt",
            attrib={"lat": f"{lat:.6f}", "lon": f"{lon:.6f}"},
        )
        ele = ET.SubElement(trkpt, f"{{{GPX_NS}}}ele")
        ele.text = f"{ele_val:.1f}"

    xml_bytes = ET.tostring(gpx, encoding="utf-8", xml_declaration=True)
    return xml_bytes.decode("utf-8")


def session_to_gpx(session: HikeSession, points: List[TelemetryPoint]) -> str:
    """Serialize a recorded HikeSession and its TelemetryPoints to GPX 1.1 XML."""
    ET.register_namespace("", GPX_NS)
    gpx = ET.Element(
        f"{{{GPX_NS}}}gpx",
        attrib={"version": "1.1", "creator": "AegisHiker - https://yourprivacysw.com"},
    )
    meta = ET.SubElement(gpx, f"{{{GPX_NS}}}metadata")
    mname = ET.SubElement(meta, f"{{{GPX_NS}}}name")
    mname.text = session.title

    trk = ET.SubElement(gpx, f"{{{GPX_NS}}}trk")
    tname = ET.SubElement(trk, f"{{{GPX_NS}}}name")
    tname.text = session.title
    trkseg = ET.SubElement(trk, f"{{{GPX_NS}}}trkseg")

    if points:
        for pt in points:
            trkpt = ET.SubElement(
                trkseg,
                f"{{{GPX_NS}}}trkpt",
                attrib={"lat": f"{pt.lat:.6f}", "lon": f"{pt.lon:.6f}"},
            )
            ele = ET.SubElement(trkpt, f"{{{GPX_NS}}}ele")
            ele.text = f"{(pt.baro_ele_m if pt.baro_ele_m is not None else pt.gps_ele_m):.1f}"
            tm = ET.SubElement(trkpt, f"{{{GPX_NS}}}time")
            tm.text = pt.timestamp
    elif session.route_snapshot and session.route_snapshot.get("coords_3d"):
        for pt in session.route_snapshot["coords_3d"]:
            trkpt = ET.SubElement(
                trkseg,
                f"{{{GPX_NS}}}trkpt",
                attrib={"lat": f"{pt[1]:.6f}", "lon": f"{pt[0]:.6f}"},
            )
            ele = ET.SubElement(trkpt, f"{{{GPX_NS}}}ele")
            ele.text = f"{(pt[2] if len(pt) > 2 else 0.0):.1f}"

    return ET.tostring(gpx, encoding="utf-8", xml_declaration=True).decode("utf-8")


export_route_to_gpx = route_to_gpx


def import_route_from_gpx(xml_text: str, destination_id: str = "white_mountains_nh") -> ComposedRoute:
    return gpx_to_route(xml_text, pack_id=destination_id or "white_mountains_nh")

