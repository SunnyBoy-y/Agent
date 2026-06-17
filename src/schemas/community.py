from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


CommunityItemStatus = Literal["active", "expired", "cancelled"]
CommunityActorRole = Literal["community_admin", "system"]


class CommunityAnnouncement(BaseModel):
    id: str
    community_id: str
    actor_role: CommunityActorRole = "community_admin"
    title: str
    content: str
    tags: List[str] = Field(default_factory=list)
    priority: int = Field(default=1, ge=0)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    status: CommunityItemStatus = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CommunityActivity(BaseModel):
    id: str
    community_id: str
    title: str
    content: str = ""
    time_text: str = ""
    location: str = ""
    tags: List[str] = Field(default_factory=list)
    priority: int = Field(default=1, ge=0)
    valid_until: datetime
    status: CommunityItemStatus = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CommunityAnnouncementCreateRequest(BaseModel):
    community_id: str
    actor_role: CommunityActorRole = "community_admin"
    id: Optional[str] = None
    title: str
    content: str
    tags: List[str] = Field(default_factory=list)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    priority: int = Field(default=1, ge=0)


class CommunityAnnouncementUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    priority: Optional[int] = Field(default=None, ge=0)
    status: Optional[CommunityItemStatus] = None


class CommunityActivityCreateRequest(BaseModel):
    community_id: str
    id: Optional[str] = None
    title: str
    content: str = ""
    time_text: str = ""
    location: str = ""
    tags: List[str] = Field(default_factory=list)
    valid_until: datetime
    priority: int = Field(default=1, ge=0)


class CommunityActivityUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    time_text: Optional[str] = None
    location: Optional[str] = None
    tags: Optional[List[str]] = None
    valid_until: Optional[datetime] = None
    priority: Optional[int] = Field(default=None, ge=0)
    status: Optional[CommunityItemStatus] = None
