from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


ActionType = Literal["music", "story", "medication", "community_activity", "quiet_message", "other"]
ActionStatus = Literal["pending", "started", "completed", "interrupted", "cancelled", "failed"]


class ActionSession(BaseModel):
    action_id: str
    elder_user_id: str
    action_type: ActionType
    status: ActionStatus = "pending"
    payload: Dict[str, Any] = Field(default_factory=dict)
    post_reply: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    ended_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    completed_intervention: bool = False
    result: Dict[str, Any] = Field(default_factory=dict)


class ActionCompleteRequest(BaseModel):
    action_id: str
    elder_user_id: str
    action_type: ActionType
    status: Literal["completed", "interrupted", "cancelled", "failed"]
    music_name: Optional[str] = None
    played_seconds: Optional[float] = Field(default=None, ge=0)
    total_seconds: Optional[float] = Field(default=None, ge=0)
    interrupt_reason: Optional[str] = None
    finished_at: Optional[datetime] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class ActionConsentRequest(BaseModel):
    elder_user_id: str
    accepted: bool
    text: Optional[str] = None
    source: str = "unknown"
    decided_at: Optional[datetime] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
