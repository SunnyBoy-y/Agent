from datetime import datetime, timedelta, timezone

import pytest

from src.schemas.family import FamilyMessageCreateRequest, QuietMessageConsentRequest
from src.services.data_store import DataStore
from src.services.family_policy_service import FamilyPolicyService
from src.services.relay_message_service import RelayMessageService


def _service(tmp_path):
    store = DataStore(tmp_path)
    relay = RelayMessageService(store)
    return FamilyPolicyService(store, relay)


def test_suggested_topic_respects_max_consumptions_and_interval(tmp_path):
    service = _service(tmp_path)
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    service.update_policy_from_payload(
        "elder_001",
        "child_001",
        {
            "preferred_tone": "warm and slow",
            "suggested_topics": [
                {
                    "id": "topic_001",
                    "title": "granddaughter update",
                    "prompt_hint": "mention the granddaughter's school progress gently",
                    "max_consumptions": 2,
                    "min_interval_hours": 24,
                }
            ],
            "long_term_goals": ["increase family connection"],
        },
    )

    first_available = service.available_topics("elder_001", "child_001", now=now)
    first_consumed = service.consume_topic("elder_001", "child_001", "topic_001", now=now)
    blocked = service.available_topics("elder_001", "child_001", now=now + timedelta(hours=2))
    second_consumed = service.consume_topic(
        "elder_001",
        "child_001",
        "topic_001",
        now=now + timedelta(hours=25),
    )

    assert first_available[0].topic_id == "topic_001"
    assert first_available[0].content == "mention the granddaughter's school progress gently"
    assert first_consumed.consumed_count == 1
    assert blocked == []
    assert second_consumed.consumed_count == 2
    assert second_consumed.status == "exhausted"
    assert service.available_topics("elder_001", "child_001", now=now + timedelta(hours=50)) == []


def test_unavailable_topic_cannot_be_consumed_twice_inside_interval(tmp_path):
    service = _service(tmp_path)
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    service.update_policy_from_payload(
        "elder_001",
        "child_001",
        {
            "suggested_topics": [
                {
                    "topic_id": "topic_001",
                    "title": "family call",
                    "max_consumptions": 3,
                    "min_interval_hours": 24,
                }
            ]
        },
    )

    service.consume_topic("elder_001", "child_001", "topic_001", now=now)

    with pytest.raises(ValueError):
        service.consume_topic("elder_001", "child_001", "topic_001", now=now + timedelta(hours=1))


def test_pending_quiet_message_metadata_hides_content_until_consent(tmp_path):
    service = _service(tmp_path)
    message = service.create_quiet_message(
        FamilyMessageCreateRequest(
            elder_user_id="elder_001",
            child_user_id="child_001",
            title="daughter",
            content="Mom, the weather is colder today. Please wear one more layer.",
            priority="normal",
        )
    )

    prompts = service.pending_quiet_message_prompts("elder_001", risk_tier="safe")

    assert prompts == [
        {
            "id": message.id,
            "from_display": "daughter",
            "message_type": "quiet_message",
            "prompt_text": "家人有句话想跟您说，您要不要听？",
            "status": "pending",
            "priority": "normal",
            "created_at": message.created_at.isoformat(),
        }
    ]
    assert "content" not in prompts[0]


def test_quiet_message_is_not_prompted_during_high_risk(tmp_path):
    service = _service(tmp_path)
    service.create_quiet_message(
        FamilyMessageCreateRequest(
            elder_user_id="elder_001",
            child_user_id="child_001",
            content="A quiet family message",
            priority="normal",
        )
    )

    assert service.pending_quiet_message_prompts("elder_001", risk_tier="high") == []
    assert service.pending_quiet_message_prompts("elder_001", risk_tier="crisis") == []


def test_accepting_quiet_message_reveals_content_and_is_idempotent(tmp_path):
    service = _service(tmp_path)
    message = service.create_quiet_message(
        FamilyMessageCreateRequest(
            elder_user_id="elder_001",
            child_user_id="child_001",
            content="I will call you tonight.",
        )
    )
    request = QuietMessageConsentRequest(
        elder_user_id="elder_001",
        consent="accepted",
        source="button",
    )

    first = service.consent_to_quiet_message(message.id, request)
    second = service.consent_to_quiet_message(message.id, request)

    assert first["status"] == "accepted"
    assert first["content"] == "I will call you tonight."
    assert first["message"].status == "acknowledged"
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert service.pending_quiet_message_prompts("elder_001") == []


def test_rejecting_quiet_message_never_reveals_content(tmp_path):
    service = _service(tmp_path)
    message = service.create_quiet_message(
        FamilyMessageCreateRequest(
            elder_user_id="elder_001",
            child_user_id="child_001",
            content="Private family text",
        )
    )

    result = service.consent_to_quiet_message(
        message.id,
        QuietMessageConsentRequest(
            elder_user_id="elder_001",
            consent="rejected",
            source="button",
            raw_text="not now",
        ),
    )

    assert result["status"] == "rejected"
    assert result["content"] is None
    assert result["message"].status == "cancelled"
    assert service.pending_quiet_message_prompts("elder_001") == []


def test_semantic_consent_accepts_clear_yes_text(tmp_path):
    service = _service(tmp_path)
    message = service.create_quiet_message(
        FamilyMessageCreateRequest(
            elder_user_id="elder_001",
            child_user_id="child_001",
            content="Dinner went well today.",
        )
    )

    result = service.consent_to_quiet_message(
        message.id,
        QuietMessageConsentRequest(
            elder_user_id="elder_001",
            source="semantic",
            raw_text="可以，读吧",
        ),
    )

    assert result["status"] == "accepted"
    assert result["content"] == "Dinner went well today."
