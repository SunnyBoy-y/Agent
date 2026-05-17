import json
import tempfile
from datetime import datetime, timezone

from src.schemas.relay import RelayAck
from src.services.assessment_service import AssessmentService
from src.services.data_store import DataStore
from src.services.relay_message_service import RelayMessageService


def _services():
    temp_dir = tempfile.TemporaryDirectory()
    store = DataStore(temp_dir.name)
    return temp_dir, AssessmentService(store), RelayMessageService(store)


def test_crisis_assessment_generates_family_and_community_alerts():
    temp_dir, assessment_service, relay_service = _services()
    with temp_dir:
        assessment = assessment_service.assess_text(
            "我不想活了",
            {"user_id": "elder_001", "turn_id": "turn_001"},
        )

        messages = relay_service.create_from_assessment(assessment)

        assert [message.target for message in messages] == ["family", "community"]
        family = next(message for message in messages if message.target == "family")
        community = next(message for message in messages if message.target == "community")
        assert family.display_type == "alert"
        assert family.raw_quotes == ["不想活了"]
        assert community.display_type == "sos"
        assert community.raw_quotes == []
        assert community.suggested_actions


def test_community_alert_does_not_leak_raw_quote_anywhere():
    temp_dir, assessment_service, relay_service = _services()
    with temp_dir:
        assessment = assessment_service.assess_text(
            "我不想活了",
            {"user_id": "elder_001", "turn_id": "turn_001"},
        )

        community = next(
            message
            for message in relay_service.create_from_assessment(assessment)
            if message.target == "community"
        )
        if hasattr(community, "model_dump"):
            serialized = json.dumps(community.model_dump(mode="json"), ensure_ascii=False)
        else:
            serialized = json.dumps(community.dict(), ensure_ascii=False, default=str)

        assert "不想活了" not in serialized
        assert "raw_quote_visible" in serialized


def test_medium_risk_generates_family_alert_only():
    temp_dir, assessment_service, relay_service = _services()
    with temp_dir:
        assessment = assessment_service.assess_text(
            "我最近焦虑，心慌，也睡不着",
            {"user_id": "elder_001", "turn_id": "turn_002"},
        )

        messages = relay_service.create_from_assessment(assessment)

        assert assessment.risk_tier == "medium"
        assert len(messages) == 1
        assert messages[0].target == "family"
        assert relay_service.get_pending("elder_001", target="community") == []


def test_assessment_relay_creation_is_idempotent():
    temp_dir, assessment_service, relay_service = _services()
    with temp_dir:
        assessment = assessment_service.assess_text(
            "我不想活了",
            {"user_id": "elder_001", "turn_id": "turn_001"},
        )

        first = relay_service.create_from_assessment(assessment)
        second = relay_service.create_from_assessment(assessment)

        assert [message.id for message in first] == [message.id for message in second]
        assert len(relay_service.list_messages("elder_001")) == 2


def test_quiet_message_preserves_actor_role_and_direction():
    temp_dir, _, relay_service = _services()
    with temp_dir:
        message = relay_service.create_quiet_message(
            "elder_001",
            "叮咚，女儿在晚上七点给您留了一句话。",
            title="女儿留言",
            actor_role="family_child",
            direction="family_to_elder",
        )

        pending = relay_service.get_pending("elder_001", target="elder")

        assert message.actor_role == "family_child"
        assert message.direction == "family_to_elder"
        assert pending[0].id == message.id
        assert pending[0].display_type == "quiet_message"


def test_acknowledge_updates_message_status_and_audit_payload():
    temp_dir, _, relay_service = _services()
    with temp_dir:
        message = relay_service.create_quiet_message(
            "elder_001",
            "要不要我为您读出来？",
        )

        updated = relay_service.acknowledge(
            RelayAck(
                elder_user_id="elder_001",
                message_id=message.id,
                actor_role="frontend",
                status="acknowledged",
                text="read_later",
                updated_at=datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc),
            )
        )

        assert updated.status == "acknowledged"
        assert relay_service.get_pending("elder_001") == []
        assert updated.payload["ack_history"][0]["actor_role"] == "frontend"
        assert updated.payload["ack_history"][0]["text"] == "read_later"
