import asyncio

from src.agents.planning_agent import PlanningAgent
from src.schemas.mental_health import CarePlan, MentalRiskAssessment
from src.services.care_plan_service import CarePlanService
from src.services.data_store import DataStore


def _assessment(turn_id="turn_001", risk_tier="medium", primary_state="anxiety"):
    return MentalRiskAssessment(
        id=f"assess_{turn_id}",
        turn_id=turn_id,
        elder_user_id="elder_001",
        primary_state=primary_state,
        risk_tier=risk_tier,
        next_goal="continue support",
    )


def test_crisis_planner_cannot_downgrade_or_leak_thought(tmp_path):
    async def review(_assessment, _plan, _context):
        return {
            "state_summary": "crisis signal confirmed",
            "suggested_next_goal": "keep the next turn grounded",
        }

    async def planner(_assessment, _plan, _review, _context):
        return {
            "thought": "this must never be exposed",
            "target_agent": "emotional_agent",
            "intervention_goal": "companionship",
            "care_plan_patch": {
                "risk_tier": "safe",
                "current_stage": "companionship",
                "target_agent": "emotional_agent",
            },
            "queued_actions": [],
        }

    async def scenario():
        agent = PlanningAgent(
            CarePlanService(DataStore(tmp_path)),
            review_callable=review,
            planner_callable=planner,
        )
        result = await agent.arun(
            _assessment("turn_crisis", "crisis", "suicidal_ideation"),
            CarePlan(elder_user_id="elder_001"),
        )

        assert result.care_plan_patch["risk_tier"] == "crisis"
        assert result.care_plan_patch["current_stage"] == "crisis.safety_grounding"
        assert result.target_agent == "mental_health_agent"
        dumped = result.model_dump(mode="python")
        assert "thought" not in dumped

    asyncio.run(scenario())


def test_review_timeout_falls_back_to_rule_planner(tmp_path):
    async def slow_review(_assessment, _plan, _context):
        await asyncio.sleep(0.05)
        return {"state_summary": "late"}

    async def scenario():
        agent = PlanningAgent(
            CarePlanService(DataStore(tmp_path)),
            review_timeout_seconds=0.001,
            review_callable=slow_review,
            planner_callable=None,
            enable_live_llm=False,
        )
        result = await agent.arun(
            _assessment(),
            CarePlan(elder_user_id="elder_001"),
        )

        assert result.review.status == "timeout"
        assert result.used_fallback is True
        assert result.care_plan_patch["risk_tier"] == "medium"
        assert result.target_agent == "mental_health_agent"

    asyncio.run(scenario())


def test_planner_filters_invalid_actions_and_keeps_structured_output(tmp_path):
    async def review(_assessment, _plan, _context):
        return {"state_summary": "anxiety support"}

    async def planner(_assessment, _plan, _review, _context):
        return {
            "target_agent": "mental_health_agent",
            "intervention_goal": "anxiety.emotional_first_aid",
            "care_plan_patch": {
                "current_stage": "anxiety.body_regulation",
                "next_turn_goal": "guide slow breathing",
            },
            "queued_actions": [
                {"type": "quiet_message", "content": "gentle reminder"},
                {"type": "unknown_action", "content": "drop me"},
            ],
        }

    async def scenario():
        agent = PlanningAgent(
            CarePlanService(DataStore(tmp_path)),
            review_callable=review,
            planner_callable=planner,
        )
        result = await agent.arun(
            _assessment(),
            CarePlan(elder_user_id="elder_001"),
        )

        assert result.care_plan_patch["current_stage"] == "anxiety.body_regulation"
        assert [action.type for action in result.queued_actions] == ["quiet_message"]
        assert result.review.status == "completed"

    asyncio.run(scenario())


def test_planner_adds_explicit_action_contract_fields(tmp_path):
    async def review(_assessment, _plan, _context):
        return {"state_summary": "low mood support"}

    async def planner(_assessment, _plan, _review, _context):
        return {
            "target_agent": "emotional_agent",
            "intervention_goal": "offer a familiar song",
            "care_plan_patch": {
                "current_stage": "companionship.music",
                "next_turn_goal": "offer music only if the elder agrees",
            },
            "queued_actions": [
                {
                    "type": "schedule_music",
                    "content": "play a familiar song",
                    "payload": {"music_name": "favorite_song"},
                }
            ],
        }

    async def scenario():
        agent = PlanningAgent(
            CarePlanService(DataStore(tmp_path)),
            review_callable=review,
            planner_callable=planner,
        )
        result = await agent.arun(
            _assessment("turn_music", "low", "loneliness"),
            CarePlan(elder_user_id="elder_001"),
        )

        action = result.queued_actions[0]
        assert action.type == "schedule_music"
        assert action.target_channel == "frontend"
        assert action.visibility_scope == "elder"
        assert action.consent_required is True
        assert action.approval_required is False
        assert action.idempotency_key.startswith("planner_action:elder_001:turn_music:schedule_music:")
        assert action.payload["contract_version"] == "target19.v1"
        assert action.payload["target_channel"] == "frontend"
        assert action.payload["visibility_scope"] == "elder"
        assert action.payload["consent_required"] is True

    asyncio.run(scenario())
