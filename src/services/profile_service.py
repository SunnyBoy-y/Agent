import copy
from typing import Any, Dict, Iterable, Optional

from src.services.data_store import DataStore


class ProfileService:
    """Per-user profile persistence built on DataStore."""

    PROFILE_FILE = "profile.json"

    def __init__(self, store: Optional[DataStore] = None):
        self.store = store or DataStore()

    def build_default_profile(self) -> Dict[str, Any]:
        return {
            "name": "",
            "health_condition": [],
            "family_members": [],
            "preferences": [],
            "medications": [],
            "dialect": "unknown",
        }

    def get_profile(self, elder_user_id: str) -> Dict[str, Any]:
        raw = self.store.read_user_json(
            elder_user_id,
            self.PROFILE_FILE,
            default=self.build_default_profile(),
        )
        return self.normalize_profile(raw)

    def update_profile(self, elder_user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        profile = self.get_profile(elder_user_id)
        if not isinstance(updates, dict):
            return copy.deepcopy(profile)
        for key, value in (updates or {}).items():
            if key == "user_id":
                continue
            self._apply_field(profile, key, value)
        normalized = self.normalize_profile(profile)
        self.store.write_user_json(elder_user_id, self.PROFILE_FILE, normalized)
        return copy.deepcopy(normalized)

    def reset_profile(self, elder_user_id: str) -> Dict[str, Any]:
        profile = self.build_default_profile()
        self.store.write_user_json(elder_user_id, self.PROFILE_FILE, profile)
        return copy.deepcopy(profile)

    def normalize_profile(self, profile: Any) -> Dict[str, Any]:
        normalized = self.build_default_profile()
        if isinstance(profile, dict):
            normalized.update(profile)

        for key in ("health_condition", "family_members", "preferences", "medications"):
            if normalized.get(key) is None:
                normalized[key] = []
            elif not isinstance(normalized.get(key), list):
                normalized[key] = [normalized[key]]

        for key in ("name", "dialect"):
            if not isinstance(normalized.get(key), str):
                normalized[key] = self.build_default_profile()[key]
            else:
                normalized[key] = normalized[key].strip()

        if normalized.get("name", "").lower() in {"unknown", "none", "null"}:
            normalized["name"] = ""

        return normalized

    def _apply_field(self, profile: Dict[str, Any], key: str, value: Any) -> None:
        if key in ("health_condition", "family_members", "preferences", "medications"):
            if isinstance(value, list):
                profile[key] = self._dedupe_list(value)
            else:
                current = profile.get(key)
                if not isinstance(current, list):
                    current = []
                if value not in current:
                    current.append(value)
                profile[key] = current
            return

        if isinstance(value, dict) and isinstance(profile.get(key), dict):
            profile[key] = {**profile[key], **value}
            return

        profile[key] = value

    def _dedupe_list(self, values: Iterable[Any]) -> list:
        result = []
        for value in values:
            if value not in result:
                result.append(value)
        return result
