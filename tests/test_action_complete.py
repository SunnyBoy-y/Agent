from fastapi.testclient import TestClient

import src.server as server
from src.orchestrator import SystemOrchestrator
from src.schemas.actions import ActionCompleteRequest
from src.services.action_session_service import ActionSessionService
from src.services.data_store import DataStore


class FakeOrchestrator:
    def __init__(self, root_dir):
        self.data_store = DataStore(root_dir)
        self.action_session_service = ActionSessionService(self.data_store)

    def complete_action(self, request):
        return self.action_session_service.complete_action(request)


def test_music_payload_creates_durable_action_session(tmp_path):
    orchestrator = object.__new__(SystemOrchestrator)
    orchestrator.action_session_service = ActionSessionService(DataStore(tmp_path))

    payload = orchestrator._normalize_music_payload(
        {"trigger_music": True, "query": "月亮代表我的心", "source": "interest_agent"},
        music_flag=True,
        elder_user_id="elder_001",
        turn_id="turn_001",
        care_plan={
            "risk_tier": "medium",
            "current_stage": "anxiety.emotional_first_aid",
            "next_turn_goal": "continue grounding",
        },
    )

    session = orchestrator.action_session_service.get_session("elder_001", payload["action_id"])
    assert payload["action_type"] == "music"
    assert payload["music_name"] == "月亮代表我的心"
    assert payload["post_reply"]
    assert session is not None
    assert session.status == "started"
    assert session.payload["turn_id"] == "turn_001"
    assert session.payload["risk_tier"] == "medium"


def test_completed_action_returns_post_reply_and_is_idempotent(tmp_path):
    service = ActionSessionService(DataStore(tmp_path))
    session = service.create_session(
        "elder_001",
        "music",
        payload={"turn_id": "turn_001", "risk_tier": "medium"},
        post_reply="听完了，我们再聊聊。",
    )
    request = ActionCompleteRequest(
        action_id=session.action_id,
        elder_user_id="elder_001",
        action_type="music",
        status="completed",
        music_name="月亮代表我的心",
        played_seconds=180,
        total_seconds=180,
    )

    first = service.complete_action(request)
    second = service.complete_action(request)
    audit = service.store.read_user_jsonl("elder_001", service.AUDIT_FILE)
    interventions = service.store.read_user_jsonl("elder_001", service.INTERVENTION_AUDIT_FILE)

    assert first["post_reply"] == "听完了，我们再聊聊。"
    assert first["completed_intervention"] is True
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert second["session"].status == "completed"
    assert len(audit) == 2
    assert len(interventions) == 1
    assert interventions[0]["result"] == "completed"
    assert interventions[0]["payload"]["completed_intervention"] is True


def test_create_session_reuses_idempotency_key(tmp_path):
    service = ActionSessionService(DataStore(tmp_path))

    first = service.create_session(
        "elder_001",
        "music",
        payload={"source": "planner"},
        idempotency_key="planner_action:elder_001:turn_001:schedule_music:abc",
    )
    second = service.create_session(
        "elder_001",
        "music",
        payload={"source": "planner_retry"},
        idempotency_key="planner_action:elder_001:turn_001:schedule_music:abc",
    )

    sessions = service.list_sessions("elder_001")
    audit = service.store.read_user_jsonl("elder_001", service.AUDIT_FILE)

    assert first.action_id == second.action_id
    assert len(sessions) == 1
    assert len(audit) == 1
    assert sessions[0].payload["idempotency_key"] == "planner_action:elder_001:turn_001:schedule_music:abc"


def test_interrupted_action_ends_session_without_counting_as_completed(tmp_path):
    service = ActionSessionService(DataStore(tmp_path))
    session = service.create_session(
        "elder_001",
        "music",
        payload={"turn_id": "turn_002", "risk_tier": "low"},
    )

    result = service.complete_action(
        ActionCompleteRequest(
            action_id=session.action_id,
            elder_user_id="elder_001",
            action_type="music",
            status="interrupted",
            interrupt_reason="user_switched_song",
            played_seconds=24,
            total_seconds=180,
        )
    )
    persisted = service.get_session("elder_001", session.action_id)
    interventions = service.store.read_user_jsonl("elder_001", service.INTERVENTION_AUDIT_FILE)

    assert persisted is not None
    assert persisted.status == "interrupted"
    assert persisted.ended_at is not None
    assert persisted.completed_at is None
    assert persisted.completed_intervention is False
    assert result["post_reply"] is None
    assert result["completed_intervention"] is False
    assert interventions[0]["result"] == "interrupted"
    assert interventions[0]["payload"]["completed_intervention"] is False


def test_action_complete_endpoint_returns_completion_payload(monkeypatch, tmp_path):
    fake = FakeOrchestrator(tmp_path)
    session = fake.action_session_service.create_session(
        "elder_001",
        "music",
        post_reply="听完了，我们再聊聊。",
    )
    monkeypatch.setattr(server, "orchestrator", fake)
    client = TestClient(server.app)

    response = client.post(
        "/api/action_complete",
        json={
            "action_id": session.action_id,
            "elder_user_id": "elder_001",
            "action_type": "music",
            "status": "completed",
            "played_seconds": 120,
            "total_seconds": 120,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["post_reply"] == "听完了，我们再聊聊。"
    assert data["completed_intervention"] is True
    assert data["session"]["status"] == "completed"


def test_action_complete_endpoint_404s_for_missing_session(monkeypatch, tmp_path):
    fake = FakeOrchestrator(tmp_path)
    monkeypatch.setattr(server, "orchestrator", fake)
    client = TestClient(server.app)

    response = client.post(
        "/api/action_complete",
        json={
            "action_id": "action_missing",
            "elder_user_id": "elder_001",
            "action_type": "music",
            "status": "completed",
        },
    )

    assert response.status_code == 404
