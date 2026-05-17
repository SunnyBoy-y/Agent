from fastapi.testclient import TestClient

import src.server as server
from src.services.data_store import DataStore
from src.services.family_policy_service import FamilyPolicyService
from src.services.relay_message_service import RelayMessageService


class FakeOrchestrator:
    def __init__(self, root_dir):
        self.data_store = DataStore(root_dir)
        self.relay_message_service = RelayMessageService(self.data_store)
        self.family_policy_service = FamilyPolicyService(
            self.data_store,
            self.relay_message_service,
        )

    def create_family_message(self, request):
        return self.family_policy_service.create_quiet_message(request)

    def get_elder_pending_messages(self, elder_user_id, risk_tier="safe"):
        return self.family_policy_service.pending_quiet_message_prompts(
            elder_user_id,
            risk_tier=risk_tier,
        )

    def consent_to_elder_message(self, message_id, request):
        return self.family_policy_service.consent_to_quiet_message(message_id, request)


def _client(monkeypatch, tmp_path):
    fake = FakeOrchestrator(tmp_path)
    monkeypatch.setattr(server, "orchestrator", fake)
    return TestClient(server.app), fake


def test_family_policy_api_saves_topics_and_consumes_them(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/family/agent_policy",
        json={
            "elder_user_id": "elder_001",
            "child_user_id": "child_001",
            "policy": {
                "suggested_topics": [
                    {
                        "id": "topic_001",
                        "title": "granddaughter",
                        "prompt_hint": "ask gently about granddaughter",
                        "max_consumptions": 1,
                    }
                ]
            },
        },
    )
    available = client.get(
        "/api/family/topics/available",
        params={"elder_user_id": "elder_001", "child_user_id": "child_001"},
    )
    consumed = client.post(
        "/api/family/topics/topic_001/consume",
        params={"elder_user_id": "elder_001", "child_user_id": "child_001"},
    )
    after = client.get(
        "/api/family/topics/available",
        params={"elder_user_id": "elder_001", "child_user_id": "child_001"},
    )

    assert response.status_code == 200
    assert available.json()["data"][0]["topic_id"] == "topic_001"
    assert consumed.status_code == 200
    assert consumed.json()["data"]["status"] == "exhausted"
    assert after.json()["data"] == []


def test_family_message_api_hides_content_until_elder_accepts(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)

    created = client.post(
        "/api/family/messages",
        json={
            "elder_user_id": "elder_001",
            "child_user_id": "child_001",
            "title": "daughter",
            "content": "Mom, I will call you tonight.",
            "priority": "normal",
        },
    )
    message_id = created.json()["data"]["id"]
    pending = client.get("/api/elder/pending_messages", params={"elder_user_id": "elder_001"})
    accepted = client.post(
        f"/api/elder/messages/{message_id}/consent",
        json={
            "elder_user_id": "elder_001",
            "consent": "accepted",
            "source": "button",
        },
    )
    after = client.get("/api/elder/pending_messages", params={"elder_user_id": "elder_001"})

    assert created.status_code == 200
    assert "content" not in created.json()["data"]
    assert pending.status_code == 200
    assert pending.json()["data"]["messages"][0]["id"] == message_id
    assert "content" not in pending.json()["data"]["messages"][0]
    assert accepted.status_code == 200
    assert accepted.json()["data"]["content"] == "Mom, I will call you tonight."
    assert accepted.json()["data"]["message"]["status"] == "acknowledged"
    assert after.json()["data"]["messages"] == []


def test_elder_rejection_api_does_not_return_quiet_message_content(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/family/messages",
        json={
            "elder_user_id": "elder_001",
            "child_user_id": "child_001",
            "content": "Private family content",
        },
    )
    message_id = created.json()["data"]["id"]

    rejected = client.post(
        f"/api/elder/messages/{message_id}/consent",
        json={
            "elder_user_id": "elder_001",
            "consent": "rejected",
            "source": "button",
            "raw_text": "not now",
        },
    )

    assert rejected.status_code == 200
    assert rejected.json()["data"]["status"] == "rejected"
    assert rejected.json()["data"]["content"] is None
    assert "content" not in rejected.json()["data"]["message"]


def test_pending_messages_api_suppresses_quiet_prompts_during_high_risk(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    client.post(
        "/api/family/messages",
        json={
            "elder_user_id": "elder_001",
            "child_user_id": "child_001",
            "content": "A quiet family message",
        },
    )

    response = client.get(
        "/api/elder/pending_messages",
        params={"elder_user_id": "elder_001", "risk_tier": "crisis"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["messages"] == []
