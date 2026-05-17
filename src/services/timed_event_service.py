from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from src.schemas.timed_events import TimedEvent, utc_now
from src.services.data_store import DataStore


class TimedEventService:
    """Current-state storage for due/overdue/expired timed events."""

    EVENTS_FILE = "timed_events.json"
    AUDIT_FILE = "timed_events.jsonl"

    def __init__(self, store: Optional[DataStore] = None):
        self.store = store or DataStore()

    def upsert_event(self, event: TimedEvent) -> TimedEvent:
        events = self._load_events(event.elder_user_id)
        replaced = False
        for idx, existing in enumerate(events):
            if existing.event_id == event.event_id:
                events[idx] = event
                replaced = True
                break
        if not replaced:
            events.append(event)
        self._save_events(event.elder_user_id, events)
        self.store.append_user_jsonl(event.elder_user_id, self.AUDIT_FILE, event)
        return event

    def list_events(
        self,
        elder_user_id: str,
        statuses: Optional[Iterable[str]] = None,
    ) -> List[TimedEvent]:
        events = self._load_events(elder_user_id)
        if statuses is None:
            return events
        wanted = set(statuses)
        return [event for event in events if event.status in wanted]

    def get_due_events(
        self,
        elder_user_id: str,
        now: Optional[datetime] = None,
    ) -> List[TimedEvent]:
        now = self._normalize_datetime(now or utc_now())
        events = self._load_events(elder_user_id)
        changed = False
        due_events: List[TimedEvent] = []

        for event in events:
            if event.status in {"acknowledged", "snoozed", "cancelled", "expired"}:
                continue
            if now > self._normalize_datetime(event.valid_until):
                event.status = "expired"
                event.updated_at = now
                changed = True
                continue
            if self._normalize_datetime(event.scheduled_at) <= now:
                due_events.append(event)

        if changed:
            self._save_events(elder_user_id, events)
        return due_events

    def mark_event(
        self,
        elder_user_id: str,
        event_id: str,
        status: str,
        now: Optional[datetime] = None,
    ) -> TimedEvent:
        events = self._load_events(elder_user_id)
        now = self._normalize_datetime(now or utc_now())
        for event in events:
            if event.event_id != event_id:
                continue
            event.status = status
            event.updated_at = now
            self._save_events(elder_user_id, events)
            self.store.append_user_jsonl(elder_user_id, self.AUDIT_FILE, event)
            return event
        raise ValueError(f"Timed event not found: {event_id}")

    def mark_events_by_payload(
        self,
        elder_user_id: str,
        payload_key: str,
        payload_value: Any,
        status: str,
        now: Optional[datetime] = None,
    ) -> List[TimedEvent]:
        events = self._load_events(elder_user_id)
        now = self._normalize_datetime(now or utc_now())
        updated: List[TimedEvent] = []
        for event in events:
            if event.payload.get(payload_key) != payload_value:
                continue
            event.status = status
            event.updated_at = now
            updated.append(event)
        if updated:
            self._save_events(elder_user_id, events)
            for event in updated:
                self.store.append_user_jsonl(elder_user_id, self.AUDIT_FILE, event)
        return updated

    def _load_events(self, elder_user_id: str) -> List[TimedEvent]:
        raw_events = self.store.read_user_json(elder_user_id, self.EVENTS_FILE, default=[])
        if not isinstance(raw_events, list):
            return []
        return [self._parse_event(item) for item in raw_events if isinstance(item, dict)]

    def _save_events(self, elder_user_id: str, events: List[TimedEvent]) -> None:
        self.store.write_user_json(elder_user_id, self.EVENTS_FILE, events)

    def _parse_event(self, item: Dict[str, Any]) -> TimedEvent:
        if hasattr(TimedEvent, "model_validate"):
            return TimedEvent.model_validate(item)
        return TimedEvent.parse_obj(item)

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
