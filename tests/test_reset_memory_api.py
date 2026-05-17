import asyncio

from fastapi.testclient import TestClient

import src.server as server
from src.orchestrator import SystemOrchestrator
from src.services.data_store import DataStore


class FakeUserContextService:
    def __init__(self, store):
        self.store = store

    def normalize_user_id(self, user_id):
        return str(user_id or "user_001").strip() or "user_001"


class FakePlannerService:
    def __init__(self):
        self.cancelled = []

    async def cancel_user_jobs(self, elder_user_id, reason="cancelled_by_user_state_reset"):
        self.cancelled.append((elder_user_id, reason))
        return {
            "elder_user_id": elder_user_id,
            "cancelled_tasks": 0,
            "active_job_id": None,
            "reason": reason,
        }


class FakeRagHelper:
    def __init__(self):
        self.calls = 0

    def reset_all_memory(self):
        self.calls += 1
        return {"legacy_reset": True}


class FakeEmotionalAgent:
    def __init__(self):
        self.rag_helper = FakeRagHelper()


def test_orchestrator_reset_user_state_cancels_planner_and_resets_only_target_user(tmp_path):
    async def scenario():
        store = DataStore(tmp_path)
        store.write_user_json("elder_001", "profile.json", {"name": "target"})
        store.write_user_json("elder_002", "profile.json", {"name": "other"})

        orchestrator = SystemOrchestrator.__new__(SystemOrchestrator)
        orchestrator.data_store = store
        orchestrator.user_context_service = FakeUserContextService(store)
        orchestrator.background_planner_service = FakePlannerService()
        orchestrator.emotional_agent = FakeEmotionalAgent()
        orchestrator.last_system_state = {
            "last_input": "hello",
            "last_route": "emotional_agent",
            "tool_calls": [{"tool": "x"}],
            "background_tasks": [{"label": "x"}],
            "context_snapshot": {"user_id": "elder_001"},
        }

        result = await SystemOrchestrator.reset_user_state(orchestrator, " elder_001 ")

        assert result["user_id"] == "elder_001"
        assert result["planner"]["reason"] == "cancelled_by_user_state_reset"
        assert orchestrator.background_planner_service.cancelled == [
            ("elder_001", "cancelled_by_user_state_reset")
        ]
        assert result["legacy_rag"]["scope"] == "not_touched"
        assert orchestrator.emotional_agent.rag_helper.calls == 0
        assert not (tmp_path / "users" / "elder_001").exists()
        assert store.read_user_json("elder_002", "profile.json") == {"name": "other"}
        assert orchestrator.last_system_state["context_snapshot"] == {}

    asyncio.run(scenario())


def test_orchestrator_reset_user_state_only_touches_legacy_rag_when_explicit(tmp_path):
    async def scenario():
        store = DataStore(tmp_path)
        store.write_user_json("elder_001", "profile.json", {"name": "target"})

        orchestrator = SystemOrchestrator.__new__(SystemOrchestrator)
        orchestrator.data_store = store
        orchestrator.user_context_service = FakeUserContextService(store)
        orchestrator.background_planner_service = FakePlannerService()
        orchestrator.emotional_agent = FakeEmotionalAgent()
        orchestrator.last_system_state = {}

        result = await SystemOrchestrator.reset_user_state(
            orchestrator,
            "elder_001",
            include_legacy_rag=True,
        )

        assert result["legacy_rag"]["requested"] is True
        assert result["legacy_rag"]["scope"] == "global"
        assert result["legacy_rag"]["result"] == {"legacy_reset": True}
        assert orchestrator.emotional_agent.rag_helper.calls == 1

    asyncio.run(scenario())


class FallbackResetOrchestrator:
    def __init__(self, root_dir):
        self.data_store = DataStore(root_dir)
        self.user_context_service = FakeUserContextService(self.data_store)
        self.emotional_agent = FakeEmotionalAgent()


def test_reset_memory_api_resets_datastore_user_without_global_legacy_rag(monkeypatch, tmp_path):
    fake = FallbackResetOrchestrator(tmp_path)
    fake.data_store.write_user_json("elder_001", "profile.json", {"name": "target"})
    fake.data_store.write_user_json("elder_002", "profile.json", {"name": "other"})
    monkeypatch.setattr(server, "orchestrator", fake)
    client = TestClient(server.app)

    response = client.post("/api/reset_memory", params={"user_id": "elder_001"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["user_id"] == "elder_001"
    assert payload["data"]["legacy_rag"]["scope"] == "not_touched"
    assert fake.emotional_agent.rag_helper.calls == 0
    assert not (tmp_path / "users" / "elder_001").exists()
    assert fake.data_store.read_user_json("elder_002", "profile.json") == {"name": "other"}


def test_reset_memory_api_can_explicitly_reset_global_legacy_rag(monkeypatch, tmp_path):
    fake = FallbackResetOrchestrator(tmp_path)
    monkeypatch.setattr(server, "orchestrator", fake)
    client = TestClient(server.app)

    response = client.post(
        "/api/reset_memory",
        params={"user_id": "elder_001", "include_legacy_rag": True},
    )

    assert response.status_code == 200
    assert response.json()["data"]["legacy_rag"]["scope"] == "global"
    assert fake.emotional_agent.rag_helper.calls == 1
