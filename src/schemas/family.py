from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


TopicStatus = Literal["active", "inactive", "exhausted"]


class SuggestedTopic(BaseModel):
    topic_id: str
    title: str
    content: str = ""
    max_consumptions: int = Field(default=1, ge=1)
    consumed_count: int = Field(default=0, ge=0)
    min_interval_hours: int = Field(default=24, ge=0)
    last_consumed_at: Optional[datetime] = None
    status: TopicStatus = "active"
    tags: List[str] = Field(default_factory=list)
    long_term_goal: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FamilyPolicy(BaseModel):
    elder_user_id: str
    child_user_id: str
    preferred_tone: str = ""
    suggested_topics: List[SuggestedTopic] = Field(default_factory=list)
    preferred_actions: List[str] = Field(default_factory=list)
    long_term_goals: List[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


QuietMessageConsent = Literal["accepted", "rejected"]
QuietMessageConsentSource = Literal["button", "semantic", "system"]


class FamilyPolicyUpdateRequest(BaseModel):
    elder_user_id: str
    child_user_id: str
    actor_role: str = "child"
    policy: Dict[str, Any] = Field(default_factory=dict)


class FamilyMessageCreateRequest(BaseModel):
    elder_user_id: str
    child_user_id: str
    actor_role: str = "child"
    direction: str = "child_to_elder"
    message_type: Literal["quiet_message"] = "quiet_message"
    content: str
    title: str = ""
    priority: Literal["low", "normal", "high"] = "normal"
    payload: Dict[str, Any] = Field(default_factory=dict)


class QuietMessageConsentRequest(BaseModel):
    elder_user_id: str
    consent: Optional[QuietMessageConsent] = None
    source: QuietMessageConsentSource = "button"
    raw_text: Optional[str] = None


class FamilyChatRequest(BaseModel):
    elder_user_id: str
    child_user_id: str
    message: str
    context: Dict[str, Any] = Field(default_factory=dict)
