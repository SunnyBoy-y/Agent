import asyncio
import json

from src.orchestrator import SystemOrchestrator
from src.policies.safety_policy import SafetyPolicy
from src.services.assessment_service import AssessmentService
from src.services.background_planner_service import BackgroundPlannerService
from src.services.care_plan_service import CarePlanService
from src.services.context_guard import ContextGuard
from src.services.data_store import DataStore
from src.services.frontend_action_service import FrontendActionService
from src.services.relay_message_service import RelayMessageService
from src.services.response_style_guard import ResponseStyleGuard
from src.services.scene_context_service import SceneContextService
from src.services.user_context_service import UserContextService


class FakeRouter:
    async def route(self, _text, context=None):
        return "daily_life_agent"

    def route_sync(self, _text, context=None):
        return "daily_life_agent"


class FailingRelayService:
    def create_from_assessment(self, _assessment):
        raise RuntimeError("relay store unavailable")


class LightweightOrchestrator(SystemOrchestrator):
    def __init__(self, root_dir, relay_service=None):
        self.data_store = DataStore(root_dir)
        self.user_context_service = UserContextService(self.data_store)
        self.profile_service = self.user_context_service.profile_service
        self.safety_policy = SafetyPolicy()
        self.assessment_service = AssessmentService(self.data_store)
        self.care_plan_service = CarePlanService(self.data_store)
        self.context_guard = ContextGuard()
        self.scene_context_service = SceneContextService(self.user_context_service)
        self.response_style_guard = ResponseStyleGuard()
        self.frontend_action_service = FrontendActionService()
        self.relay_message_service = relay_service or RelayMessageService(self.data_store)
        self.background_planner_service = BackgroundPlannerService(
            self.data_store,
            self.care_plan_service,
            safe_low_debounce_seconds=0,
            on_job_event=self._record_planner_job_event,
        )
        self.router = FakeRouter()
        self.state_lock = asyncio.Lock()
        self.background_tasks = set()
        self.last_system_state = {
            "last_input": "",
            "last_route": "",
            "tool_calls": [],
            "background_tasks": [],
            "context_snapshot": {},
            "agent_context": {},
            "llm_inputs": [],
        }

    async def _build_shared_context(self, _user_input, context):
        shared = dict(context)
        shared.setdefault("recent_history_text", "")
        shared.setdefault("memory_context", "")
        shared.setdefault("user_profile", {})
        return shared

    async def _run_specific_agent(self, agent_name, _input_text, _context):
        return {
            "content": f"handled by {agent_name}",
            "action": "nod",
            "risk_level": "safe",
        }

    def _get_agent_instance(self, _agent_name):
        return None

    async def _stream_llm_first_response(self, **_kwargs):
        if False:
            yield ""


async def _collect_events(orchestrator, text, context):
    events = []
    async for raw_event in orchestrator.process_input_stream(text, context):
        events.append(json.loads(raw_event))
    if orchestrator.background_tasks:
        await asyncio.gather(*list(orchestrator.background_tasks), return_exceptions=True)
    planner_tasks = list(orchestrator.background_planner_service.all_tasks)
    if planner_tasks:
        await asyncio.gather(*planner_tasks, return_exceptions=True)
    await asyncio.sleep(0)
    return events


def test_crisis_stream_emits_risk_detail_and_background_relay(tmp_path):
    orchestrator = LightweightOrchestrator(tmp_path)

    events = asyncio.run(
        _collect_events(
            orchestrator,
            "\u6211\u4e0d\u60f3\u6d3b\u4e86",
            {"user_id": "elder_001", "turn_id": "turn_001"},
        )
    )

    risk_detail = next(event for event in events if event["type"] == "risk_detail")["data"]
    relay_messages = orchestrator.relay_message_service.list_messages("elder_001")

    assert risk_detail["assessment_id"] == risk_detail["id"]
    assert risk_detail["tier"] == "crisis"
    assert risk_detail["risk_tier"] == "crisis"
    assert risk_detail["next_goal"]
    assert any(event == {"type": "risk", "data": "crisis"} for event in events)
    assert any(event == {"type": "sos", "data": True} for event in events)
    assert orchestrator.last_system_state["last_route"] == "mental_health_agent"
    assert {message.target for message in relay_messages} == {"family", "community"}
    assert any(item["status"] == "done" for item in orchestrator.last_system_state["background_tasks"])


