from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


MusicSyncMode = Literal["upsert", "replace"]
MusicStatus = Literal["active", "inactive"]


class MusicLibrarySong(BaseModel):
    music_id: Optional[str] = None
    name: str
    artist: str = ""
    description: str = ""
    aliases: List[str] = Field(default_factory=list)
    mood_tags: List[str] = Field(default_factory=list)
    scene_tags: List[str] = Field(default_factory=list)
    duration_seconds: Optional[int] = Field(default=None, ge=0)
    playable_ref: str = ""
    status: MusicStatus = "active"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MusicLibrarySyncRequest(BaseModel):
    elder_user_id: str
    sync_mode: MusicSyncMode = "upsert"
    songs: List[MusicLibrarySong] = Field(default_factory=list)


class MusicLibraryRecord(MusicLibrarySong):
    music_id: str
    elder_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MusicMatchResult(BaseModel):
    song: Optional[MusicLibraryRecord] = None
    score: float = 0.0
    reason: str = ""

