import json

from fastapi.testclient import TestClient

import src.server as server


class FakeChatOrchestrator:
    def __init__(self):
        self.last_message = None
        self.last_context = None

    async def process_input_stream(self, message, context):
        self.last_message = message
        self.last_context = context
        yield json.dumps(
            {"type": "done", "data": {"user_id": context.get("user_id")}},
            ensure_ascii=False,
        )


def test_chat_rejects_conflicting_user_id_between_body_and_context(monkeypatch):
    fake = FakeChatOrchestrator()
    monkeypatch.setattr(server, "orchestrator", fake)
    client = TestClient(server.app)

    response = client.post(
        "/api/chat",
        json={
            "message": "hello",
            "user_id": "elder_a",
            "context": {"user_id": "elder_b"},
        },
    )

    assert response.status_code == 400
    assert "Conflicting user_id" in response.json()["message"]


def test_chat_uses_top_level_user_id_as_hard_boundary(monkeypatch):
    fake = FakeChatOrchestrator()
    monkeypatch.setattr(server, "orchestrator", fake)
    client = TestClient(server.app)

    response = client.post(
        "/api/chat",
        json={
            "message": "hello",
            "user_id": "elder_a",
            "context": {"turn_id": "turn_001"},
        },
    )

    assert response.status_code == 200
    assert fake.last_context["user_id"] == "elder_a"
    assert '"elder_a"' in response.text
