import base64
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests

try:
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional until live vision captioning is invoked
    HumanMessage = None
    ChatOpenAI = None

from src.config import Config
from src.schemas.photo_library import (
    PhotoCaptionRunResult,
    PhotoLibraryItem,
    PhotoLibraryRecord,
    PhotoLibrarySyncRequest,
    PhotoVisionCaption,
)
from src.services.data_store import DataStore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class QwenVisionCaptioner:
    """Vision captioner for Qwen-compatible OpenAI chat APIs."""

    def __init__(self, model_name: Optional[str] = None):
        if ChatOpenAI is None or HumanMessage is None:
            raise RuntimeError("langchain_openai and langchain_core are required for Qwen vision captioning")
        self.model_name = model_name or Config.MODEL_NAME
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=self.model_name,
            temperature=0.1,
        )

    def caption(self, image_url: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        metadata = metadata or {}
        image_payload = self._build_image_payload(image_url, metadata)
        prompt = (
            "请用严格 JSON 描述这张家庭相册照片。不要凭脸识别具体身份；"
            "如果 metadata 里已经有人物标签，可以引用这些标签。"
            "字段必须包括 description, people_hint, family_labels, scene, objects, "
            "activity, emotion_hint, time_hint, searchable_text, safety_flags。"
        )
        response = self.llm.invoke(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {"type": "text", "text": f"metadata: {json.dumps(metadata, ensure_ascii=False)}"},
                        image_payload,
                    ]
                )
            ]
        )
        return self._parse_json_response(getattr(response, "content", "") or "")

    def _build_image_payload(self, image_url: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        if metadata.get("image_base64"):
            mime_type = metadata.get("mime_type") or "image/jpeg"
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{metadata['image_base64']}"},
            }

        if image_url and self._should_inline_url(image_url):
            try:
                response = requests.get(image_url, timeout=Config.EXTERNAL_REQUEST_TIMEOUT)
                if response.status_code == 200 and response.content:
                    max_bytes = int(getattr(Config, "PHOTO_CAPTION_MAX_IMAGE_BYTES", 8 * 1024 * 1024))
                    if len(response.content) <= max_bytes:
                        mime_type = metadata.get("mime_type") or response.headers.get("content-type") or "image/jpeg"
                        encoded = base64.b64encode(response.content).decode("ascii")
                        return {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        }
            except Exception:
                pass

        return {"type": "image_url", "image_url": {"url": image_url}}

    def _should_inline_url(self, image_url: str) -> bool:
        lowered = (image_url or "").lower()
        return "localhost" in lowered or "127.0.0.1" in lowered

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        text = (text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).strip()
            text = re.sub(r"```$", "", text).strip()
        try:
            return json.loads(text)
        except Exception:
            match = re.search(r"\{.*\}", text, re.S)
            if match:
                return json.loads(match.group(0))
            return {"description": text}


