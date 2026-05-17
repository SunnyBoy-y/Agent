import tempfile
from datetime import datetime, timedelta, timezone

from src.schemas.timed_events import MedicationPlan, ScheduleEntry, TimedEventAck
from src.services.data_store import DataStore
from src.services.medication_reminder_service import MedicationReminderService


TZ = timezone(timedelta(hours=8))


def _service():
    temp_dir = tempfile.TemporaryDirectory()
    service = MedicationReminderService(DataStore(temp_dir.name))
    return temp_dir, service


def _plan(dosage_text="one tablet"):
    return MedicationPlan(
        medication_id="med_001",
        elder_user_id="elder_001",
        name="recorded medicine",
        dosage_text=dosage_text,
        instruction_text="after breakfast",
        schedule=[ScheduleEntry(time="08:00", label="breakfast")],
        window_after_minutes=30,
        overdue_after_minutes=30,
        expire_after_minutes=180,
    )


def test_due_window_returns_single_medication_due_event():
    temp_dir, service = _service()
    with temp_dir:
        service.upsert_plan(_plan())

        reminders = service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 8, 0, tzinfo=TZ))
        duplicate = service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 8, 1, tzinfo=TZ))

        assert len(reminders) == 1
        assert reminders[0].event_type == "medication_due"
        assert reminders[0].payload["dose_status"] == "due"
        assert "recorded medicine" in reminders[0].payload["content"]
        assert "one tablet" in reminders[0].payload["content"]
        assert duplicate == []


def test_overdue_returns_once_after_due_window():
    temp_dir, service = _service()
    with temp_dir:
        service.upsert_plan(_plan())
        service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 8, 0, tzinfo=TZ))

        overdue = service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 8, 31, tzinfo=TZ))
        duplicate = service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 8, 32, tzinfo=TZ))

        assert len(overdue) == 1
        assert overdue[0].event_type == "medication_overdue"
        assert overdue[0].priority == "high"
        assert duplicate == []


def test_expired_event_is_marked_missed_and_not_returned():
    temp_dir, service = _service()
    with temp_dir:
        service.upsert_plan(_plan())

        reminders = service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 11, 1, tzinfo=TZ))
        dose_events = service.store.read_user_json("elder_001", "medication_dose_events.json")

        assert reminders == []
        assert dose_events[0]["status"] == "expired"
        assert dose_events[0]["ack"] == "missed"


def test_taken_ack_stops_future_reminders():
    temp_dir, service = _service()
    with temp_dir:
        service.upsert_plan(_plan())
        due = service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 8, 0, tzinfo=TZ))[0]
        dose_id = due.payload["dose_event_id"]

        service.acknowledge(
            "elder_001",
            dose_id,
            TimedEventAck(elder_user_id="elder_001", ack="taken"),
            now=datetime(2026, 5, 16, 8, 5, tzinfo=TZ),
        )
        overdue = service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 8, 31, tzinfo=TZ))

        assert overdue == []


def test_snooze_delays_next_reminder():
    temp_dir, service = _service()
    with temp_dir:
        service.upsert_plan(_plan())
        due = service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 8, 0, tzinfo=TZ))[0]
        dose_id = due.payload["dose_event_id"]
        service.acknowledge(
            "elder_001",
            dose_id,
            TimedEventAck(elder_user_id="elder_001", ack="snooze", snooze_minutes=10),
            now=datetime(2026, 5, 16, 8, 5, tzinfo=TZ),
        )

        before = service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 8, 14, tzinfo=TZ))
        after = service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 8, 15, tzinfo=TZ))

        assert before == []
        assert len(after) == 1
        assert after[0].payload["dose_status"] == "due"


def test_missing_dosage_does_not_invent_dosage_or_forbidden_advice():
    temp_dir, service = _service()
    with temp_dir:
        service.upsert_plan(_plan(dosage_text=None))

        reminder = service.scan_due_reminders("elder_001", datetime(2026, 5, 16, 8, 0, tzinfo=TZ))[0]
        content = reminder.payload["content"]

        assert "one tablet" not in content
        assert "\u6ca1\u6709\u770b\u5230\u5177\u4f53\u5242\u91cf" in content
        for forbidden in ["\u8865\u670d", "\u52a0\u91cf", "\u51cf\u91cf", "\u505c\u836f", "\u6362\u836f", "\u53bb\u533b\u9662", "\u770b\u533b\u751f"]:
            assert forbidden not in content
