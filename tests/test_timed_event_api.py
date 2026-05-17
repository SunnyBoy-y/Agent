import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import src.server as server
from src.schemas.timed_events import MedicationPlan, ScheduleEntry
from src.services.data_store import DataStore
from src.services.medication_reminder_service import MedicationReminderService
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

    def get_due_timed_events(self, user_id, now=None):
        self.medication_reminder_service.scan_due_reminders(user_id, now=now)
        return self.timed_event_service.get_due_events(user_id, now=now)

    def acknowledge_timed_event(self, event_id, ack, now=None):
        events = self.timed_event_service.list_events(ack.elder_user_id)
        matched = next(event for event in events if event.event_id == event_id)
        dose_event_id = matched.payload["dose_event_id"]
        dose_event = self.medication_reminder_service.acknowledge(
            ack.elder_user_id,
            dose_event_id,
            ack,
            now=now,
        )
        status = "snoozed" if ack.ack == "snooze" else "acknowledged"
        updated = self.timed_event_service.mark_events_by_payload(
            ack.elder_user_id,
            "dose_event_id",
            dose_event_id,
            status,
            now=now,
        )
        return {
            "event_id": event_id,
            "ack": ack.ack,
            "timed_events": [self.format_timed_event_response(event) for event in updated],
            "dose_event": self._model_to_dict(dose_event),
        }

    async def check_and_generate_proactive_event(self, user_id="user_001", now=None):
        events = self.get_due_timed_events(user_id, now=now)
        if not events:
            return None
        return json.dumps(
            {"type": "timed_event", "data": self.format_timed_event_response(events[0])},
            ensure_ascii=False,
        )

    def format_timed_event_response(self, event):
        data = self._model_to_dict(event)
        data["display_text"] = (data.get("payload") or {}).get("content", "")
        return data

    def _model_to_dict(self, model):
        if hasattr(model, "model_dump"):
            return model.model_dump(mode="json")
        if hasattr(model, "dict"):
            return model.dict()
        return dict(model or {})


def _client(monkeypatch, tmp_path):
    fake = FakeOrchestrator(tmp_path)
    monkeypatch.setattr(server, "orchestrator", fake)
    return TestClient(server.app), fake


def _plan_payload():
    return {
        "medication_id": "med_001",
        "elder_user_id": "elder_001",
        "name": "recorded medicine",
        "dosage_text": "one tablet",
        "instruction_text": "after breakfast",
        "schedule": [{"time": "08:00", "label": "breakfast"}],
        "window_after_minutes": 30,
        "overdue_after_minutes": 30,
        "expire_after_minutes": 180,
    }


def test_create_and_list_medication_plans(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)

    created = client.post("/api/medication/plans", json=_plan_payload())
    listed = client.get("/api/medication/plans", params={"elder_user_id": "elder_001"})

    assert created.status_code == 200
    assert created.json()["data"]["medication_id"] == "med_001"
    assert listed.status_code == 200
    assert listed.json()["data"][0]["name"] == "recorded medicine"


def test_due_timed_event_endpoint_returns_display_text(monkeypatch, tmp_path):
    client, fake = _client(monkeypatch, tmp_path)
    fake.medication_reminder_service.upsert_plan(MedicationPlan(**_plan_payload()))

    response = client.get(
        "/api/timed_events/due",
        params={"elder_user_id": "elder_001", "now": "2026-05-16T08:00:00+08:00"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["event_type"] == "medication_due"
    assert "recorded medicine" in data[0]["display_text"]


def test_ack_taken_stops_future_timed_reminders(monkeypatch, tmp_path):
    client, fake = _client(monkeypatch, tmp_path)
    fake.medication_reminder_service.upsert_plan(MedicationPlan(**_plan_payload()))
    due_response = client.get(
        "/api/timed_events/due",
        params={"elder_user_id": "elder_001", "now": "2026-05-16T08:00:00+08:00"},
    )
    event_id = due_response.json()["data"][0]["event_id"]

    ack_response = client.post(
        f"/api/timed_events/{event_id}/ack",
        params={"now": "2026-05-16T08:05:00+08:00"},
        json={"elder_user_id": "elder_001", "ack": "taken"},
    )
    after_ack = client.get(
        "/api/timed_events/due",
        params={"elder_user_id": "elder_001", "now": "2026-05-16T08:31:00+08:00"},
    )

    assert ack_response.status_code == 200
    assert ack_response.json()["data"]["ack"] == "taken"
    assert after_ack.json()["data"] == []


def test_proactive_check_prioritizes_timed_event(monkeypatch, tmp_path):
    client, fake = _client(monkeypatch, tmp_path)
    fake.medication_reminder_service.upsert_plan(
        MedicationPlan(
            medication_id="med_001",
            elder_user_id="elder_001",
            name="recorded medicine",
            dosage_text="one tablet",
            instruction_text="after breakfast",
            schedule=[ScheduleEntry(time="08:00", label="breakfast")],
        )
    )

    response = client.get(
        "/api/proactive_check",
        params={"user_id": "elder_001", "now": datetime(2026, 5, 16, 8, 0, tzinfo=TZ).isoformat()},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "timed_event"
    assert body["data"]["event_type"] == "medication_due"
