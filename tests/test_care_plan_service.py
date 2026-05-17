import tempfile

from src.schemas.mental_health import MentalRiskAssessment, SafetyFlags
from src.services.care_plan_service import CarePlanService
from src.services.data_store import DataStore


def _assessment(
    *,
    turn_id="turn_001",
    risk_tier="medium",
    primary_state="anxiety",
    medical_emergency=False,
):
    return MentalRiskAssessment(
        id=f"assess_{turn_id}",
        turn_id=turn_id,
        elder_user_id="elder_001",
        primary_state=primary_state,
        risk_tier=risk_tier,
        next_goal="continue support",
        safety_flags=SafetyFlags(medical_emergency=medical_emergency),
    )


def test_get_plan_returns_default_for_new_user():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = CarePlanService(DataStore(temp_dir))

        plan = service.get_plan("elder_001")

        assert plan.elder_user_id == "elder_001"
        assert plan.version == 0
        assert plan.target_agent == "emotional_agent"


def test_update_plan_increments_version_and_writes_history():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = CarePlanService(DataStore(temp_dir))

        updated = service.update_plan(
            "elder_001",
            {"risk_tier": "medium", "target_agent": "mental_health_agent"},
            "turn_001",
            updated_by="planner",
        )
        history = service.store.read_user_jsonl("elder_001", "care_plan_history.jsonl")

        assert updated.version == 1
        assert updated.source_turn_id == "turn_001"
        assert updated.updated_by == "planner"
        assert history[-1]["version"] == 1


def test_compare_and_swap_rejects_stale_version():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = CarePlanService(DataStore(temp_dir))
        service.update_plan(
            "elder_001",
            {"risk_tier": "low"},
            "turn_001",
        )

        committed = service.compare_and_swap(
            "elder_001",
            expected_version=0,
            patch={"risk_tier": "crisis"},
            source_turn_id="turn_002",
        )

        assert committed is False
        assert service.get_plan("elder_001").risk_tier == "low"


def test_create_from_assessment_maps_crisis_and_medical_emergency():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = CarePlanService(DataStore(temp_dir))

        crisis = service.create_from_assessment(
            _assessment(turn_id="turn_crisis", risk_tier="crisis", primary_state="suicidal_ideation")
        )
        emergency = service.create_from_assessment(
            _assessment(
                turn_id="turn_medical",
                risk_tier="high",
                primary_state="physical_emergency",
                medical_emergency=True,
            )
        )

        assert crisis.current_stage == "crisis.safety_grounding"
        assert crisis.target_agent == "mental_health_agent"
        assert emergency.current_stage == "medical.safety_check"
        assert emergency.target_agent == "medical_agent"
