"""Tests for InterviewEngine and AgentEngine with deterministic fallbacks."""
from src.core.agent_engine import AgentEngine
from src.core.database import Database
from src.core.interview_engine import InterviewEngine


def test_interview_and_agent_engines(seeded_db: Database) -> None:
    ie = InterviewEngine(seeded_db)
    res = ie.run_interview(
        answers={
            "party_and_fitness": "athletic",
            "max_distance_km": "15.0",
            "max_elevation_gain_m": "1200",
            "route_shape_and_scenery": "loop, ridge, summit",
            "conditions_and_dogs": "Open & dry trails preferred",
            "pack_and_start_time": "08:00",
        },
        destination_id="white_mountains_nh",
        use_llm=False,
    )
    assert len(res["candidates"]) >= 1
    top_route = res["candidates"][0]
    seeded_db.upsert_route(top_route)

    agent = AgentEngine(seeded_db)
    loop_resp = agent.process_user_prompt(
        "Close this route into a loop",
        active_route_id=top_route.id,
        active_destination_id="white_mountains_nh",
    )
    assert loop_resp["proposal"] is not None
    assert loop_resp["proposed_route"] is not None

    water_resp = agent.process_user_prompt(
        "How much water and calories should I bring?",
        active_route_id=top_route.id,
        active_destination_id="white_mountains_nh",
    )
    assert "L" in water_resp["reply"] and "kcal" in water_resp["reply"]

