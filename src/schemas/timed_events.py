from datetime import date, datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


TimedEventType = Literal[
    "medication_due",
    "medication_overdue",
    "community_activity",
    "quiet_message",
    "action_followup",
]
TimedEventStatus = Literal["pending", "delivered", "acknowledged", "snoozed", "expired", "cancelled"]
TimedEventPriority = Literal["low", "medium", "high", "crisis"]
MedicationAckValue = Literal["taken", "snooze", "skip", "not_sure", "missed"]


class ScheduleEntry(BaseModel):
    time: str
    label: Optional[str] = None


class MedicationPlan(BaseModel):
    medication_id: str
    elder_user_id: str
    name: str
    dosage_text: Optional[str] = None
    instruction_text: Optional[str] = None
    source: str = "caregiver_prescription_record"
    schedule: List[ScheduleEntry] = Field(default_factory=list)
    window_before_minutes: int = Field(default=0, ge=0)
    window_after_minutes: int = Field(default=30, ge=0)
    overdue_after_minutes: int = Field(default=30, ge=0)
    expire_after_minutes: int = Field(default=180, ge=1)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Literal["active", "paused", "cancelled"] = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MedicationDoseEvent(BaseModel):
    event_id: str
    elder_user_id: str
    medication_id: str
    scheduled_at: datetime
    window_start: datetime
    window_end: datetime
    overdue_at: datetime
    expire_at: datetime
    status: Literal["pending", "due", "overdue", "acknowledged", "snoozed", "expired", "missed"] = "pending"
    notify_count: int = Field(default=0, ge=0)
    last_notified_at: Optional[datetime] = None
    ack: Optional[MedicationAckValue] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class TimedEvent(BaseModel):
    event_id: str
    elder_user_id: str
    event_type: TimedEventType
    priority: TimedEventPriority = "medium"
    scheduled_at: datetime
    valid_until: datetime
    status: TimedEventStatus = "pending"
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TimedEventAck(BaseModel):
    elder_user_id: str
    ack: MedicationAckValue
    snooze_minutes: Optional[int] = Field(default=None, ge=1)
    text: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)
