import copy
import json
import os
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from filelock import FileLock

from src.config import Config


PathLike = Union[str, Path]


class DataStore:
    """Small JSON/JSONL persistence helper for non-vector business data."""

    def __init__(self, root_dir: Optional[PathLike] = None):
        self.root_dir = Path(root_dir or Config.DATA_DIR).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def resolve_path(self, relative_path: PathLike) -> Path:
        path = Path(relative_path)
        if path.is_absolute():
            raise ValueError("DataStore paths must be relative")

        resolved = (self.root_dir / path).resolve()
        root_text = self._path_for_compare(self.root_dir)
        resolved_text = self._path_for_compare(resolved)
        try:
            common_path = os.path.commonpath([root_text, resolved_text])
        except ValueError as exc:
            raise ValueError("DataStore path escapes root_dir") from exc
        if common_path != root_text:
            raise ValueError("DataStore path escapes root_dir")
        return resolved

    @staticmethod
    def _path_for_compare(path: Path) -> str:
        text = os.path.normcase(os.path.abspath(str(path)))
        if text.startswith("\\\\?\\"):
            text = text[4:]
        return text

    def user_path(self, elder_user_id: str, relative_path: PathLike) -> Path:
        user_id = self._validate_id(elder_user_id, "elder_user_id")
        return Path("users") / user_id / Path(relative_path)

    def read_json(self, relative_path: PathLike, default: Any = None) -> Any:
        path = self.resolve_path(relative_path)
        self._ensure_parent(path)
        lock = self._lock_for(path)
        with lock:
            if not path.exists():
                return copy.deepcopy(default)
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)

    def write_json(self, relative_path: PathLike, data: Any) -> Path:
        path = self.resolve_path(relative_path)
        self._ensure_parent(path)
        lock = self._lock_for(path)
        with lock:
            tmp_path = path.with_name(f"{path.name}.tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(self._to_jsonable(data), f, ensure_ascii=False, indent=2)
            tmp_path.replace(path)
        return path

    def append_jsonl(self, relative_path: PathLike, record: Any) -> Path:
        path = self.resolve_path(relative_path)
        self._ensure_parent(path)
        lock = self._lock_for(path)
        with lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(self._to_jsonable(record), ensure_ascii=False))
                f.write("\n")
        return path

    def read_jsonl(self, relative_path: PathLike, limit: Optional[int] = None) -> List[Any]:
        path = self.resolve_path(relative_path)
        self._ensure_parent(path)
        lock = self._lock_for(path)
        with lock:
            if not path.exists():
                return []
            records: List[Any] = []
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    records.append(json.loads(line))
            if limit is not None:
                return records[-limit:]
            return records

    def read_user_json(self, elder_user_id: str, relative_path: PathLike, default: Any = None) -> Any:
        return self.read_json(self.user_path(elder_user_id, relative_path), default=default)

    def write_user_json(self, elder_user_id: str, relative_path: PathLike, data: Any) -> Path:
        return self.write_json(self.user_path(elder_user_id, relative_path), data)

    def append_user_jsonl(self, elder_user_id: str, relative_path: PathLike, record: Any) -> Path:
        return self.append_jsonl(self.user_path(elder_user_id, relative_path), record)

    def read_user_jsonl(self, elder_user_id: str, relative_path: PathLike, limit: Optional[int] = None) -> List[Any]:
        return self.read_jsonl(self.user_path(elder_user_id, relative_path), limit=limit)

    def reset_user_state(self, elder_user_id: str) -> Dict[str, Any]:
        """Remove all DataStore-managed state for a single elder user.

        This is intentionally scoped to ``users/{elder_user_id}`` and does not
        touch global community data or the legacy vector/RAG store.
        """

        user_id = self._validate_id(elder_user_id, "elder_user_id")
        relative_dir = Path("users") / user_id
        user_dir = self.resolve_path(relative_dir)
        user_dir.parent.mkdir(parents=True, exist_ok=True)

        existed = user_dir.exists()
        files_removed = 0
        dirs_removed = 0
        if existed:
            files_removed = sum(1 for item in user_dir.rglob("*") if item.is_file())
            dirs_removed = sum(1 for item in user_dir.rglob("*") if item.is_dir())
            lock = self._lock_for(user_dir)
            with lock:
                if user_dir.exists():
                    shutil.rmtree(user_dir)

        return {
            "user_id": user_id,
            "path": relative_dir.as_posix(),
            "existed": existed,
            "files_removed": files_removed,
            "dirs_removed": dirs_removed,
        }

    def _ensure_parent(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

    def _lock_for(self, path: Path) -> FileLock:
        return FileLock(str(path) + ".lock")

    def _validate_id(self, value: str, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        if any(part in text for part in ("/", "\\", "..")):
            raise ValueError(f"{field_name} contains invalid path characters")
        return text

    def _to_jsonable(self, data: Any) -> Any:
        if hasattr(data, "model_dump"):
            return data.model_dump(mode="json")
        if hasattr(data, "dict"):
            return data.dict()
        if isinstance(data, dict):
            return {key: self._to_jsonable(value) for key, value in data.items()}
        if isinstance(data, (list, tuple)):
            return [self._to_jsonable(item) for item in data]
        if isinstance(data, (datetime, date)):
            return data.isoformat()
        return data
