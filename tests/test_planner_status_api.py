from fastapi.testclient import TestClient

import src.server as server
from src.services.background_planner_service import BackgroundPlannerService
from src.services.care_plan_service import CarePlanService
from src.services.data_store import DataStore


class FakeOrchestrator:
    def __init__(self, root_dir):
        self.data_store = DataStore(root_dir)
        self.care_plan_service = CarePlanService(self.data_store)
        self.background_planner_service = BackgroundPlannerService(
            self.data_store,
            self.care_plan_service,
            safe_low_debounce_seconds=0,
        )


def test_planner_status_endpoint_returns_status_and_care_plan(monkeypatch, tmp_path):
    fake = FakeOrchestrator(tmp_path)
    fake.care_plan_service.update_plan(
        "elder_001",
        {"risk_tier": "medium", "target_agent": "mental_health_agent"},
        "turn_001",
    )
    monkeypatch.setattr(server, "orchestrator", fake)
    client = TestClient(server.app)

    response = client.get("/api/planner/status", params={"elder_user_id": "elder_001"})

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["planner"]["status"] == "idle"
    assert body["care_plan"]["version"] == 1
    assert body["care_plan"]["target_agent"] == "mental_health_agent"
