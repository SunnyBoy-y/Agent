import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from src.config import Config
from src.policies.safety_policy import SafetyPolicy
from src.schemas.mental_health import CarePlan, MentalRiskAssessment
from src.schemas.planner import LLMReview, PlannerQueuedAction, PlannerResult
from src.services.care_plan_service import CarePlanService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _ReviewDraft(BaseModel):
    state_summary: str = ""
    suggested_primary_state: Optional[str] = None
    suggested_next_response_mode: Optional[str] = None
    suggested_next_goal: Optional[str] = None
    family_summary: Optional[str] = None
    family_suggestion: Optional[str] = None


class _PlannerDraft(BaseModel):
    target_agent: str
    intervention_goal: str
    care_plan_patch: Dict[str, Any] = Field(default_factory=dict)
    queued_actions: List[PlannerQueuedAction] = Field(default_factory=list)


ReviewCallable = Callable[
    [MentalRiskAssessment, CarePlan, Dict[str, Any]],
    Awaitable[Any],
]
PlannerCallable = Callable[
    [MentalRiskAssessment, CarePlan, LLMReview, Dict[str, Any]],
    Awaitable[Any],
]


class PlanningAgent:
    """Structured LLM review + constrained planner output.

    Live LLM calls are optional at runtime so the planner can preserve deterministic
    safety behavior when credentials or providers are unavailable.
    """

    LIVE_LLM_INVALID_KEYS = {"", "your_api_key_here", "your_qwen_api_key_here"}
    PATCH_KEYS = {
        "active_domain",
        "risk_tier",
        "current_stage",
        "stage_goal",
        "next_turn_goal",
        "target_agent",
        "allowed_interventions",
        "blocked_interventions",
        "abort_conditions",
        "expires_after_turns",
    }
    CRISIS_LOCKED_KEYS = {
        "active_domain",
        "risk_tier",
        "current_stage",
        "target_agent",
        "allowed_interventions",
        "blocked_interventions",
        "abort_conditions",
    }

    def __init__(
        self,
        care_plan_service: Optional[CarePlanService] = None,
        *,
        safety_policy: Optional[SafetyPolicy] = None,
        review_timeout_seconds: float = 0.8,
        planner_timeout_seconds: float = 0.8,
        review_callable: Optional[ReviewCallable] = None,
        planner_callable: Optional[PlannerCallable] = None,
        enable_live_llm: bool = True,
    ):
        self.care_plan_service = care_plan_service or CarePlanService()
        self.safety_policy = safety_policy or SafetyPolicy()
        self.review_timeout_seconds = review_timeout_seconds
        self.planner_timeout_seconds = planner_timeout_seconds
        self.review_callable = review_callable
        self.planner_callable = planner_callable
        self.enable_live_llm = enable_live_llm
        self._review_chain = None
        self._planner_chain = None

    async def arun(
        self,
        assessment: MentalRiskAssessment,
        current_plan: CarePlan,
        context: Optional[Dict[str, Any]] = None,
    ) -> PlannerResult:
        context = dict(context or {})
        review = await self._review_assessment(assessment, current_plan, context)
        return await self._plan_next_step(assessment, current_plan, review, context)

    async def _review_assessment(
        self,
        assessment: MentalRiskAssessment,
        current_plan: CarePlan,
        context: Dict[str, Any],
    ) -> LLMReview:
        started_at = utc_now()
        review = LLMReview(
            status="pending",
            source_turn_id=assessment.turn_id,
            review_started_at=started_at,
            expires_at=started_at + timedelta(minutes=5),
        )
        review_callable = self.review_callable or self._live_review_callable()
        if review_callable is None:
            return self._finalize_review(review, "failed", error="llm_unavailable")

        try:
            draft = await asyncio.wait_for(
                review_callable(assessment, current_plan, context),
                timeout=self.review_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return self._finalize_review(review, "timeout", error="review_timeout")
        except Exception as exc:
            return self._finalize_review(review, "failed", error=str(exc))

        payload = self._coerce_dict(draft)
        return self._finalize_review(
            review,
            "completed",
            state_summary=self._sanitize_text(payload.get("state_summary", "")),
            suggested_primary_state=self._optional_text(payload.get("suggested_primary_state")),
            suggested_next_response_mode=self._optional_text(payload.get("suggested_next_response_mode")),
            suggested_next_goal=self._optional_text(payload.get("suggested_next_goal")),
            family_summary=self._optional_text(payload.get("family_summary")),
            family_suggestion=self._optional_text(payload.get("family_suggestion")),
        )

    async def _plan_next_step(
        self,
        assessment: MentalRiskAssessment,
        current_plan: CarePlan,
        review: LLMReview,
        context: Dict[str, Any],
    ) -> PlannerResult:
        planner_callable = self.planner_callable or self._live_planner_callable()
        base_patch = self.care_plan_service.patch_from_assessment(assessment)
        fallback = self._fallback_result(assessment, review, base_patch)

        if planner_callable is None:
            return fallback

        try:
            draft = await asyncio.wait_for(
                planner_callable(assessment, current_plan, review, context),
                timeout=self.planner_timeout_seconds,
            )
        except (asyncio.TimeoutError, Exception):
            return fallback

        payload = self._coerce_dict(draft)
        safe_patch = self._sanitize_patch(
            assessment=assessment,
            base_patch=base_patch,
            candidate_patch=payload.get("care_plan_patch") or {},
            review=review,
        )
        actions = self._sanitize_actions(
            payload.get("queued_actions") or [],
            assessment=assessment,
        )
        return PlannerResult(
            source_turn_id=assessment.turn_id,
            target_agent=str(safe_patch["target_agent"]),
            intervention_goal=self._sanitize_text(
                payload.get("intervention_goal") or safe_patch.get("current_stage") or assessment.next_goal
            ),
            care_plan_patch=safe_patch,
            queued_actions=actions or self._fallback_actions(assessment),
            review=review,
            used_fallback=False,
        )

    def _fallback_result(
        self,
        assessment: MentalRiskAssessment,
        review: LLMReview,
        base_patch: Dict[str, Any],
    ) -> PlannerResult:
        patch = self._sanitize_patch(
            assessment=assessment,
            base_patch=base_patch,
            candidate_patch={},
            review=review,
        )
        return PlannerResult(
            source_turn_id=assessment.turn_id,
            target_agent=str(patch["target_agent"]),
            intervention_goal=str(patch.get("current_stage") or assessment.next_goal or "companionship"),
            care_plan_patch=patch,
            queued_actions=self._fallback_actions(assessment),
            review=review,
            used_fallback=True,
        )

    def _fallback_actions(self, assessment: MentalRiskAssessment) -> List[PlannerQueuedAction]:
        if assessment.risk_tier == "crisis":
            actions = [
                PlannerQueuedAction(
                    type="family_message",
                    target="family",
                    display_type="alert",
                    reason_summary=assessment.family_summary or assessment.evidence_summary,
                    suggested_actions=[assessment.family_suggestion] if assessment.family_suggestion else [],
                    payload={"assessment_id": assessment.id, "turn_id": assessment.turn_id},
                ),
                PlannerQueuedAction(
                    type="community_alert",
                    target="community",
                    display_type="sos",
                    reason_summary=assessment.community_reason_summary or assessment.evidence_summary,
                    suggested_actions=list(assessment.community_suggested_actions),
                    payload={"assessment_id": assessment.id, "turn_id": assessment.turn_id},
                ),
            ]
            return [self._finalize_action_contract(action, assessment) for action in actions]
        if assessment.risk_tier in {"medium", "high"}:
            actions = [
                PlannerQueuedAction(
                    type="family_message",
                    target="family",
                    display_type="alert",
                    reason_summary=assessment.family_summary or assessment.evidence_summary,
                    suggested_actions=[assessment.family_suggestion] if assessment.family_suggestion else [],
                    payload={"assessment_id": assessment.id, "turn_id": assessment.turn_id},
                )
            ]
            return [self._finalize_action_contract(action, assessment) for action in actions]
        return []

    def _sanitize_patch(
        self,
        *,
        assessment: MentalRiskAssessment,
        base_patch: Dict[str, Any],
        candidate_patch: Dict[str, Any],
        review: LLMReview,
    ) -> Dict[str, Any]:
        patch = dict(base_patch)
        for key, value in dict(candidate_patch or {}).items():
            if key in self.PATCH_KEYS:
                patch[key] = value

        # LLM review can enrich goals, but it never re-rates risk.
        patch["risk_tier"] = base_patch["risk_tier"]
        if review.suggested_next_goal:
            patch["next_turn_goal"] = review.suggested_next_goal

        if assessment.risk_tier == "crisis":
            for key in self.CRISIS_LOCKED_KEYS:
                patch[key] = base_patch[key]

        for text_key in ("stage_goal", "next_turn_goal"):
            if text_key in patch:
                patch[text_key] = self._sanitize_text(str(patch[text_key] or ""))

        for list_key in ("allowed_interventions", "blocked_interventions", "abort_conditions"):
            value = patch.get(list_key)
            if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
                patch[list_key] = [str(item) for item in value]
            elif value is None:
                patch[list_key] = []
            else:
                patch[list_key] = [str(value)]

        patch["target_agent"] = str(patch.get("target_agent") or base_patch["target_agent"])
        patch["current_stage"] = str(patch.get("current_stage") or base_patch["current_stage"])
        patch["active_domain"] = str(patch.get("active_domain") or base_patch["active_domain"])
        patch["expires_after_turns"] = max(0, int(patch.get("expires_after_turns", base_patch["expires_after_turns"])))
        return patch

    def _sanitize_actions(
        self,
        actions: Iterable[Any],
        *,
        assessment: MentalRiskAssessment,
    ) -> List[PlannerQueuedAction]:
        sanitized: List[PlannerQueuedAction] = []
        for action in actions:
            try:
                parsed = action if isinstance(action, PlannerQueuedAction) else PlannerQueuedAction(**self._coerce_dict(action))
            except Exception:
                continue
            parsed.content = self._sanitize_text(parsed.content)
            if parsed.reason_summary:
                parsed.reason_summary = self._sanitize_text(parsed.reason_summary)
            parsed.suggested_actions = [self._sanitize_text(item) for item in parsed.suggested_actions]
            if assessment.risk_tier == "crisis" and parsed.type == "quiet_message":
                continue
            sanitized.append(self._finalize_action_contract(parsed, assessment))
        return sanitized

    def _finalize_action_contract(
        self,
        action: PlannerQueuedAction,
        assessment: MentalRiskAssessment,
    ) -> PlannerQueuedAction:
        action.target_channel = action.target_channel or self._default_target_channel(action)
        action.visibility_scope = action.visibility_scope or self._default_visibility_scope(action)
        action.consent_required = bool(
            action.consent_required
            or self._default_consent_required(action)
        )
        action.approval_required = bool(
            action.approval_required
            or self._default_approval_required(action, assessment)
        )
        action.idempotency_key = action.idempotency_key or self._build_action_idempotency_key(
            action,
            assessment,
        )
        action.payload = dict(action.payload or {})
        action.payload.setdefault("contract_version", "target19.v1")
        action.payload.setdefault("target_channel", action.target_channel)
        action.payload.setdefault("visibility_scope", action.visibility_scope)
        action.payload.setdefault("consent_required", action.consent_required)
        action.payload.setdefault("approval_required", action.approval_required)
        action.payload.setdefault("idempotency_key", action.idempotency_key)
        action.payload.setdefault("assessment_id", assessment.id)
        action.payload.setdefault("turn_id", assessment.turn_id)
        return action

    def _default_target_channel(self, action: PlannerQueuedAction) -> str:
        if action.type == "family_message":
            return "family"
        if action.type == "community_alert":
            return "community"
        if action.type in {"schedule_music", "schedule_story"}:
            return "frontend"
        if action.type == "quiet_message":
            target = str(action.target or "elder")
            return target if target in {"elder", "family", "community", "frontend"} else "elder"
        return "background"

    def _default_visibility_scope(self, action: PlannerQueuedAction) -> str:
        if action.type == "family_message":
            return "family"
        if action.type == "community_alert":
            return "community"
        if action.type in {"quiet_message", "schedule_music", "schedule_story"}:
            return "elder"
        return "internal"

    def _default_consent_required(self, action: PlannerQueuedAction) -> bool:
        if action.type in {"schedule_music", "schedule_story"}:
            return True
        if action.type == "quiet_message":
            actor_role = str(action.payload.get("actor_role") or "system")
            direction = str(action.payload.get("direction") or "system_to_elder")
            return actor_role == "family" or direction == "family_to_elder"
        return False

    def _default_approval_required(
        self,
        action: PlannerQueuedAction,
        assessment: MentalRiskAssessment,
    ) -> bool:
        return action.type == "community_alert" and assessment.risk_tier != "crisis"

    def _build_action_idempotency_key(
        self,
        action: PlannerQueuedAction,
        assessment: MentalRiskAssessment,
    ) -> str:
        payload = {
            "elder_user_id": assessment.elder_user_id,
            "turn_id": assessment.turn_id,
            "assessment_id": assessment.id,
            "type": action.type,
            "target_channel": action.target_channel,
            "visibility_scope": action.visibility_scope,
            "target": action.target,
            "content": action.content,
            "reason_summary": action.reason_summary,
            "payload": action.payload,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        return f"planner_action:{assessment.elder_user_id}:{assessment.turn_id}:{action.type}:{digest}"

    def _finalize_review(self, review: LLMReview, status: str, **updates: Any) -> LLMReview:
        reviewed_at = utc_now()
        data = self._coerce_dict(review)
        data.update(updates)
        data["status"] = status
        data["reviewed_at"] = reviewed_at
        data["latency_ms"] = max(
            0,
            int((reviewed_at - review.review_started_at).total_seconds() * 1000),
        )
        return LLMReview(**data)

    def _optional_text(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = self._sanitize_text(str(value))
        return text or None

    def _sanitize_text(self, value: str) -> str:
        if not value:
            return ""
        return self.safety_policy.sanitize_response(value)

    def _coerce_dict(self, value: Any) -> Dict[str, Any]:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="python")
        if hasattr(value, "dict"):
            return value.dict()
        if isinstance(value, dict):
            return dict(value)
        return {}

    def _live_review_callable(self) -> Optional[ReviewCallable]:
        if self.review_callable is not None:
            return self.review_callable
        if not self._can_use_live_llm():
            return None

        async def _call(
            assessment: MentalRiskAssessment,
            current_plan: CarePlan,
            context: Dict[str, Any],
        ) -> Any:
            chain = self._get_review_chain()
            return await chain.ainvoke(self._review_prompt_payload(assessment, current_plan, context))

        return _call

    def _live_planner_callable(self) -> Optional[PlannerCallable]:
        if self.planner_callable is not None:
            return self.planner_callable
        if not self._can_use_live_llm():
            return None

        async def _call(
            assessment: MentalRiskAssessment,
            current_plan: CarePlan,
            review: LLMReview,
            context: Dict[str, Any],
        ) -> Any:
            chain = self._get_planner_chain()
            return await chain.ainvoke(
                self._planner_prompt_payload(assessment, current_plan, review, context)
            )

        return _call

    def _can_use_live_llm(self) -> bool:
        key = str(Config.OPENAI_API_KEY or "").strip()
        return self.enable_live_llm and key not in self.LIVE_LLM_INVALID_KEYS

    def _get_review_chain(self):
        if self._review_chain is None:
            from langchain_core.prompts import ChatPromptTemplate
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(
                openai_api_key=Config.OPENAI_API_KEY,
                openai_api_base=Config.OPENAI_API_BASE,
                model_name=Config.MODEL_NAME,
                temperature=0,
                timeout=self.review_timeout_seconds,
                max_retries=1,
            ).with_structured_output(_ReviewDraft)
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You review elder-support risk evidence. "
                        "Return only structured semantic review. Never diagnose, never give medical advice, "
                        "never downgrade a crisis signal, never include chain-of-thought.",
                    ),
                    ("human", "{payload}"),
                ]
            )
            self._review_chain = prompt | llm
        return self._review_chain

    def _get_planner_chain(self):
        if self._planner_chain is None:
            from langchain_core.prompts import ChatPromptTemplate
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(
                openai_api_key=Config.OPENAI_API_KEY,
                openai_api_base=Config.OPENAI_API_BASE,
                model_name=Config.MODEL_NAME,
                temperature=0,
                timeout=self.planner_timeout_seconds,
                max_retries=1,
            ).with_structured_output(_PlannerDraft)
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a constrained ReAct-style planner for elder support. "
                        "Return only structured fields: target_agent, intervention_goal, care_plan_patch, queued_actions. "
                        "Do not reveal thoughts. Do not diagnose or provide medical advice. "
                        "Risk tier is fixed by the deterministic assessment.",
                    ),
                    ("human", "{payload}"),
                ]
            )
            self._planner_chain = prompt | llm
        return self._planner_chain

    def _review_prompt_payload(
        self,
        assessment: MentalRiskAssessment,
        current_plan: CarePlan,
        context: Dict[str, Any],
    ) -> Dict[str, str]:
        payload = {
            "assessment": self._coerce_dict(assessment),
            "current_plan": self._coerce_dict(current_plan),
            "recent_history": context.get("recent_history") or [],
            "recent_history_text": context.get("recent_history_text") or "",
        }
        return {"payload": json.dumps(payload, ensure_ascii=False)}

    def _planner_prompt_payload(
        self,
        assessment: MentalRiskAssessment,
        current_plan: CarePlan,
        review: LLMReview,
        context: Dict[str, Any],
    ) -> Dict[str, str]:
        payload = {
            "assessment": self._coerce_dict(assessment),
            "current_plan": self._coerce_dict(current_plan),
            "review": self._coerce_dict(review),
            "allowed_actions": [
                "family_message",
                "community_alert",
                "quiet_message",
                "schedule_music",
                "schedule_story",
            ],
            "recent_history": context.get("recent_history") or [],
        }
        return {"payload": json.dumps(payload, ensure_ascii=False)}
