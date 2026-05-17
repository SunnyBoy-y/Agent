import json

from fastapi.testclient import TestClient

import src.server as server
from src.agents.family_agent import FamilyAgent
from src.services.assessment_service import AssessmentService
from src.services.care_plan_service import CarePlanService
from src.services.data_store import DataStore
from src.services.family_context_service import FamilyContextService
from src.services.family_policy_service import FamilyPolicyService
from src.services.profile_service import ProfileService
from src.services.relay_message_service import RelayMessageService


CRISIS_TEXT = "\u6211\u4e0d\u60f3\u6d3b\u4e86"


class FakeOrchestrator:
    def __init__(self, root_dir):
        self.data_store = DataStore(root_dir)
        self.profile_service = ProfileService(self.data_store)
        self.relay_message_service = RelayMessageService(self.data_store)
        self.care_plan_service = CarePlanService(self.data_store)
        self.family_policy_service = FamilyPolicyService(
            self.data_store,
            self.relay_message_service,
        )
        self.assessment_service = AssessmentService(self.data_store)
        self.family_context_service = FamilyContextService(
            self.data_store,
            care_plan_service=self.care_plan_service,
            family_policy_service=self.family_policy_service,
            relay_message_service=self.relay_message_service,
            profile_service=self.profile_service,
        )
        self.family_agent = FamilyAgent(self.family_context_service)

    async def process_family_chat_stream(self, request):
        async for event in self.family_agent.process_chat_stream(request):
            yield event

    def get_family_elder_summary(self, elder_user_id, child_user_id):
        return self.family_agent.build_elder_summary(elder_user_id, child_user_id)


def _client(monkeypatch, tmp_path):
    fake = FakeOrchestrator(tmp_path)
    monkeypatch.setattr(server, "orchestrator", fake)
    return TestClient(server.app), fake


def _seed_crisis(fake):
    fake.profile_service.update_profile("elder_001", {"name": "Mom"})
    assessment = fake.assessment_service.assess_text(
        CRISIS_TEXT,
        {"user_id": "elder_001", "turn_id": "turn_001"},
    )
    fake.care_plan_service.create_from_assessment(assessment)
    fake.relay_message_service.create_from_assessment(assessment)
    return assessment


def _parse_sse(text):
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        events.append(json.loads(block[len("data:") :].strip()))
    return events


def test_family_elder_summary_api_returns_family_visible_context(monkeypatch, tmp_path):
    client, fake = _client(monkeypatch, tmp_path)
    _seed_crisis(fake)

    response = client.get(
        "/api/family/elder_summary",
        params={"elder_user_id": "elder_001", "child_user_id": "child_001"},
    )
    payload = response.json()["data"]
    serialized = json.dumps(payload, ensure_ascii=False)

    assert response.status_code == 200
    assert payload["summary"]["risk_tier"] == "crisis"
    assert payload["visible_evidence"][0]["raw_quotes"] == ["\u4e0d\u60f3\u6d3b\u4e86"]
    assert "Community SOS" not in serialized
    assert "community_crisis_summary" not in serialized


def test_family_chat_api_streams_tokens_context_and_done_without_elder_memory_pollution(monkeypatch, tmp_path):
    client, fake = _client(monkeypatch, tmp_path)
    _seed_crisis(fake)

    with client.stream(
        "POST",
        "/api/family/chat",
        json={
            "elder_user_id": "elder_001",
            "child_user_id": "child_001",
            "message": "How should I talk with mom today?",
        },
    ) as response:
        body = "".join(response.iter_text())

    events = _parse_sse(body)
    token_text = "".join(event["data"] for event in events if event["type"] == "token")
    context_event = next(event for event in events if event["type"] == "family_context")
    elder_history = fake.data_store.read_json("users/elder_001/chat_history.json", default=[])
    family_history = fake.family_context_service.get_recent_family_history("elder_001", "child_001")

    assert response.status_code == 200
    assert events[-1] == {"type": "done", "data": "stop"}
    assert token_text
    assert context_event["data"]["summary"]["risk_tier"] == "crisis"
    assert elder_history == []
    assert [item["role"] for item in family_history] == ["child", "assistant"]


def test_family_chat_api_rejects_empty_message(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/family/chat",
        json={
            "elder_user_id": "elder_001",
            "child_user_id": "child_001",
            "message": "   ",
        },
    )

    assert response.status_code == 400
