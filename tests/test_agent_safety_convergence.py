import asyncio
import inspect
import json
from types import SimpleNamespace

from src.agents.antifraud_agent import AntiFraudAgent
from src.agents.emotional_agent import EmotionalConnectionAgent
from src.agents.medical_agent import MedicalAgent
from src.agents.mental_health_agent import MentalHealthAgent
from src.orchestrator import SystemOrchestrator
from src.policies.safety_policy import SafetyPolicy
from src.schemas.timed_events import MedicationPlan
from src.tools.professional_skills import ProfessionalSkills


class FakeRag:
    def __init__(self):
        self.updated = []

    def update_user_profile(self, key, value):
        self.updated.append((key, value))


class FakeUserContextService:
    def __init__(self):
        self.updated = []

    def update_profile(self, elder_user_id, updates):
        self.updated.append((elder_user_id, updates))
        return updates


class FakeMedicationReminderService:
    def __init__(self, plans):
        self.plans = plans
        self.calls = []

    def list_plans(self, elder_user_id, include_inactive=False):
        self.calls.append((elder_user_id, include_inactive))
        return self.plans


class FakeChunk:
    def __init__(self, content):
        self.content = content


class UnsafeStreamingEmotionalAgent:
    async def astream_run(self, **_kwargs):
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": FakeChunk("我带您去")},
        }
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": FakeChunk("医院看看，可以吃点药。")},
        }
        yield {
            "event": "on_chat_model_end",
            "metadata": {"langgraph_node": "agent"},
            "data": {
                "output": SimpleNamespace(
                    content="我带您去医院看看，可以吃点药。",
                    tool_calls=[],
                )
            },
        }



class SentenceStreamingEmotionalAgent:
    async def astream_run(self, **_kwargs):
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": FakeChunk("Safe first!")},
        }
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": FakeChunk("Still waiting")},
        }
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": FakeChunk(" now!")},
        }
        yield {
            "event": "on_chat_model_end",
            "metadata": {"langgraph_node": "agent"},
            "data": {
                "output": SimpleNamespace(
                    content="Safe first!Still waiting now!",
                    tool_calls=[],
                )
            },
        }


class CrisisStreamingEmotionalAgent:
    async def astream_run(self, **_kwargs):
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": FakeChunk("Hold steady!")},
        }
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": FakeChunk("We are here!")},
        }
        yield {
            "event": "on_chat_model_end",
            "metadata": {"langgraph_node": "agent"},
            "data": {
                "output": SimpleNamespace(
                    content="Hold steady!We are here!",
                    tool_calls=[],
                )
            },
        }

def test_antifraud_agent_has_single_async_entrypoint():
    source = inspect.getsource(AntiFraudAgent)
    assert source.count("async def arun") == 1


def test_antifraud_intervention_is_sanitized():
    agent = AntiFraudAgent.__new__(AntiFraudAgent)
    agent.safety_policy = SafetyPolicy()

    cleaned = agent._sanitize_intervention(
        {
            "action_to_senior": "您是抑郁症，可以吃点药，去医院看看。",
            "action_to_family": "老人可能是焦虑症，建议吃药。",
            "action_to_community": "老人说怎么自杀，请协助。",
            "intervention_type": "Warning",
        }
    )

    serialized = json.dumps(cleaned, ensure_ascii=False)
    assert "抑郁症" not in serialized
    assert "焦虑症" not in serialized
    assert "可以吃" not in serialized
    assert "建议吃" not in serialized
    assert "去医院" not in serialized
    assert "怎么自杀" not in serialized
    assert cleaned["intervention_type"] == "Warning"


def test_medical_agent_symptom_report_is_record_only():
    agent = MedicalAgent.__new__(MedicalAgent)
    agent.safety_policy = SafetyPolicy()
    agent.rag_helper = FakeRag()
    agent.emergency_keywords = []

    async def fake_analyze(_text):
        return {"intent": "symptom_report", "is_emergency": False, "symptom": "头晕"}

    agent._analyze_health_intent = fake_analyze

    result = asyncio.run(
        agent.arun(
            "我头晕",
            {
                "user_profile": {"medications": []},
                "recent_history_text": "",
                "memory_context": "",
            },
        )
    )

    assert result["risk_level"] == "medium"
    assert "头晕" in result["content"]
    assert "您这是" not in result["content"]
    assert "去医院" not in result["content"]
    assert "可以吃" not in result["content"]
    assert agent.rag_helper.updated == [("health_condition", "头晕")]


