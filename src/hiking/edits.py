"""Non-mutating route and gear edits that the AI chat agent can propose.

The agent never mutates `AppState` directly: `apply_route_edits` applies edits to a deep copy
and returns `{summary, changes, before, after, updated_route}` for `ProposalDialog`.
"""
from typing import Any, Dict, List

from .gear import generate_gear_checklist
from .models import ComposedRoute, DestinationPack, GearItem, RouteWaypoint
from .pace import estimate_hike_pace
from .routing import recompute_composed_route, route_from_trail_segment
from .units import format_distance, format_duration, format_elevation


def _route_metrics_dict(route: ComposedRoute, units: str = "us") -> Dict[str, str]:
    est = estimate_hike_pace(
        route.coords_3d,
        route.distance_m,
        route.elevation_gain_m,
        max_ele_m=route.max_ele_m,
    )
    return {
        "Route": route.name,
        "Distance": format_distance(route.distance_m, units),
        "Elevation Gain": f"+{format_elevation(route.elevation_gain_m, units)}",
        "Max Altitude": format_elevation(route.max_ele_m, units),
        "Max Grade": f"{route.max_grade_pct:.1f}%",
        "Est. Duration": format_duration(est.recommended_seconds),
        "Waypoints": str(len(route.waypoints)),
    }


def apply_route_edits(
    route: ComposedRoute,
    pack: DestinationPack,
    edits: List[Dict[str, Any]],
    summary: str = "Proposed route update",
    units: str = "us",
) -> Dict[str, Any]:
    """Apply a list of edit dicts to a deep copy of `route` and compute before/after metrics."""
    before = _route_metrics_dict(route, units)
    candidate = route.model_copy(deep=True)
    changes_desc: List[str] = []
    trails_by_id = {t.id: t for t in pack.trails}
    trails_by_name = {t.name.lower(): t for t in pack.trails}

    for edit in edits:
        action = str(edit.get("action") or "").strip().lower()
        if action == "use_trail":
            tid = str(edit.get("trail_id") or "").strip()
            seg = trails_by_id.get(tid) or trails_by_name.get(tid.lower())
            if seg:
                oab = bool(edit.get("out_and_back", False))
                candidate = route_from_trail_segment(seg, make_out_and_back=oab)
                changes_desc.append(f"Switch route to '{seg.name}'" + (" (Out & Back)" if oab else ""))
        elif action == "append_segment":
            tid = str(edit.get("trail_id") or "").strip()
            seg = trails_by_id.get(tid) or trails_by_name.get(tid.lower())
            if seg and seg.coords_3d:
                end_pt = seg.coords_3d[-1]
                candidate.waypoints.append(
                    RouteWaypoint(
                        name=f"{seg.name} End",
                        lat=end_pt[1],
                        lon=end_pt[0],
                        ele_m=end_pt[2] if len(end_pt) > 2 else 0.0,
                        kind="waypoint",
                        snapped_trail_id=seg.id,
                    )
                )
                if seg.id not in candidate.trail_ids:
                    candidate.trail_ids.append(seg.id)
                changes_desc.append(f"Append trail segment '{seg.name}'")
        elif action == "make_out_and_back":
            if len(candidate.waypoints) >= 2:
                start_wp = candidate.waypoints[0].model_copy(deep=True)
                start_wp.name = f"Return to {start_wp.name}"
                start_wp.kind = "end"
                candidate.waypoints.append(start_wp)
                candidate.trail_type = "out_and_back"
                if "(Out & Back)" not in candidate.name:
                    candidate.name += " (Out & Back)"
                changes_desc.append("Make route Out & Back returning to start trailhead")
        elif action == "reverse_route":
            candidate.waypoints.reverse()
            candidate.coords_3d.reverse()
            changes_desc.append("Reverse route direction")
        elif action == "remove_last_waypoint":
            if len(candidate.waypoints) > 1:
                removed = candidate.waypoints.pop()
                changes_desc.append(f"Remove waypoint '{removed.name}'")
        elif action == "rename_route":
            new_name = str(edit.get("name") or "").strip()
            if new_name:
                candidate.name = new_name
                changes_desc.append(f"Rename route to '{new_name}'")
        elif action == "add_gear_item":
            gname = str(edit.get("name") or "").strip()
            gcat = str(edit.get("category") or "Extra Gear").strip()
            if gname:
                candidate.gear_checklist.append(
                    GearItem(
                        id=f"custom_{len(candidate.gear_checklist) + 1}",
                        category=gcat,
                        name=gname,
                        weight_g=int(edit.get("weight_g") or 150),
                        essential=False,
                        checked=False,
                        reason=str(edit.get("reason") or "Added by Trail Assistant"),
                    )
                )
                changes_desc.append(f"Add gear item: {gname}")

    candidate = recompute_composed_route(candidate, pack.trails, dem=pack.dem)
    if not candidate.gear_checklist:
        candidate.gear_checklist = generate_gear_checklist(candidate)
    after = _route_metrics_dict(candidate, units)

    return {
        "summary": summary,
        "changes": changes_desc or [summary],
        "before": before,
        "after": after,
        "updated_route": candidate,
    }


def close_loop_route(route: ComposedRoute, trails: List) -> ComposedRoute:
    candidate = route.model_copy(deep=True)
    if len(candidate.waypoints) >= 2:
        start_wp = candidate.waypoints[0].model_copy(deep=True)
        start_wp.name = f"Return to {start_wp.name}"
        start_wp.kind = "end"
        candidate.waypoints.append(start_wp)
    elif len(candidate.coords_3d) >= 2:
        candidate.coords_3d.append(list(candidate.coords_3d[0]))
    candidate.trail_type = "loop"
    return recompute_composed_route(candidate, trails)


def reverse_route(route: ComposedRoute) -> ComposedRoute:
    candidate = route.model_copy(deep=True)
    candidate.waypoints.reverse()
    candidate.coords_3d.reverse()
    gain, loss = candidate.elevation_gain_m, candidate.elevation_loss_m
    candidate.elevation_gain_m = loss
    candidate.elevation_loss_m = gain
    return candidate

