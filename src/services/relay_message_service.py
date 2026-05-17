import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from src.schemas.mental_health import MentalRiskAssessment
from src.schemas.relay import RelayAck, RelayMessage, RelayStatus, RelayTarget
from src.services.data_store import DataStore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RelayMessageService:
    """Durable relay queue for elder, family, community, and frontend messages."""

    MESSAGES_FILE = "relay_messages.json"
    AUDIT_FILE = "relay_messages.jsonl"

    def __init__(self, store: Optional[DataStore] = None):
        self.store = store or DataStore()

    def create_message(self, message: RelayMessage) -> RelayMessage:
        if not message.id:
            message.id = f"relay_{uuid.uuid4().hex}"
        message.updated_at = utc_now()
        messages = self._load_messages(message.elder_user_id)
        messages.append(message)
        self._save_messages(message.elder_user_id, messages)
        self.store.append_user_jsonl(message.elder_user_id, self.AUDIT_FILE, message)
        return message

    def create_messages(self, messages: Iterable[RelayMessage]) -> List[RelayMessage]:
        return [self.create_message(message) for message in messages]

    def create_from_assessment(self, assessment: MentalRiskAssessment) -> List[RelayMessage]:
        messages = self.build_from_assessment(assessment)
        created: List[RelayMessage] = []
        for message in messages:
            existing = self._find_existing_assessment_message(
                assessment.elder_user_id,
                assessment.id,
                message.target,
            )
            if existing is not None:
                created.append(existing)
                continue
            created.append(self.create_message(message))
        return created

    def build_from_assessment(self, assessment: MentalRiskAssessment) -> List[RelayMessage]:
        if assessment.risk_tier == "crisis":
            return [
                self._family_alert_from_assessment(assessment),
                self._community_alert_from_assessment(assessment),
            ]
        if assessment.risk_tier in {"medium", "high"}:
            return [self._family_alert_from_assessment(assessment)]
        return []

    def create_quiet_message(
        self,
        elder_user_id: str,
        content: str,
        *,
        title: str = "",
        target: RelayTarget = "elder",
        actor_role: str = "family",
        direction: str = "family_to_elder",
        payload: Optional[Dict[str, Any]] = None,
    ) -> RelayMessage:
        return self.create_message(
            RelayMessage(
                elder_user_id=elder_user_id,
                target=target,
                actor_role=actor_role,
                direction=direction,
                display_type="quiet_message",
                title=title,
                content=content,
                payload=payload or {},
            )
        )

    def list_messages(
        self,
        elder_user_id: str,
        *,
        target: Optional[RelayTarget] = None,
        statuses: Optional[Iterable[RelayStatus]] = None,
    ) -> List[RelayMessage]:
        messages = self._load_messages(elder_user_id)
        if target is not None:
            messages = [message for message in messages if message.target == target]
        if statuses is not None:
            wanted = set(statuses)
            messages = [message for message in messages if message.status in wanted]
        return messages

    def get_pending(
        self,
        elder_user_id: str,
        *,
        target: Optional[RelayTarget] = None,
    ) -> List[RelayMessage]:
        return self.list_messages(elder_user_id, target=target, statuses=["pending"])

    def mark_message(
        self,
        elder_user_id: str,
        message_id: str,
        status: RelayStatus,
        *,
        actor_role: str = "system",
        text: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> RelayMessage:
        messages = self._load_messages(elder_user_id)
        now = now or utc_now()
        for message in messages:
            if message.id != message_id:
                continue
            message.status = status
            message.updated_at = now
            history = list(message.payload.get("ack_history") or [])
            history.append({
                "actor_role": actor_role,
                "status": status,
                "text": text,
                "updated_at": now.isoformat(),
            })
            message.payload["ack_history"] = history
            self._save_messages(elder_user_id, messages)
            self.store.append_user_jsonl(elder_user_id, self.AUDIT_FILE, message)
            return message
        raise ValueError(f"Relay message not found: {message_id}")

    def acknowledge(self, ack: RelayAck) -> RelayMessage:
        return self.mark_message(
            ack.elder_user_id,
            ack.message_id,
            ack.status,
            actor_role=ack.actor_role,
            text=ack.text,
            now=ack.updated_at,
        )

    def _family_alert_from_assessment(self, assessment: MentalRiskAssessment) -> RelayMessage:
        suggestion = assessment.family_suggestion or "Use calm companionship and avoid blame or debate."
        summary = assessment.family_summary or assessment.evidence_summary
        return RelayMessage(
            elder_user_id=assessment.elder_user_id,
            target="family",
            actor_role="system",
            direction="system_to_family",
            display_type="alert",
            risk_tier=assessment.risk_tier,
            title=self._family_title(assessment),
            content=f"{summary}\nSuggested action: {suggestion}",
            reason_summary=summary,
            raw_quotes=list(assessment.raw_quotes),
            suggested_actions=[suggestion],
            payload={
                "assessment_id": assessment.id,
                "turn_id": assessment.turn_id,
                "primary_state": assessment.primary_state,
                "next_goal": assessment.next_goal,
                "visibility": "family",
                "raw_quote_visible": True,
            },
        )

    def _find_existing_assessment_message(
        self,
        elder_user_id: str,
        assessment_id: Optional[str],
        target: RelayTarget,
    ) -> Optional[RelayMessage]:
        if not assessment_id:
            return None
        for message in self._load_messages(elder_user_id):
            if message.target == target and message.payload.get("assessment_id") == assessment_id:
                return message
        return None

    def _community_alert_from_assessment(self, assessment: MentalRiskAssessment) -> RelayMessage:
        reason_summary = (
            assessment.community_reason_summary
            or "Crisis-level signal detected; original quote is hidden from community view."
        )
        return RelayMessage(
            elder_user_id=assessment.elder_user_id,
            target="community",
            actor_role="system",
            direction="system_to_community",
            display_type="sos",
            risk_tier="crisis",
            title="Community SOS",
            content=reason_summary,
            reason_summary=reason_summary,
            raw_quotes=[],
            suggested_actions=list(assessment.community_suggested_actions),
            payload={
                "assessment_id": assessment.id,
                "turn_id": assessment.turn_id,
                "primary_state": assessment.primary_state,
                "visibility": "community_crisis_summary",
                "raw_quote_visible": False,
            },
        )

    def _family_title(self, assessment: MentalRiskAssessment) -> str:
        if assessment.risk_tier == "crisis":
            return "Family Crisis Alert"
        return "Family Support Alert"

    def _load_messages(self, elder_user_id: str) -> List[RelayMessage]:
        raw_messages = self.store.read_user_json(elder_user_id, self.MESSAGES_FILE, default=[])
        if not isinstance(raw_messages, list):
            return []
        return [self._parse_message(item) for item in raw_messages if isinstance(item, dict)]

    def _save_messages(self, elder_user_id: str, messages: List[RelayMessage]) -> None:
        self.store.write_user_json(elder_user_id, self.MESSAGES_FILE, messages)

    def _parse_message(self, item: Dict[str, Any]) -> RelayMessage:
        if hasattr(RelayMessage, "model_validate"):
            return RelayMessage.model_validate(item)
        return RelayMessage.parse_obj(item)
