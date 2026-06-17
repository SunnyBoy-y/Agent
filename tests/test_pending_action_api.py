from fastapi.testclient import TestClient

import src.server as server
from src.schemas.actions import ActionConsentRequest
from src.services.action_session_service import ActionSessionService
from src.services.data_store import DataStore


class FakeOrchestrator:
    def __init__(self, root_dir):
        self.data_store = DataStore(root_dir)
        self.action_session_service = ActionSessionService(self.data_store)

    def list_pending_actions(self, elder_user_id, *, target_channel="frontend", limit=None):
        return self.action_session_service.list_pending_actions(
            elder_user_id,
            target_channel=target_channel,
            limit=limit,
        )

    def consent_action(self, action_id, request):
        return self.action_session_service.consent_action(action_id, request)

    def complete_action(self, request):
        return self.action_session_service.complete_action(request)


def test_pending_actions_list_filters_frontend_consent_required(tmp_path):
    service = ActionSessionService(DataStore(tmp_path))
    expected = service.create_session(
        "user_001",
        "music",
        payload={
            "target_channel": "frontend",
            "visibility_scope": "elder",
            "consent_required": True,
            "content": "Would you like to hear a familiar song?",
            "music_name": "Sweet Song",
        },
        status="pending",
    )
    service.create_session(
        "user_001",
        "music",
        payload={"target_channel": "background", "consent_required": True},
        status="pending",
    )
    service.create_session(
        "user_001",
        "story",
        payload={"target_channel": "frontend", "consent_required": False},
        status="pending",
    )
    service.create_session(
        "user_001",
        "music",
        payload={"target_channel": "frontend", "consent_required": True},
        status="started",
    )

    pending = service.list_pending_actions("user_001")

    assert len(pending) == 1
    assert pending[0]["action_id"] == expected.action_id
    assert pending[0]["action_type"] == "music"
    assert pending[0]["content"] == "Would you like to hear a familiar song?"
    assert pending[0]["payload"]["music_name"] == "Sweet Song"
    assert pending[0]["post_reply"]


def test_consent_accepts_pending_action_and_removes_from_pending(tmp_path):
    service = ActionSessionService(DataStore(tmp_path))
    session = service.create_session(
        "user_001",
        "music",
        payload={"target_channel": "frontend", "consent_required": True},
        status="pending",
    )

    result = service.consent_action(
        session.action_id,
        ActionConsentRequest(
            elder_user_id="user_001",
            accepted=True,
            text="yes",
            source="voice",
        ),
    )

    persisted = service.get_session("user_001", session.action_id)
    assert result["status"] == "started"
    assert persisted is not None
    assert persisted.status == "started"
    assert persisted.payload["consent"]["accepted"] is True
    assert service.list_pending_actions("user_001") == []


def test_consent_rejects_pending_action(tmp_path):
    service = ActionSessionService(DataStore(tmp_path))
    session = service.create_session(
        "user_001",
        "music",
        payload={"target_channel": "frontend", "consent_required": True},
        status="pending",
    )

    result = service.consent_action(
        session.action_id,
        ActionConsentRequest(
            elder_user_id="user_001",
            accepted=False,
            text="no",
            source="voice",
        ),
    )

    persisted = service.get_session("user_001", session.action_id)
    assert result["status"] == "cancelled"
    assert persisted is not None
    assert persisted.status == "cancelled"
    assert persisted.ended_at is not None
    assert persisted.result["consent"]["accepted"] is False


def test_pending_action_endpoints(monkeypatch, tmp_path):
    fake = FakeOrchestrator(tmp_path)
    session = fake.action_session_service.create_session(
        "user_001",
        "music",
        payload={
            "target_channel": "frontend",
            "visibility_scope": "elder",
            "consent_required": True,
            "content": "Play music?",
            "music_name": "Sweet Song",
        },
        status="pending",
    )
    monkeypatch.setattr(server, "orchestrator", fake)
    client = TestClient(server.app)

    list_response = client.get("/api/actions/pending?elder_user_id=user_001")
    assert list_response.status_code == 200
    listed = list_response.json()["data"]
    assert len(listed) == 1
    assert listed[0]["action_id"] == session.action_id
    assert listed[0]["status"] == "pending"
    assert listed[0]["post_reply"]

    consent_response = client.post(
        f"/api/actions/{session.action_id}/consent",
        json={
            "elder_user_id": "user_001",
            "accepted": True,
            "text": "ok",
            "source": "click",
        },
    )
    assert consent_response.status_code == 200
    data = consent_response.json()["data"]
    assert data["status"] == "started"
    assert data["session"]["status"] == "started"

    empty_response = client.get("/api/actions/pending?elder_user_id=user_001")
    assert empty_response.status_code == 200
    assert empty_response.json()["data"] == []
