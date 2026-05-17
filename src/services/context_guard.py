from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


class ContextGuard:
    """Lightweight context cleanup and semantic route hints for the realtime path."""

    DEFAULT_MAX_HISTORY_ITEMS = 8
    DEFAULT_MAX_AGE_HOURS = 24

    CRISIS_MARKERS = [
        "\u6d3b\u7740\u6ca1\u610f\u601d",
        "\u4e0d\u60f3\u6d3b\u4e86",
        "\u6b7b\u4e86\u7b97\u4e86",
        "\u6211\u60f3\u53bb\u6b7b",
        "\u4e0d\u60f3\u518d\u6491",
    ]
    ANXIETY_MARKERS = [
        "\u7d27\u5f20",
        "\u7126\u8651",
        "\u5fc3\u614c",
        "\u53d1\u614c",
        "\u5bb3\u6015",
        "\u62c5\u5fc3",
        "\u7761\u4e0d\u7740",
    ]
    LOW_MOOD_MARKERS = [
        "\u5b64\u72ec",
        "\u96be\u53d7",
        "\u6ca1\u610f\u601d",
        "\u4e0d\u60f3\u52a8",
        "\u6ca1\u529b\u6c14",
        "\u7a7a\u843d\u843d",
    ]
    SOMATIC_MARKERS = [
        "\u5934\u75bc",
        "\u5934\u75db",
        "\u80c3\u4e0d\u8212\u670d",
        "\u80f8\u95f7",
        "\u5fc3\u53e3\u4e0d\u8212\u670d",
    ]
    EMERGENCY_MARKERS = [
        "\u6551\u547d",
        "\u6454\u5012",
        "\u8dcc\u5012",
        "\u8d77\u4e0d\u6765",
        "\u80f8\u53e3\u75bc",
        "\u547c\u5438\u56f0\u96be",
        "\u5598\u4e0d\u4e0a\u6c14",
        "\u5feb\u4e0d\u884c\u4e86",
    ]
    MEDICATION_QUERY_MARKERS = [
        "\u5403\u836f\u65f6\u95f4",
        "\u5230\u70b9\u5403\u836f",
        "\u8be5\u5403\u836f",
        "\u7528\u836f\u63d0\u9192",
        "\u836f\u54c1\u8ba1\u5212",
        "\u670d\u836f\u63d0\u9192",
    ]
    SYSTEM_NOISE_MARKERS = [
        "[\u7cfb\u7edf",
        "\u7cfb\u7edf\u4e3b\u52a8\u5173\u6000",
        "proactive_question",
        "Generated proactive event",
    ]

    def sanitize_context(
        self,
        context: Optional[Dict[str, Any]],
        *,
        now: Optional[datetime] = None,
        max_history_items: int = DEFAULT_MAX_HISTORY_ITEMS,
        max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
    ) -> Dict[str, Any]:
        raw_context = dict(context or {})
        history = raw_context.get("recent_history") or []
        clean_history, dropped = self.sanitize_history(
            history,
            now=now,
            max_items=max_history_items,
            max_age_hours=max_age_hours,
        )
        raw_context["recent_history"] = clean_history
        raw_context["recent_history_text"] = self.format_history(clean_history)
        raw_context["elder_recent_utterances"] = [
            item.get("content", "")
            for item in clean_history
            if item.get("role") == "user"
        ]
        raw_context["memory_context"] = self.sanitize_memory_text(raw_context.get("memory_context"))
        raw_context["context_guard"] = {
            "dropped_history_count": dropped,
            "kept_history_count": len(clean_history),
        }
        return raw_context

    def sanitize_history(
        self,
        history: Iterable[Dict[str, Any]],
        *,
        now: Optional[datetime] = None,
        max_items: int = DEFAULT_MAX_HISTORY_ITEMS,
        max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
    ) -> Tuple[List[Dict[str, Any]], int]:
        now = self._normalize_datetime(now or datetime.now(timezone.utc))
        clean: List[Dict[str, Any]] = []
        dropped = 0
        for item in history or []:
            if not isinstance(item, dict):
                dropped += 1
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                dropped += 1
                continue
            if self._is_system_noise(content):
                dropped += 1
                continue
            timestamp = self._parse_datetime(item.get("timestamp"))
            if timestamp and now - timestamp > timedelta(hours=max_age_hours):
                dropped += 1
                continue
            clean.append({
                "timestamp": item.get("timestamp"),
                "role": role,
                "content": content,
            })
        if max_items is not None and len(clean) > max_items:
            dropped += len(clean) - max_items
            clean = clean[-max_items:]
        return clean, dropped

    def sanitize_memory_text(self, memory_text: Any, max_chars: int = 1200) -> str:
        text = str(memory_text or "")
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or self._is_system_noise(stripped):
                continue
            lines.append(stripped)
        return "\n".join(lines)[:max_chars]

    def format_history(self, history: Iterable[Dict[str, Any]]) -> str:
        lines = []
        for item in history or []:
            role = "elder" if item.get("role") == "user" else "assistant"
            content = str(item.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def route_override(
        self,
        input_text: str,
        *,
        assessment: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        text = str(input_text or "").strip()
        if not text:
            return None
        risk_tier = getattr(assessment, "risk_tier", None)
        if risk_tier in {"crisis", "high"} or self._contains_any(text, self.CRISIS_MARKERS):
            return "mental_health_agent"
        if self._contains_any(text, self.MEDICATION_QUERY_MARKERS):
            return "medical_agent"
        if self._contains_any(text, self.EMERGENCY_MARKERS):
            return "medical_agent"
        if self._contains_any(text, self.ANXIETY_MARKERS) and self._contains_any(text, self.SOMATIC_MARKERS):
            return "mental_health_agent"
        if self._contains_any(text, self.ANXIETY_MARKERS + self.LOW_MOOD_MARKERS):
            return "mental_health_agent"
        return None

    def _is_system_noise(self, content: str) -> bool:
        return any(marker in content for marker in self.SYSTEM_NOISE_MARKERS)

    def _contains_any(self, text: str, markers: Iterable[str]) -> bool:
        return any(marker in text for marker in markers)

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return self._normalize_datetime(value)
        text = str(value or "").strip()
        if not text:
            return None
        for parser in (
            lambda raw: datetime.fromisoformat(raw),
            lambda raw: datetime.strptime(raw, "%Y-%m-%d %H:%M:%S"),
        ):
            try:
                return self._normalize_datetime(parser(text))
            except ValueError:
                continue
        return None

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