def test_medical_agent_records_symptom_through_user_context_service_when_available():
    agent = MedicalAgent.__new__(MedicalAgent)
    agent.safety_policy = SafetyPolicy()
    agent.rag_helper = FakeRag()
    agent.user_context_service = FakeUserContextService()
    agent.emergency_keywords = []

    async def fake_analyze(_text):
        return {"intent": "symptom_report", "is_emergency": False, "symptom": "leg pain"}

    agent._analyze_health_intent = fake_analyze

    result = asyncio.run(
        agent.arun(
            "my leg hurts",
            {
                "user_id": "elder_ctx",
                "user_profile": {"medications": []},
                "recent_history_text": "",
                "memory_context": "",
            },
        )
    )

    assert result["risk_level"] == "medium"
    assert agent.user_context_service.updated == [
        ("elder_ctx", {"health_condition": "leg pain"})
    ]
    assert agent.rag_helper.updated == []


def test_medical_agent_medication_query_reads_recorded_medication_plans():
    agent = MedicalAgent.__new__(MedicalAgent)
    agent.safety_policy = SafetyPolicy()
    agent.rag_helper = FakeRag()
    agent.user_context_service = FakeUserContextService()
    agent.medication_reminder_service = FakeMedicationReminderService(
        [
            MedicationPlan(
                medication_id="med_001",
                elder_user_id="elder_med",
                name="recorded medicine",
                dosage_text="one tablet",
                instruction_text="after breakfast",
                schedule=[{"time": "08:00", "label": "breakfast"}],
            )
        ]
    )
    agent.emergency_keywords = []

    async def fake_analyze(_text):
        return {"intent": "medication_query", "is_emergency": False, "symptom": None}

    agent._analyze_health_intent = fake_analyze

    result = asyncio.run(
        agent.arun(
            "what medicine should I take",
            {
                "user_id": "elder_med",
                "user_profile": {"medications": []},
                "recent_history_text": "",
                "memory_context": "",
            },
        )
    )

    assert "recorded medicine" in result["content"]
    assert agent.medication_reminder_service.calls == [("elder_med", False)]


def test_emotional_health_tool_call_is_bound_to_session_user_id():
    agent = EmotionalConnectionAgent.__new__(EmotionalConnectionAgent)
    calls = [
        {
            "name": "record_health_complaint",
            "args": {"symptom": "leg pain"},
            "id": "call_001",
        }
    ]

    bound = agent._bind_session_context_to_tool_calls(calls, {"user_id": "elder_ctx"})

    assert bound[0]["args"]["elder_user_id"] == "elder_ctx"
    assert "elder_user_id" not in calls[0]["args"]


def test_emotional_health_tool_call_keeps_explicit_user_id():
    agent = EmotionalConnectionAgent.__new__(EmotionalConnectionAgent)
    calls = [
        {
            "name": "record_health_complaint",
            "args": {"symptom": "leg pain", "elder_user_id": "explicit_elder"},
            "id": "call_001",
        }
    ]

    bound = agent._bind_session_context_to_tool_calls(calls, {"user_id": "elder_ctx"})

    assert bound[0]["args"]["elder_user_id"] == "explicit_elder"


def test_medical_agent_finalizer_blocks_medical_advice():
    agent = MedicalAgent.__new__(MedicalAgent)
    agent.safety_policy = SafetyPolicy()

    result = agent._finalize_response(
        {
            "content": "我带您去医院看看，可以吃点药，别自己加量。",
            "risk_level": "medium",
        }
    )

    assert "去医院" not in result["content"]
    assert "可以吃" not in result["content"]
    assert "加量" not in result["content"]
    assert "医疗处置" in result["content"]


def test_medical_agent_no_longer_owns_medication_timer():
    agent = MedicalAgent.__new__(MedicalAgent)
    assert agent.check_medication_reminder() is None


