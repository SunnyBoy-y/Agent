import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.services.data_store import DataStore
from src.services.profile_service import ProfileService


class UserContextService:
    """Per-user lightweight context storage for fast-path orchestration."""

    CHAT_HISTORY_FILE = "chat_history.json"
    CHAT_CONTEXT_SUMMARY_FILE = "chat_context_summary.json"
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
        records = [
            self._sanitize_history_record(item)
            for item in history
            if self._is_history_record(item)
        ]
        return records[-limit:] if limit is not None else records

    def add_memory(self, elder_user_id: str, user_input: str, agent_response: str) -> None:
        user_id = self.normalize_user_id(elder_user_id)
        history = self.get_recent_history(user_id, limit=None)
        timestamp = self._now_text()
        history.append({"timestamp": timestamp, "role": "user", "content": user_input})
        history.append({
            "timestamp": timestamp,
            "role": "assistant",
            "content": self._sanitize_history_content(agent_response, user_input=user_input),
        })
        if len(history) > 100:
            overflow = history[:-60]
            self._merge_chat_summary(user_id, overflow)
            history = history[-60:]
        self.store.write_user_json(user_id, self.CHAT_HISTORY_FILE, history)

    def get_layered_chat_context(
        self,
        elder_user_id: str,
        *,
        recent_turns: int = 5,
        max_summary_chars: int = 1200,
    ) -> Dict[str, Any]:
        """Return Hermes-style layered context for model prompts.

        Recent raw dialogue stays as a sliding window. Older retained dialogue
        is compressed into a prompt-only summary and combined with any durable
        summary created before chat_history truncation.
        """
        user_id = self.normalize_user_id(elder_user_id)
        history = self.get_recent_history(user_id, limit=None)
        window_size = max(1, recent_turns) * 2
        recent_window = history[-window_size:] if history else []
        overflow = history[:-window_size] if len(history) > window_size else []
        stored_summary = self._load_chat_summary(user_id).get("summary", "")
        overflow_summary = self._summarize_history_records(overflow)
        summary_text = self._join_summary_parts(
            [stored_summary, overflow_summary],
            max_chars=max_summary_chars,
        )
        return {
            "summary": summary_text,
            "recent_window": recent_window,
            "recent_window_text": self._format_history_records(recent_window),
            "overflow_count": len(overflow),
            "recent_turns": recent_turns,
        }

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
            "profile_name": self.display_name(profile),
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

    def display_name(self, profile: Optional[Dict[str, Any]], fallback: str = "您") -> str:
        if not isinstance(profile, dict):
            return fallback
        name = str(profile.get("name") or "").strip()
        if name.lower() in {"unknown", "none", "null"}:
            return fallback
        return name or fallback

    def _sanitize_history_record(self, item: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = dict(item)
        cleaned["content"] = self._sanitize_history_content(cleaned.get("content", ""))
        return cleaned

    def _load_chat_summary(self, elder_user_id: str) -> Dict[str, Any]:
        raw = self.store.read_user_json(
            self.normalize_user_id(elder_user_id),
            self.CHAT_CONTEXT_SUMMARY_FILE,
            default={},
        )
        return raw if isinstance(raw, dict) else {}

    def _save_chat_summary(self, elder_user_id: str, summary: str) -> None:
        self.store.write_user_json(
            self.normalize_user_id(elder_user_id),
            self.CHAT_CONTEXT_SUMMARY_FILE,
            {
                "summary": str(summary or "").strip(),
                "updated_at": self._now_text(),
                "format": "deterministic_dialogue_summary_v1",
            },
        )

    def _merge_chat_summary(self, elder_user_id: str, records: List[Dict[str, Any]]) -> None:
        if not records:
            return
        existing = self._load_chat_summary(elder_user_id).get("summary", "")
        addition = self._summarize_history_records(records, max_turns=24)
        merged = self._join_summary_parts([existing, addition], max_chars=2400)
        if merged:
            self._save_chat_summary(elder_user_id, merged)

    def _join_summary_parts(self, parts: List[str], *, max_chars: int) -> str:
        lines: List[str] = []
        seen = set()
        for part in parts:
            for raw_line in str(part or "").splitlines():
                line = raw_line.strip()
                if not line or line in seen:
                    continue
                seen.add(line)
                lines.append(line)
        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text
        return text[-max_chars:].lstrip()

    def _summarize_history_records(
        self,
        records: List[Dict[str, Any]],
        *,
        max_turns: int = 16,
        max_chars_per_side: int = 90,
    ) -> str:
        if not records:
            return ""
        turns: List[Dict[str, str]] = []
        current: Dict[str, str] = {}
        for item in records:
            role = item.get("role")
            content = self._clip_text(item.get("content"), max_chars_per_side)
            if not content:
                continue
            if role == "user":
                if current:
                    turns.append(current)
                current = {"user": content}
            elif role == "assistant":
                if not current:
                    current = {}
                current["assistant"] = content
                turns.append(current)
                current = {}
        if current:
            turns.append(current)
        selected = turns[-max_turns:]
        lines = []
        for turn in selected:
            user = turn.get("user")
            assistant = turn.get("assistant")
            if user and assistant:
                lines.append(f"- 老人曾说：{user}；小暖回应：{assistant}")
            elif user:
                lines.append(f"- 老人曾说：{user}")
            elif assistant:
                lines.append(f"- 小暖曾回应：{assistant}")
        return "\n".join(lines)

    def _format_history_records(self, records: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for item in records or []:
            role = "老人" if item.get("role") == "user" else "小暖"
            content = str(item.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _clip_text(self, value: Any, max_chars: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 1)].rstrip() + "…"

    def _sanitize_history_content(self, content: Any, user_input: Any = None) -> str:
        text = str(content or "")
        for prefix in ("unknown，", "unknown,"):
            if text.lower().startswith(prefix.lower()):
                return text[len(prefix):].lstrip()
        if self._is_current_time_query(user_input) or "临时时间上下文" in text or "当前北京时间" in text:
            return self._redact_current_time_answer(text)
        return text

    def _is_current_time_query(self, text: Any) -> bool:
        value = str(text or "")
        if not value:
            return False
        return any(
            marker in value
            for marker in (
                "现在几点",
                "现在几时",
                "几点了",
                "几时了",
                "什么时间",
                "当前时间",
                "北京时间",
                "今天几号",
                "今天周几",
                "今天星期几",
            )
        )

    def _redact_current_time_answer(self, text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        generic = "我按当时的当前时间回答了老人。"
        value = re.sub(
            r"(?:当前)?北京?时间[是为：:\s]*\d{4}[-年/]\d{1,2}[-月/]\d{1,2}[日号]?"
            r"(?:\s*(?:周|星期)[一二三四五六日天])?(?:\s*\d{1,2}[:：点时]\d{0,2}\s*分?)?",
            generic,
            value,
        )
        value = re.sub(
            r"现在[是为：:\s]*(?:\d{4}[-年/]\d{1,2}[-月/]\d{1,2}[日号]?)?"
            r"(?:\s*(?:周|星期)[一二三四五六日天])?\s*\d{1,2}[:：点时]\d{0,2}\s*分?",
            generic,
            value,
        )
        value = re.sub(r"\b\d{1,2}:\d{2}\b", "当时那个时间", value)
        return value

    def _now_text(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
