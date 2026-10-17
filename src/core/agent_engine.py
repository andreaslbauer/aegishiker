"""The Trail Chat Agent in the right-hand dock (src/core/agent_engine.py).

An agentic tool-calling loop over `AgentTools` (max 6 iterations), aware of which page
the user is viewing, grounded in the app's own trail/elevation/pace numbers, and staging
route or gear modifications as non-mutating proposals for user approval.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from .about import SAFETY
from .llm_service import parse_arguments

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 6
CONDENSE_AFTER = 24
KEEP_RECENT = 10

SYSTEM_PROMPT = """\
You are Aegis, the user's private backcountry hiking and trail advisor, running locally on their computer.

How to work:
- Always call the tools (`get_current_route`, `search_trails`, `get_elevation_profile`,
  `estimate_hike_pace`, `get_hike_history`, `find_voyager_photos_for_route`) before quoting
  distances, elevations, grades, durations, or trail conditions. Never invent trail numbers.
- For any change to the route or gear list, call `propose_route_changes`. The app will show
  the user a before/after diff to accept or reject. Never claim a change has already been applied.
- Safety: {safety}

How to answer: plain English, concise (a few sentences or a tight bullet list). Use the user's
units ({units}) when mentioning distances and elevations.
The user is currently on the {page} page.
"""


class AgentEngine:
    def __init__(
        self,
        llm_or_db: Any,
        tools_or_llm: Optional[Any] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ):
        if hasattr(llm_or_db, "list_destination_packs"):
            self.db = llm_or_db
            self.llm = tools_or_llm
            self.tools = None
        else:
            self.db = getattr(tools_or_llm, "db", None)
            self.llm = llm_or_db
            self.tools = tools_or_llm
        self.history: List[Dict[str, Any]] = list(history or [])
        self.memory_note = ""
        self.page = "Explore Region"

    def _messages(self) -> List[Dict[str, Any]]:
        units = "US (miles, feet, lb, °F)" if self.tools.units == "us" else "Metric (km, meters, kg, °C)"
        system = SYSTEM_PROMPT.format(page=self.page, safety=SAFETY, units=units)
        if self.memory_note:
            system += f"\nEarlier in this conversation: {self.memory_note}"
        return [{"role": "system", "content": system}] + self.history

    def _condense(self) -> None:
        if len(self.history) <= CONDENSE_AFTER:
            return
        old, recent = self.history[:-KEEP_RECENT], self.history[-KEEP_RECENT:]
        transcript = "\n".join(
            f"{m['role']}: {m.get('content') or ''}"
            for m in old
            if m.get("role") in ("user", "assistant") and m.get("content")
        )
        try:
            resp = self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": "Summarize this hiking conversation in at most 5 short sentences: chosen region, routes, and open questions.",
                    },
                    {"role": "user", "content": (self.memory_note + "\n" + transcript)[-8000:]},
                ],
                temperature=0.2,
                kind="chat_memory",
            )
            self.memory_note = ((resp.get("message") or {}).get("content") or "").strip()
        except Exception as e:
            logger.warning(f"Could not condense chat memory: {e}")
        self.history = recent

    def chat(self, user_text: str) -> str:
        self.tools.pending = None
        self.history.append({"role": "user", "content": user_text})
        content = ""
        nudged = False
        try:
            for _ in range(MAX_TOOL_ITERATIONS):
                resp = self.llm.chat(
                    self._messages(),
                    tools=self.tools.schemas(),
                    kind="agent_chat",
                )
                message = resp.get("message") or {}
                content = message.get("content") or ""
                calls = message.get("tool_calls") or []
                self.history.append(
                    {
                        k: v
                        for k, v in message.items()
                        if k in ("role", "content", "tool_calls") and v is not None
                    }
                )
                if not calls:
                    if not content.strip() and not nudged:
                        nudged = True
                        self.history.pop()
                        self.history.append(
                            {
                                "role": "system",
                                "content": "Your last reply was empty. Call one tool (`propose_route_changes` for edits) or answer in plain text.",
                            }
                        )
                        continue
                    break
                for call in calls:
                    fn = call.get("function") or {}
                    name = fn.get("name", "")
                    result = self.tools.execute(name, parse_arguments(fn.get("arguments")))
                    self.history.append({"role": "tool", "name": name, "content": result})
            else:
                content = content or "I ran out of tool steps before finishing — could you narrow the question?"
        except Exception as e:
            logger.error(f"Agent chat failed: {e}", exc_info=True)
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            return (
                f"I couldn't reach the local Ollama model right now.\n\n`{e}`\n\n"
                "Check that Ollama is running and the model in Settings is pulled."
            )
        content = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", content).strip() or "I don't have an answer for that."
        self._condense()
        return content

    def process_user_prompt(
        self,
        user_prompt: str,
        active_route_id: Optional[str] = None,
        active_destination_id: Optional[str] = None,
        use_llm: bool = False,
    ) -> Dict[str, Any]:
        from src.hiking.edits import close_loop_route, reverse_route
        from src.hiking.gear import estimate_calories_kcal, estimate_hydration_ml
        from src.hiking.models import ProposalEnvelope, ProposalOperation

        route = self.db.get_route(active_route_id) if (self.db and active_route_id) else None
        if not route and self.db:
            routes = self.db.list_routes(active_destination_id)
            route = routes[0] if routes else None

        prompt_l = user_prompt.lower()
        if route and "loop" in prompt_l:
            trails = self.db.list_trails(route.pack_id) if self.db else []
            proposed = close_loop_route(route, trails)
            env = ProposalEnvelope(
                summary=f"Close '{route.name}' into a complete loop",
                rationale="Connects the final waypoint back to the starting trailhead via the regional trail graph.",
                operations=[ProposalOperation(op_type="close_loop", reason="Close loop back to start")],
            )
            return {
                "reply": f"I prepared a proposal to close **{route.name}** into a loop ({proposed.distance_m / 1000.0:.1f} km).",
                "proposal": env,
                "proposed_route": proposed,
            }

        if route and "reverse" in prompt_l:
            proposed = reverse_route(route)
            env = ProposalEnvelope(
                summary=f"Reverse direction of '{route.name}'",
                rationale="Inverts the waypoint order and elevation profile direction.",
                operations=[ProposalOperation(op_type="reverse_route", reason="Reverse direction")],
            )
            return {
                "reply": f"I staged a proposal to reverse **{route.name}**.",
                "proposal": env,
                "proposed_route": proposed,
            }

        if route and ("water" in prompt_l or "hydrat" in prompt_l or "calori" in prompt_l):
            water_ml = estimate_hydration_ml(route.estimated_duration_min, route.elevation_gain_m)
            kcal = estimate_calories_kcal(route.distance_m, route.elevation_gain_m)
            return {
                "reply": (
                    f"For **{route.name}** ({route.distance_m / 1000.0:.1f} km, +{route.elevation_gain_m:.0f} m), "
                    f"carry at least **{water_ml / 1000.0:.1f} L** of water and plan for **~{kcal} kcal**."
                ),
                "proposal": None,
                "proposed_route": None,
            }

        if route:
            return {
                "reply": (
                    f"Active route **{route.name}** covers **{route.distance_m / 1000.0:.1f} km** "
                    f"with **+{route.elevation_gain_m:.0f} m** elevation gain. "
                    "You can ask me to close it into a loop, reverse direction, or estimate water & calories."
                ),
                "proposal": None,
                "proposed_route": None,
            }

        return {
            "reply": "Select a destination pack or route and ask me to close a loop, reverse direction, or estimate water needs.",
            "proposal": None,
            "proposed_route": None,
        }

