import re
from typing import Any, Dict


class ResponseStyleGuard:
    """Final-pass cleanup for repeated addressing and awkward openings."""

    def clean(self, text: str, context: Dict[str, Any]) -> str:
        value = str(text or "").strip()
        if not value:
            return ""

        scene = context.get("scene_context") or {}
        addressing = scene.get("addressing_policy") or {}
        display_name = str(addressing.get("display_name") or "").strip()
        last_used_name = bool(addressing.get("last_assistant_used_name"))

        if display_name:
            value = self._remove_repeated_name(value, display_name, last_used_name)

        value = self._collapse_repeated_openings(value)
        return value.strip()

    def filter_repeated_input_prefix(self, text: str, user_input: str):
        """Suppress a model echo of the current user input at reply start."""
        raw = str(text or "")
        user_text = str(user_input or "").strip()
        if not raw or not user_text:
            return raw, "", True

        leading_match = re.match(
            "^[\\s\"'\\u201c\\u201d\\u2018\\u2019\\u300c\\u300d\\u300e\\u300f\\u3010\\u3011]+",
            raw,
        )
        leading = leading_match.group(0) if leading_match else ""
        body = raw[len(leading):]
        if not body:
            return "", raw, False

        if user_text.startswith(body):
            return "", raw, False

        if body.startswith(user_text):
            remaining = body[len(user_text):]
            remaining = re.sub(
                "^[\\s\"'\\u201c\\u201d\\u2018\\u2019\\u300c\\u300d\\u300e\\u300f\\u3010\\u3011"
                ":\\uff1a,\\uff0c.\\u3002!\\uff01?\\uff1f;\\uff1b\\u3001\\u2026\\-]+",
                "",
                remaining,
            )
            return remaining, "", True

        return raw, "", True

    def _remove_repeated_name(self, text: str, display_name: str, last_used_name: bool) -> str:
        escaped = re.escape(display_name)
        leading_pattern = re.compile(rf"^\s*{escaped}\s*[，,。:：、\-\s]*")

        if last_used_name and leading_pattern.match(text):
            return leading_pattern.sub("", text, count=1).lstrip()

        first_index = text.find(display_name)
        if first_index < 0:
            return text

        head = text[: first_index + len(display_name)]
        tail = text[first_index + len(display_name):].replace(display_name, "")
        return head + tail

    def _collapse_repeated_openings(self, text: str) -> str:
        text = re.sub(r"^(?:您[，,。:：\s]*){2,}", "您", text)
        text = re.sub(r"^(?:你[，,。:：\s]*){2,}", "你", text)
        text = re.sub(r"^(?:咱们[，,。:：\s]*){2,}", "咱们", text)
        text = re.sub(r"^(?:您好[，,。:：\s]*){2,}", "您好", text)
        return text
