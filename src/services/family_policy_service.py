from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from src.schemas.family import (
    FamilyMessageCreateRequest,
    FamilyPolicy,
    QuietMessageConsentRequest,
    SuggestedTopic,
)
from src.schemas.relay import RelayMessage
from src.services.data_store import DataStore
from src.services.relay_message_service import RelayMessageService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FamilyPolicyService:
    """Family-side policy and quiet-message consumption rules."""

    POLICY_FILE = "family_policy.json"
    POLICY_AUDIT_FILE = "family_policy_history.jsonl"
    ALLOWED_PROMPT_RISK_TIERS = {"safe", "low"}
    ALLOWED_PROMPT_PRIORITIES = {"low", "normal"}

    def __init__(
        self,
        store: Optional[DataStore] = None,
        relay_message_service: Optional[RelayMessageService] = None,
    ):
        self.store = store or DataStore()
        self.relay_message_service = relay_message_service or RelayMessageService(self.store)
        self._locks: Dict[str, RLock] = {}
        self._locks_guard = RLock()

    def get_policy(self, elder_user_id: str, child_user_id: str) -> FamilyPolicy:
        elder_id = self._normalize_id(elder_user_id, "elder_user_id")
        child_id = self._normalize_id(child_user_id, "child_user_id")
        with self._lock_for(elder_id, child_id):
            raw = self.store.read_user_json(
                elder_id,
                self._family_path(child_id, self.POLICY_FILE),
                default=None,
            )
            if not isinstance(raw, dict):
                return FamilyPolicy(elder_user_id=elder_id, child_user_id=child_id)
            payload = dict(raw)
            payload["elder_user_id"] = elder_id
            payload["child_user_id"] = child_id
            return self._parse_policy(payload)

    def upsert_policy(self, policy: FamilyPolicy) -> FamilyPolicy:
        elder_id = self._normalize_id(policy.elder_user_id, "elder_user_id")
        child_id = self._normalize_id(policy.child_user_id, "child_user_id")
        with self._lock_for(elder_id, child_id):
            policy.elder_user_id = elder_id
            policy.child_user_id = child_id
            policy.updated_at = utc_now()
            self.store.write_user_json(elder_id, self._family_path(child_id, self.POLICY_FILE), policy)
            self.store.append_user_jsonl(elder_id, self._family_path(child_id, self.POLICY_AUDIT_FILE), policy)
            return policy

    def update_policy_from_payload(
        self,
        elder_user_id: str,
        child_user_id: str,
        policy_payload: Dict[str, Any],
    ) -> FamilyPolicy:
        elder_id = self._normalize_id(elder_user_id, "elder_user_id")
        child_id = self._normalize_id(child_user_id, "child_user_id")
        normalized = self._normalize_policy_payload(policy_payload)
        normalized["elder_user_id"] = elder_id
        normalized["child_user_id"] = child_id
        return self.upsert_policy(self._parse_policy(normalized))

    def available_topics(
        self,
        elder_user_id: str,
        child_user_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> List[SuggestedTopic]:
        policy = self.get_policy(elder_user_id, child_user_id)
        now = self._normalize_datetime(now or utc_now())
        return [topic for topic in policy.suggested_topics if self._topic_available(topic, now)]

    def consume_topic(
        self,
        elder_user_id: str,
        child_user_id: str,
        topic_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> SuggestedTopic:
        elder_id = self._normalize_id(elder_user_id, "elder_user_id")
        child_id = self._normalize_id(child_user_id, "child_user_id")
        wanted_topic_id = self._normalize_id(topic_id, "topic_id")
        now = self._normalize_datetime(now or utc_now())

        with self._lock_for(elder_id, child_id):
            policy = self.get_policy(elder_id, child_id)
            for topic in policy.suggested_topics:
                if topic.topic_id != wanted_topic_id:
                    continue
                if not self._topic_available(topic, now):
                    raise ValueError(f"Suggested topic is not available: {wanted_topic_id}")
                topic.consumed_count += 1
                topic.last_consumed_at = now
                topic.updated_at = now
                if topic.consumed_count >= topic.max_consumptions:
                    topic.status = "exhausted"
                self.upsert_policy(policy)
                return topic
        raise ValueError(f"Suggested topic not found: {wanted_topic_id}")

    def create_quiet_message(self, request: FamilyMessageCreateRequest) -> RelayMessage:
        if request.message_type != "quiet_message":
            raise ValueError("Only quiet_message is supported in this endpoint")
        child_id = self._normalize_id(request.child_user_id, "child_user_id")
        payload = dict(request.payload or {})
        payload.update(
            {
                "child_user_id": child_id,
                "priority": request.priority,
                "message_type": "quiet_message",
                "consent_required": True,
                "content_visible_after_consent": True,
            }
        )
        return self.relay_message_service.create_quiet_message(
            request.elder_user_id,
            request.content,
            title=request.title,
            target="elder",
            actor_role=request.actor_role,
            direction=request.direction,
            payload=payload,
        )

    def pending_quiet_message_prompts(
        self,
        elder_user_id: str,
        *,
        risk_tier: str = "safe",
    ) -> List[Dict[str, Any]]:
        if str(risk_tier or "safe") not in self.ALLOWED_PROMPT_RISK_TIERS:
            return []

        prompts: List[Dict[str, Any]] = []
        for message in self.relay_message_service.get_pending(elder_user_id, target="elder"):
            if message.display_type != "quiet_message":
                continue
            if message.direction != "child_to_elder":
                continue
            priority = str(message.payload.get("priority") or "normal")
            if priority not in self.ALLOWED_PROMPT_PRIORITIES:
                continue
            prompts.append(self._prompt_metadata(message))
        return prompts

    def consent_to_quiet_message(
        self,
        message_id: str,
        request: QuietMessageConsentRequest,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        elder_id = self._normalize_id(request.elder_user_id, "elder_user_id")
        message = self._find_quiet_message(elder_id, message_id)
        consent = request.consent or self._infer_semantic_consent(request.raw_text or "")
        if consent is None:
            raise ValueError("Consent is ambiguous; do not reveal quiet-message content")

        if message.status == "acknowledged":
            return self._consent_response(message, accepted=True, idempotent_replay=True)
        if message.status in {"cancelled", "expired"}:
            return self._consent_response(message, accepted=False, idempotent_replay=True)

        if consent == "accepted":
            updated = self.relay_message_service.mark_message(
                elder_id,
                message.id or message_id,
                "acknowledged",
                actor_role="elder",
                text=self._consent_history_text(request, "accepted"),
                now=now,
            )
            return self._consent_response(updated, accepted=True, idempotent_replay=False)

        updated = self.relay_message_service.mark_message(
            elder_id,
            message.id or message_id,
            "cancelled",
            actor_role="elder",
            text=self._consent_history_text(request, "rejected"),
            now=now,
        )
        return self._consent_response(updated, accepted=False, idempotent_replay=False)

    def list_family_alerts(
        self,
        elder_user_id: str,
        *,
        limit: int = 20,
    ) -> List[RelayMessage]:
        messages = self.relay_message_service.list_messages(elder_user_id, target="family")
        return messages[-max(limit, 0):]

    def _prompt_metadata(self, message: RelayMessage) -> Dict[str, Any]:
        child_user_id = message.payload.get("child_user_id") or ""
        from_display = message.payload.get("from_display") or message.title or child_user_id or "family"
        prompt_text = message.payload.get("prompt_text") or "家人有句话想跟您说，您要不要听？"
        return {
            "id": message.id,
            "from_display": from_display,
            "message_type": "quiet_message",
            "prompt_text": prompt_text,
            "status": message.status,
            "priority": message.payload.get("priority", "normal"),
            "created_at": message.created_at.isoformat(),
        }

    def _consent_response(
        self,
        message: RelayMessage,
        *,
        accepted: bool,
        idempotent_replay: bool,
    ) -> Dict[str, Any]:
        return {
            "id": message.id,
            "status": "accepted" if accepted else "rejected",
            "content": message.content if accepted else None,
            "message": message,
            "idempotent_replay": idempotent_replay,
        }

    def _find_quiet_message(self, elder_user_id: str, message_id: str) -> RelayMessage:
        target_id = self._normalize_id(message_id, "message_id")
        for message in self.relay_message_service.list_messages(elder_user_id, target="elder"):
            if message.id != target_id:
                continue
            if message.display_type != "quiet_message":
                raise ValueError(f"Relay message is not a quiet_message: {target_id}")
            return message
        raise ValueError(f"Quiet message not found: {target_id}")

    def _topic_available(self, topic: SuggestedTopic, now: datetime) -> bool:
        if topic.status != "active":
            return False
        if topic.consumed_count >= topic.max_consumptions:
            return False
        if topic.last_consumed_at is None:
            return True
        last = self._normalize_datetime(topic.last_consumed_at)
        return now >= last + timedelta(hours=topic.min_interval_hours)

    def _normalize_policy_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(payload or {})
        topics = []
        for item in normalized.get("suggested_topics") or []:
            if not isinstance(item, dict):
                continue
            topic = dict(item)
            if "topic_id" not in topic and "id" in topic:
                topic["topic_id"] = topic.pop("id")
            if "content" not in topic and "prompt_hint" in topic:
                topic["content"] = topic.pop("prompt_hint")
            topics.append(topic)
        normalized["suggested_topics"] = topics
        return normalized

    def _parse_policy(self, payload: Dict[str, Any]) -> FamilyPolicy:
        if hasattr(FamilyPolicy, "model_validate"):
            return FamilyPolicy.model_validate(payload)
        return FamilyPolicy.parse_obj(payload)

    def _infer_semantic_consent(self, raw_text: str) -> Optional[str]:
        text = str(raw_text or "").strip().lower()
        if not text:
            return None
        reject_keywords = ["不要", "不听", "算了", "别读", "拒绝", "no", "not now"]
        accept_keywords = ["同意", "可以", "好", "愿意", "读吧", "听听", "念", "yes", "ok"]
        if any(keyword in text for keyword in reject_keywords):
            return "rejected"
        if any(keyword in text for keyword in accept_keywords):
            return "accepted"
        return None

    def _consent_history_text(self, request: QuietMessageConsentRequest, consent: str) -> str:
        return f"{consent}:{request.source}:{request.raw_text or ''}"

    def _family_path(self, child_user_id: str, relative_path: str) -> Path:
        return Path("family") / child_user_id / relative_path

    def _lock_for(self, elder_user_id: str, child_user_id: str) -> RLock:
        key = f"{elder_user_id}:{child_user_id}"
        with self._locks_guard:
            return self._locks.setdefault(key, RLock())

    def _normalize_id(self, value: str, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        if any(part in text for part in ("/", "\\", "..")):
            raise ValueError(f"{field_name} contains invalid path characters")
        return text

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
