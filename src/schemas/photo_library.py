from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


PhotoSyncMode = Literal["upsert", "replace"]
PhotoStatus = Literal["active", "deleted"]


class PhotoPermission(BaseModel):
    allow_backend_cache: bool = True
    allow_visual_caption: bool = True


class PhotoLibraryItem(BaseModel):
    photo_id: Optional[str] = None
    file_uuid: Optional[str] = None
    url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    original_file_name: str = ""
    mime_type: str = "image/jpeg"
    size_bytes: Optional[int] = Field(default=None, ge=0)
    content_hash: Optional[str] = None
    taken_at: Optional[datetime] = None
    album: str = ""
    frontend_caption: str = ""
    tags: List[str] = Field(default_factory=list)
    people: List[str] = Field(default_factory=list)
    location: str = ""
    permission: PhotoPermission = Field(default_factory=PhotoPermission)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PhotoLibrarySyncRequest(BaseModel):
    elder_user_id: str
    source: str = "frontend_album"
    sync_mode: PhotoSyncMode = "upsert"
    photos: List[PhotoLibraryItem] = Field(default_factory=list)


class PhotoVisionCaption(BaseModel):
    description: str = ""
    people_hint: List[str] = Field(default_factory=list)
    family_labels: List[str] = Field(default_factory=list)
    scene: str = ""
    objects: List[str] = Field(default_factory=list)
    activity: str = ""
    emotion_hint: str = ""
    time_hint: str = ""
    searchable_text: str = ""
    safety_flags: List[str] = Field(default_factory=list)
    caption_source: str = "qwen_vision"
    vision_model: str = ""
    captioned_at: datetime = Field(default_factory=utc_now)


class PhotoLibraryRecord(PhotoLibraryItem):
    photo_id: str
    elder_user_id: str
    source: str = "frontend_album"
    status: PhotoStatus = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    vision: Optional[PhotoVisionCaption] = None
    last_caption_hash: Optional[str] = None


class PhotoCaptionRunResult(BaseModel):
    elder_user_id: str
    requested: int = 0
    captioned: int = 0
    skipped: int = 0
    failed: int = 0
    results: List[Dict[str, Any]] = Field(default_factory=list)

