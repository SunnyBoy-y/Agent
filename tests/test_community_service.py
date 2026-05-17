import json
from datetime import datetime, timedelta, timezone

from src.schemas.community import (
    CommunityActivityCreateRequest,
    CommunityAnnouncementCreateRequest,
)
from src.services.assessment_service import AssessmentService
from src.services.community_service import CommunityService
from src.services.data_store import DataStore
from src.services.relay_message_service import RelayMessageService


def _services(tmp_path):
    store = DataStore(tmp_path)
    relay = RelayMessageService(store)
    return CommunityService(store, relay), relay, AssessmentService(store)


def test_announcements_filter_valid_window_and_keep_community_isolation(tmp_path):
    service, _, _ = _services(tmp_path)
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    service.create_announcement(
        CommunityAnnouncementCreateRequest(
            community_id="community_001",
            id="active_notice",
            title="Water notice",
            content="Water maintenance from 9 to 11.",
            tags=["notice", "water"],
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(hours=2),
            priority=2,
        ),
        now=now,
    )
    service.create_announcement(
        CommunityAnnouncementCreateRequest(
            community_id="community_001",
            id="expired_notice",
            title="Old notice",
            content="Already expired.",
            valid_until=now - timedelta(minutes=1),
            priority=10,
        ),
        now=now,
    )
    service.create_announcement(
        CommunityAnnouncementCreateRequest(
            community_id="community_001",
            id="future_notice",
            title="Future notice",
            content="Not visible yet.",
            valid_from=now + timedelta(days=1),
            priority=20,
        ),
        now=now,
    )
    service.create_announcement(
        CommunityAnnouncementCreateRequest(
            community_id="community_002",
            id="other_notice",
            title="Other community",
            content="This must not cross community boundaries.",
        ),
        now=now,
    )

    active = service.list_announcements("community_001", now=now)
    all_items = service.list_announcements("community_001", only_active=False, now=now)
    other = service.list_announcements("community_002", now=now)

    assert [item.id for item in active] == ["active_notice"]
    assert {item.id: item.status for item in all_items}["expired_notice"] == "expired"
    assert [item.id for item in other] == ["other_notice"]


def test_activities_filter_expired_items_and_sort_by_priority(tmp_path):
    service, _, _ = _services(tmp_path)
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    service.create_activity(
        CommunityActivityCreateRequest(
            community_id="community_001",
            id="low_priority",
            title="Morning walk",
            content="Slow walk in the garden.",
            time_text="9 AM",
            location="Garden",
            tags=["low_intensity"],
            valid_until=now + timedelta(hours=2),
            priority=1,
        ),
        now=now,
    )
    service.create_activity(
        CommunityActivityCreateRequest(
            community_id="community_001",
            id="high_priority",
            title="Choir activity",
            content="A gentle group singing activity.",
            time_text="10 AM",
            location="Activity room",
            tags=["music", "social"],
            valid_until=now + timedelta(hours=1),
            priority=5,
        ),
        now=now,
    )
    service.create_activity(
        CommunityActivityCreateRequest(
            community_id="community_001",
            id="expired_activity",
            title="Expired activity",
            valid_until=now - timedelta(minutes=1),
            priority=99,
        ),
        now=now,
    )

    active = service.list_activities("community_001", now=now)
    all_items = service.list_activities("community_001", only_active=False, now=now)

    assert [item.id for item in active] == ["high_priority", "low_priority"]
    assert {item.id: item.status for item in all_items}["expired_activity"] == "expired"
    assert all(item.id != "expired_activity" for item in active)


def test_community_crisis_alerts_never_expose_raw_quote(tmp_path):
    service, relay, assessment_service = _services(tmp_path)
    raw_text = "\u6211\u4e0d\u60f3\u6d3b\u4e86"
    assessment = assessment_service.assess_text(
        raw_text,
        {"user_id": "elder_001", "turn_id": "turn_001"},
    )
    relay.create_from_assessment(assessment)

    alerts = service.list_crisis_alerts("elder_001")
    serialized = json.dumps(alerts, ensure_ascii=False)

    assert len(alerts) == 1
    assert alerts[0]["target"] == "community"
    assert alerts[0]["raw_quotes"] == []
    assert alerts[0]["payload"]["raw_quote_visible"] is False
    assert raw_text not in serialized


def test_only_crisis_level_messages_are_visible_to_community_crisis_alerts(tmp_path):
    service, relay, assessment_service = _services(tmp_path)
    medium = assessment_service.assess_text(
        "\u7126\u8651\uff0c\u5fc3\u614c\uff0c\u7761\u4e0d\u7740",
        {"user_id": "elder_001", "turn_id": "turn_medium"},
    )
    crisis = assessment_service.assess_text(
        "\u6211\u4e0d\u60f3\u6d3b\u4e86",
        {"user_id": "elder_001", "turn_id": "turn_crisis"},
    )
    relay.create_from_assessment(medium)
    relay.create_from_assessment(crisis)

    alerts = service.list_crisis_alerts("elder_001")

    assert len(alerts) == 1
    assert alerts[0]["risk_tier"] == "crisis"
