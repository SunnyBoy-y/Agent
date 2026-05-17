from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from src.schemas.mental_health import CarePlan, MentalRiskAssessment
from src.services.care_plan_service import CarePlanService
from src.services.data_store import DataStore
from src.services.family_policy_service import FamilyPolicyService
from src.services.profile_service import ProfileService
from src.services.relay_message_service import RelayMessageService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FamilyContextService:
    """Family-side context with strict memory isolation from the elder chat path."""

    FAMILY_CHAT_HISTORY_FILE = "family_chat_history.json"
    FAMILY_CHAT_MEMORY_FILE = "family_chat_memory.jsonl"
    ASSESSMENTS_FILE = "mental_assessments.jsonl"
    INTERVENTION_LOG_FILE = "intervention_log.jsonl"

    def __init__(
        self,
        store: Optional[DataStore] = None,
        *,
        care_plan_service: Optional[CarePlanService] = None,
        family_policy_service: Optional[FamilyPolicyService] = None,
        relay_message_service: Optional[RelayMessageService] = None,
        profile_service: Optional[ProfileService] = None,
    ):
        self.store = store or DataStore()
        self.care_plan_service = care_plan_service or CarePlanService(self.store)
        self.relay_message_service = relay_message_service or RelayMessageService(self.store)
        self.family_policy_service = family_policy_service or FamilyPolicyService(
            self.store,
            self.relay_message_service,
        )
        self.profile_service = profile_service or ProfileService(self.store)
        self._locks: Dict[str, RLock] = {}
        self._locks_guard = RLock()

    def build_elder_summary(
        self,
        elder_user_id: str,
        child_user_id: str,
        *,
        assessment_limit: int = 5,
        alert_limit: int = 10,
        intervention_limit: int = 10,
    ) -> Dict[str, Any]:
        elder_id = self._normalize_id(elder_user_id, "elder_user_id")
        child_id = self._normalize_id(child_user_id, "child_user_id")
        care_plan = self.care_plan_service.get_plan(elder_id)
        assessments = self.get_recent_assessments(elder_id, limit=assessment_limit)
        latest = assessments[-1] if assessments else None
        visible_evidence = [self._family_visible_evidence(item) for item in assessments]
        visible_evidence = [item for item in visible_evidence if item is not None]
        family_alerts = self.get_family_alerts(elder_id, limit=alert_limit)
        policy = self.family_policy_service.get_policy(elder_id, child_id)
        profile = self.profile_service.get_profile(elder_id)
        interventions = self.get_recent_interventions(elder_id, limit=intervention_limit)

        risk_tier = latest.risk_tier if latest is not None else care_plan.risk_tier
        primary_state = latest.primary_state if latest is not None else care_plan.active_domain
        suggested_action = (
            latest.family_suggestion
            if latest is not None and latest.family_suggestion
            else self._suggested_action_from_plan(care_plan)
        )

        return {
            "elder_user_id": elder_id,
            "child_user_id": child_id,
            "summary": {
                "profile_name": profile.get("name", ""),
                "risk_tier": risk_tier,
                "primary_state": primary_state,
                "recent_trend": self._recent_trend(assessments),
                "care_plan_stage": care_plan.current_stage,
                "care_plan_goal": care_plan.next_turn_goal or care_plan.stage_goal,
                "suggested_family_action": suggested_action,
            },
            "care_plan": self._model_to_dict(care_plan),
            "visible_evidence": visible_evidence,
            "recent_family_alerts": family_alerts,
            "family_policy": self._model_to_dict(policy),
            "recent_interventions": interventions,
        }

    def build_family_chat_context(
        self,
        elder_user_id: str,
        child_user_id: str,
        *,
        family_history_limit: int = 8,
    ) -> Dict[str, Any]:
        summary = self.build_elder_summary(elder_user_id, child_user_id)
        summary["recent_family_history"] = self.get_recent_family_history(
            elder_user_id,
            child_user_id,
            limit=family_history_limit,
        )
        return summary

    def get_recent_family_history(
        self,
        elder_user_id: str,
        child_user_id: str,
        *,
        limit: Optional[int] = 10,
    ) -> List[Dict[str, Any]]:
        elder_id = self._normalize_id(elder_user_id, "elder_user_id")
        child_id = self._normalize_id(child_user_id, "child_user_id")
        raw = self.store.read_user_json(
            elder_id,
            self._family_path(child_id, self.FAMILY_CHAT_HISTORY_FILE),
            default=[],
        )
        if not isinstance(raw, list):
            return []
        records = [item for item in raw if self._is_chat_record(item)]
        return records[-limit:] if limit is not None else records

    def add_family_turn(
        self,
        elder_user_id: str,
        child_user_id: str,
        user_input: str,
        assistant_response: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        elder_id = self._normalize_id(elder_user_id, "elder_user_id")
        child_id = self._normalize_id(child_user_id, "child_user_id")
        timestamp = utc_now().isoformat()
        with self._lock_for(elder_id, child_id):
            history = self.get_recent_family_history(elder_id, child_id, limit=None)
            history.append(
                {
                    "timestamp": timestamp,
                    "role": "child",
                    "content": str(user_input or ""),
                    "metadata": dict(metadata or {}),
                }
            )
            history.append(
                {
                    "timestamp": timestamp,
                    "role": "assistant",
                    "content": str(assistant_response or ""),
                    "metadata": dict(metadata or {}),
                }
            )
            if len(history) > 120:
                history = history[-80:]
            self.store.write_user_json(
                elder_id,
                self._family_path(child_id, self.FAMILY_CHAT_HISTORY_FILE),
                history,
            )
            self.store.append_user_jsonl(
                elder_id,
                self._family_path(child_id, self.FAMILY_CHAT_MEMORY_FILE),
                {
                    "timestamp": timestamp,
                    "elder_user_id": elder_id,
                    "child_user_id": child_id,
                    "child_message": str(user_input or ""),
                    "assistant_response": str(assistant_response or ""),
                    "metadata": dict(metadata or {}),
                },
            )

    def get_recent_assessments(self, elder_user_id: str, *, limit: int = 5) -> List[MentalRiskAssessment]:
        elder_id = self._normalize_id(elder_user_id, "elder_user_id")
        raw_records = self.store.read_user_jsonl(elder_id, self.ASSESSMENTS_FILE, limit=limit)
        assessments: List[MentalRiskAssessment] = []
        for item in raw_records:
            if not isinstance(item, dict):
                continue
            try:
                assessments.append(self._parse_assessment(item))
            except Exception:
                continue
        return assessments

    def get_family_alerts(self, elder_user_id: str, *, limit: int = 10) -> List[Dict[str, Any]]:
        elder_id = self._normalize_id(elder_user_id, "elder_user_id")
        messages = self.relay_message_service.list_messages(elder_id, target="family")
        selected = messages[-max(limit, 0):]
        return [self._family_safe_alert(item) for item in selected]

    def get_recent_interventions(self, elder_user_id: str, *, limit: int = 10) -> List[Dict[str, Any]]:
        elder_id = self._normalize_id(elder_user_id, "elder_user_id")
        raw_records = self.store.read_user_jsonl(elder_id, self.INTERVENTION_LOG_FILE, limit=limit)
        return [self._family_safe_intervention(item) for item in raw_records if isinstance(item, dict)]

    def _family_visible_evidence(self, assessment: MentalRiskAssessment) -> Optional[Dict[str, Any]]:
        if assessment.risk_tier in {"safe", "low"} and not assessment.family_summary:
            return None
        return {
            "id": assessment.id,
            "turn_id": assessment.turn_id,
            "created_at": assessment.created_at.isoformat(),
            "risk_tier": assessment.risk_tier,
            "primary_state": assessment.primary_state,
            "summary": assessment.family_summary or assessment.evidence_summary,
            "family_suggestion": assessment.family_suggestion,
            "raw_quotes": list(assessment.raw_quotes),
            "visibility": "family",
        }

    def _family_safe_alert(self, message: Any) -> Dict[str, Any]:
        data = self._model_to_dict(message)
        payload = dict(data.get("payload") or {})
        payload.pop("community_reason_summary", None)
        payload.pop("community_suggested_actions", None)
        data["payload"] = payload
        return {
            "id": data.get("id"),
            "elder_user_id": data.get("elder_user_id"),
            "risk_tier": data.get("risk_tier"),
            "display_type": data.get("display_type"),
            "title": data.get("title"),
            "content": data.get("content"),
            "reason_summary": data.get("reason_summary"),
            "raw_quotes": data.get("raw_quotes") or [],
            "suggested_actions": data.get("suggested_actions") or [],
            "payload": data.get("payload") or {},
            "status": data.get("status"),
            "created_at": data.get("created_at"),
        }

    def _family_safe_intervention(self, item: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(item.get("payload") or {})
        return {
            "id": item.get("id"),
            "turn_id": item.get("turn_id"),
            "created_at": item.get("created_at"),
            "risk_tier": item.get("risk_tier"),
            "intervention_type": item.get("intervention_type"),
            "stage": item.get("stage"),
            "goal": item.get("goal"),
            "result": item.get("result"),
            "payload": {
                key: value
                for key, value in payload.items()
                if key
                not in {
                    "internal_thought",
                    "chain_of_thought",
                    "community_reason_summary",
                    "community_suggested_actions",
                }
            },
        }

    def _suggested_action_from_plan(self, care_plan: CarePlan) -> str:
        if care_plan.risk_tier == "crisis":
            return "优先确认老人当前是否有人陪伴，用平静短句表达陪伴，不追问刺激性细节。"
        if care_plan.risk_tier in {"medium", "high"}:
            return "建议先用短句表达陪伴和理解，少讲道理，避免责备或辩论。"
        return "保持轻量问候和稳定陪伴，尊重老人当前意愿。"

    def _recent_trend(self, assessments: List[MentalRiskAssessment]) -> str:
        if not assessments:
            return "暂无风险评估记录"
        tiers = [item.risk_tier for item in assessments]
        primary_states = [item.primary_state for item in assessments if item.primary_state]
        if "crisis" in tiers:
            return f"最近 {len(assessments)} 次记录中出现 crisis 信号，主要状态：{primary_states[-1]}"
        if any(tier in {"medium", "high"} for tier in tiers):
            return f"最近风险等级：{tiers}；主要状态：{primary_states[-1]}"
        return f"最近风险等级：{tiers}；整体较稳定"

    def _family_path(self, child_user_id: str, relative_path: str) -> Path:
        return Path("family") / child_user_id / relative_path

    def _lock_for(self, elder_user_id: str, child_user_id: str) -> RLock:
        key = f"{elder_user_id}:{child_user_id}"
        with self._locks_guard:
            return self._locks.setdefault(key, RLock())

    def _is_chat_record(self, item: Any) -> bool:
        return isinstance(item, dict) and "role" in item and "content" in item

    def _parse_assessment(self, item: Dict[str, Any]) -> MentalRiskAssessment:
        if hasattr(MentalRiskAssessment, "model_validate"):
            return MentalRiskAssessment.model_validate(item)
        return MentalRiskAssessment.parse_obj(item)

    def _normalize_id(self, value: str, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        if any(part in text for part in ("/", "\\", "..")):
            raise ValueError(f"{field_name} contains invalid path characters")
        return text

    def _model_to_dict(self, model: Any) -> Dict[str, Any]:
        if hasattr(model, "model_dump"):
            return model.model_dump(mode="json")
        if hasattr(model, "dict"):
            return model.dict()
        return dict(model or {})
