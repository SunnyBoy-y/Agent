import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import src.server as server
from src.services.assessment_service import AssessmentService
from src.services.community_service import CommunityService
from src.services.data_store import DataStore
from src.services.relay_message_service import RelayMessageService


class FakeOrchestrator:
    def __init__(self, root_dir):
        self.data_store = DataStore(root_dir)
        self.relay_message_service = RelayMessageService(self.data_store)
        self.community_service = CommunityService(
            self.data_store,
            self.relay_message_service,
        )
        self.assessment_service = AssessmentService(self.data_store)

    def create_community_announcement(self, request, now=None):
        return self.community_service.create_announcement(request, now=now)

    def list_community_announcements(self, community_id, *, only_active=True, now=None, limit=None):
        return self.community_service.list_announcements(
            community_id,
            only_active=only_active,
            now=now,
            limit=limit,
        )

    def create_community_activity(self, request, now=None):
        return self.community_service.create_activity(request, now=now)

    def list_community_activities(self, community_id, *, only_active=True, now=None, limit=None):
        return self.community_service.list_activities(
            community_id,
            only_active=only_active,
            now=now,
            limit=limit,
        )

    def list_community_crisis_alerts(self, elder_user_id, *, limit=20):
        return self.community_service.list_crisis_alerts(elder_user_id, limit=limit)


def _client(monkeypatch, tmp_path):
    fake = FakeOrchestrator(tmp_path)
    monkeypatch.setattr(server, "orchestrator", fake)
    return TestClient(server.app), fake


def test_community_announcements_api_returns_only_active_items(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    client.post(
        "/api/community/announcements",
        params={"now": now.isoformat()},
        json={
            "community_id": "community_001",
            "id": "active_notice",
            "title": "Water notice",
            "content": "Water maintenance from 9 to 11.",
            "valid_until": (now + timedelta(hours=1)).isoformat(),
            "priority": 1,
        },
    )
    client.post(
        "/api/community/announcements",
        params={"now": now.isoformat()},
        json={
            "community_id": "community_001",
            "id": "expired_notice",
            "title": "Old notice",
            "content": "Already expired.",
            "valid_until": (now - timedelta(minutes=1)).isoformat(),
            "priority": 99,
        },
    )

    active = client.get(
        "/api/community/announcements",
        params={"community_id": "community_001", "now": now.isoformat()},
    )
    all_items = client.get(
        "/api/community/announcements",
        params={
            "community_id": "community_001",
            "only_active": False,
            "now": now.isoformat(),
        },
    )

    assert active.status_code == 200
    assert [item["id"] for item in active.json()["data"]] == ["active_notice"]
    assert {item["id"]: item["status"] for item in all_items.json()["data"]}["expired_notice"] == "expired"


def test_community_activities_api_filters_expired_by_valid_until(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    active = client.post(
        "/api/community/activities",
        params={"now": now.isoformat()},
        json={
            "community_id": "community_001",
            "id": "choir",
            "title": "Choir activity",
            "content": "Gentle group singing.",
            "time_text": "10 AM",
            "location": "Activity room",
            "tags": ["music", "social"],
            "valid_until": (now + timedelta(hours=1)).isoformat(),
            "priority": 3,
        },
    )
    expired = client.post(
        "/api/community/activities",
        params={"now": now.isoformat()},
        json={
            "community_id": "community_001",
            "id": "expired",
            "title": "Expired activity",
            "valid_until": (now - timedelta(minutes=1)).isoformat(),
            "priority": 9,
        },
    )
    listed = client.get(
        "/api/community/activities",
        params={"community_id": "community_001", "now": now.isoformat()},
    )

    assert active.status_code == 200
    assert expired.status_code == 200
    assert expired.json()["data"]["status"] == "expired"
    assert [item["id"] for item in listed.json()["data"]] == ["choir"]


def test_community_crisis_alerts_api_sanitizes_elder_raw_quote(monkeypatch, tmp_path):
    client, fake = _client(monkeypatch, tmp_path)
    raw_text = "\u6211\u4e0d\u60f3\u6d3b\u4e86"
    assessment = fake.assessment_service.assess_text(
        raw_text,
        {"user_id": "elder_001", "turn_id": "turn_001"},
    )
    fake.relay_message_service.create_from_assessment(assessment)

    response = client.get(
        "/api/community/crisis_alerts",
        params={"elder_user_id": "elder_001"},
    )
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert response.status_code == 200
    assert len(payload["data"]["alerts"]) == 1
    assert payload["data"]["alerts"][0]["raw_quotes"] == []
    assert payload["data"]["alerts"][0]["payload"]["raw_quote_visible"] is False
    assert raw_text not in serialized
