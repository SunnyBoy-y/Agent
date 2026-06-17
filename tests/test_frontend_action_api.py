from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import src.server as server
from src.schemas.family import FamilyMessageCreateRequest
from src.schemas.timed_events import TimedEvent
from src.services.data_store import DataStore
from src.services.family_policy_service import FamilyPolicyService
from src.services.frontend_action_service import FrontendActionService
from src.services.medication_reminder_service import MedicationReminderService
from src.services.relay_message_service import RelayMessageService
from src.services.timed_event_service import TimedEventService


TZ = timezone(timedelta(hours=8))


class FakeOrchestrator:
    def __init__(self, root_dir):
        self.data_store = DataStore(root_dir)
        self.timed_event_service = TimedEventService(self.data_store)
        self.medication_reminder_service = MedicationReminderService(
            self.data_store,
            self.timed_event_service,
        )
        self.relay_message_service = RelayMessageService(self.data_store)
        self.family_policy_service = FamilyPolicyService(
            self.data_store,
            self.relay_message_service,
        )
        self.frontend_action_service = FrontendActionService()

    def get_due_timed_events(self, user_id, now=None):
        self.medication_reminder_service.scan_due_reminders(user_id, now=now)
        return self.timed_event_service.get_due_events(user_id, now=now)

    def list_pending_frontend_actions(self, elder_user_id, *, risk_tier="safe", now=None):
        timed_actions = self.frontend_action_service.build_timed_event_actions(
            self.get_due_timed_events(elder_user_id, now=now)
        )
        quiet_actions = [
            self.frontend_action_service.build_quiet_message_prompt_action(prompt)
            for prompt in self.family_policy_service.pending_quiet_message_prompts(
                elder_user_id,
                risk_tier=risk_tier,
            )
        ]
        return self.frontend_action_service.sort_actions([*timed_actions, *quiet_actions])


def _client(monkeypatch, tmp_path):
    fake = FakeOrchestrator(tmp_path)
    monkeypatch.setattr(server, "orchestrator", fake)
    return TestClient(server.app), fake


def test_frontend_action_endpoint_prioritizes_high_interruptions(monkeypatch, tmp_path):
    client, fake = _client(monkeypatch, tmp_path)
    fake.family_policy_service.create_quiet_message(
        FamilyMessageCreateRequest(
            elder_user_id="elder_001",
            child_user_id="child_001",
            content="今晚给您打电话。",
            title="女儿",
        )
    )
    fake.timed_event_service.upsert_event(
        TimedEvent(
            event_id="call_001",
            elder_user_id="elder_001",
            event_type="incoming_call",
            priority="high",
            scheduled_at=datetime(2026, 5, 18, 8, 0, tzinfo=TZ),
            valid_until=datetime(2026, 5, 18, 8, 5, tzinfo=TZ),
            status="delivered",
            payload={"target": "daughter", "display_name": "女儿"},
        )
    )

    response = client.get(
        "/api/frontend/actions/pending",
        params={
            "elder_user_id": "elder_001",
            "now": datetime(2026, 5, 18, 8, 1, tzinfo=TZ).isoformat(),
        },
    )

    assert response.status_code == 200
    actions = response.json()["data"]
    assert [item["name"] for item in actions] == ["incoming_call", "prompt_quiet_message"]
    assert actions[0]["interrupt_policy"] == "interrupt_lower_priority"


def test_frontend_action_endpoint_suppresses_quiet_messages_during_crisis(monkeypatch, tmp_path):
    client, fake = _client(monkeypatch, tmp_path)
    fake.family_policy_service.create_quiet_message(
        FamilyMessageCreateRequest(
            elder_user_id="elder_001",
            child_user_id="child_001",
            content="平安到家告诉我。",
            title="儿子",
        )
    )

    response = client.get(
        "/api/frontend/actions/pending",
        params={"elder_user_id": "elder_001", "risk_tier": "crisis"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == []
