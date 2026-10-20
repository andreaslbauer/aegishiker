"""Guided AI Hike Brainstorm engine (src/core/interview_engine.py).

Follows the Aegis interview pattern:
1. Low-temperature structured extraction call updates `HikeBrief` via `HikeBriefPatch`.
2. Conversation call asks only about the single next missing topic (`next_missing_topic`),
   grounded in the available regional packs and trail ranges.
3. Includes deterministic rule-based extraction fallback if local Ollama is not running,
   so the user can always complete the brainstorm and get ranked trail recommendations.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.hiking.interview import (
    HikeBriefPatch,
    BriefPatchItem,
    apply_patch,
    is_brief_ready,
    next_missing_topic,
    rank_candidates_for_brief,
)
from src.hiking.models import DestinationPack, HikeBrief

from .about import SAFETY
from .llm_service import LLMService

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM = """\
You extract structured hike planning preferences from the user's message into a HikeBriefPatch JSON object.
Valid pack_id values:
- white_mountains_nh (New Hampshire White Mountains / Franconia Ridge / Mt. Washington)
- yosemite_valley (Yosemite Valley & High Country, CA)
- acadia_np (Acadia National Park, ME)
- shenandoah_central (Shenandoah National Park, VA)
- rocky_mountain_bear_lake (Rocky Mountain National Park, CO)
- zion_canyon (Zion Canyon, UT)
- mount_rainier_paradise (Mount Rainier Paradise, WA)
- pioneer_valley (Pioneer Valley, MA: Mt. Tom, Holyoke Range, Mt. Sugarloaf, Mt. Toby)

