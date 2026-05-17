from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.schemas.timed_events import (
    MedicationDoseEvent,
    MedicationPlan,
    ScheduleEntry,
    TimedEvent,
    TimedEventAck,
    utc_now,
)
from src.services.data_store import DataStore
from src.services.timed_event_service import TimedEventService


class MedicationReminderService:
    """Deterministic medication plan scanner with no LLM-generated dosage."""

    PLANS_FILE = "medication_plans.json"
    DOSE_EVENTS_FILE = "medication_dose_events.json"
    DOSE_AUDIT_FILE = "medication_dose_events.jsonl"

    def __init__(
        self,
        store: Optional[DataStore] = None,
        timed_event_service: Optional[TimedEventService] = None,
    ):
        self.store = store or DataStore()
        self.timed_event_service = timed_event_service or TimedEventService(self.store)

    def upsert_plan(self, plan: MedicationPlan) -> MedicationPlan:
        plans = self.list_plans(plan.elder_user_id, include_inactive=True)
        replaced = False
        plan.updated_at = utc_now()
        for idx, existing in enumerate(plans):
            if existing.medication_id == plan.medication_id:
                plans[idx] = plan
                replaced = True
                break
        if not replaced:
            plans.append(plan)
        self.store.write_user_json(plan.elder_user_id, self.PLANS_FILE, plans)
        return plan

    def list_plans(self, elder_user_id: str, include_inactive: bool = False) -> List[MedicationPlan]:
        raw_plans = self.store.read_user_json(elder_user_id, self.PLANS_FILE, default=[])
        if not isinstance(raw_plans, list):
            return []
        plans = [self._parse_plan(item) for item in raw_plans if isinstance(item, dict)]
        if include_inactive:
            return plans
        return [plan for plan in plans if plan.status == "active"]

    def scan_due_reminders(
        self,
        elder_user_id: str,
        now: Optional[datetime] = None,
    ) -> List[TimedEvent]:
        now = self._normalize_datetime(now or utc_now())
        plans = self.list_plans(elder_user_id)
        dose_events = self._load_dose_events(elder_user_id)
        dose_by_id = {event.event_id: event for event in dose_events}

        for plan in plans:
            if not self._plan_active_on(plan, now):
                continue
            for schedule in plan.schedule:
                event = self._ensure_dose_event(plan, schedule, now, dose_by_id)
                dose_by_id[event.event_id] = event

        reminders: List[TimedEvent] = []
        changed = False
        for event in dose_by_id.values():
            plan = self._find_plan(plans, event.medication_id)
            if not plan:
                continue
            previous_status = event.status
            timed_event = self._advance_event(event, plan, now)
            if timed_event is not None:
                reminders.append(timed_event)
            if event.status != previous_status or timed_event is not None:
                changed = True

        if changed or len(dose_by_id) != len(dose_events):
            self._save_dose_events(elder_user_id, list(dose_by_id.values()))
        return reminders

    def acknowledge(
        self,
        elder_user_id: str,
        event_id: str,
        ack: TimedEventAck,
        now: Optional[datetime] = None,
    ) -> MedicationDoseEvent:
        now = self._normalize_datetime(now or utc_now())
        events = self._load_dose_events(elder_user_id)
        for event in events:
            if event.event_id != event_id:
                continue
            if ack.ack == "snooze":
                minutes = ack.snooze_minutes or 10
                event.status = "snoozed"
                event.ack = "snooze"
                event.last_notified_at = None
                event.payload["snoozed_until"] = (now + timedelta(minutes=minutes)).isoformat()
            elif ack.ack == "missed":
                event.status = "missed"
                event.ack = "missed"
            else:
                event.status = "acknowledged"
                event.ack = ack.ack
            event.payload["ack_text"] = ack.text
            event.payload["acknowledged_at"] = now.isoformat()
            self._save_dose_events(elder_user_id, events)
            self.store.append_user_jsonl(elder_user_id, self.DOSE_AUDIT_FILE, event)
            return event
        raise ValueError(f"Medication dose event not found: {event_id}")

    def _advance_event(
        self,
        event: MedicationDoseEvent,
        plan: MedicationPlan,
        now: datetime,
    ) -> Optional[TimedEvent]:
        if event.status in {"acknowledged", "missed", "expired"}:
            return None

        if event.status == "snoozed":
            snoozed_until = self._parse_datetime(event.payload.get("snoozed_until"))
            if snoozed_until and now < snoozed_until:
                return None
            event.status = "pending"

        next_status = self._status_for_time(event, now)
        if next_status == "pending":
            event.status = "pending"
            return None
        if next_status == "expired":
            event.status = "expired"
            event.ack = "missed"
            self.store.append_user_jsonl(event.elder_user_id, self.DOSE_AUDIT_FILE, event)
            return None

        should_notify = event.status != next_status or event.last_notified_at is None
        event.status = next_status
        if not should_notify:
            return None

        event.notify_count += 1
        event.last_notified_at = now
        timed_event = self._build_timed_event(plan, event, next_status, now)
        self.timed_event_service.upsert_event(timed_event)
        self.store.append_user_jsonl(event.elder_user_id, self.DOSE_AUDIT_FILE, event)
        return timed_event

    def _status_for_time(self, event: MedicationDoseEvent, now: datetime) -> str:
        window_start = self._normalize_datetime(event.window_start)
        window_end = self._normalize_datetime(event.window_end)
        expire_at = self._normalize_datetime(event.expire_at)
        if now < window_start:
            return "pending"
        if window_start <= now <= window_end:
            return "due"
        if now <= expire_at:
            return "overdue"
        return "expired"

    def _build_timed_event(
        self,
        plan: MedicationPlan,
        dose_event: MedicationDoseEvent,
        dose_status: str,
        now: datetime,
    ) -> TimedEvent:
        event_type = "medication_overdue" if dose_status == "overdue" else "medication_due"
        return TimedEvent(
            event_id=f"{dose_event.event_id}_{dose_status}",
            elder_user_id=plan.elder_user_id,
            event_type=event_type,
            priority="high" if dose_status == "overdue" else "medium",
            scheduled_at=now,
            valid_until=dose_event.expire_at,
            status="delivered",
            payload={
                "dose_event_id": dose_event.event_id,
                "medication_id": plan.medication_id,
                "name": plan.name,
                "dosage_text": plan.dosage_text,
                "instruction_text": plan.instruction_text,
                "source": plan.source,
                "dose_status": dose_status,
                "content": self.render_reminder_text(plan, dose_status),
            },
        )

    def render_reminder_text(self, plan: MedicationPlan, dose_status: str) -> str:
        detail = self._recorded_medication_detail(plan)
        if dose_status == "overdue":
            prefix = "\u521a\u624d\u90a3\u6b21\u6309\u8bb0\u5f55\u7684\u670d\u836f\u63d0\u9192\u65f6\u95f4\u8fc7\u4e86\u4e00\u4f1a\u513f\uff0c\u6211\u62c5\u5fc3\u60a8\u5fd9\u5fd8\u4e86\u3002"
        else:
            prefix = "\u53ee\u549a\uff0c\u6309\u5bb6\u91cc\u8bb0\u5f55\u6216\u533b\u5631\uff0c\u5230\u4e86\u670d\u836f\u63d0\u9192\u65f6\u95f4\u3002"
        return (
            f"{prefix}{detail}"
            "\u60a8\u8981\u662f\u5df2\u7ecf\u5403\u8fc7\u4e86\uff0c\u6211\u5e2e\u60a8\u8bb0\u4e00\u4e0b\u3002"
        )

    def _recorded_medication_detail(self, plan: MedicationPlan) -> str:
        parts = [plan.name]
        if plan.dosage_text:
            parts.append(plan.dosage_text)
        if plan.instruction_text:
            parts.append(plan.instruction_text)
        detail = "\u8bb0\u5f55\u91cc\u662f\uff1a" + "\uff0c".join(parts) + "\u3002"
        if not plan.dosage_text:
            detail += "\u6211\u8fd9\u8fb9\u6ca1\u6709\u770b\u5230\u5177\u4f53\u5242\u91cf\uff0c\u5148\u6309\u5bb6\u91cc\u4fdd\u5b58\u7684\u533b\u5631\u6216\u836f\u76d2\u6807\u7b7e\u786e\u8ba4\u4e00\u4e0b\u3002"
        return detail

    def _ensure_dose_event(
        self,
        plan: MedicationPlan,
        schedule: ScheduleEntry,
        now: datetime,
        dose_by_id: Dict[str, MedicationDoseEvent],
    ) -> MedicationDoseEvent:
        scheduled_at = self._scheduled_datetime(now, schedule.time)
        event_id = self._dose_event_id(plan.medication_id, scheduled_at)
        existing = dose_by_id.get(event_id)
        if existing:
            return existing
        window_start = scheduled_at - timedelta(minutes=plan.window_before_minutes)
        window_end = scheduled_at + timedelta(minutes=plan.window_after_minutes)
        overdue_at = scheduled_at + timedelta(minutes=plan.overdue_after_minutes)
        expire_at = scheduled_at + timedelta(minutes=plan.expire_after_minutes)
        return MedicationDoseEvent(
            event_id=event_id,
            elder_user_id=plan.elder_user_id,
            medication_id=plan.medication_id,
            scheduled_at=scheduled_at,
            window_start=window_start,
            window_end=window_end,
            overdue_at=overdue_at,
            expire_at=expire_at,
            payload={
                "schedule_label": schedule.label,
                "schedule_time": schedule.time,
            },
        )

    def _scheduled_datetime(self, now: datetime, schedule_time: str) -> datetime:
        hour, minute = self._parse_schedule_time(schedule_time)
        tzinfo = now.tzinfo or timezone.utc
        return datetime.combine(now.date(), time(hour=hour, minute=minute), tzinfo=tzinfo)

    def _parse_schedule_time(self, schedule_time: str) -> Tuple[int, int]:
        hour_text, minute_text = schedule_time.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError(f"Invalid schedule time: {schedule_time}")
        return hour, minute

    def _dose_event_id(self, medication_id: str, scheduled_at: datetime) -> str:
        return f"dose_{scheduled_at.strftime('%Y%m%d_%H%M')}_{medication_id}"

    def _plan_active_on(self, plan: MedicationPlan, now: datetime) -> bool:
        current_date = now.date()
        if plan.status != "active":
            return False
        if plan.start_date and current_date < plan.start_date:
            return False
        if plan.end_date and current_date > plan.end_date:
            return False
        return True

    def _find_plan(self, plans: List[MedicationPlan], medication_id: str) -> Optional[MedicationPlan]:
        for plan in plans:
            if plan.medication_id == medication_id:
                return plan
        return None

    def _load_dose_events(self, elder_user_id: str) -> List[MedicationDoseEvent]:
        raw_events = self.store.read_user_json(elder_user_id, self.DOSE_EVENTS_FILE, default=[])
        if not isinstance(raw_events, list):
            return []
        return [self._parse_dose_event(item) for item in raw_events if isinstance(item, dict)]

    def _save_dose_events(self, elder_user_id: str, events: List[MedicationDoseEvent]) -> None:
        self.store.write_user_json(elder_user_id, self.DOSE_EVENTS_FILE, events)

    def _parse_plan(self, item: Dict[str, Any]) -> MedicationPlan:
        if hasattr(MedicationPlan, "model_validate"):
            return MedicationPlan.model_validate(item)
        return MedicationPlan.parse_obj(item)

    def _parse_dose_event(self, item: Dict[str, Any]) -> MedicationDoseEvent:
        if hasattr(MedicationDoseEvent, "model_validate"):
            return MedicationDoseEvent.model_validate(item)
        return MedicationDoseEvent.parse_obj(item)

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return self._normalize_datetime(value)
        return self._normalize_datetime(datetime.fromisoformat(str(value)))

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
