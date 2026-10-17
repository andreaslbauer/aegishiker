"""Tools exposed to the right-hand Trail Chat Agent (src/core/agent_tools.py).

Follows the Aegis agent_tools pattern:
* Read tools return JSON strings computed from the app's database and pure-Python calculators.
* `propose_route_changes` applies edits to a deep copy of the active route and stores the
  before/after diff in `self.pending` for `ProposalDialog` — never mutating `AppState` directly.
"""
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from src.hiking.edits import apply_route_edits
from src.hiking.elevation import build_elevation_profile
from src.hiking.filters import TrailFilterCriteria, filter_trails
from src.hiking.models import ComposedRoute, DestinationPack
from src.hiking.pace import estimate_hike_pace
from src.hiking.units import format_distance, format_duration, format_elevation, format_water

from . import llm_log
from .database import Database
from .voyager_bridge import VoyagerBridge

logger = logging.getLogger(__name__)


class AgentTools:
    def __init__(
        self,
        db: Database,
        get_active_pack: Callable[[], DestinationPack],
        get_active_route: Callable[[], ComposedRoute],
        get_units: Callable[[], str],
    ):
        self.db = db
        self.get_active_pack = get_active_pack
        self.get_active_route = get_active_route
        self.get_units = get_units
        self.pending: Optional[Dict[str, Any]] = None

    @property
    def units(self) -> str:
        return self.get_units()

    def schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_destinations",
                    "description": "List all installed regional destination packs and their elevation/trail counts.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_trails",
                    "description": "Search and filter trails in the active regional destination pack.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Optional name/feature keyword"},
                            "max_distance_km": {"type": "number", "description": "Maximum trail distance in km"},
                            "max_gain_m": {"type": "number", "description": "Maximum elevation gain in meters"},
                            "dogs_only": {"type": "boolean", "description": "Only return dog-friendly trails"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_current_route",
                    "description": "Get the currently composed hike route, waypoints, distance, elevation gain, and condition warnings.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_elevation_profile",
                    "description": "Get the elevation profile and steepest grade points along the active route.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "estimate_hike_pace",
                    "description": "Calculate Naismith, Langmuir, and Tobler durations, turnaround time, water, and calories for the active route.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pack_weight_kg": {"type": "number", "description": "Pack weight in kg"},
                            "start_time": {"type": "string", "description": "Start time HH:MM (e.g. 08:00)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_hike_history",
                    "description": "List completed and traced hikes in the user's logbook.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_voyager_photos_for_route",
                    "description": "Search AegisPhotoVoyager's database for geotagged photos taken along the active route corridor.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "corridor_m": {"type": "number", "description": "Search corridor width in meters (default 250)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "propose_route_changes",
                    "description": "Propose non-mutating changes to the active route or gear checklist for the user to accept or reject.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string", "description": "Short explanation of the proposed change"},
                            "edits": {
                                "type": "array",
                                "description": "List of edit objects, e.g. [{'action': 'use_trail', 'trail_id': '...'}, {'action': 'make_out_and_back'}, {'action': 'reverse_route'}, {'action': 'add_gear_item', 'name': '...'}]",
                                "items": {"type": "object"},
                            },
                        },
                        "required": ["summary", "edits"],
                    },
                },
            },
        ]

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        error = None
        result_str = ""
        with llm_log.Timer() as timer:
            try:
                result_str = self._dispatch(name, arguments)
            except Exception as e:
                error = str(e)
                result_str = json.dumps({"error": error})
            finally:
                llm_log.log_tool_call(
                    name,
                    arguments,
                    result=result_str,
                    duration_ms=timer.elapsed_ms,
                    error=error,
                )
        return result_str

    def _dispatch(self, name: str, args: Dict[str, Any]) -> str:
        u = self.units
        pack = self.get_active_pack()
        route = self.get_active_route()

        if name == "list_destinations":
            packs = self.db.list_destination_packs()
            return json.dumps(
                [
                    {
                        "id": p.id,
                        "name": p.name,
                        "region": p.state_or_region,
                        "trail_count": len(p.trails),
                        "max_ele": format_elevation(p.max_ele_m, u),
                    }
                    for p in packs
                ]
            )

        if name == "search_trails":
            max_d = float(args["max_distance_km"]) * 1000.0 if args.get("max_distance_km") else None
            max_g = float(args["max_gain_m"]) if args.get("max_gain_m") else None
            criteria = TrailFilterCriteria(
                query=str(args.get("query") or ""),
                max_distance_m=max_d,
                max_elevation_gain_m=max_g,
                dogs_only=bool(args.get("dogs_only", False)),
            )
            matches = filter_trails(pack.trails, criteria)
            return json.dumps(
                [
                    {
                        "id": t.id,
                        "name": t.name,
                        "difficulty": t.difficulty,
                        "type": t.trail_type,
                        "distance": format_distance(t.distance_m, u),
                        "elevation_gain": format_elevation(t.elevation_gain_m, u),
                        "max_ele": format_elevation(t.max_ele_m, u),
                        "dogs_allowed": t.dogs_allowed,
                        "water_available": t.water_available,
                        "condition": t.condition_status,
                        "condition_note": t.condition_note,
                    }
                    for t in matches
                ]
            )

        if name == "get_current_route":
            return json.dumps(
                {
                    "id": route.id,
                    "name": route.name,
                    "pack_id": route.pack_id,
                    "difficulty": route.difficulty,
                    "trail_type": route.trail_type,
                    "distance": format_distance(route.distance_m, u),
                    "elevation_gain": format_elevation(route.elevation_gain_m, u),
                    "elevation_loss": format_elevation(route.elevation_loss_m, u),
                    "min_ele": format_elevation(route.min_ele_m, u),
                    "max_ele": format_elevation(route.max_ele_m, u),
                    "max_grade_pct": route.max_grade_pct,
                    "waypoints": [w.name for w in route.waypoints],
                    "condition_warnings": route.condition_warnings,
                }
            )

        if name == "get_elevation_profile":
            prof = build_elevation_profile(route.coords_3d, dem=pack.dem)
            step = max(1, len(prof) // 10)
            sampled = [
                {
                    "distance": format_distance(p.distance_m, u),
                    "elevation": format_elevation(p.ele_m, u),
                    "grade_pct": p.grade_pct,
                }
                for p in prof[::step]
            ]
            return json.dumps({"sample_points": sampled, "max_grade_pct": route.max_grade_pct})

        if name == "estimate_hike_pace":
            pw = float(args.get("pack_weight_kg") or 6.0)
            st = str(args.get("start_time") or "08:00")
            est = estimate_hike_pace(
                route.coords_3d,
                route.distance_m,
                route.elevation_gain_m,
                max_ele_m=route.max_ele_m,
                pack_weight_kg=pw,
                start_time_hhmm=st,
            )
            return json.dumps(
                {
                    "naismith": format_duration(est.naismith_seconds),
                    "langmuir": format_duration(est.langmuir_seconds),
                    "tobler": format_duration(est.tobler_seconds),
                    "recommended_duration": format_duration(est.recommended_seconds),
                    "turnaround_time": est.turnaround_time_str,
                    "estimated_finish": est.estimated_finish_str,
                    "water_recommended": format_water(est.water_liters, u),
                    "calories_kcal": round(est.calories_kcal),
                }
            )

        if name == "get_hike_history":
            sessions = self.db.list_sessions()
            return json.dumps(
                [
                    {
                        "id": s.id,
                        "title": s.title,
                        "date": s.started_at[:10],
                        "status": s.status,
                        "distance": format_distance(s.stats.actual_distance_m or s.stats.planned_distance_m, u),
                        "gain": format_elevation(s.stats.actual_gain_m or s.stats.planned_gain_m, u),
                        "photos": s.stats.photo_count,
                    }
                    for s in sessions[:15]
                ]
            )

        if name == "find_voyager_photos_for_route":
            corridor = float(args.get("corridor_m") or 250.0)
            vb = VoyagerBridge()
            pins = vb.find_photos_for_traced_trail(route.coords_3d, corridor_m=corridor)
            return json.dumps(
                {
                    "voyager_available": vb.is_available(),
                    "matched_count": len(pins),
                    "photos": [
                        {
                            "filename": p.filename,
                            "date": p.date_iso,
                            "distance_along": format_distance(p.distance_along_m, u),
                            "elevation": format_elevation(p.ele_m, u),
                            "summary": p.ai_summary,
                            "people": p.people,
                        }
                        for p in pins[:15]
                    ],
                }
            )

        if name == "propose_route_changes":
            summary = str(args.get("summary") or "Proposed route adjustment")
            edits = list(args.get("edits") or [])
            self.pending = apply_route_edits(route, pack, edits, summary=summary, units=u)
            return json.dumps(
                {
                    "status": "proposal_staged_for_user_approval",
                    "summary": summary,
                    "before": self.pending["before"],
                    "after": self.pending["after"],
                    "changes": self.pending["changes"],
                }
            )

        return json.dumps({"error": f"Unknown tool: {name}"})

