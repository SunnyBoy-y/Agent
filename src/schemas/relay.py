from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from src.schemas.mental_health import RiskTier


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


RelayTarget = Literal["elder", "family", "community", "frontend"]
RelayStatus = Literal["pending", "delivered", "acknowledged", "cancelled", "expired"]
DisplayType = Literal["quiet_message", "sos", "alert", "announcement", "activity", "reminder"]


class RelayMessage(BaseModel):
    id: Optional[str] = None
    elder_user_id: str
    target: RelayTarget
    actor_role: str = "system"
    direction: str = "system_to_user"
    display_type: DisplayType = "quiet_message"
    risk_tier: RiskTier = "safe"
    title: str = ""
    content: str = ""
    reason_summary: Optional[str] = None
    raw_quotes: List[str] = Field(default_factory=list)
    suggested_actions: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)
    status: RelayStatus = "pending"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RelayAck(BaseModel):
    elder_user_id: str
    message_id: str
    actor_role: str
    status: RelayStatus = "acknowledged"
    text: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)
