"""GeoJSON FeatureCollection import and export for AegisHiker routes and trails."""
import json
from typing import Any, Dict, List

from .elevation import summarize_elevation
from .models import ComposedRoute, RouteWaypoint


def route_to_geojson(route: ComposedRoute) -> str:
    features: List[Dict[str, Any]] = [
        {
            "type": "Feature",
            "properties": {
                "id": route.id,
                "name": route.name,
                "pack_id": route.pack_id,
                "difficulty": route.difficulty,
                "trail_type": route.trail_type,
                "distance_m": route.distance_m,
                "elevation_gain_m": route.elevation_gain_m,
                "elevation_loss_m": route.elevation_loss_m,
                "notes": route.notes,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": route.coords_3d,
            },
        }
    ]
    for wp in route.waypoints:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": wp.name,
                    "kind": wp.kind,
                    "note": wp.note,
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [wp.lon, wp.lat, wp.ele_m],
                },
            }
        )
    collection = {"type": "FeatureCollection", "features": features}
    return json.dumps(collection, indent=2)


def geojson_to_route(geojson_text: str, pack_id: str = "white_mountains_nh") -> ComposedRoute:
    data = json.loads(geojson_text)
    features = data.get("features") if data.get("type") == "FeatureCollection" else [data]
    name = "Imported GeoJSON Route"
    notes = ""
    coords_3d: List[List[float]] = []
    waypoints: List[RouteWaypoint] = []

    for feat in features or []:
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        if gtype == "LineString":
            if props.get("name"):
                name = str(props["name"])
            if props.get("notes"):
                notes = str(props["notes"])
            for c in geom.get("coordinates") or []:
                if len(c) >= 2:
                    coords_3d.append([float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 else 0.0])
        elif gtype == "MultiLineString":
            if props.get("name"):
                name = str(props["name"])
            for line in geom.get("coordinates") or []:
                for c in line:
                    if len(c) >= 2:
                        coords_3d.append([float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 else 0.0])
        elif gtype == "Point":
            c = geom.get("coordinates") or [0.0, 0.0, 0.0]
            if len(c) >= 2:
                waypoints.append(
                    RouteWaypoint(
                        name=str(props.get("name") or "Waypoint"),
                        lon=float(c[0]),
                        lat=float(c[1]),
                        ele_m=float(c[2]) if len(c) > 2 else 0.0,
                        kind=str(props.get("kind") or "waypoint"),
                        note=str(props.get("note") or ""),
                    )
                )

    summary = summarize_elevation(coords_3d)
    if not waypoints and coords_3d:
        p0, plast = coords_3d[0], coords_3d[-1]
        waypoints = [
            RouteWaypoint(name="Start", lat=p0[1], lon=p0[0], ele_m=p0[2], kind="start"),
            RouteWaypoint(name="End", lat=plast[1], lon=plast[0], ele_m=plast[2], kind="end"),
        ]
    return ComposedRoute(
        pack_id=pack_id,
        name=name,
        waypoints=waypoints,
        coords_3d=coords_3d,
        distance_m=summary["distance_m"],
        elevation_gain_m=summary["elevation_gain_m"],
        elevation_loss_m=summary["elevation_loss_m"],
        min_ele_m=summary["min_ele_m"],
        max_ele_m=summary["max_ele_m"],
        max_grade_pct=summary["max_grade_pct"],
        notes=notes,
    )


export_route_to_geojson = route_to_geojson


def import_route_from_geojson(geojson_text: str, destination_id: str = "white_mountains_nh") -> ComposedRoute:
    return geojson_to_route(geojson_text, pack_id=destination_id or "white_mountains_nh")

