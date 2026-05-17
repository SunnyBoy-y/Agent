import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.schemas.music_library import MusicLibraryRecord, MusicLibrarySong, MusicLibrarySyncRequest
from src.services.data_store import DataStore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def model_to_dict(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model or {})


class MusicLibraryService:
    """Per-user local music manifest used by agents and frontend play actions."""

    SONGS_FILE = "music_library/songs.json"

    def __init__(self, store: Optional[DataStore] = None):
        self.store = store or DataStore()

    def sync_library(self, request: MusicLibrarySyncRequest) -> Dict[str, Any]:
        user_id = self._normalize_id(request.elder_user_id, "elder_user_id")
        existing = [] if request.sync_mode == "replace" else self.list_records(user_id, include_inactive=True)
        by_id = {record.music_id: record for record in existing}
        now = utc_now()
        upserted = 0
        skipped_unchanged = 0

        for song in request.songs:
            record = self._record_from_song(user_id, song)
            old = by_id.get(record.music_id)
            if old and self._content_unchanged(old, record):
                old.updated_at = now
                by_id[record.music_id] = old
                skipped_unchanged += 1
                continue
            record.created_at = old.created_at if old else now
            record.updated_at = now
            by_id[record.music_id] = record
            upserted += 1

        records = list(by_id.values())
        self._save_records(user_id, records)
        return {
            "elder_user_id": user_id,
            "received": len(request.songs),
            "upserted": upserted,
            "skipped_unchanged": skipped_unchanged,
            "total": len([record for record in records if record.status == "active"]),
        }

    def list_records(self, elder_user_id: str, include_inactive: bool = False) -> List[MusicLibraryRecord]:
        raw = self.store.read_user_json(elder_user_id, self.SONGS_FILE, default=[])
        if not isinstance(raw, list):
            return []
        records = [MusicLibraryRecord(**item) for item in raw if isinstance(item, dict)]
        if include_inactive:
            return records
        return [record for record in records if record.status == "active"]

    def match_song(self, elder_user_id: str, query: str, *, limit: int = 1) -> Dict[str, Any]:
        user_id = self._normalize_id(elder_user_id, "elder_user_id")
        query = (query or "").strip()
        records = self.list_records(user_id)
        scored: List[Tuple[float, MusicLibraryRecord, str]] = []
        for record in records:
            score, reason = self.score_record(record, query)
            if score >= 25.0 or (not query and score >= 0):
                scored.append((score, record, reason))
        scored.sort(key=lambda item: item[0], reverse=True)
        matches = [
            {"score": score, "reason": reason, "song": model_to_dict(record)}
            for score, record, reason in scored[: max(int(limit or 0), 0)]
        ]
        return {
            "elder_user_id": user_id,
            "query": query,
            "matched": bool(matches),
            "matches": matches,
            "song": matches[0]["song"] if matches else None,
            "score": matches[0]["score"] if matches else 0.0,
            "reason": matches[0]["reason"] if matches else "",
        }

    def library_summary(self, elder_user_id: str, limit: int = 12) -> List[Dict[str, Any]]:
        records = self.list_records(elder_user_id)[: max(int(limit or 0), 0)]
        return [
            {
                "music_id": record.music_id,
                "name": record.name,
                "artist": record.artist,
                "description": record.description,
                "mood_tags": record.mood_tags,
                "scene_tags": record.scene_tags,
            }
            for record in records
        ]

    def score_record(self, record: MusicLibraryRecord, query: str) -> Tuple[float, str]:
        q = self._normalize(query)
        if not q:
            return (1.0, "empty_query_recent_library_order")
        fields = {
            "name": record.name,
            "artist": record.artist,
            "description": record.description,
            "aliases": record.aliases,
            "mood_tags": record.mood_tags,
            "scene_tags": record.scene_tags,
        }
        weights = {
            "name": 120.0,
            "aliases": 110.0,
            "mood_tags": 90.0,
            "scene_tags": 85.0,
            "description": 75.0,
            "artist": 40.0,
        }
        score = 0.0
        reasons: List[str] = []
        all_text_parts = []
        for field, value in fields.items():
            text = self._normalize(value)
            if not text:
                continue
            all_text_parts.append(text)
            if q in text:
                score += weights[field]
                reasons.append(field)
            elif text in q:
                score += weights[field] * 0.5
                reasons.append(f"query_contains_{field}")
        bag = "|".join(all_text_parts)
        if bag:
            score += SequenceMatcher(None, q, bag).ratio() * 60.0
        for token in self._tokens(query):
            t = self._normalize(token)
            if t and t in bag:
                score += 18.0
        return score, ",".join(reasons) or "semantic_similarity"

    def _record_from_song(self, user_id: str, song: MusicLibrarySong) -> MusicLibraryRecord:
        data = model_to_dict(song)
        data.pop("music_id", None)
        return MusicLibraryRecord(
            **data,
            music_id=self._music_id_for(song),
            elder_user_id=user_id,
        )

    def _music_id_for(self, song: MusicLibrarySong) -> str:
        for value in (song.music_id, song.playable_ref, song.name):
            text = str(value or "").strip()
            if text:
                return self._safe_identifier(text)
        return f"music_{uuid.uuid4().hex}"

    @staticmethod
    def _content_unchanged(old: MusicLibraryRecord, new: MusicLibraryRecord) -> bool:
        old_data = model_to_dict(old)
        new_data = model_to_dict(new)
        for key in ("created_at", "updated_at"):
            old_data.pop(key, None)
            new_data.pop(key, None)
        return old_data == new_data

    def _save_records(self, elder_user_id: str, records: Sequence[MusicLibraryRecord]) -> None:
        records = sorted(records, key=lambda record: record.updated_at, reverse=True)
        self.store.write_user_json(elder_user_id, self.SONGS_FILE, [model_to_dict(record) for record in records])

    @staticmethod
    def _normalize(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (list, tuple, set)):
            text = " ".join(str(item) for item in value if item is not None)
        elif isinstance(value, dict):
            text = " ".join(str(item) for item in value.values() if item is not None)
        else:
            text = str(value)
        return re.sub(r"\s+", "", text.lower())

    @staticmethod
    def _tokens(value: str) -> List[str]:
        return [part for part in re.split(r"[^\w\u4e00-\u9fff]+", value or "") if part]

    @staticmethod
    def _safe_identifier(value: str) -> str:
        text = re.sub(r"[^\w.:-]+", "_", value.strip(), flags=re.UNICODE).strip("_")
        return text[:160] or f"music_{uuid.uuid4().hex}"

    @staticmethod
    def _normalize_id(value: str, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        if any(part in text for part in ("/", "\\", "..")):
            raise ValueError(f"{field_name} contains invalid path characters")
        return text
