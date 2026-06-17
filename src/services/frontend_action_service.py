import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


class FrontendActionService:
    """Translate backend events into one stable frontend action contract."""

    PRIORITY_RANK = {
        "low": 0,
        "normal": 1,
        "medium": 2,
        "high": 3,
        "crisis": 4,
    }
    WEATHER_CONDITIONS = {"sunny", "cloudy", "rain", "snow"}

    def is_weather_request(self, text: str) -> bool:
        value = str(text or "").strip().lower()
        if not value:
            return False
        explicit_phrases = (
            "看天气",
            "查天气",
            "今天天气怎么样",
            "今天天气如何",
            "天气怎么样",
            "天气如何",
            "会下雨吗",
            "要下雨吗",
            "需要带伞吗",
            "weather",
            "forecast",
        )
        return any(phrase in value for phrase in explicit_phrases)

    def is_restore_view_request(self, text: str) -> bool:
        value = str(text or "").strip().lower()
        if not value:
            return False
        explicit_phrases = (
            "恢复默认视角",
            "恢复默认",
            "回到默认视角",
            "收起天气",
            "关闭天气",
            "关掉天气",
            "close weather",
            "default view",
        )
        return any(phrase in value for phrase in explicit_phrases)

    def build_weather_action(
        self,
        *,
        source_turn_id: str,
        weather_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        weather = self._normalize_weather_snapshot(weather_snapshot)
        return {
            "action_id": f"act_weather_{uuid.uuid4().hex}",
            "source": "fast_chat",
            "source_turn_id": source_turn_id,
            "target_channel": "frontend",
            "action_type": "frontend_ui",
            "name": "show_weather",
            "priority": "normal",
            "requires_confirmation": False,
            "interrupt_policy": "replace_same_type",
            "payload": {
                "view": {
                    "camera_mode": "weather",
                    "show_weather_panel": True,
                    "letter_side": "right",
                },
                "weather": weather,
            },
        }

    def build_restore_default_view_action(self, *, source_turn_id: str) -> Dict[str, Any]:
        return {
            "action_id": f"act_restore_{uuid.uuid4().hex}",
            "source": "fast_chat",
            "source_turn_id": source_turn_id,
            "target_channel": "frontend",
            "action_type": "frontend_ui",
            "name": "restore_default_view",
            "priority": "normal",
            "requires_confirmation": False,
            "interrupt_policy": "replace_same_type",
            "payload": {},
        }

    def build_quiet_message_prompt_action(self, prompt: Dict[str, Any]) -> Dict[str, Any]:
        prompt_id = str(prompt.get("id") or "").strip()
        priority = str(prompt.get("priority") or "normal").strip() or "normal"
        if priority not in self.PRIORITY_RANK:
            priority = "normal"
        return {
            "action_id": f"quiet_message_{prompt_id}",
            "source": "family_policy",
            "source_turn_id": "",
            "target_channel": "frontend",
            "action_type": "quiet_message",
            "name": "prompt_quiet_message",
            "priority": priority,
            "requires_confirmation": True,
            "interrupt_policy": "queue",
            "payload": {
                "message_id": prompt_id,
                "from_display": prompt.get("from_display") or "",
                "message_type": prompt.get("message_type") or "quiet_message",
                "prompt_text": prompt.get("prompt_text") or "",
                "created_at": prompt.get("created_at") or "",
            },
        }

    def build_timed_event_actions(self, events: Iterable[Any]) -> List[Dict[str, Any]]:
        selected: Dict[str, Dict[str, Any]] = {}
        passthrough: List[Dict[str, Any]] = []

        for event in events or []:
            data = self._model_to_dict(event)
            action = self.build_timed_event_action(data)
            if action is None:
                continue

            dose_event_id = str((data.get("payload") or {}).get("dose_event_id") or "").strip()
            if data.get("event_type") in {"medication_due", "medication_overdue"} and dose_event_id:
                previous = selected.get(dose_event_id)
                if previous is None or self._priority_value(action) > self._priority_value(previous):
                    selected[dose_event_id] = action
                continue

            passthrough.append(action)

        return self.sort_actions([*selected.values(), *passthrough])

    def build_timed_event_action(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        event_type = str(event.get("event_type") or "").strip()
        payload = dict(event.get("payload") or {})
        priority = str(event.get("priority") or "medium").strip() or "medium"
        if priority not in self.PRIORITY_RANK:
            priority = "medium"

        if event_type in {"medication_due", "medication_overdue"}:
            return {
                "action_id": f"timed_{event.get('event_id')}",
                "source": "timed_event",
                "source_turn_id": "",
                "target_channel": "frontend",
                "action_type": "medication",
                "name": "show_medication_reminder",
                "priority": priority,
                "requires_confirmation": False,
                "interrupt_policy": "interrupt_lower_priority",
                "payload": {
                    "timed_event_id": event.get("event_id"),
                    "event_type": event_type,
                    "display_text": event.get("display_text") or payload.get("content") or "",
                    **payload,
                },
            }

        if event_type == "incoming_call":
            return {
                "action_id": f"timed_{event.get('event_id')}",
                "source": "timed_event",
                "source_turn_id": "",
                "target_channel": "frontend",
                "action_type": "other",
                "name": "incoming_call",
                "priority": priority,
                "requires_confirmation": False,
                "interrupt_policy": "interrupt_lower_priority" if priority in {"high", "crisis"} else "queue",
                "payload": {
                    "timed_event_id": event.get("event_id"),
                    "event_type": event_type,
                    "target": payload.get("target") or "contact",
                    "display_name": payload.get("display_name") or payload.get("from_display") or "",
                    **payload,
                },
            }

        return None

    def sort_actions(self, actions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def sort_key(action: Dict[str, Any]):
            payload = action.get("payload") or {}
            created_at = payload.get("created_at") or payload.get("scheduled_at") or ""
            return (-self._priority_value(action), str(created_at), str(action.get("action_id") or ""))

        return sorted((dict(item) for item in actions or []), key=sort_key)

    def _normalize_weather_snapshot(self, snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        data = dict(snapshot or {})
        condition = str(data.get("condition") or data.get("scene_condition") or "").strip().lower()
        if condition not in self.WEATHER_CONDITIONS:
            condition = ""
        return {
            "condition": condition,
            "temperature_text": str(data.get("temperature_text") or data.get("temperature") or "").strip(),
            "humidity_text": str(data.get("humidity_text") or data.get("humidity") or "").strip(),
            "wind_text": str(data.get("wind_text") or data.get("wind") or "").strip(),
            "summary": str(data.get("summary") or "").strip(),
            "tips": str(data.get("tips") or "").strip(),
        }

    def _priority_value(self, action: Dict[str, Any]) -> int:
        return self.PRIORITY_RANK.get(str(action.get("priority") or "normal"), 1)

    def _model_to_dict(self, model: Any) -> Dict[str, Any]:
        if isinstance(model, dict):
            return dict(model)
        if hasattr(model, "model_dump"):
            return model.model_dump(mode="json")
        if hasattr(model, "dict"):
            return model.dict()
        return dict(model or {})
