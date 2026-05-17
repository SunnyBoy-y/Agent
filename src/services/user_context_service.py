from datetime import datetime
from typing import Any, Dict, List, Optional

from src.services.data_store import DataStore
from src.services.profile_service import ProfileService


class UserContextService:
    """Per-user lightweight context storage for fast-path orchestration."""

    CHAT_HISTORY_FILE = "chat_history.json"
    EMOTION_LOG_FILE = "emotion_log.json"
    AGENT_STATUS_FILE = "agent_status.json"

    def __init__(
        self,
        store: Optional[DataStore] = None,
        profile_service: Optional[ProfileService] = None,
    ):
        self.store = store or DataStore()
        self.profile_service = profile_service or ProfileService(self.store)

    def normalize_user_id(self, user_id: Optional[str]) -> str:
        return str(user_id or "user_001").strip() or "user_001"

    def get_profile(self, elder_user_id: str) -> Dict[str, Any]:
        return self.profile_service.get_profile(self.normalize_user_id(elder_user_id))

    def update_profile(self, elder_user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        return self.profile_service.update_profile(self.normalize_user_id(elder_user_id), updates)

    def reset_profile(self, elder_user_id: str) -> Dict[str, Any]:
        return self.profile_service.reset_profile(self.normalize_user_id(elder_user_id))

    def get_recent_history(self, elder_user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        history = self.store.read_user_json(
            self.normalize_user_id(elder_user_id),
            self.CHAT_HISTORY_FILE,
            default=[],
        )
        if not isinstance(history, list):
            history = []
        records = [item for item in history if self._is_history_record(item)]
        return records[-limit:] if limit is not None else records

    def add_memory(self, elder_user_id: str, user_input: str, agent_response: str) -> None:
        user_id = self.normalize_user_id(elder_user_id)
        history = self.get_recent_history(user_id, limit=None)
        timestamp = self._now_text()
        history.append({"timestamp": timestamp, "role": "user", "content": user_input})
        history.append({"timestamp": timestamp, "role": "assistant", "content": agent_response})
        if len(history) > 100:
            history = history[-60:]
        self.store.write_user_json(user_id, self.CHAT_HISTORY_FILE, history)

    def build_default_agent_status(self) -> Dict[str, Any]:
        now = self._now_text()
        return {
            "last_user_interaction": now,
            "last_proactive_time": "2000-01-01 00:00:00",
            "last_proactive_domain": "",
            "last_proactive_content": "",
            "agent_last_update": {
                "medical": "2000-01-01 00:00:00",
                "daily_life": "2000-01-01 00:00:00",
                "interest": "2000-01-01 00:00:00",
                "mental_health": "2000-01-01 00:00:00",
                "emotional": "2000-01-01 00:00:00",
                "antifraud": "2000-01-01 00:00:00",
            },
        }

    def get_agent_status(self, elder_user_id: str) -> Dict[str, Any]:
        raw = self.store.read_user_json(
            self.normalize_user_id(elder_user_id),
            self.AGENT_STATUS_FILE,
            default=self.build_default_agent_status(),
        )
        return self.normalize_agent_status(raw)

    def update_agent_status(
        self,
        elder_user_id: str,
        user_interaction_time: Optional[str] = None,
        agent_type: Optional[str] = None,
        touch_user_interaction: bool = True,
    ) -> Dict[str, Any]:
        user_id = self.normalize_user_id(elder_user_id)
        status = self.get_agent_status(user_id)
        now = self._now_text()
        if user_interaction_time:
            status["last_user_interaction"] = user_interaction_time
        elif agent_type and touch_user_interaction:
            status["last_user_interaction"] = now
        if agent_type:
            status["agent_last_update"][agent_type] = now
        self.store.write_user_json(user_id, self.AGENT_STATUS_FILE, status)
        return status

    def update_proactive_status(self, elder_user_id: str, domain: str, content: str) -> Dict[str, Any]:
        user_id = self.normalize_user_id(elder_user_id)
        status = self.get_agent_status(user_id)
        now = self._now_text()
        status["last_proactive_time"] = now
        status["last_proactive_domain"] = domain
        status["last_proactive_content"] = content
        if domain in status["agent_last_update"]:
            status["agent_last_update"][domain] = now
        self.store.write_user_json(user_id, self.AGENT_STATUS_FILE, status)
        return status

    def log_emotion(self, elder_user_id: str, emotion: str, risk_level: str) -> None:
        user_id = self.normalize_user_id(elder_user_id)
        logs = self.store.read_user_json(user_id, self.EMOTION_LOG_FILE, default=[])
        if not isinstance(logs, list):
            logs = []
        logs = [item for item in logs if isinstance(item, dict)]
        logs.append({
            "timestamp": self._now_text(),
            "emotion": emotion,
            "risk_level": risk_level,
        })
        if len(logs) > 50:
            logs = logs[-50:]
        self.store.write_user_json(user_id, self.EMOTION_LOG_FILE, logs)

    def get_emotion_trend(self, elder_user_id: str) -> str:
        logs = self.store.read_user_json(
            self.normalize_user_id(elder_user_id),
            self.EMOTION_LOG_FILE,
            default=[],
        )
        if not isinstance(logs, list):
            return "no emotion records"
        recent = [item for item in logs if isinstance(item, dict)][-5:]
        if not recent:
            return "no emotion records"
        levels = [str(item.get("risk_level", "safe")) for item in recent]
        high_count = sum(1 for level in levels if level in ("high", "crisis"))
        medium_count = sum(1 for level in levels if level == "medium")
        if high_count:
            trend = "high risk signal"
        elif medium_count >= 2:
            trend = "unstable mood signal"
        else:
            trend = "stable"
        return f"recent risk levels: {levels} | trend: {trend}"

    def build_context_snapshot(self, elder_user_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        profile = context.get("user_profile") or {}
        return {
            "user_id": self.normalize_user_id(elder_user_id),
            "profile_name": profile.get("name", "unknown"),
            "health_condition": profile.get("health_condition", []),
            "preferences": profile.get("preferences", []),
            "visual_analysis": context.get("visual_analysis"),
            "voice_emotion": context.get("voice_emotion"),
            "recent_history_preview": (context.get("recent_history_text") or "")[:300],
            "memory_context_preview": (context.get("memory_context") or "")[:300],
        }

    def normalize_agent_status(self, status: Any) -> Dict[str, Any]:
        default = self.build_default_agent_status()
        if not isinstance(status, dict):
            return default
        merged = {**default, **status}
        raw_agent_updates = status.get("agent_last_update")
        if not isinstance(raw_agent_updates, dict):
            raw_agent_updates = {}
        merged["agent_last_update"] = {
            **default["agent_last_update"],
            **raw_agent_updates,
        }
        return merged

    def _is_history_record(self, item: Any) -> bool:
        return isinstance(item, dict) and "role" in item and "content" in item

    def _now_text(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