class PhotoLibraryService:
    """Per-user local photo index and optional vision-caption cache."""

    PHOTOS_FILE = "photo_library/photos.json"
    CAPTION_AUDIT_FILE = "photo_library/caption_audit.jsonl"
    IMPORT_AUDIT_FILE = "photo_library/imports.jsonl"

    SEARCH_FIELDS = (
        "description",
        "frontend_caption",
        "tags",
        "people",
        "location",
        "album",
        "original_file_name",
        "vision.description",
        "vision.people_hint",
        "vision.family_labels",
        "vision.scene",
        "vision.objects",
        "vision.activity",
        "vision.emotion_hint",
        "vision.time_hint",
        "vision.searchable_text",
    )

    def __init__(self, store: Optional[DataStore] = None, captioner: Optional[Any] = None):
        self.store = store or DataStore()
        self.captioner = captioner

    def sync_photos(self, request: PhotoLibrarySyncRequest) -> Dict[str, Any]:
        user_id = self._normalize_id(request.elder_user_id, "elder_user_id")
        skipped_permission = 0
        incoming = []
        for item in request.photos:
            if not item.permission.allow_backend_cache:
                skipped_permission += 1
                continue
            incoming.append(self._record_from_item(user_id, item, source=request.source))
        existing = [] if request.sync_mode == "replace" else self.list_records(user_id, include_deleted=True)
        by_id = {record.photo_id: record for record in existing}

        upserted = 0
        skipped_unchanged = 0
        now = utc_now()
        for record in incoming:
            old = by_id.get(record.photo_id)
            if old and self._content_unchanged(old, record):
                old.updated_at = now
                by_id[record.photo_id] = old
                skipped_unchanged += 1
                continue
            if old and old.vision and old.last_caption_hash == record.content_hash:
                record.vision = old.vision
                record.last_caption_hash = old.last_caption_hash
            record.created_at = old.created_at if old else now
            record.updated_at = now
            by_id[record.photo_id] = record
            upserted += 1

        records = list(by_id.values())
        self._save_records(user_id, records)
        caption_jobs_created = sum(1 for record in incoming if self._needs_caption(record))
        return {
            "elder_user_id": user_id,
            "received": len(request.photos),
            "upserted": upserted,
            "skipped_unchanged": skipped_unchanged,
            "skipped_permission": skipped_permission,
            "caption_jobs_created": caption_jobs_created,
            "total": len([record for record in records if record.status == "active"]),
        }

    def import_library_bytes(
        self,
        elder_user_id: str,
        content: bytes,
        *,
        file_name: str = "photo_library.json",
        source: str = "frontend_album",
        sync_mode: str = "upsert",
    ) -> Dict[str, Any]:
        user_id = self._normalize_id(elder_user_id, "elder_user_id")
        if not content:
            raise ValueError("import file is empty")
        max_bytes = int(getattr(Config, "PHOTO_IMPORT_MAX_BYTES", 32 * 1024 * 1024))
        if len(content) > max_bytes:
            raise ValueError(f"import file is too large: {len(content)} bytes")

        safe_name = self._safe_file_name(file_name)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".json", ".sqlite", ".db"}:
            raise ValueError("photo library import supports .json, .sqlite, or .db")

        import_id = f"import_{uuid.uuid4().hex}"
        raw_path = self.store.user_path(user_id, Path("photo_library") / "imports" / import_id / safe_name)
        resolved = self.store.resolve_path(raw_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(content)

        photos = self._extract_imported_photos(resolved, suffix)
        result = self.sync_photos(
            PhotoLibrarySyncRequest(
                elder_user_id=user_id,
                source=source,
                sync_mode=sync_mode,  # type: ignore[arg-type]
                photos=photos,
            )
        )
        audit = {
            "import_id": import_id,
            "elder_user_id": user_id,
            "file_name": safe_name,
            "bytes": len(content),
            "photos": len(photos),
            "stored_path": str(raw_path).replace("\\", "/"),
            "created_at": utc_now().isoformat(),
        }
        self.store.append_user_jsonl(user_id, self.IMPORT_AUDIT_FILE, audit)
        return {**result, "import_id": import_id, "stored_path": audit["stored_path"]}

    def list_records(self, elder_user_id: str, include_deleted: bool = False) -> List[PhotoLibraryRecord]:
        raw = self.store.read_user_json(elder_user_id, self.PHOTOS_FILE, default=[])
        if not isinstance(raw, list):
            return []
        records = [self._parse_record(item) for item in raw if isinstance(item, dict)]
        if include_deleted:
            return records
        return [record for record in records if record.status == "active"]

    def search_photos(
        self,
        elder_user_id: str,
        query: str = "",
        *,
        limit: int = 20,
    ) -> List[PhotoLibraryRecord]:
        records = self.list_records(elder_user_id)
        query = (query or "").strip()
        if not query:
            return sorted(records, key=lambda record: record.updated_at, reverse=True)[: max(limit, 0)]

        scored = [(self.score_record(record, query), record) for record in records]
        selected = [(score, record) for score, record in scored if score >= 30.0]
        selected.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in selected[: max(limit, 0)]]

    def search_for_agent(self, elder_user_id: str, query: str = "", *, limit: int = 20) -> List[Dict[str, Any]]:
        return [self.build_agent_photo_result(record) for record in self.search_photos(elder_user_id, query, limit=limit)]

    def caption_pending(
        self,
        elder_user_id: str,
        *,
        photo_ids: Optional[Sequence[str]] = None,
        limit: int = 5,
        force: bool = False,
        captioner: Optional[Any] = None,
    ) -> PhotoCaptionRunResult:
        user_id = self._normalize_id(elder_user_id, "elder_user_id")
        records = self.list_records(user_id, include_deleted=True)
        requested_ids = set(photo_ids or [])
        candidates = [
            record
            for record in records
            if record.status == "active"
            and (not requested_ids or record.photo_id in requested_ids)
            and (force or self._needs_caption(record))
        ][: max(limit, 0)]

        result = PhotoCaptionRunResult(elder_user_id=user_id, requested=len(candidates))
        active_captioner = captioner or self.captioner or QwenVisionCaptioner()
        by_id = {record.photo_id: record for record in records}

        for record in candidates:
            try:
                image_url = record.url or self._download_url(record)
                if not image_url:
                    result.skipped += 1
                    result.results.append({"photo_id": record.photo_id, "status": "skipped", "reason": "missing_url"})
                    continue
                caption_payload = active_captioner.caption(image_url, self._caption_metadata(record))
                caption = self._caption_from_payload(caption_payload)
                caption.vision_model = caption.vision_model or getattr(active_captioner, "model_name", Config.MODEL_NAME)
                record.vision = caption
                record.last_caption_hash = record.content_hash
                record.updated_at = utc_now()
                by_id[record.photo_id] = record
                result.captioned += 1
                item = {"photo_id": record.photo_id, "status": "captioned", "caption": self._model_to_dict(caption)}
                result.results.append(item)
                self.store.append_user_jsonl(user_id, self.CAPTION_AUDIT_FILE, item)
            except Exception as exc:
                result.failed += 1
                item = {"photo_id": record.photo_id, "status": "failed", "error": str(exc)}
                result.results.append(item)
                self.store.append_user_jsonl(user_id, self.CAPTION_AUDIT_FILE, item)

        result.skipped += max(0, result.requested - result.captioned - result.failed)
        self._save_records(user_id, list(by_id.values()))
        return result

    def score_record(self, record: PhotoLibraryRecord, query: str) -> float:
        query_text = self._normalize_search_text(query)
        if not query_text:
            return 0.0
        search_text = self._normalize_search_text(self._record_search_text(record))
        if not search_text:
            return 0.0
        score = 0.0
        if query_text in search_text:
            score += 120.0
        score += SequenceMatcher(None, query_text, search_text).ratio() * 80.0
        for token in self._tokens(query):
            normalized = self._normalize_search_text(token)
            if normalized and normalized in search_text:
                score += 25.0
        return score

    def build_agent_photo_result(self, record: PhotoLibraryRecord) -> Dict[str, Any]:
        vision = record.vision
        description = ""
        caption_source = None
        if vision and vision.description:
            description = vision.description
            caption_source = vision.caption_source
        elif record.frontend_caption:
            description = record.frontend_caption
            caption_source = "frontend"
        original_file_name = record.original_file_name or ""
        people = self._dedupe([*record.people, *((vision.family_labels if vision else []) or [])])
        tags = self._dedupe([*record.tags, *((vision.objects if vision else []) or []), *(([vision.scene] if vision and vision.scene else []))])
        return {
            "url": record.url or self._download_url(record),
            "thumbnail_url": record.thumbnail_url,
            "desc": description or original_file_name,
            "type": record.mime_type,
            "tags": tags,
            "description": description,
            "people": people,
            "location": record.location or (vision.scene if vision else ""),
            "time_text": self._time_text(record, vision),
            "caption_source": caption_source,
            "original_file_name": original_file_name,
            "metadata_available": bool(description or people or tags or record.location or record.taken_at),
            "photo_id": record.photo_id,
            "file_uuid": record.file_uuid,
        }

    def summarize_music_photo_context(self, elder_user_id: str, limit: int = 8) -> str:
        records = self.list_records(elder_user_id)[:limit]
        lines = []
        for record in records:
            result = self.build_agent_photo_result(record)
            bits = [result.get("desc"), ",".join(result.get("people") or []), result.get("location")]
            line = " / ".join(str(bit) for bit in bits if bit)
            if line:
                lines.append(f"- {line}")
        return "\n".join(lines)

    def _record_from_item(self, user_id: str, item: PhotoLibraryItem, *, source: str) -> PhotoLibraryRecord:
        photo_id = self._photo_id_for(item)
        data = self._model_to_dict(item)
        data.pop("photo_id", None)
        return PhotoLibraryRecord(
            **data,
            photo_id=photo_id,
            elder_user_id=user_id,
            source=source or "frontend_album",
        )

    def _photo_id_for(self, item: PhotoLibraryItem) -> str:
        for value in (item.photo_id, item.file_uuid, item.content_hash, item.url, item.original_file_name):
            text = str(value or "").strip()
            if text:
                return self._safe_identifier(text)
        return f"photo_{uuid.uuid4().hex}"

    def _extract_imported_photos(self, path: Path, suffix: str) -> List[PhotoLibraryItem]:
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            raw_items = data.get("photos", data) if isinstance(data, dict) else data
            if not isinstance(raw_items, list):
                raise ValueError("JSON photo import must be a list or an object with photos")
            return [self._coerce_photo_item(item) for item in raw_items if isinstance(item, dict)]
        return self._extract_sqlite_photos(path)

    def _extract_sqlite_photos(self, path: Path) -> List[PhotoLibraryItem]:
        uri = f"file:{path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.row_factory = sqlite3.Row
            tables = {
                row["name"]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "photos" not in tables:
                raise ValueError("SQLite photo import must contain a photos table")
            rows = connection.execute("SELECT * FROM photos").fetchall()
            return [self._photo_item_from_sqlite_row(dict(row)) for row in rows]
        finally:
            connection.close()

    def _photo_item_from_sqlite_row(self, row: Dict[str, Any]) -> PhotoLibraryItem:
        def first(*names: str) -> Any:
            for name in names:
                if name in row and row.get(name) is not None:
                    return row.get(name)
            return None

        def list_field(name: str) -> List[str]:
            value = row.get(name)
            if not value:
                return []
            if isinstance(value, list):
                return value
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else [str(parsed)]
            except Exception:
                return [part.strip() for part in str(value).split(",") if part.strip()]

        return PhotoLibraryItem(
            photo_id=first("photo_id", "id"),
            file_uuid=first("file_uuid", "uuid"),
            url=first("url", "download_url"),
            thumbnail_url=first("thumbnail_url", "thumb_url"),
            original_file_name=first("original_file_name", "originalFileName", "file_name", "filename") or "",
            mime_type=first("mime_type", "fileType", "content_type") or "image/jpeg",
            size_bytes=first("size_bytes", "size"),
            content_hash=first("content_hash", "sha256", "md5"),
            taken_at=self._parse_datetime(first("taken_at", "created_at", "date")),
            album=first("album", "album_name") or "",
            frontend_caption=first("frontend_caption", "caption", "description") or "",
            tags=list_field("tags_json") or list_field("tags"),
            people=list_field("people_json") or list_field("people"),
            location=first("location", "place") or "",
            permission={
                "allow_backend_cache": bool(first("allow_backend_cache") if first("allow_backend_cache") is not None else 1),
                "allow_visual_caption": bool(first("allow_visual_caption") if first("allow_visual_caption") is not None else 1),
            },
        )

    def _coerce_photo_item(self, item: Dict[str, Any]) -> PhotoLibraryItem:
        data = dict(item)
        if "caption" in data and "frontend_caption" not in data:
            data["frontend_caption"] = data.pop("caption")
        if "description" in data and "frontend_caption" not in data:
            data["frontend_caption"] = data.pop("description")
        if "uuid" in data and "file_uuid" not in data:
            data["file_uuid"] = data.pop("uuid")
        if "originalFileName" in data and "original_file_name" not in data:
            data["original_file_name"] = data.pop("originalFileName")
        if "fileType" in data and "mime_type" not in data:
            data["mime_type"] = data.pop("fileType")
        return PhotoLibraryItem(**data)

    def _caption_from_payload(self, payload: Dict[str, Any]) -> PhotoVisionCaption:
        normalized = dict(payload or {})
        for key in ("people_hint", "family_labels", "objects", "safety_flags"):
            value = normalized.get(key)
            if value is None:
                normalized[key] = []
            elif isinstance(value, str):
                normalized[key] = [part.strip() for part in re.split(r"[,，、\s]+", value) if part.strip()]
        if not normalized.get("searchable_text"):
            normalized["searchable_text"] = " ".join(
                str(normalized.get(key) or "")
                for key in ("description", "scene", "activity", "emotion_hint", "time_hint")
            )
        return PhotoVisionCaption(**normalized)

    def _needs_caption(self, record: PhotoLibraryRecord) -> bool:
        if not record.permission.allow_visual_caption:
            return False
        if not (record.url or record.file_uuid):
            return False
        if record.vision is None:
            return True
        return bool(record.content_hash and record.last_caption_hash != record.content_hash)

    def _caption_metadata(self, record: PhotoLibraryRecord) -> Dict[str, Any]:
        data = self._model_to_dict(record)
        data.pop("vision", None)
        return data

    def _content_unchanged(self, old: PhotoLibraryRecord, new: PhotoLibraryRecord) -> bool:
        return bool(old.content_hash and new.content_hash and old.content_hash == new.content_hash)

    def _record_search_text(self, record: PhotoLibraryRecord) -> str:
        parts: List[str] = []
        data = self._model_to_dict(record)
        for field in self.SEARCH_FIELDS:
            value: Any = data
            for segment in field.split("."):
                value = value.get(segment) if isinstance(value, dict) else None
            parts.append(self._flatten(value))
        return " ".join(part for part in parts if part)

    def _download_url(self, record: PhotoLibraryRecord) -> Optional[str]:
        if record.file_uuid:
            return f"{Config.FILE_SERVICE_BASE_URL}/api/file/download/{record.file_uuid}"
        return None

    def _time_text(self, record: PhotoLibraryRecord, vision: Optional[PhotoVisionCaption]) -> str:
        if record.taken_at:
            return record.taken_at.isoformat()
        return vision.time_hint if vision else ""

    def _load_records(self, elder_user_id: str) -> List[PhotoLibraryRecord]:
        return self.list_records(elder_user_id, include_deleted=True)

    def _save_records(self, elder_user_id: str, records: List[PhotoLibraryRecord]) -> None:
        self.store.write_user_json(elder_user_id, self.PHOTOS_FILE, records)

    def _parse_record(self, item: Dict[str, Any]) -> PhotoLibraryRecord:
        if hasattr(PhotoLibraryRecord, "model_validate"):
            return PhotoLibraryRecord.model_validate(item)
        return PhotoLibraryRecord.parse_obj(item)

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None

    def _safe_file_name(self, value: str) -> str:
        name = Path(str(value or "photo_library.json")).name
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", name) or "photo_library.json"

    def _safe_identifier(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.:-]+", "_", value).strip("_")[:160] or f"photo_{uuid.uuid4().hex}"

    def _normalize_id(self, value: str, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        if any(part in text for part in ("/", "\\", "..")):
            raise ValueError(f"{field_name} contains invalid path characters")
        return text

    def _normalize_search_text(self, value: Any) -> str:
        return re.sub(r"\s+", "", self._flatten(value).lower())

    def _tokens(self, value: str) -> Iterable[str]:
        return [part for part in re.split(r"[^\w\u4e00-\u9fff]+", value or "") if part]

    def _flatten(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (list, tuple, set)):
            return " ".join(self._flatten(item) for item in value)
        if isinstance(value, dict):
            return " ".join(self._flatten(item) for item in value.values())
        return str(value)

    def _dedupe(self, values: Iterable[Any]) -> List[str]:
        result: List[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    def _model_to_dict(self, model: Any) -> Dict[str, Any]:
        if hasattr(model, "model_dump"):
            return model.model_dump(mode="json")
        if hasattr(model, "dict"):
            return model.dict()
        return dict(model or {})
