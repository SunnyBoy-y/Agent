import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, TypeVar

from src.schemas.community import (
    CommunityActivity,
    CommunityActivityCreateRequest,
    CommunityActivityUpdateRequest,
    CommunityAnnouncement,
    CommunityAnnouncementCreateRequest,
    CommunityAnnouncementUpdateRequest,
)
from src.schemas.relay import RelayMessage
from src.services.data_store import DataStore
from src.services.relay_message_service import RelayMessageService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


TCommunityItem = TypeVar("TCommunityItem", CommunityAnnouncement, CommunityActivity)


class CommunityService:
    """Community announcements, activities, and community-visible crisis alerts."""

    ANNOUNCEMENTS_FILE = "announcements.json"
    ANNOUNCEMENTS_AUDIT_FILE = "announcements.jsonl"
    ACTIVITIES_FILE = "activities.json"
    ACTIVITIES_AUDIT_FILE = "activities.jsonl"

    def __init__(
        self,
        store: Optional[DataStore] = None,
        relay_message_service: Optional[RelayMessageService] = None,
    ):
        self.store = store or DataStore()
        self.relay_message_service = relay_message_service or RelayMessageService(self.store)
        self._locks: Dict[str, RLock] = {}
        self._locks_guard = RLock()

    def create_announcement(
        self,
        request: CommunityAnnouncementCreateRequest,
        *,
        now: Optional[datetime] = None,
    ) -> CommunityAnnouncement:
        community_id = self._normalize_id(request.community_id, "community_id")
        now = self._normalize_datetime(now or utc_now())
        announcement = CommunityAnnouncement(
            id=self._normalize_optional_id(request.id) or f"ann_{uuid.uuid4().hex}",
            community_id=community_id,
            actor_role=request.actor_role,
            title=self._normalize_text(request.title, "title"),
            content=self._normalize_text(request.content, "content"),
            tags=self._normalize_tags(request.tags),
            priority=request.priority,
            valid_from=self._normalize_datetime(request.valid_from) if request.valid_from else None,
            valid_until=self._normalize_datetime(request.valid_until) if request.valid_until else None,
            status="active",
            created_at=now,
            updated_at=now,
        )
        announcement.status = self._announcement_status_at(announcement, now)
        with self._lock_for(community_id):
            items = self._load_announcements(community_id)
            if any(item.id == announcement.id for item in items):
                raise ValueError(f"Community announcement already exists: {announcement.id}")
            items.append(announcement)
            self._save_announcements(community_id, items)
            self.store.append_jsonl(
                self._community_path(community_id, self.ANNOUNCEMENTS_AUDIT_FILE),
                {"event": "created", "item": announcement},
            )
        return announcement

    def list_announcements(
        self,
        community_id: str,
        *,
        only_active: bool = True,
        now: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[CommunityAnnouncement]:
        community_id = self._normalize_id(community_id, "community_id")
        now = self._normalize_datetime(now or utc_now())
        with self._lock_for(community_id):
            items = self._refresh_announcement_statuses(community_id, self._load_announcements(community_id), now)
        if only_active:
            items = [item for item in items if self._announcement_is_consumable(item, now)]
        return self._limit(self._sort_items(items), limit)

    def update_announcement(
        self,
        community_id: str,
        announcement_id: str,
        request: CommunityAnnouncementUpdateRequest,
        *,
        now: Optional[datetime] = None,
    ) -> CommunityAnnouncement:
        community_id = self._normalize_id(community_id, "community_id")
        announcement_id = self._normalize_id(announcement_id, "announcement_id")
        now = self._normalize_datetime(now or utc_now())
        with self._lock_for(community_id):
            items = self._load_announcements(community_id)
            for idx, item in enumerate(items):
                if item.id != announcement_id:
                    continue
                updates = self._model_to_dict(request)
                updates = {key: value for key, value in updates.items() if value is not None}
                if "title" in updates:
                    item.title = self._normalize_text(updates["title"], "title")
                if "content" in updates:
                    item.content = self._normalize_text(updates["content"], "content")
                if "tags" in updates:
                    item.tags = self._normalize_tags(updates["tags"])
                if "valid_from" in updates:
                    item.valid_from = self._parse_datetime(updates["valid_from"])
                if "valid_until" in updates:
                    item.valid_until = self._parse_datetime(updates["valid_until"])
                if "priority" in updates:
                    item.priority = int(updates["priority"])
                if "status" in updates:
                    item.status = updates["status"]
                item.updated_at = now
                item.status = self._announcement_status_at(item, now)
                items[idx] = item
                self._save_announcements(community_id, items)
                self.store.append_jsonl(
                    self._community_path(community_id, self.ANNOUNCEMENTS_AUDIT_FILE),
                    {"event": "updated", "item": item},
                )
                return item
        raise ValueError(f"Community announcement not found: {announcement_id}")

    def delete_announcement(
        self,
        community_id: str,
        announcement_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> CommunityAnnouncement:
        return self.update_announcement(
            community_id,
            announcement_id,
            CommunityAnnouncementUpdateRequest(status="cancelled"),
            now=now,
        )

    def create_activity(
        self,
        request: CommunityActivityCreateRequest,
        *,
        now: Optional[datetime] = None,
    ) -> CommunityActivity:
        community_id = self._normalize_id(request.community_id, "community_id")
        now = self._normalize_datetime(now or utc_now())
        activity = CommunityActivity(
            id=self._normalize_optional_id(request.id) or f"act_{uuid.uuid4().hex}",
            community_id=community_id,
            title=self._normalize_text(request.title, "title"),
            content=str(request.content or "").strip(),
            time_text=str(request.time_text or "").strip(),
            location=str(request.location or "").strip(),
            tags=self._normalize_tags(request.tags),
            priority=request.priority,
            valid_until=self._normalize_datetime(request.valid_until),
            status="active",
            created_at=now,
            updated_at=now,
        )
        activity.status = self._activity_status_at(activity, now)
        with self._lock_for(community_id):
            items = self._load_activities(community_id)
            if any(item.id == activity.id for item in items):
                raise ValueError(f"Community activity already exists: {activity.id}")
            items.append(activity)
            self._save_activities(community_id, items)
            self.store.append_jsonl(
                self._community_path(community_id, self.ACTIVITIES_AUDIT_FILE),
                {"event": "created", "item": activity},
            )
        return activity

    def list_activities(
        self,
        community_id: str,
        *,
        only_active: bool = True,
        now: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[CommunityActivity]:
        community_id = self._normalize_id(community_id, "community_id")
        now = self._normalize_datetime(now or utc_now())
        with self._lock_for(community_id):
            items = self._refresh_activity_statuses(community_id, self._load_activities(community_id), now)
        if only_active:
            items = [item for item in items if self._activity_is_consumable(item, now)]
        return self._limit(self._sort_items(items), limit)

    def update_activity(
        self,
        community_id: str,
        activity_id: str,
        request: CommunityActivityUpdateRequest,
        *,
        now: Optional[datetime] = None,
    ) -> CommunityActivity:
        community_id = self._normalize_id(community_id, "community_id")
        activity_id = self._normalize_id(activity_id, "activity_id")
        now = self._normalize_datetime(now or utc_now())
        with self._lock_for(community_id):
            items = self._load_activities(community_id)
            for idx, item in enumerate(items):
                if item.id != activity_id:
                    continue
                updates = self._model_to_dict(request)
                updates = {key: value for key, value in updates.items() if value is not None}
                if "title" in updates:
                    item.title = self._normalize_text(updates["title"], "title")
                if "content" in updates:
                    item.content = str(updates["content"] or "").strip()
                if "time_text" in updates:
                    item.time_text = str(updates["time_text"] or "").strip()
                if "location" in updates:
                    item.location = str(updates["location"] or "").strip()
                if "tags" in updates:
                    item.tags = self._normalize_tags(updates["tags"])
                if "valid_until" in updates:
                    item.valid_until = self._parse_datetime(updates["valid_until"])
                if "priority" in updates:
                    item.priority = int(updates["priority"])
                if "status" in updates:
                    item.status = updates["status"]
                item.updated_at = now
                item.status = self._activity_status_at(item, now)
                items[idx] = item
                self._save_activities(community_id, items)
                self.store.append_jsonl(
                    self._community_path(community_id, self.ACTIVITIES_AUDIT_FILE),
                    {"event": "updated", "item": item},
                )
                return item
        raise ValueError(f"Community activity not found: {activity_id}")

    def delete_activity(
        self,
        community_id: str,
        activity_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> CommunityActivity:
        return self.update_activity(
            community_id,
            activity_id,
            CommunityActivityUpdateRequest(status="cancelled"),
            now=now,
        )

    def list_crisis_alerts(
        self,
        elder_user_id: str,
        *,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        messages = self.relay_message_service.list_messages(elder_user_id, target="community")
        crisis_messages = [
            message
            for message in messages
            if message.risk_tier == "crisis" or message.display_type == "sos"
        ]
        selected = crisis_messages[-max(limit, 0):]
        return [self._community_safe_alert(message) for message in selected]

    def list_crisis_alerts_by_community(
        self,
        community_id: str,
        *,
        group_by: str = "elder",
        limit: int = 20,
    ) -> Dict[str, Any]:
        community_id = self._normalize_id(community_id, "community_id")
        all_alerts: List[Dict[str, Any]] = []
        for elder_user_id in self.store.list_user_ids():
            for message in self.relay_message_service.list_messages(elder_user_id, target="community"):
                if message.risk_tier != "crisis" and message.display_type != "sos":
                    continue
                payload = message.payload or {}
                message_community_id = payload.get("community_id") or "community_001"
                if message_community_id != community_id:
                    continue
                all_alerts.append(self._community_safe_alert(message))

        all_alerts = self._sort_alerts(all_alerts)[: max(limit, 0)]
        return {
            "community_id": community_id,
            "group_by": group_by,
            "alerts": all_alerts,
            "groups": self._group_crisis_alerts(all_alerts, group_by),
            "total": len(all_alerts),
        }

    def _refresh_announcement_statuses(
        self,
        community_id: str,
        items: List[CommunityAnnouncement],
        now: datetime,
    ) -> List[CommunityAnnouncement]:
        changed = False
        for item in items:
            if item.status != "active":
                continue
            status = self._announcement_status_at(item, now)
            if status != item.status:
                item.status = status
                item.updated_at = now
                changed = True
        if changed:
            self._save_announcements(community_id, items)
            self.store.append_jsonl(
                self._community_path(community_id, self.ANNOUNCEMENTS_AUDIT_FILE),
                {"event": "expired_refresh", "at": now.isoformat()},
            )
        return items

    def _refresh_activity_statuses(
        self,
        community_id: str,
        items: List[CommunityActivity],
        now: datetime,
    ) -> List[CommunityActivity]:
        changed = False
        for item in items:
            if item.status != "active":
                continue
            status = self._activity_status_at(item, now)
            if status != item.status:
                item.status = status
                item.updated_at = now
                changed = True
        if changed:
            self._save_activities(community_id, items)
            self.store.append_jsonl(
                self._community_path(community_id, self.ACTIVITIES_AUDIT_FILE),
                {"event": "expired_refresh", "at": now.isoformat()},
            )
        return items

    def _announcement_status_at(self, item: CommunityAnnouncement, now: datetime) -> str:
        if item.status in {"cancelled", "expired"}:
            return item.status
        if item.valid_until is not None and now > self._normalize_datetime(item.valid_until):
            return "expired"
        return "active"

    def _activity_status_at(self, item: CommunityActivity, now: datetime) -> str:
        if item.status in {"cancelled", "expired"}:
            return item.status
        if now > self._normalize_datetime(item.valid_until):
            return "expired"
        return "active"

    def _announcement_is_consumable(self, item: CommunityAnnouncement, now: datetime) -> bool:
        if item.status != "active":
            return False
        if item.valid_from is not None and now < self._normalize_datetime(item.valid_from):
            return False
        if item.valid_until is not None and now > self._normalize_datetime(item.valid_until):
            return False
        return True

    def _activity_is_consumable(self, item: CommunityActivity, now: datetime) -> bool:
        return item.status == "active" and now <= self._normalize_datetime(item.valid_until)

    def _community_safe_alert(self, message: RelayMessage) -> Dict[str, Any]:
        data = self._model_to_dict(message)
        data["raw_quotes"] = []
        data["content"] = message.reason_summary or message.content
        data["payload"] = self._sanitize_community_payload(data.get("payload", {}))
        data["payload"]["visibility"] = "community_crisis_summary"
        data["payload"]["raw_quote_visible"] = False
        return data

    def _sanitize_community_payload(self, value: Any) -> Any:
        blocked_keys = {"raw_quotes", "raw_quote", "original_quote", "original_text", "elder_raw_text"}
        if isinstance(value, dict):
            return {
                key: self._sanitize_community_payload(item)
                for key, item in value.items()
                if key not in blocked_keys
            }
        if isinstance(value, list):
            return [self._sanitize_community_payload(item) for item in value]
        return value

    def _sort_alerts(self, alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def sort_key(alert: Dict[str, Any]) -> str:
            return str(alert.get("updated_at") or alert.get("created_at") or "")

        return sorted(alerts, key=sort_key, reverse=True)

    def _group_crisis_alerts(self, alerts: List[Dict[str, Any]], group_by: str) -> List[Dict[str, Any]]:
        if group_by not in {"elder", "event", "owner"}:
            group_by = "elder"
        groups: Dict[str, Dict[str, Any]] = {}
        for alert in alerts:
            payload = alert.get("payload") or {}
            if group_by == "event":
                key = str(payload.get("assessment_id") or alert.get("id") or "unknown_event")
            elif group_by == "owner":
                key = str(
                    payload.get("owner_id")
                    or payload.get("assignee_id")
                    or payload.get("responsible_user_id")
                    or "unassigned"
                )
            else:
                key = str(alert.get("elder_user_id") or "unknown_elder")
            group = groups.setdefault(
                key,
                {
                    "group_key": key,
                    "group_by": group_by,
                    "alert_count": 0,
                    "latest_updated_at": None,
                    "alerts": [],
                },
            )
            group["alert_count"] += 1
            group["alerts"].append(alert)
            updated_at = alert.get("updated_at") or alert.get("created_at")
            if updated_at and (group["latest_updated_at"] is None or str(updated_at) > str(group["latest_updated_at"])):
                group["latest_updated_at"] = updated_at
        return sorted(
            groups.values(),
            key=lambda item: (item["alert_count"], str(item.get("latest_updated_at") or "")),
            reverse=True,
        )

    def _load_announcements(self, community_id: str) -> List[CommunityAnnouncement]:
        raw_items = self.store.read_json(
            self._community_path(community_id, self.ANNOUNCEMENTS_FILE),
            default=[],
        )
        if not isinstance(raw_items, list):
            return []
        return [self._parse_announcement(item) for item in raw_items if isinstance(item, dict)]

    def _save_announcements(self, community_id: str, items: List[CommunityAnnouncement]) -> None:
        self.store.write_json(self._community_path(community_id, self.ANNOUNCEMENTS_FILE), items)

    def _load_activities(self, community_id: str) -> List[CommunityActivity]:
        raw_items = self.store.read_json(
            self._community_path(community_id, self.ACTIVITIES_FILE),
            default=[],
        )
        if not isinstance(raw_items, list):
            return []
        return [self._parse_activity(item) for item in raw_items if isinstance(item, dict)]

    def _save_activities(self, community_id: str, items: List[CommunityActivity]) -> None:
        self.store.write_json(self._community_path(community_id, self.ACTIVITIES_FILE), items)

    def _parse_announcement(self, item: Dict[str, Any]) -> CommunityAnnouncement:
        if hasattr(CommunityAnnouncement, "model_validate"):
            return CommunityAnnouncement.model_validate(item)
        return CommunityAnnouncement.parse_obj(item)

    def _parse_activity(self, item: Dict[str, Any]) -> CommunityActivity:
        if hasattr(CommunityActivity, "model_validate"):
            return CommunityActivity.model_validate(item)
        return CommunityActivity.parse_obj(item)

    def _community_path(self, community_id: str, relative_path: str) -> Path:
        return Path("communities") / community_id / relative_path

    def _lock_for(self, community_id: str) -> RLock:
        with self._locks_guard:
            return self._locks.setdefault(community_id, RLock())

    def _sort_items(self, items: List[TCommunityItem]) -> List[TCommunityItem]:
        return sorted(
            items,
            key=lambda item: (item.priority, item.created_at),
            reverse=True,
        )

    def _limit(self, items: List[TCommunityItem], limit: Optional[int]) -> List[TCommunityItem]:
        if limit is None:
            return items
        return items[: max(limit, 0)]

    def _normalize_id(self, value: str, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        if any(part in text for part in ("/", "\\", "..")):
            raise ValueError(f"{field_name} contains invalid path characters")
        return text

    def _normalize_optional_id(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return self._normalize_id(value, "id")

    def _normalize_text(self, value: str, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        return text

    def _normalize_tags(self, tags: List[str]) -> List[str]:
        normalized: List[str] = []
        for tag in tags or []:
            text = str(tag or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def _parse_datetime(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return self._normalize_datetime(value)
        return self._normalize_datetime(datetime.fromisoformat(str(value)))

    def _model_to_dict(self, model: Any) -> Dict[str, Any]:
        if hasattr(model, "model_dump"):
            return model.model_dump(mode="json")
        if hasattr(model, "dict"):
            return model.dict()
        return dict(model or {})
