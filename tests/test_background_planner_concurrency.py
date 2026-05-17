import asyncio
import tempfile

from src.schemas.mental_health import MentalRiskAssessment
from src.services.background_planner_service import BackgroundPlannerService
from src.services.care_plan_service import CarePlanService
from src.services.data_store import DataStore


def _assessment(user_id, turn_id, risk_tier, primary_state="anxiety"):
    return MentalRiskAssessment(
        id=f"assess_{turn_id}",
        turn_id=turn_id,
        elder_user_id=user_id,
        primary_state=primary_state,
        risk_tier=risk_tier,
        next_goal=f"goal for {turn_id}",
    )


class DelayedPlannerService(BackgroundPlannerService):
    def __init__(self, *args, delays=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.delays = delays or {}

    async def _run_rule_planner(self, job, assessment, current_plan):
        delay = self.delays.get(job.base_turn_id, 0)
        if delay:
            await asyncio.sleep(delay)
        return await super()._run_rule_planner(job, assessment, current_plan)


async def _wait_all(service):
    tasks = list(service.all_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def test_newer_turn_prevents_old_result_from_overwriting_plan():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DataStore(temp_dir)
            care = CarePlanService(store)
            service = DelayedPlannerService(
                store,
                care,
                safe_low_debounce_seconds=0,
                delays={"turn_001": 0.05},
            )

            service.schedule_from_assessment(_assessment("elder_001", "turn_001", "medium"))
            await asyncio.sleep(0)
            service.schedule_from_assessment(_assessment("elder_001", "turn_002", "medium"))
            await _wait_all(service)

            plan = care.get_plan("elder_001")
            jobs = service.list_jobs("elder_001")

            assert plan.source_turn_id == "turn_002"
            assert plan.version == 1
            assert any(
                job.base_turn_id == "turn_001"
                and job.status == "stale_discarded"
                for job in jobs
            )

    asyncio.run(scenario())


def test_planner_jobs_are_isolated_per_user():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DataStore(temp_dir)
            care = CarePlanService(store)
            service = BackgroundPlannerService(store, care, safe_low_debounce_seconds=0)

            service.schedule_from_assessment(_assessment("elder_a", "turn_a", "medium"))
            service.schedule_from_assessment(_assessment("elder_b", "turn_b", "medium"))
            await _wait_all(service)

            assert care.get_plan("elder_a").source_turn_id == "turn_a"
            assert care.get_plan("elder_b").source_turn_id == "turn_b"
            assert service.get_status("elder_a").last_completed_job_id
            assert service.get_status("elder_b").last_completed_job_id

    asyncio.run(scenario())


def test_crisis_preempts_lower_priority_job():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DataStore(temp_dir)
            care = CarePlanService(store)
            service = DelayedPlannerService(
                store,
                care,
                safe_low_debounce_seconds=0,
                delays={"turn_low": 0.1},
            )

            service.schedule_from_assessment(_assessment("elder_001", "turn_low", "low"))
            await asyncio.sleep(0)
            service.schedule_from_assessment(
                _assessment(
                    "elder_001",
                    "turn_crisis",
                    "crisis",
                    primary_state="suicidal_ideation",
                )
            )
            await _wait_all(service)

            plan = care.get_plan("elder_001")
            jobs = service.list_jobs("elder_001")

            assert plan.source_turn_id == "turn_crisis"
            assert plan.risk_tier == "crisis"
            assert any(
                job.base_turn_id == "turn_low"
                and job.status == "stale_discarded"
                and job.stale_reason == "cancelled_by_newer_priority_turn"
                for job in jobs
            )

    asyncio.run(scenario())


def test_cancel_user_jobs_marks_job_as_user_state_reset():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DataStore(temp_dir)
            care = CarePlanService(store)
            service = DelayedPlannerService(
                store,
                care,
                safe_low_debounce_seconds=0,
                delays={"turn_reset": 0.1},
            )

            service.schedule_from_assessment(_assessment("elder_001", "turn_reset", "medium"))
            await asyncio.sleep(0)
            result = await service.cancel_user_jobs("elder_001")

            jobs = service.list_jobs("elder_001")

            assert result["cancelled_tasks"] == 1
            assert any(
                job.base_turn_id == "turn_reset"
                and job.status == "stale_discarded"
                and job.stale_reason == "cancelled_by_user_state_reset"
                for job in jobs
            )

    asyncio.run(scenario())
