from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, Optional

from src.schemas.mental_health import CarePlan, MentalRiskAssessment
from src.services.data_store import DataStore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CarePlanService:
    """Versioned per-user care-plan persistence with compare-and-swap writes."""

    PLAN_FILE = "care_plan.json"
    HISTORY_FILE = "care_plan_history.jsonl"

    def __init__(self, store: Optional[DataStore] = None):
        self.store = store or DataStore()
        self._locks: Dict[str, RLock] = {}
        self._locks_guard = RLock()

    def get_plan(self, elder_user_id: str) -> CarePlan:
        user_id = self._normalize_user_id(elder_user_id)
        with self._lock_for(user_id):
            raw = self.store.read_user_json(user_id, self.PLAN_FILE, default=None)
            if not isinstance(raw, dict):
                return CarePlan(elder_user_id=user_id)
            return self._parse_plan(raw, user_id)

    def current_version(self, elder_user_id: str) -> int:
        return self.get_plan(elder_user_id).version

    def update_plan(
        self,
        elder_user_id: str,
        patch: Dict[str, Any],
        source_turn_id: Optional[str],
        *,
        expected_version: Optional[int] = None,
        updated_by: str = "system",
    ) -> CarePlan:
        user_id = self._normalize_user_id(elder_user_id)
        with self._lock_for(user_id):
            current = self._load_plan_unlocked(user_id)
            if expected_version is not None and current.version != expected_version:
                raise ValueError(
                    f"CarePlan version mismatch: expected {expected_version}, got {current.version}"
                )
            next_plan = self._apply_patch(
                current,
                patch,
                source_turn_id=source_turn_id,
                updated_by=updated_by,
            )
            self._save_plan_unlocked(user_id, next_plan)
            return next_plan

    def compare_and_swap(
        self,
        elder_user_id: str,
        expected_version: int,
        patch: Dict[str, Any],
        source_turn_id: Optional[str],
        *,
        updated_by: str = "planner",
    ) -> bool:
        user_id = self._normalize_user_id(elder_user_id)
        with self._lock_for(user_id):
            current = self._load_plan_unlocked(user_id)
            if current.version != expected_version:
                return False
            next_plan = self._apply_patch(
                current,
                patch,
                source_turn_id=source_turn_id,
                updated_by=updated_by,
            )
            self._save_plan_unlocked(user_id, next_plan)
            return True

    def create_from_assessment(
        self,
        assessment: MentalRiskAssessment,
        *,
        expected_version: Optional[int] = None,
        updated_by: str = "assessment",
    ) -> CarePlan:
        return self.update_plan(
            assessment.elder_user_id,
            self.patch_from_assessment(assessment),
            assessment.turn_id,
            expected_version=expected_version,
            updated_by=updated_by,
        )

    def patch_from_assessment(self, assessment: MentalRiskAssessment) -> Dict[str, Any]:
        state = assessment.primary_state
        tier = assessment.risk_tier

        if tier == "crisis":
            return {
                "active_domain": "crisis",
                "risk_tier": "crisis",
                "current_stage": "crisis.safety_grounding",
                "stage_goal": "stabilize immediate safety",
                "next_turn_goal": assessment.next_goal or "keep the next turn grounded and safe",
                "target_agent": "mental_health_agent",
                "allowed_interventions": [
                    "short_companion",
                    "family_relay",
                    "community_sos",
                ],
                "blocked_interventions": [
                    "diagnosis",
                    "medical_advice",
                    "stimulating_topic_shift",
                ],
                "abort_conditions": ["newer_turn", "safety_escalation"],
                "expires_after_turns": 2,
            }

        if assessment.safety_flags.medical_emergency:
            return {
                "active_domain": "physical_emergency",
                "risk_tier": tier,
                "current_stage": "medical.safety_check",
                "stage_goal": "keep the response brief and safety-oriented",
                "next_turn_goal": assessment.next_goal or "continue safety-oriented follow-up",
                "target_agent": "medical_agent",
                "allowed_interventions": ["brief_check", "safety_response"],
                "blocked_interventions": ["diagnosis", "medical_advice", "medication_adjustment"],
                "abort_conditions": ["newer_turn", "crisis_signal"],
                "expires_after_turns": 1,
            }

        state_to_stage = {
            "anxiety": ("anxiety", "anxiety.emotional_first_aid"),
            "depressive_low_mood": ("depression", "depression.low_energy_companion"),
            "manic_activation": ("bipolar_mania", "bipolar_mania.accept_and_slow"),
        }
        if state in state_to_stage:
            active_domain, current_stage = state_to_stage[state]
            return {
                "active_domain": active_domain,
                "risk_tier": tier,
                "current_stage": current_stage,
                "stage_goal": assessment.next_goal or "continue low-stimulation support",
                "next_turn_goal": assessment.next_goal or "continue warm mental-health support",
                "target_agent": "mental_health_agent",
                "allowed_interventions": [
                    "companionship",
                    "grounding",
                    "micro_action",
                ],
                "blocked_interventions": [
                    "diagnosis",
                    "medical_advice",
                    "high_stimulation",
                ],
                "abort_conditions": ["newer_turn", "crisis_signal"],
                "expires_after_turns": 2,
            }

        return {
            "active_domain": "general",
            "risk_tier": tier,
            "current_stage": "companionship",
            "stage_goal": assessment.next_goal or "continue warm companionship",
            "next_turn_goal": assessment.next_goal or "continue warm companionship",
            "target_agent": "emotional_agent",
            "allowed_interventions": ["companionship", "light_topic_shift"],
            "blocked_interventions": ["diagnosis", "medical_advice"],
            "abort_conditions": ["newer_turn", "crisis_signal"],
            "expires_after_turns": 2,
        }

    def _apply_patch(
        self,
        current: CarePlan,
        patch: Dict[str, Any],
        *,
        source_turn_id: Optional[str],
        updated_by: str,
    ) -> CarePlan:
        data = self._model_to_dict(current)
        data.update(dict(patch or {}))
        data["elder_user_id"] = current.elder_user_id
        data["version"] = current.version + 1
        data["source_turn_id"] = source_turn_id
        data["updated_by"] = updated_by
        data["updated_at"] = utc_now()
        return CarePlan(**data)

    def _save_plan_unlocked(self, elder_user_id: str, plan: CarePlan) -> None:
        self.store.write_user_json(elder_user_id, self.PLAN_FILE, plan)
        self.store.append_user_jsonl(elder_user_id, self.HISTORY_FILE, plan)

    def _load_plan_unlocked(self, elder_user_id: str) -> CarePlan:
        raw = self.store.read_user_json(elder_user_id, self.PLAN_FILE, default=None)
        if not isinstance(raw, dict):
            return CarePlan(elder_user_id=elder_user_id)
        return self._parse_plan(raw, elder_user_id)

    def _parse_plan(self, raw: Dict[str, Any], elder_user_id: str) -> CarePlan:
        payload = dict(raw)
        payload["elder_user_id"] = elder_user_id
        if hasattr(CarePlan, "model_validate"):
            return CarePlan.model_validate(payload)
        return CarePlan.parse_obj(payload)

    def _lock_for(self, elder_user_id: str) -> RLock:
        with self._locks_guard:
            return self._locks.setdefault(elder_user_id, RLock())

    def _normalize_user_id(self, elder_user_id: str) -> str:
        text = str(elder_user_id or "").strip()
        if not text:
            raise ValueError("elder_user_id is required")
        return text

    def _model_to_dict(self, model: Any) -> Dict[str, Any]:
        if hasattr(model, "model_dump"):
            return model.model_dump(mode="python")
        if hasattr(model, "dict"):
            return model.dict()
        return dict(model or {})