def test_background_relay_failure_does_not_break_stream(tmp_path):
    orchestrator = LightweightOrchestrator(tmp_path, relay_service=FailingRelayService())

    events = asyncio.run(
        _collect_events(
            orchestrator,
            "\u6211\u4e0d\u60f3\u6d3b\u4e86",
            {"user_id": "elder_001", "turn_id": "turn_001"},
        )
    )

    assert events[-1] == {"type": "done", "data": "stop"}
    assert any(item["status"] == "failed" for item in orchestrator.last_system_state["background_tasks"])


def test_safe_input_has_consistent_risk_detail_and_no_relay(tmp_path):
    orchestrator = LightweightOrchestrator(tmp_path)

    events = asyncio.run(
        _collect_events(
            orchestrator,
            "\u4eca\u5929\u5929\u6c14\u4e0d\u9519",
            {"user_id": "elder_001", "turn_id": "turn_002"},
        )
    )

    risk_detail = next(event for event in events if event["type"] == "risk_detail")["data"]

    assert risk_detail["tier"] == "safe"
    assert risk_detail["risk_tier"] == "safe"
    assert not any(event["type"] == "risk" for event in events)
    assert orchestrator.relay_message_service.list_messages("elder_001") == []
    assert orchestrator.last_system_state["last_route"] == "daily_life_agent"


def test_safe_input_emits_visible_ack_before_slow_chain(tmp_path):
    orchestrator = LightweightOrchestrator(tmp_path)

    events = asyncio.run(
        _collect_events(
            orchestrator,
            "\u4eca\u5929\u5fc3\u91cc\u6709\u70b9\u95f7\uff0c\u60f3\u804a\u804a\u5929",
            {"user_id": "elder_001", "turn_id": "turn_latency"},
        )
    )

    first_token_index = next(index for index, event in enumerate(events) if event["type"] == "token")
    risk_detail_index = next(index for index, event in enumerate(events) if event["type"] == "risk_detail")

    assert first_token_index < risk_detail_index
    assert events[first_token_index]["data"] == "\u4eca\u5929\u5fc3\u91cc\u6709\u70b9\u95f7\uff0c\u60f3\u804a\u804a\u5929\u2026\u2026"
    assert orchestrator.last_system_state["last_route"] == "daily_life_agent"


def test_explicit_weather_request_emits_frontend_weather_action(tmp_path):
    orchestrator = LightweightOrchestrator(tmp_path)

    events = asyncio.run(
        _collect_events(
            orchestrator,
            "帮我看看天气",
            {
                "user_id": "elder_001",
                "turn_id": "turn_weather",
                "weather": {
                    "condition": "cloudy",
                    "temperature_text": "26°C",
                    "summary": "今天多云。",
                },
            },
        )
    )

    action = next(event for event in events if event["type"] == "action")["data"]
    assert action["name"] == "show_weather"
    assert action["source_turn_id"] == "turn_weather"
    assert action["payload"]["weather"]["condition"] == "cloudy"
    assert action["payload"]["weather"]["summary"] == "今天多云。"


def test_existing_care_plan_guides_safe_follow_up_turn(tmp_path):
    orchestrator = LightweightOrchestrator(tmp_path)
    orchestrator.care_plan_service.update_plan(
        "elder_001",
        {"risk_tier": "medium", "target_agent": "mental_health_agent"},
        "turn_prev",
    )

    events = asyncio.run(
        _collect_events(
            orchestrator,
            "嗯",
            {"user_id": "elder_001", "turn_id": "turn_003"},
        )
    )

    assert events[-1] == {"type": "done", "data": "stop"}
    assert orchestrator.last_system_state["last_route"] == "mental_health_agent"
