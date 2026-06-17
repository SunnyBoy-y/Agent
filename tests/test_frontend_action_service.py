from datetime import datetime, timedelta, timezone

from src.schemas.timed_events import TimedEvent
from src.services.frontend_action_service import FrontendActionService


TZ = timezone(timedelta(hours=8))


def test_weather_action_uses_backend_contract_without_fabricating_unknown_fields():
    service = FrontendActionService()

    action = service.build_weather_action(
        source_turn_id="turn_weather",
        weather_snapshot={
            "condition": "cloudy",
            "temperature_text": "26°C",
            "humidity_text": "60%",
            "wind_text": "东风 2 级",
            "summary": "今天多云。",
        },
    )

    assert action["name"] == "show_weather"
    assert action["source_turn_id"] == "turn_weather"
    assert action["payload"]["view"]["camera_mode"] == "weather"
    assert action["payload"]["weather"] == {
        "condition": "cloudy",
        "temperature_text": "26°C",
        "humidity_text": "60%",
        "wind_text": "东风 2 级",
        "summary": "今天多云。",
        "tips": "",
    }


def test_timed_event_actions_dedupe_due_and_overdue_for_same_dose():
    service = FrontendActionService()
    due = TimedEvent(
        event_id="dose_001_due",
        elder_user_id="elder_001",
        event_type="medication_due",
        priority="medium",
        scheduled_at=datetime(2026, 5, 18, 8, 0, tzinfo=TZ),
        valid_until=datetime(2026, 5, 18, 11, 0, tzinfo=TZ),
        status="delivered",
        payload={"dose_event_id": "dose_001", "content": "到点吃药"},
    )
    overdue = TimedEvent(
        event_id="dose_001_overdue",
        elder_user_id="elder_001",
        event_type="medication_overdue",
        priority="high",
        scheduled_at=datetime(2026, 5, 18, 8, 31, tzinfo=TZ),
        valid_until=datetime(2026, 5, 18, 11, 0, tzinfo=TZ),
        status="delivered",
        payload={"dose_event_id": "dose_001", "content": "已经超时"},
    )

    actions = service.build_timed_event_actions([due, overdue])

    assert len(actions) == 1
    assert actions[0]["name"] == "show_medication_reminder"
    assert actions[0]["priority"] == "high"
    assert actions[0]["payload"]["display_text"] == "已经超时"


def test_quiet_message_and_incoming_call_share_same_sortable_contract():
    service = FrontendActionService()
    quiet_action = service.build_quiet_message_prompt_action(
        {
            "id": "msg_001",
            "from_display": "女儿",
            "prompt_text": "女儿有句话想和您说。",
            "priority": "normal",
            "created_at": "2026-05-18T08:00:00+08:00",
        }
    )
    call_action = service.build_timed_event_action(
        {
            "event_id": "call_001",
            "event_type": "incoming_call",
            "priority": "high",
            "payload": {"target": "daughter", "display_name": "女儿"},
        }
    )

    actions = service.sort_actions([quiet_action, call_action])

    assert [item["name"] for item in actions] == ["incoming_call", "prompt_quiet_message"]
    assert actions[0]["interrupt_policy"] == "interrupt_lower_priority"
    assert actions[1]["requires_confirmation"] is True
