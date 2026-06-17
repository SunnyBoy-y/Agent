import asyncio

from src.agents.planning_agent import PlanningAgent
from src.schemas.mental_health import MentalRiskAssessment
from src.services.background_planner_service import BackgroundPlannerService
from src.services.care_plan_service import CarePlanService
from src.services.data_store import DataStore


def _assessment(turn_id="turn_001", risk_tier="medium", primary_state="anxiety"):
    return MentalRiskAssessment(
        id=f"assess_{turn_id}",
        turn_id=turn_id,
        elder_user_id="elder_001",
        primary_state=primary_state,
        risk_tier=risk_tier,
        next_goal=f"goal for {turn_id}",
    )


def test_planner_persists_review_snapshot_and_action_audit(tmp_path):
    async def review(_assessment, _plan, _context):
        return {
            "state_summary": "anxiety signs reviewed",
            "suggested_next_goal": "continue grounding",
        }

    async def planner(_assessment, _plan, _review, _context):
        return {
            "target_agent": "mental_health_agent",
            "intervention_goal": "anxiety.emotional_first_aid",
            "care_plan_patch": {
                "current_stage": "anxiety.body_regulation",
                "next_turn_goal": "continue grounding",
            },
            "queued_actions": [
                {
                    "type": "quiet_message",
                    "target": "elder",
                    "content": "remember the next small step",
                    "payload": {"title": "Gentle note"},
                }
            ],
        }

    async def scenario():
        store = DataStore(tmp_path)
        care = CarePlanService(store)
        planning_agent = PlanningAgent(
            care,
            review_callable=review,
            planner_callable=planner,
        )
        service = BackgroundPlannerService(
            store,
            care,
            planning_agent=planning_agent,
            safe_low_debounce_seconds=0,
        )

        service.schedule_from_assessment(_assessment())
        await service.wait_for_idle("elder_001")

        assessments = store.read_user_jsonl("elder_001", "mental_assessments.jsonl")
        actions = store.read_user_jsonl("elder_001", "planner_actions.jsonl")
        relay_messages = service.relay_message_service.list_messages("elder_001")
        jobs = service.list_jobs("elder_001")
        status = service.get_status("elder_001")

        assert assessments[-1]["llm_review"]["status"] == "completed"
        assert assessments[-1]["llm_review"]["state_summary"] == "anxiety signs reviewed"
        assert actions[-1]["type"] == "quiet_message"
        assert relay_messages[-1].display_type == "quiet_message"
        assert jobs[-1].review_status == "completed"
        assert jobs[-1].used_fallback is False
        assert status.last_review_status == "completed"
        assert status.last_used_fallback is False

    asyncio.run(scenario())


def test_background_planner_persists_action_contract_and_frontend_session(tmp_path):
    async def review(_assessment, _plan, _context):
        return {
            "state_summary": "loneliness reviewed",
            "suggested_next_goal": "offer a familiar song with consent",
        }

    async def planner(_assessment, _plan, _review, _context):
        return {
            "target_agent": "emotional_agent",
            "intervention_goal": "offer music",
            "care_plan_patch": {
                "current_stage": "companionship.music",
                "next_turn_goal": "ask whether music is welcome",
            },
            "queued_actions": [
                {
                    "type": "schedule_music",
                    "content": "offer a familiar song",
                    "payload": {
                        "music_name": "favorite_song",
                        "post_reply": "Did that song help a little?",
                    },
                }
            ],
        }

    async def scenario():
        store = DataStore(tmp_path)
        care = CarePlanService(store)
        planning_agent = PlanningAgent(
            care,
            review_callable=review,
            planner_callable=planner,
        )
        service = BackgroundPlannerService(
            store,
            care,
            planning_agent=planning_agent,
            safe_low_debounce_seconds=0,
        )

        service.schedule_from_assessment(_assessment("turn_music", "low", "loneliness"))
        await service.wait_for_idle("elder_001")

        actions = store.read_user_jsonl("elder_001", "planner_actions.jsonl")
        sessions = service.action_session_service.list_sessions("elder_001")

        assert len(actions) == 1
        action = actions[0]
        assert action["type"] == "schedule_music"
        assert action["action_type"] == "schedule_music"
        assert action["contract_version"] == "target19.v1"
        assert action["target_channel"] == "frontend"
        assert action["visibility_scope"] == "elder"
        assert action["consent_required"] is True
        assert action["approval_required"] is False
        assert action["idempotency_key"].startswith("planner_action:elder_001:turn_music:schedule_music:")
        assert action["action_session_id"] == sessions[0].action_id
        assert action["payload"]["action_session_id"] == sessions[0].action_id

        assert len(sessions) == 1
        assert sessions[0].action_type == "music"
        assert sessions[0].status == "pending"
        assert sessions[0].payload["idempotency_key"] == action["idempotency_key"]
        assert sessions[0].payload["target_channel"] == "frontend"
        assert sessions[0].payload["visibility_scope"] == "elder"
        assert sessions[0].payload["consent_required"] is True

    asyncio.run(scenario())