def test_mental_health_safe_text_blocks_diagnosis_and_medical_advice():
    agent = MentalHealthAgent.__new__(MentalHealthAgent)
    agent.safety_policy = SafetyPolicy()

    sanitized = agent._safe_text("您是焦虑症，可以吃点药。", risk_tier="medium")

    assert "焦虑症" not in sanitized
    assert "可以吃" not in sanitized
    assert "情绪困扰" in sanitized


def test_mental_health_prompt_no_longer_claims_clinical_role():
    source = inspect.getsource(MentalHealthAgent)
    assert "专业的心理咨询师" not in source
    assert "不做“抑郁症/焦虑症/双相”等诊断命名" in source


def test_emotional_stream_is_buffered_through_safety_policy():
    orchestrator = SystemOrchestrator.__new__(SystemOrchestrator)
    orchestrator.emotional_agent = UnsafeStreamingEmotionalAgent()
    orchestrator.safety_policy = SafetyPolicy()

    async def collect_tokens():
        tokens = []
        async for raw_event in orchestrator._run_emotional_agent(
            "我不舒服",
            {"risk_assessment": {"risk_tier": "medium"}},
        ):
            event = json.loads(raw_event)
            if event["type"] == "token":
                tokens.append(event["data"])
        return "".join(tokens)

    content = asyncio.run(collect_tokens())

    assert "去医院" not in content
    assert "可以吃" not in content
    assert "医疗处置" in content



def test_emotional_stream_flushes_completed_safe_sentences():
    orchestrator = SystemOrchestrator.__new__(SystemOrchestrator)
    orchestrator.emotional_agent = SentenceStreamingEmotionalAgent()
    orchestrator.safety_policy = SafetyPolicy()

    async def collect_tokens():
        tokens = []
        async for raw_event in orchestrator._run_emotional_agent(
            "hello",
            {"risk_assessment": {"risk_tier": "medium"}},
        ):
            event = json.loads(raw_event)
            if event["type"] == "token":
                tokens.append(event["data"])
        return tokens

    tokens = asyncio.run(collect_tokens())

    assert tokens == ["Safe first!", "Still waiting now!"]


def test_emotional_crisis_stream_stays_fully_buffered():
    orchestrator = SystemOrchestrator.__new__(SystemOrchestrator)
    orchestrator.emotional_agent = CrisisStreamingEmotionalAgent()
    orchestrator.safety_policy = SafetyPolicy()

    async def collect_tokens():
        tokens = []
        async for raw_event in orchestrator._run_emotional_agent(
            "urgent",
            {"risk_assessment": {"risk_tier": "crisis"}},
        ):
            event = json.loads(raw_event)
            if event["type"] == "token":
                tokens.append(event["data"])
        return tokens

    tokens = asyncio.run(collect_tokens())

    assert len(tokens) == 1
    assert tokens[0].startswith("\u6211\u5728\u8fd9\u91cc\u966a\u7740\u60a8")
    assert tokens[0].endswith("Hold steady!We are here!")

def test_emergency_contact_splits_family_community_sos_without_fake_calls():
    raw_reason = "老人原话：救命，我摔倒了"

    tool_result = ProfessionalSkills.emergency_contact.invoke(
        {"reason": raw_reason, "level": "high"}
    )
    data = json.loads(tool_result) if isinstance(tool_result, str) else tool_result
    serialized = json.dumps(data, ensure_ascii=False)

    assert data["status"] == "success"
    assert data["level"] == "high"
    assert data["trigger_sos"] is True
    assert "family" in data["recommended_channels"]
    assert "community" in data["recommended_channels"]
    assert raw_reason in data["family_message"]
    assert raw_reason not in data["community_message"]
    assert data["community_raw_quote_visible"] is False
    assert "120" not in serialized
    assert "上门" not in serialized
    assert "正在拨打" not in serialized


def test_low_level_emergency_contact_notifies_family_without_sos():
    tool_result = ProfessionalSkills.emergency_contact.invoke(
        {"reason": "老人说有些不舒服", "level": "low"}
    )
    data = json.loads(tool_result) if isinstance(tool_result, str) else tool_result

    assert data["trigger_sos"] is False
    assert data["recommended_channels"] == ["family"]
    assert data["actions"] == ["notify_family_message"]
