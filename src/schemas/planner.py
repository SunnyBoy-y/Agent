from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


PlannerPriority = Literal["safe", "low", "medium", "high", "crisis"]
PlannerJobStatus = Literal[
    "queued",
    "running",
    "cancel_requested",
    "stale_discarded",
    "completed",
    "failed",
]
PlannerServiceStatus = Literal[
    "idle",
    "queued",
    "running",
    "cancel_requested",
    "stale_discarded",
    "completed",
    "failed",
]
LLMReviewStatus = Literal["pending", "completed", "timeout", "failed", "skipped"]
PlannerQueuedActionType = Literal[
    "family_message",
    "community_alert",
    "quiet_message",
    "schedule_music",
    "schedule_story",
]
PlannerTargetChannel = Literal["elder", "family", "community", "frontend", "background"]
PlannerVisibilityScope = Literal["elder", "family", "community", "internal"]


class PlannerJob(BaseModel):
    job_id: str
    elder_user_id: str
    assessment_id: Optional[str] = None
    base_turn_id: str
    base_care_plan_version: int = Field(default=0, ge=0)
    priority: PlannerPriority = "safe"
    status: PlannerJobStatus = "queued"
    created_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    latency_ms: Optional[int] = Field(default=None, ge=0)
    stale_reason: Optional[str] = None
    error: Optional[str] = None
    review_status: Optional[LLMReviewStatus] = None
    used_fallback: bool = False


class PlannerStatus(BaseModel):
    elder_user_id: str
    status: PlannerServiceStatus = "idle"
    latest_turn_id: Optional[str] = None
    running_job_id: Optional[str] = None
    last_completed_job_id: Optional[str] = None
    last_discarded_job_id: Optional[str] = None
    last_error: Optional[str] = None
    last_review_status: Optional[LLMReviewStatus] = None
    last_used_fallback: Optional[bool] = None
    updated_at: datetime = Field(default_factory=utc_now)


class LLMReview(BaseModel):
    status: LLMReviewStatus = "pending"
    source_turn_id: str
    review_started_at: datetime = Field(default_factory=utc_now)
    reviewed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    latency_ms: Optional[int] = Field(default=None, ge=0)
    state_summary: str = ""
    suggested_primary_state: Optional[str] = None
    suggested_next_response_mode: Optional[str] = None
    suggested_next_goal: Optional[str] = None
    family_summary: Optional[str] = None
    family_suggestion: Optional[str] = None
    error: Optional[str] = None


class PlannerQueuedAction(BaseModel):
    type: PlannerQueuedActionType
    target: Optional[str] = None
    target_channel: Optional[PlannerTargetChannel] = None
    display_type: Optional[str] = None
    content: str = ""
    reason_summary: Optional[str] = None
    suggested_actions: List[str] = Field(default_factory=list)
    consent_required: bool = False
    approval_required: bool = False
    visibility_scope: Optional[PlannerVisibilityScope] = None
    idempotency_key: Optional[str] = None
    action_session_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class PlannerResult(BaseModel):
    source_turn_id: str
    target_agent: str
    intervention_goal: str
    care_plan_patch: Dict[str, Any] = Field(default_factory=dict)
    queued_actions: List[PlannerQueuedAction] = Field(default_factory=list)
    review: LLMReview
    used_fallback: bool = False
