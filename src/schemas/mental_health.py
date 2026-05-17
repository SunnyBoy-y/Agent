from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


RiskTier = Literal["safe", "low", "medium", "high", "crisis"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DetectedState(BaseModel):
    state: str
    severity: int = Field(default=0, ge=0, le=10)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: List[str] = Field(default_factory=list)
    source: str = "text"


class SafetyFlags(BaseModel):
    self_harm_ideation: bool = False
    explicit_death_wish: bool = False
    medical_emergency: bool = False
    fraud_risk: bool = False
    manic_activation: bool = False


class VisibilityPolicy(BaseModel):
    elder: str = "none"
    family: str = "summary"
    community: str = "none"


class MentalRiskAssessment(BaseModel):
    id: Optional[str] = None
    turn_id: str
    elder_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    primary_state: str = "unknown"
    detected_states: List[DetectedState] = Field(default_factory=list)
    risk_tier: RiskTier = "safe"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    score: int = Field(default=0, ge=0)
    evidence_summary: str = ""
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    raw_quotes: List[str] = Field(default_factory=list)
    safety_flags: SafetyFlags = Field(default_factory=SafetyFlags)
    next_response_mode: str = "companionship"
    next_goal: str = ""
    elder_wording: Optional[str] = None
    family_summary: Optional[str] = None
    family_suggestion: Optional[str] = None
    community_reason_summary: Optional[str] = None
    community_suggested_actions: List[str] = Field(default_factory=list)
    visibility: VisibilityPolicy = Field(default_factory=VisibilityPolicy)
    llm_review: Dict[str, Any] = Field(default_factory=dict)


class CarePlan(BaseModel):
    elder_user_id: str
    version: int = Field(default=0, ge=0)
    source_turn_id: Optional[str] = None
    active_domain: str = "general"
    risk_tier: RiskTier = "safe"
    current_stage: str = "companionship"
    stage_goal: str = ""
    next_turn_goal: str = ""
    target_agent: str = "emotional_agent"
    allowed_interventions: List[str] = Field(default_factory=list)
    blocked_interventions: List[str] = Field(default_factory=list)
    abort_conditions: List[str] = Field(default_factory=list)
    expires_after_turns: int = Field(default=2, ge=0)
    updated_by: str = "system"
    updated_at: datetime = Field(default_factory=utc_now)


class InterventionLog(BaseModel):
    id: Optional[str] = None
    turn_id: str
    elder_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    risk_tier: RiskTier = "safe"
    intervention_type: str
    stage: str = ""
    goal: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[str] = None