Convert distances to kilometers (`max_distance_km`, 1 mile = 1.61 km) and elevation gain to meters
(`max_elevation_gain_m`, 1000 ft = 305 m). Only populate fields the user mentioned or clearly implied.
"""

CONVERSATION_SYSTEM = """\
You are Aegis, a friendly backcountry trail planner running locally on the user's computer.
Keep your reply to 2-3 concise sentences.
Current Hike Brief: {brief_summary}
Next topic to ask about: {next_topic}
Safety reminder: {safety}
"""


def _heuristic_extract(user_text: str, brief: HikeBrief, packs: List[DestinationPack]) -> HikeBriefPatch:
    """Deterministic fallback extractor so the interview progresses even without Ollama."""
    text_l = user_text.lower()
    patch = HikeBriefPatch()

    # 1. Pack matching
    pack_keywords = {
        "white_mountains_nh": ("white mountain", "new hampshire", "franconia", "lafayette", "washington", "presidential", "nh"),
        "yosemite_valley": ("yosemite", "half dome", "vernal", "nevada fall", "mist trail", "glacier point"),
        "acadia_np": ("acadia", "beehive", "cadillac", "jordan pond", "maine"),
        "shenandoah_central": ("shenandoah", "old rag", "hawksbill", "blue ridge", "virginia"),
        "rocky_mountain_bear_lake": ("rocky mountain", "bear lake", "emerald lake", "sky pond", "flattop", "colorado"),
        "zion_canyon": ("zion", "scout lookout", "angels landing", "watchman", "utah"),
        "mount_rainier_paradise": ("rainier", "paradise", "skyline", "nisqually", "washington state"),
        "pioneer_valley": ("pioneer valley", "holyoke", "norwottuck", "mt tom", "mount tom", "sugarloaf", "mount toby",
                           "mt toby", "northampton", "amherst"),
    }
    for pid, kws in pack_keywords.items():
        if any(kw in text_l for kw in kws):
            patch.pack_id = BriefPatchItem(value=pid, source="stated", evidence=user_text[:80])
            break
    for p in packs:
        if p.id.lower() in text_l or p.name.lower() in text_l:
            patch.pack_id = BriefPatchItem(value=p.id, source="stated", evidence=user_text[:80])

    # 2. Fitness / party
    for lvl in ("leisurely", "easy", "moderate", "strenuous", "athletic", "mountaineer", "family", "solo"):
        if lvl in text_l:
            patch.party_and_fitness = BriefPatchItem(value=lvl, source="stated", evidence=user_text[:80])
            break

    # 3. Distance
    m_mi = re.search(r"(\d+(?:\.\d+)?)\s*(?:mi|mile|miles)", text_l)
    m_km = re.search(r"(\d+(?:\.\d+)?)\s*(?:km|kilometer|kilometers)", text_l)
    if m_mi:
        km = round(float(m_mi.group(1)) * 1.60934, 1)
        patch.max_distance_km = BriefPatchItem(value=km, source="stated", evidence=m_mi.group(0))
    elif m_km:
        patch.max_distance_km = BriefPatchItem(
            value=round(float(m_km.group(1)), 1), source="stated", evidence=m_km.group(0)
        )

    # 4. Elevation gain
    m_ft = re.search(r"(\d{3,5})\s*(?:ft|feet|foot)", text_l)
    m_m = re.search(r"(\d{3,4})\s*(?:m|meters|metres)\b", text_l)
    if m_ft:
        gain_m = round(float(m_ft.group(1)) / 3.28084, 0)
        patch.max_elevation_gain_m = BriefPatchItem(value=gain_m, source="stated", evidence=m_ft.group(0))
    elif m_m:
        patch.max_elevation_gain_m = BriefPatchItem(
            value=round(float(m_m.group(1)), 0), source="stated", evidence=m_m.group(0)
        )

    # 5. Scenery / shape
    scenery_hits = [
        w
        for w in ("loop", "out and back", "out-and-back", "waterfall", "peak", "summit", "ridge", "lake", "canyon", "alpine")
        if w in text_l
    ]
    if scenery_hits:
        patch.route_shape_and_scenery = BriefPatchItem(
            value=", ".join(scenery_hits), source="stated", evidence=user_text[:80]
        )

    # 6. Dogs & conditions
    if "dog" in text_l or "dogs" in text_l:
        no_dog = any(neg in text_l for neg in ("no dog", "without dog", "not bringing"))
        patch.dogs_with_party = not no_dog
        patch.conditions_and_dogs = BriefPatchItem(
            value="With dog" if not no_dog else "No dogs",
            source="stated",
            evidence=user_text[:80],
        )
    elif "no snow" in text_l or "dry" in text_l or "open only" in text_l:
        patch.avoid_snow_and_mud = True
        patch.conditions_and_dogs = BriefPatchItem(
            value="Open & dry trails preferred", source="stated", evidence=user_text[:80]
        )

    return patch


class InterviewEngine:
    def __init__(self, db_or_llm: Optional[Any] = None, llm: Optional[LLMService] = None):
        if hasattr(db_or_llm, "list_destination_packs"):
            self.db = db_or_llm
            self.llm = llm
        else:
            self.db = None
            self.llm = db_or_llm

    def run_interview(
        self,
        answers: Dict[str, str],
        destination_id: Optional[str] = None,
        use_llm: bool = False,
    ) -> Dict[str, Any]:
        from src.hiking.models import BriefField
        from src.hiking.routing import route_from_trail_segment

        pack_id = destination_id or "white_mountains_nh"
        brief = HikeBrief(
            pack_id=BriefField(value=pack_id, source="stated"),
            party_and_fitness=BriefField(value=answers.get("party_and_fitness", "moderate"), source="stated"),
            max_distance_km=BriefField(value=answers.get("max_distance_km", "15.0"), source="stated"),
            max_elevation_gain_m=BriefField(value=answers.get("max_elevation_gain_m", "1000"), source="stated"),
            route_shape_and_scenery=BriefField(
                value=answers.get("route_shape_and_scenery", "loop, ridge, summit"), source="stated"
            ),
            conditions_and_dogs=BriefField(
                value=answers.get("conditions_and_dogs", "Open & dry trails preferred"), source="stated"
            ),
            pack_and_start_time=BriefField(value=answers.get("pack_and_start_time", "08:00"), source="stated"),
            dogs_with_party="dog" in str(answers.get("conditions_and_dogs", "")).lower(),
            avoid_snow_and_mud="dry" in str(answers.get("conditions_and_dogs", "")).lower(),
        )
        packs = self.db.list_destination_packs() if self.db else []
        active_pack = next((p for p in packs if p.id == pack_id), packs[0] if packs else None)
        if not active_pack:
            return {"brief": brief, "candidates": [], "summary": "No destination pack loaded."}

        raw_ranked = rank_candidates_for_brief(brief, active_pack, limit=5)
        routes = [route_from_trail_segment(item["trail"]) for item in raw_ranked]
        names = ", ".join(r.name for r in routes[:3]) if routes else "none"
        summary = (
            f"Recommended routes for {active_pack.name} ({active_pack.state_or_region}): {names}.\n"
            f"Filtered for max {brief.max_distance_km.value} km and +{brief.max_elevation_gain_m.value} m climb."
        )
        return {"brief": brief, "candidates": routes, "summary": summary}

    def step(
        self,
        user_text: str,
        brief: HikeBrief,
        packs: List[DestinationPack],
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[HikeBrief, str, List[Dict[str, Any]]]:
        """Process one user turn: update `brief`, generate reply, and rank candidates if ready."""
        patch = _heuristic_extract(user_text, brief, packs)
        updated_brief = apply_patch(brief, patch)

        if self.llm is not None:
            try:
                llm_patch = self.llm.structured(
                    [
                        {"role": "system", "content": EXTRACTION_SYSTEM},
                        {"role": "user", "content": user_text},
                    ],
                    schema=HikeBriefPatch,
                    kind="interview_extract",
                )
                updated_brief = apply_patch(updated_brief, llm_patch)
            except Exception as e:
                logger.info(f"Using heuristic brief extraction ({e})")

        missing = next_missing_topic(updated_brief)
        active_pack_id = str(updated_brief.pack_id.value or "white_mountains_nh")
        active_pack = next((p for p in packs if p.id == active_pack_id), packs[0] if packs else None)

        candidates: List[Dict[str, Any]] = []
        if is_brief_ready(updated_brief) and active_pack is not None:
            candidates = rank_candidates_for_brief(updated_brief, active_pack)

        # Build conversational reply
        if self.llm is not None:
            try:
                topic_instruction = (
                    missing.prompt_hint
                    if missing
                    else "All required topics are complete! Summarize the top matching trails and invite the user to click 'Load in Route Composer'."
                )
                sys_msg = CONVERSATION_SYSTEM.format(
                    brief_summary=updated_brief.model_dump_json(),
                    next_topic=topic_instruction,
                    safety=SAFETY,
                )
                msgs = [{"role": "system", "content": sys_msg}]
                for m in (history or [])[-6:]:
                    msgs.append({"role": m["role"], "content": m["content"]})
                msgs.append({"role": "user", "content": user_text})
                resp = self.llm.chat(msgs, temperature=0.4, kind="interview_chat")
                reply = ((resp.get("message") or {}).get("content") or "").strip()
                if reply:
                    return (updated_brief, reply, candidates)
            except Exception as e:
                logger.info(f"Interview conversation fallback ({e})")

        if missing:
            reply = f"Got it! Next, let's check **{missing.title}**: {missing.prompt_hint}"
        else:
            pack_title = active_pack.name if active_pack else active_pack_id
            names = ", ".join(c["trail"].name for c in candidates[:3]) if candidates else "several routes"
            reply = (
                f"Your Hike Brief for **{pack_title}** is complete! Based on your distance, elevation, "
                f"and condition rules, top code-verified matches include: **{names}**. "
                "Click any recommendation card on the right to open it in the Route Composer."
            )
        return (updated_brief, reply, candidates)

